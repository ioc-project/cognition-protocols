#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Run the comparison scenarios (A: free speech on a star, a model at the hub; B: free speech on SLIM; C: CONCORD on a star, a model at the
hub; D: CONCORD on SLIM; CONCORD always starts with a free-text step in which each participant posts what it knows) several
times, one after the other, and print what each run decided and a table per scenario.

    uv run python batch.py --scenarios A,B,C,D --thresholds 0.7 --repeats 5 [any other flag of run.sh: --model, --temperature, --fake-llm, ...]

Each run is checked by verify.py (a referee of the protocol and the harness); a run that fails is left out of the totals.
Each run is a `run.sh` in its own folder, runs/batch-<time>/t<threshold>-r<n>/ (the runs share one SLIM address, so they are
never parallel). The table and a summary per threshold are printed at the end and written to summary.json next to the runs.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import re
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path

import show
import verify
from concord_comparison.scenario import AGENTS, FACILITATOR, Scenario

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIO = HERE / "scenarios" / "evacuation-west-city.yaml"


def failure(run_dir, stderr=""):
    """Why a run left no outcome: the last error line of the logs of its agents (participant-a first), else what run.sh said."""
    for who in AGENTS:
        try:
            lines = [l.strip() for l in (run_dir / f"{who}.log").read_text(errors="replace").splitlines() if l.strip()]
        except OSError:
            continue
        errors = [l for l in lines if "Error" in l or "error:" in l]
        if errors:
            return f"{who}: {errors[-1][:300]}"
    last = [l for l in stderr.splitlines() if l.strip()]
    return last[-1][:300] if last else "no log (see the folder)"


def first_answers(run_dir):
    """A fingerprint of what each agent first wrote in a run. The same fingerprint in two runs means the model gave the very same text
    twice: not chance at a temperature above 0, but a cache replaying an earlier answer."""
    parts = []
    for who in AGENTS:
        try:
            transcript = json.loads((run_dir / f"{who}.json").read_text())["transcript"]
        except (OSError, ValueError, KeyError):
            return None
        for m in transcript:
            if m["role"] != "assistant":
                continue
            calls = [b for b in m["content"] if b.get("type") == "tool_use"]
            if calls:
                parts.append(json.dumps(calls[0]["input"], sort_keys=True))
                break
    return hashlib.md5("\n".join(parts).encode()).hexdigest()[:8] if parts else None


def _load(run_dir, who):
    try:
        return json.loads((run_dir / f"{who}.json").read_text())
    except (OSError, ValueError):
        return None


def _tokens(run_dir):
    used = 0
    for who in (FACILITATOR,) + AGENTS:
        u = (_load(run_dir, who) or {}).get("usage") or {}
        used += sum(u.get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))   # what was sent, read from a cache, and written
    return used


def _hidden_shared(scenario, channel):
    """How many of the hidden facts reached the channel, by a rough test: most of the words of a fact appear in one post (a heuristic)."""
    posts = [set(re.findall(r"[a-z]{4,}", m["text"].lower())) for m in channel if m["from"] in AGENTS]
    facts = [f for hidden in scenario.hidden.values() for f in hidden]
    shared = sum(any(len(words & post) >= 0.6 * len(words) for post in posts) for words in (set(re.findall(r"[a-z]{4,}", f.lower())) for f in facts))
    return shared, len(facts)


