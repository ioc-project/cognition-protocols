#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Run the comparison scenarios (M: free speech on a full mesh; S: free speech on SLIM; C: CONCORD on SLIM; four agents, all in the
discussion, no facilitator; O1 to O3: a coordinator with no facts opens and runs the session: O1 free speech over a star, O2 free
speech over SLIM, O3 CONCORD over SLIM; CONCORD always starts with a free-text step in which each agent posts
what it knows) several times, and print what each run decided and a table per scenario.

    uv run python batch.py --scenarios M,S,C --thresholds 0.7 --repeats 5 [any other flag of run.sh: --model, --temperature, --fake-llm, ...]

Each run is checked by verify.py (a referee of the protocol and the harness) for C and by a record check for M and S; a run that fails is
left out of the totals. Each run is a `run.sh` in its own folder, runs/batch-<time>/<label>/. The initiator of a CONCORD run rotates over
the four agents from one repeat to the next. The table and a summary per threshold are printed at the end and written to summary.json
next to the runs.
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

from tools import show
from tools import verify
from blog_v2.common import NO_DECISION, ORCH
from blog_v2.scenario import AGENTS, DEFAULT_SCENARIO, SCENARIOS as SCENARIOS_DIR, Scenario, scenario_files, set_roster

HERE = Path(__file__).resolve().parent


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
    for who in AGENTS + (ORCH,):
        u = (_load(run_dir, who) or {}).get("usage") or {}
        used += sum(u.get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))   # what was sent, read from a cache, and written
    return used


def _hidden_shared(scenario, channel):
    """How many of the hidden facts reached the channel, by a rough test: most of the words of a fact appear in one post (a heuristic)."""
    posts = [set(re.findall(r"[a-z]{4,}", m["text"].lower())) for m in channel if m["from"] in AGENTS]
    facts = [f for hidden in scenario.hidden.values() for f in hidden]
    shared = sum(any(len(words & post) >= 0.6 * len(words) for post in posts) for words in (set(re.findall(r"[a-z]{4,}", f.lower())) for f in facts))
    return shared, len(facts)


def channel_of(run_dir):
    """What was posted in free text in a run (the discussion of free speech, step 1 of CONCORD), oldest first: [{"t", "from", "text"}],
    from the `posted` event of each agent."""
    out = [{"t": e["at"], "from": who, "text": e["text"]} for who in AGENTS for e in (_load(run_dir, who) or {}).get("events") or []
           if e["event"] == "posted"]
    return sorted(out, key=lambda m: m["t"])


