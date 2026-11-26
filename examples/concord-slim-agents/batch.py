#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Run the scenario for several thresholds, several times each, one after the other, and print what each run decided.

    uv run python batch.py --thresholds 0.7 --repeats 5 [any other flag of run.sh: --model, --temperature, --fake-llm, ...]

Each run is checked by verify.py (a referee of the protocol and the harness); a run that fails is left out of the totals.
Each run is a `run.sh` in its own folder, runs/batch-<time>/t<threshold>-r<n>/ (one after the other; with --parallel N, N at a time, each
on its own SLIM and hub addresses). The table and a summary per threshold are printed at the end and written to summary.json next to the runs.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import verify

HERE = Path(__file__).resolve().parent
AGENTS = ("participant-a", "participant-b", "participant-c")
CORRECT = "West City"


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
            if calls and calls[0]["name"] == "shell":            # skills mode: what it wrote is the answer it passes to the script, not the
                calls = [b for b in calls if " answer <<" in b["input"].get("command", "")]      # commands it ran to find its way
                if calls:
                    parts.append(calls[0]["input"]["command"].split("<<'EOF'", 1)[1])
                    break
            elif calls:
                parts.append(json.dumps(calls[0]["input"], sort_keys=True))
                break
    return hashlib.md5("\n".join(parts).encode()).hexdigest()[:8] if parts else None


def read(run_dir):
    """What one run decided, from the record of participant-a (and the token use of all three)."""
    try:
        a = json.loads((run_dir / "participant-a.json").read_text())
    except (OSError, ValueError):
        return {"outcome": "NO RECORD", "winner": None, "rounds": None, "status": None, "tokens": None}
    r = a.get("report") or {}
    if not r.get("outcome"):
        return {"outcome": "FAILED", "winner": None, "rounds": None, "status": None, "tokens": 0}
    used = 0
    for who in AGENTS:
        try:
            u = json.loads((run_dir / f"{who}.json").read_text())["usage"]
            used += u["input"] + u["output"]
        except (OSError, ValueError, KeyError):
            pass
    return {"outcome": r.get("outcome"), "winner": (r.get("winner") or {}).get("candidate"), "rounds": r.get("rounds"),
            "status": r.get("status"), "tokens": used}


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


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="Other flags are passed to run.sh.")
    p.add_argument("--thresholds", default="", help="comma-separated thresholds fixed by the harness; empty: the initiator chooses")
    p.add_argument("--repeats", type=int, default=1, help="runs for each threshold (default 1)")
    p.add_argument("--parallel", type=int, default=1, help="runs at the same time, each with its own SLIM node and hub address (default 1)")
    args, rest = p.parse_known_args()
    thresholds = [t for t in args.thresholds.split(",") if t != ""] or [None]
    base = HERE / "runs" / f"batch-{time.strftime('%Y%m%d-%H%M%S')}"
    jobs = [(t, n) for t in thresholds for n in range(args.repeats)]
    slots = queue.Queue()                                           # a slot is a pair of addresses: runs side by side never share one
    for i in range(args.parallel):
        slots.put(i)
    lock = threading.Lock()

    def one(job):
        t, n = job
        name = f"t{t if t is not None else 'agent'}-r{n}"
        run_dir = base / name
        slot = slots.get()
        env = {**os.environ, "RUN_DIR": str(run_dir), "SLIM_ADDR": f"127.0.0.1:{12345 + 10 * slot}", "HUB_ADDR": f"127.0.0.1:{12346 + 10 * slot}"}
        cmd = [str(HERE / "run.sh"), *(["--threshold", t] if t is not None else []), *rest]
        with lock:
            print(f"==> {name}", flush=True)
        try:
            done = subprocess.run(cmd, cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        finally:
            slots.put(slot)
        row = {"run": name, "threshold": t, "first_answers": first_answers(run_dir), **read(run_dir)}
        lines = []
        if row["outcome"] in ("FAILED", "NO RECORD"):
            row["error"] = failure(run_dir, done.stderr)
            row["valid"] = None                                   # nothing to verify
            lines.append(f"    {row['outcome']}: {row['error']}\n    logs: {run_dir}")
        else:
            result = verify.verify(run_dir)                       # a referee for the run: see verify.py
            row["valid"] = not result["failed"]
            row["failed_checks"] = [c[1] for c in result["checks"] if c[2] == "FAIL"]
            lines.append(f"    {row['outcome']} {row['winner']} rounds={row['rounds']} status={row['status']} tokens={row['tokens']} "
                         f"verify={'ok' if row['valid'] else 'FAILED ' + ','.join(row['failed_checks'])}")
        with lock:
            for line in ([f"  [{name}]"] if args.parallel > 1 else []) + lines:
                print(line, flush=True)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        rows = list(pool.map(one, jobs))                            # in the order of the jobs, whichever ends first
    print(f"\n{'run':<22}{'outcome':<13}{'winner':<12}{'rounds':<8}{'status':<11}{'tokens':<8}{'valid':<8}{'right'}")
    for r in rows:
        valid = "n/a" if r["valid"] is None else "yes" if r["valid"] else "NO"
        print(f"{r['run']:<22}{str(r['outcome']):<13}{str(r['winner']):<12}{str(r['rounds']):<8}{str(r['status']):<11}"
              f"{str(r['tokens']):<8}{valid:<8}{'yes' if r['winner'] == CORRECT else 'no'}")
    by = defaultdict(list)
    for r in rows:
        by[r["threshold"]].append(r)
    # only the runs that passed the referee count: a run that broke the protocol says nothing about the models
    print("\nthreshold   runs  valid  right   avg rounds   (the totals count the valid runs only)")
    for t, rs in by.items():
        ok = [r for r in rs if r["valid"]]
        done = [r["rounds"] for r in ok if isinstance(r["rounds"], int)]
        print(f"{str(t or 'agent'):<12}{len(rs):<6}{len(ok):<7}{sum(r['winner'] == CORRECT for r in ok):<8}{(sum(done) / len(done)) if done else '-'}")
    excluded = [r for r in rows if r["valid"] is False]
    if excluded:
        print("\nexcluded, failed verification: " + "; ".join(f"{r['run']} ({','.join(r['failed_checks'])})" for r in excluded)
              + "\n  run `uv run python verify.py <run folder>` for the detail")
    print_replays(rows)
    (base).mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(rows, indent=2))
    print(f"\nrecords: {base}")


if __name__ == "__main__":
    sys.exit(main())