def read(run_dir, mode, scenario):
    """What one run decided, from the record of the facilitator (and the token use of all four agents)."""
    f = _load(run_dir, "facilitator")
    if f is None:
        return {"outcome": "NO RECORD", "winner": None, "rounds": None, "status": None, "tokens": None}
    r = f.get("report") or {}
    if not r.get("outcome"):
        return {"outcome": "FAILED", "winner": None, "rounds": None, "status": None, "tokens": 0}
    slowest = max(((_load(run_dir, w) or {}).get("usage", {}).get("max_seconds", 0) for w in (FACILITATOR,) + AGENTS), default=0)
    row = {"outcome": r.get("outcome"), "tokens": _tokens(run_dir), "slowest_call": slowest, "rounds": None, "status": r.get("status")}
    events = f.get("events") or []
    row["misrouted"] = sum(e["event"] == "misrouted" for e in events)
    row["dropped"] = sum(e["event"] == "dropped" for e in events)
    row["finals"] = [((_load(run_dir, a) or {}).get("final") or {}).get("option") for a in AGENTS]   # private, asked in both modes
    if mode == "free":
        row["winner"] = r.get("option")
        row["posts"] = (r.get("close") or {}).get("posts")
        row["closed_by"] = (r.get("close") or {}).get("reason")
        row["shared"] = _hidden_shared(scenario, f.get("channel") or [])
    else:
        row["winner"] = (r.get("winner") or {}).get("candidate")
        row["rounds"] = ((_load(run_dir, "participant-a") or {}).get("report") or {}).get("rounds")
        msgs = show.load(run_dir)
        row["posts"] = len(msgs)
        # what the participants wrote for the others: the rationale and the reasoning of each message
        texts = []
        for m in msgs:
            if m["from"] in AGENTS:
                try:
                    texts += [{"from": m["from"], "text": t} for _, t in show.texts(m["text"])]
                except Exception:                                      # a message that cannot be read has no text to count
                    pass
        texts += [m for m in f.get("channel") or [] if m["from"] in AGENTS]       # the posts of step 1
        row["shared"] = _hidden_shared(scenario, texts)
    return row


def check_free(run_dir):
    """The light referee of a free run: the record is whole (a channel, a verdict, a private final answer of every participant)."""
    f = _load(run_dir, "facilitator") or {}
    problems = []
    if not f.get("channel"):
        problems.append("no channel transcript")
    if (f.get("report") or {}).get("outcome") not in ("AGREED", "NOT_AGREED"):
        problems.append("no verdict")
    problems += [f"{a} gave no final answer" for a in AGENTS if not ((_load(run_dir, a) or {}).get("final") or {}).get("option")]
    return problems


def check_hub(run_dir):
    """What the hub model did with the messages it was given: the routing, which verify.py does not look at."""
    f = _load(run_dir, "facilitator") or {}
    events = f.get("events") or []
    sent = len(f.get("routed") or {})
    passed = {e["message"] for e in events if e["event"] == "delegated"}
    return [f"{sent - len(passed)} of {sent} messages were never passed on"] if sent > len(passed) else []


def print_replays(rows):
    """Say which runs began with exactly the same answers, and how many distinct runs there really are."""
    groups = defaultdict(list)
    for r in rows:
        if r.get("first_answers"):
            groups[r["first_answers"]].append(r["run"])
    same = [g for g in groups.values() if len(g) > 1]
    if not groups:
        return
    print(f"\ndistinct starts: {len(groups)} of {sum(len(g) for g in groups.values())} runs")
    for g in same:
        print(f"  identical first answers in {', '.join(g)}: the same text twice, most likely a cache replaying one answer. "
              "Count them as one run (with --fake-llm this is expected).")


SCENARIOS = {                                                       # name: (what it is, mode, transport)
    "A": ("free star", "free", "star"),                              # free speech; a model at the hub passes the posts on
    "B": ("free slim", "free", "slim"),                              # free speech on a SLIM group session (broadcast)
    "C": ("concord star", "concord", "star"),                        # step 1 in free text, then CONCORD; a model at the hub
    "D": ("concord slim", "concord", "slim"),                        # step 1 in free text, then CONCORD, on SLIM
}
SCHEME = 2            # the letters of the scenarios: before 2026-11-18 they were A concord star, B concord slim, C free slim, D concord slim
                      # with step 1, E free star, F concord star with step 1 (batches without "scheme" in their rows; metrics.py knows them)

_print_lock = threading.Lock()
_ports_lock = threading.Lock()
_ports_given = set()


def emit(*lines):
    """Print whole lines from the thread of a run, so that those of runs in parallel do not mix."""
    with _print_lock:
        print("\n".join(lines), flush=True)


def new_ports(count):
    """`count` TCP ports that are free now and not given to another run of this batch (the ports of a run stay in use after this call)."""
    with _ports_lock:
        while True:
            held = [socket.socket() for _ in range(count)]
            for h in held:
                h.bind(("127.0.0.1", 0))
            ports = [h.getsockname()[1] for h in held]
            for h in held:
                h.close()
            if not _ports_given & set(ports):
                _ports_given.update(ports)
                return ports


def run_with_limit(cmd, env, limit):
    """Run `cmd` (in a group of its own) for at most `limit` seconds. Returns (stderr, timed_out). On a timeout the whole group gets SIGTERM,
    which makes each agent write its record, and SIGKILL if it is still there after 15 seconds."""
    proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        return proc.communicate(timeout=limit)[1], False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            return proc.communicate(timeout=15)[1], True
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            return proc.communicate()[1], True