def read(run_dir, mode, scenario, orchestrated=False):
    """What one run decided, from the records of the agents (and of the coordinator, when there is one)."""
    records = {a: _load(run_dir, a) for a in AGENTS + ((ORCH,) if orchestrated else ())}
    if any(r is None for r in records.values()):
        return {"outcome": "NO RECORD", "winner": None, "rounds": None, "status": None, "tokens": None}
    finals = [(records[a].get("final") or {}).get("option") for a in AGENTS]    # private, asked in both modes (O1, O2: `done`)
    slowest = max((r.get("usage", {}).get("max_seconds", 0) for r in records.values()), default=0)
    row = {"tokens": _tokens(run_dir), "slowest_call": slowest, "rounds": None, "status": None, "finals": finals}
    channel = channel_of(run_dir)
    events = (records.get(ORCH) or {}).get("events") or []
    row["misrouted"] = sum(e["event"] == "misrouted" for e in events)
    row["dropped"] = sum(e["event"] == "dropped" for e in events)
    if orchestrated:
        row["orchestrator_tokens"] = sum((records[ORCH].get("usage") or {}).get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))
    if mode == "free":
        if orchestrated:                                           # O1, O2: the coordinator summarizes the outcome from the final choices
            report = records[ORCH].get("report") or {}
            if not report.get("outcome"):
                return {"outcome": "FAILED", "winner": None, "rounds": None, "status": None, "tokens": 0}
            row.update(outcome=report["outcome"], winner=report.get("option"), posts=len(channel), shared=_hidden_shared(scenario, channel))
            row["closed_by"] = (report.get("close") or {}).get("reason")
            row["unanimous"] = len(set(finals)) == 1 and finals[0] not in (None, NO_DECISION)     # the four final choices, as a check on the summary
        else:
            agreed = len(set(finals)) == 1 and finals[0] not in (None, NO_DECISION)       # everyone's private final choice is the same option
            row.update(outcome="AGREED" if agreed else "NOT_AGREED", winner=finals[0] if agreed else None, posts=len(channel),
                       shared=_hidden_shared(scenario, channel))
            closes = [(records[a].get("close") or {}).get("reason") for a in AGENTS]
            row["closed_by"] = ",".join(sorted(set(str(c) for c in closes)))
    else:
        report = next((records[a]["report"] for a in ((ORCH,) if orchestrated else ()) + AGENTS if records[a].get("report")), None) or {}
        if not report.get("outcome"):
            return {"outcome": "FAILED", "winner": None, "rounds": None, "status": None, "tokens": 0}
        row.update(outcome=report["outcome"], status=report.get("status"), winner=(report.get("winner") or {}).get("candidate"),
                   rounds=report.get("rounds") or ((records[scenario.agents[0]].get("report") or {}).get("rounds")))
        msgs = show.load(run_dir)
        row["posts"] = len(msgs)
        # what the agents wrote for the others: the rationale and the reasoning of each message, and the posts of step 1
        texts = [m for m in channel]
        for m in msgs:
            try:
                texts += [{"from": m["from"], "text": t} for _, t in show.texts(m["text"])]
            except Exception:                                      # a message that cannot be read has no text to count
                pass
        row["shared"] = _hidden_shared(scenario, texts)
    return row


def check_free(run_dir, mode="free", orchestrated=False):
    """The light referee of a free run: the record is whole (everyone posted in step 1; a final answer of every agent; with a coordinator, its summary)."""
    problems = []
    channel = channel_of(run_dir)
    problems += [f"{a} did not post in step 1" for a in AGENTS if not any(m["from"] == a for m in channel)]
    problems += [f"{a} gave no final answer" for a in AGENTS if not ((_load(run_dir, a) or {}).get("final") or {}).get("option")]
    if orchestrated:
        report = (_load(run_dir, ORCH) or {}).get("report") or {}
        if report.get("outcome") not in ("AGREED", "NOT_AGREED", "DECIDED"):
            problems.append("no outcome from the coordinator")
    return problems


def check_hub(run_dir):
    """What the coordinator's model did with the posts it was given: the routing (O1)."""
    f = _load(run_dir, ORCH) or {}
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


SCENARIOS = {                                                       # name: (what it is, mode, transport, a coordinator)
    "M": ("free mesh", "free", "mesh", False),                       # free speech on a full mesh: every post goes to all the others
    "S": ("free slim", "free", "slim", False),                       # free speech on a SLIM group session (broadcast)
    "C": ("concord slim", "concord", "slim", False),                 # step 1 in free text, then CONCORD, on SLIM
    "O1": ("orch free star", "free", "star", True),                  # a coordinator passes every post on (delegate_to); summarizes the outcome
    "O2": ("orch free slim", "free", "slim", True),                  # the same over SLIM: nothing to pass on
    "O3": ("orch concord slim", "concord", "slim", True),            # the coordinator is the initiator of a CONCORD run, not a participant
}
SCHEME = 4            # the letters of this set of scenarios (blog-v2); before it (3) O1 was ask-and-decide and O2 to O4 are today's O1 to O3 (metrics.py maps them)

_print_lock = threading.Lock()
_roster_lock = threading.Lock()      # the roster (AGENTS) is one object for the process: a scenario with other agents sets it while its records are read
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


