# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Summarize ACCORD runs from their trace cache.

Usage: python3 check_run.py <trace-cache-dir>      (the dir given to --trace-cache)

For every run found under the directory it prints:
  - the sequence of accord_initiator.py calls and how each one ended,
  - whether the run reached the lock (LOCKED),
  - the smc block from init (retrieval status, whether it was injected),
  - how many messages carried a PRIOR_CONTEXT line,
  - the smc block returned at the lock (ingest started),
and, at the end, the ingest status file written by the background worker.
Only the last ingest status survives, because all runs share one state file.
"""
import glob
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "accord-initiator"

if len(sys.argv) != 2:
    sys.exit(__doc__)

root = os.path.expanduser(sys.argv[1])
if root.endswith(".jsonl"):
    events = [root]
else:
    events = sorted(
        glob.glob(os.path.join(os.path.abspath(root), "**", "events.jsonl"), recursive=True),
        key=os.path.getmtime,
    )
if not events:
    sys.exit(f"no events.jsonl found under {root}")

for path in events:
    rows = [json.loads(line) for line in open(path)]
    print("trace:", path, "events:", len(rows))
    phases, smc, locked, prior = [], None, False, 0
    for e in rows:
        if e.get("kind") != "tool_call_end" or e.get("tool_name") != "run_skill_script":
            continue
        o = e.get("output")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except ValueError:
                o = {}
        o = o if isinstance(o, dict) else {}
        try:
            j = json.loads(o.get("stdout") or "")
        except ValueError:
            continue
        d = j.get("data") or {}
        if not j.get("ok"):
            phases.append(f"{j.get('phase')}:ERROR {str(j.get('error') or j.get('message'))[:80]}")
            continue
        phases.append(f"{j.get('phase')}:{d.get('status') or d.get('phase') or ''}")
        if j.get("phase") == "init":
            smc = d.get("smc")
        if "PRIOR_CONTEXT" in json.dumps(d.get("messages") or {}):
            prior += 1
        if d.get("status") == "LOCKED" or "locked_intent_contract" in d:
            locked = True
            print("  lock result smc:", json.dumps(d.get("smc")))
    print("  script calls:", " -> ".join(phases))
    print("  reached lock:", locked)
    print("  init smc block:", json.dumps(smc))
    print("  messages carrying PRIOR_CONTEXT:", prior)

for f in sorted(glob.glob(str(SKILL_DIR / ".accord" / "*.smc_ingest.json"))):
    print("ingest status file:", f, "\n ", open(f).read().replace("\n", " "))