def one(base, key, t, n, rest, scenario_file, scenario, parallel=1, limit=900):
    name, mode, transport = SCENARIOS[key]
    label = f"{key}-t{t if t is not None else 'agent'}-r{n}" if mode == "concord" else f"{key}-r{n}"
    run_dir = base / label
    where, env = [], {**os.environ, "RUN_DIR": str(run_dir)}
    if parallel > 1:                                                    # runs in parallel each have their own SLIM node and star port
        slim, hub = new_ports(2)
        where = ["--slim", f"127.0.0.1:{slim}", "--hub", f"127.0.0.1:{hub}"]
        env["SKIP_SYNC"] = "1"                                          # main has synced already: runs must not sync together
    cmd = [str(HERE / "run.sh"), "--mode", mode, "--transport", transport, "--scenario", str(scenario_file),
           *(["--threshold", t] if t is not None and mode == "concord" else []), *where, *rest]
    emit(f"==> {label} ({name})")
    t0 = time.time()
    stderr, timed_out = run_with_limit(cmd, env, limit)
    row = {"run": label, "scheme": SCHEME, "scenario": key, "mode": mode, "transport": transport, "threshold": t if mode == "concord" else None,
           "seconds": round(time.time() - t0), "first_answers": first_answers(run_dir), **read(run_dir, mode, scenario)}
    if timed_out:                                                        # whatever it wrote, a run that did not end in time is not a result
        row.update(outcome="TIMEOUT", valid=None, error=f"the run did not end in {limit}s and was stopped")
        emit(f"{label}: TIMEOUT: {row['error']}\n    logs: {run_dir}")
        return row
    if row["outcome"] in ("FAILED", "NO RECORD"):
        row["error"] = failure(run_dir, stderr)
        row["valid"] = None
        emit(f"{label}: {row['outcome']}: {row['error']}\n    logs: {run_dir}")
        return row
    if mode == "free":
        problems = check_free(run_dir) + (check_hub(run_dir) if key == "A" else [])
    else:
        result = verify.verify(run_dir)                                # a referee for the run: see verify.py
        problems = [c[1] for c in result["checks"] if c[2] == "FAIL"] + (check_hub(run_dir) if key == "C" else []) + [
            f"{a} gave no final answer" for a in AGENTS if not ((_load(run_dir, a) or {}).get("final") or {}).get("option")]
    row["valid"], row["failed_checks"] = not problems, problems
    lines = []
    if row["slowest_call"] > 30:
        lines.append(f"    note: a model call took {row['slowest_call']:.0f}s: the endpoint was slow (an idle close may have come early)")
    lines.append(f"    {label}: {row['outcome']} {row['winner']} rounds={row['rounds']} posts={row['posts']} tokens={row['tokens']} "
                 f"seconds={row['seconds']} verify={'ok' if row['valid'] else 'FAILED ' + ','.join(row['failed_checks'])}")
    emit(*lines)
    return row