def one(base, key, t, n, rest, scenario_file, scenario, parallel=1, limit=900, task=None):
    name, mode, transport, orchestrated = SCENARIOS[key]
    where_task = f"-{task}" if task else ""                              # the task is in the name only when a batch has several
    label = f"{key}{where_task}-t{t if t is not None else 'agent'}-r{n}" if mode == "concord" else f"{key}{where_task}-r{n}"
    run_dir = base / label
    initiator = scenario.agents[n % len(scenario.agents)]              # the initiator of a CONCORD run rotates with the repeat
    where, env = [], {**os.environ, "RUN_DIR": str(run_dir)}
    if parallel > 1:                                                    # runs in parallel each have their own SLIM node and mesh ports
        slim, hub, *mesh = new_ports(2 + len(scenario.agents))
        where = ["--slim", f"127.0.0.1:{slim}", "--hub", f"127.0.0.1:{hub}", "--mesh-ports", ",".join(map(str, mesh))]
        env["SKIP_SYNC"] = "1"                                          # main has synced already: runs must not sync together
    cmd = [str(HERE / "run.sh"), "--mode", mode, "--transport", transport, "--scenario", str(scenario_file),
           *(["--orchestrated"] if orchestrated else []),
           *(["--threshold", t] if t is not None and mode == "concord" else []),
           *(["--initiator", initiator] if mode == "concord" and not orchestrated else []),
           *where, *rest]
    emit(f"==> {label} ({name})")
    t0 = time.time()
    stderr, timed_out = run_with_limit(cmd, env, limit)
    with _roster_lock:                                                   # read the records with the agents of this scenario
        set_roster(scenario.agents)
        return finish(run_dir, label, key, name, mode, transport, orchestrated, t, initiator, scenario, t0, timed_out, stderr, limit)


def finish(run_dir, label, key, name, mode, transport, orchestrated, t, initiator, scenario, t0, timed_out, stderr, limit):
    """What a run left, as a row: what it decided, and whether its record passes the referee."""
    row = {"run": label, "scheme": SCHEME, "scenario": key, "task": scenario.id, "correct": scenario.correct, "decoy": scenario.decoys,
           "agents": list(scenario.agents), "mode": mode, "transport": transport, "threshold": t if mode == "concord" else None,
           "initiator": ORCH if orchestrated else initiator if mode == "concord" else None, "orchestrated": orchestrated,
           "seconds": round(time.time() - t0), "first_answers": first_answers(run_dir), **read(run_dir, mode, scenario, orchestrated)}
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
        problems = check_free(run_dir, mode, orchestrated) + (check_hub(run_dir) if key == "O1" else [])
    else:
        result = verify.verify(run_dir)                                # a referee for the run: see verify.py
        problems = [c[1] for c in result["checks"] if c[2] == "FAIL"] + [
            f"{a} gave no final answer" for a in AGENTS if not ((_load(run_dir, a) or {}).get("final") or {}).get("option")]
    row["valid"], row["failed_checks"] = not problems, problems
    lines = []
    if row["slowest_call"] > 30:
        lines.append(f"    note: a model call took {row['slowest_call']:.0f}s: the endpoint was slow (an idle close may have come early)")
    lines.append(f"    {label}: {row['outcome']} {row['winner']} rounds={row['rounds']} posts={row['posts']} tokens={row['tokens']} "
                 f"seconds={row['seconds']} verify={'ok' if row['valid'] else 'FAILED ' + ','.join(row['failed_checks'])}")
    emit(*lines)
    return row