def summarize(rows, correct):
    print(f"\n{'scenario':<24}{'runs':<6}{'valid':<7}{'right':<7}{'agents right':<14}{'facts shared':<14}{'avg tokens':<12}{'avg secs':<10}{'avg msgs':<10}{'misrouted':<11}{'dropped'}")
    for sc, (name, _, _) in SCENARIOS.items():
        rs = [r for r in rows if r["scenario"] == sc]
        if not rs:
            continue
        ok = [r for r in rs if r["valid"]]                              # only valid runs count
        avg = lambda key: round(sum(r[key] for r in ok if isinstance(r.get(key), (int, float))) / max(1, len(ok)))
        agents = f"{sum(f == correct for r in ok for f in r['finals'])}/{3 * len(ok)}"
        facts = f"{sum(r['shared'][0] for r in ok)}/{sum(r['shared'][1] for r in ok)}"
        print(f"{sc + ' ' + name:<24}{len(rs):<6}{len(ok):<7}{sum(r['winner'] == correct for r in ok):<7}{agents:<14}{facts:<14}"
              f"{avg('tokens'):<12}{avg('seconds'):<10}{avg('posts'):<10}{sum(r.get('misrouted', 0) for r in ok):<11}{sum(r.get('dropped', 0) for r in ok)}")
    free = [r for r in rows if r["scenario"] in ("A", "B") and r["valid"]]
    if free:
        print("\nfree speech details: " + "; ".join(f"{r['run']}: {r['outcome']} {r['winner']}, finals {r['finals']}, closed by {r['closed_by']}, "
                                           f"hidden facts shared {r['shared'][0]}/{r['shared'][1]}" for r in free))
    for a, b, what in (("A", "B", "transport, free speech: star with a model hub vs SLIM broadcast"),
                       ("C", "D", "transport, CONCORD: star with a model hub vs SLIM broadcast"),
                       ("B", "D", "protocol: free speech vs CONCORD, both on SLIM"),
                       ("A", "C", "protocol: free speech vs CONCORD, both on a star")):
        ra, rb = [r for r in rows if r["scenario"] == a and r["valid"]], [r for r in rows if r["scenario"] == b and r["valid"]]
        if ra and rb:
            print(f"{a} vs {b} ({what}): right {sum(r['winner'] == correct for r in ra)}/{len(ra)} vs {sum(r['winner'] == correct for r in rb)}/{len(rb)}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="Other flags are passed to run.sh.")
    p.add_argument("--scenarios", default="A,B,C,D", help="which to run: A (free speech, star, a model at the hub), B (free speech, SLIM), "
                                                    "C (CONCORD, star, a model at the hub), D (CONCORD, SLIM); CONCORD starts with a free-text step")
    p.add_argument("--thresholds", default="", help="comma-separated thresholds fixed by the harness for C and D; empty: the default of the facilitator")
    p.add_argument("--scenario", default=str(DEFAULT_SCENARIO), help="the scenario file (default the evacuation one)")
    p.add_argument("--parallel", type=int, default=1,
                   help="how many runs at the same time (default 1). Each has its own SLIM node and ports. The model endpoint then gets "
                        "that many times more calls at once: if it slows down, the seconds of the runs, and C's idle close, are less "
                        "comparable, so use 1 when the time matters")
    p.add_argument("--run-timeout", type=int, default=900,
                   help="seconds after which a run is stopped and counted as TIMEOUT (default 900; free speech alone can take 10 minutes by design)")
    p.add_argument("--repeats", type=int, default=1, help="runs of each scenario (and threshold) (default 1)")
    args, rest = p.parse_known_args()
    keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
    if not keys or any(k not in SCENARIOS for k in keys):
        p.error(f"--scenarios takes some of {', '.join(SCENARIOS)}, comma-separated")
    scenario = Scenario(args.scenario)
    thresholds = [t for t in args.thresholds.split(",") if t != ""] or [None]
    base = HERE / "runs" / f"batch-{time.strftime('%Y%m%d-%H%M%S')}"
    jobs = [(key, t, n) for n in range(args.repeats)            # the scenarios take turns, so that a drift of the model hits all of them alike
            for key in keys for t in (thresholds if SCENARIOS[key][1] == "concord" else [None])]
    parallel = max(1, args.parallel)
    if parallel > 1:
        subprocess.run(["uv", "sync", "--quiet"], cwd=HERE, check=True)     # once, here: the runs must not sync together
        emit(f"{len(jobs)} runs, {parallel} at a time")
    base.mkdir(parents=True, exist_ok=True)
    finished, lock = [], threading.Lock()

    def work(job):
        key, t, n = job
        row = one(base, key, t, n, rest, args.scenario, scenario, parallel, args.run_timeout)
        with lock:
            finished.append(row)
            (base / "summary.json").write_text(json.dumps(finished, indent=2))   # kept up to date, in case the batch is stopped
        return row

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        rows = list(pool.map(work, jobs))                                       # in the order of the jobs, whatever finishes first
    summarize(rows, scenario.correct)
    excluded = [r for r in rows if r["valid"] is False]
    if excluded:
        print("\nexcluded, failed verification: " + "; ".join(f"{r['run']} ({','.join(r['failed_checks'])})" for r in excluded)
              + "\n  run `uv run python verify.py <run folder>` for the detail")
    left_out = [r for r in rows if r["valid"] is None]
    if left_out:
        print("\nnot counted, no result: " + "; ".join(f"{r['run']} ({r['outcome']}: {r.get('error', '')[:100]})" for r in left_out))
    print_replays(rows)
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(rows, indent=2))
    print(f"\nrecords: {base}")


if __name__ == "__main__":
    sys.exit(main())