def summarize(rows):
    print(f"\n{'scenario':<24}{'runs':<6}{'valid':<7}{'right':<7}{'agents right':<14}{'facts shared':<14}{'avg tokens':<12}{'avg secs':<10}{'avg msgs':<10}{'misrouted':<11}{'dropped'}")
    for sc, (name, *_) in SCENARIOS.items():
        rs = [r for r in rows if r["scenario"] == sc]
        if not rs:
            continue
        ok = [r for r in rs if r["valid"]]                              # only valid runs count
        avg = lambda key: round(sum(r[key] for r in ok if isinstance(r.get(key), (int, float))) / max(1, len(ok)))
        agents = f"{sum(f == r['correct'] for r in ok for f in r['finals'])}/{sum(len(r['finals']) for r in ok)}"
        facts = f"{sum(r['shared'][0] for r in ok)}/{sum(r['shared'][1] for r in ok)}" if any(r["shared"][1] for r in ok) else "-"
        print(f"{sc + ' ' + name:<24}{len(rs):<6}{len(ok):<7}{sum(r['winner'] == r['correct'] for r in ok):<7}{agents:<14}{facts:<14}"
              f"{avg('tokens'):<12}{avg('seconds'):<10}{avg('posts'):<10}{sum(r.get('misrouted', 0) for r in ok):<11}{sum(r.get('dropped', 0) for r in ok)}")
    if any(r.get("decoy") for r in rows):                               # the decoy of each task is the plan or discount that looks right on the shared evidence
        print("\ndecoy chosen (the winner is the decoy of its task):")
        for sc, (name, *_) in SCENARIOS.items():
            ok = [r for r in rows if r["scenario"] == sc and r["valid"] and r.get("decoy")]
            if ok:
                print(f"  {sc + ' ' + name:<24}{sum(r['winner'] in r['decoy'] for r in ok)}/{len(ok)}")
    tasks = sorted({r["task"] for r in rows})
    if len(tasks) > 1:                                                  # right answers by task: "right/valid runs"
        print(f"\n{'right by task':<24}" + "".join(f"{t.removeprefix('evacuation-'):<14}" for t in tasks))
        for sc, (name, *_) in SCENARIOS.items():
            if any(r["scenario"] == sc for r in rows):
                cells = []
                for t in tasks:
                    ok = [r for r in rows if r["scenario"] == sc and r["task"] == t and r["valid"]]
                    cells.append(f"{sum(r['winner'] == r['correct'] for r in ok)}/{len(ok)}")
                print(f"{sc + ' ' + name:<24}" + "".join(f"{c:<14}" for c in cells))
    free = [r for r in rows if r["scenario"] in ("M", "S", "O1", "O2") and r["valid"]]
    if free:
        print("\nfree speech details: " + "; ".join(f"{r['run']}: {r['outcome']} {r['winner']}, finals {r['finals']}, closed by {r['closed_by']}, "
                                           f"hidden facts shared {r['shared'][0]}/{r['shared'][1]}" for r in free))
    for a, b, what in (("M", "S", "transport, free speech: full mesh vs SLIM broadcast"),
                       ("S", "C", "protocol: free speech vs CONCORD, both on SLIM"),
                       ("O1", "O2", "transport, free speech with a coordinator: star with a routing model vs SLIM broadcast"),
                       ("O2", "O3", "protocol with a coordinator: free speech vs CONCORD, both on SLIM"),
                       ("S", "O2", "a coordinator or none, free speech on SLIM"),
                       ("C", "O3", "a coordinator or none, CONCORD on SLIM")):
        ra, rb = [r for r in rows if r["scenario"] == a and r["valid"]], [r for r in rows if r["scenario"] == b and r["valid"]]
        if ra and rb:
            print(f"{a} vs {b} ({what}): right {sum(r['winner'] == r['correct'] for r in ra)}/{len(ra)} vs {sum(r['winner'] == r['correct'] for r in rb)}/{len(rb)}")


def task_paths(text):
    """The task files named in `--tasks`: a file name from scenarios/ (with or without 'evacuation-'), a path, a use case (a sub-folder of
    scenarios/: hidden-bench, e-commerce, renewal; or `commerce`), 'mas' (e-commerce and renewal), or 'all' (the hidden-bench tasks)."""
    out = []
    for name in (n.strip() for n in text.split(",") if n.strip()):
        if name == "all":
            out += scenario_files("hidden-bench")
            continue
        folder = {"mas": None, "commerce": "e-commerce"}.get(name, name)      # a use case: its sub-folder of scenarios/
        if name == "mas":                                                     # the apps of the mas-lab library
            out += scenario_files("e-commerce") + scenario_files("renewal")
            continue
        if (SCENARIOS_DIR / folder).is_dir():
            out += scenario_files(folder)
            continue
        by_name = {f.stem: f for f in scenario_files()}
        found = Path(name) if Path(name).suffix == ".yaml" and Path(name).is_file() else by_name.get(name) or by_name.get(f"evacuation-{name}")
        if found is None:
            sys.exit(f"--tasks: no task file for {name!r} (looked in scenarios/)")
        out.append(found)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="Other flags are passed to run.sh.")
    p.add_argument("--scenarios", default="M,S,C", help="which to run: M (free speech, full mesh), S (free speech, SLIM), "
                                                    "C (CONCORD, SLIM); with a coordinator: O1 (free speech, star), "
                                                    "O2 (free speech, SLIM), O3 (CONCORD, SLIM); CONCORD starts with a free-text step")
    p.add_argument("--thresholds", default="", help="comma-separated thresholds fixed by the harness for C; empty: the default (0.6)")
    p.add_argument("--scenario", default=str(DEFAULT_SCENARIO), help="the task file (default the West City one)")
    p.add_argument("--tasks", default="", help="comma-separated tasks to run, instead of --scenario: a name from scenarios/ (west-city, east-town, "
                                               "north-hill, with or without 'evacuation-'), a path, or 'all'. Each run is scored against the correct "
                                               "answer of its own task")
    p.add_argument("--parallel", type=int, default=1,
                   help="how many runs at the same time (default 1). Each has its own SLIM node and ports. The model endpoint then gets "
                        "that many times more calls at once: if it slows down, the seconds of the runs, and the idle close of M and S, are less "
                        "comparable, so use 1 when the time matters")
    p.add_argument("--run-timeout", type=int, default=900,
                   help="seconds after which a run is stopped and counted as TIMEOUT (default 900; free speech alone can take 10 minutes by design)")
    p.add_argument("--repeats", type=int, default=1, help="runs of each scenario (and threshold) (default 1)")
    args, rest = p.parse_known_args()
    keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
    if not keys or any(k not in SCENARIOS for k in keys):
        p.error(f"--scenarios takes some of {', '.join(SCENARIOS)}, comma-separated")
    task_files = task_paths(args.tasks) if args.tasks else [args.scenario]
    tasks = [(str(f), Scenario(f)) for f in task_files]
    if any(sc.kind == "mas" for _, sc in tasks) and any(not SCENARIOS[k][3] for k in keys):
        p.error("the scenarios of the mas-lab library run with a coordinator for now: --scenarios O1,O2,O3")
    thresholds = [t for t in args.thresholds.split(",") if t != ""] or [None]
    base = HERE / "runs" / f"batch-{time.strftime('%Y%m%d-%H%M%S')}"
    jobs = [(key, t, n, task) for n in range(args.repeats)      # the scenarios take turns, so that a drift of the model hits all of them alike
            for task in range(len(tasks))
            for key in keys for t in (thresholds if SCENARIOS[key][1] == "concord" else [None])]
    parallel = max(1, args.parallel)
    if parallel > 1:
        subprocess.run(["uv", "sync", "--quiet"], cwd=HERE, check=True)     # once, here: the runs must not sync together
        emit(f"{len(jobs)} runs, {parallel} at a time")
    base.mkdir(parents=True, exist_ok=True)
    finished, lock = [], threading.Lock()

    def work(job):
        key, t, n, task = job
        file, scenario = tasks[task]
        row = one(base, key, t, n, rest, file, scenario, parallel, args.run_timeout,
                  scenario.id.removeprefix("evacuation-") if len(tasks) > 1 else None)
        with lock:
            finished.append(row)
            (base / "summary.json").write_text(json.dumps(finished, indent=2))   # kept up to date, in case the batch is stopped
        return row

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        rows = list(pool.map(work, jobs))                                       # in the order of the jobs, whatever finishes first
    summarize(rows)
    excluded = [r for r in rows if r["valid"] is False]
    if excluded:
        print("\nexcluded, failed verification: " + "; ".join(f"{r['run']} ({','.join(r['failed_checks'])})" for r in excluded)
              + "\n  run `uv run python -m tools.verify <run folder>` for the detail")
    left_out = [r for r in rows if r["valid"] is None]
    if left_out:
        print("\nnot counted, no result: " + "; ".join(f"{r['run']} ({r['outcome']}: {r.get('error', '')[:100]})" for r in left_out))
    print_replays(rows)
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(rows, indent=2))
    print(f"\nrecords: {base}")


if __name__ == "__main__":
    sys.exit(main())
