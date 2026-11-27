#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The numbers of the evaluation of the blog, from one or more batches: cost, where the cost goes, and the waste of free speech.

    uv run python -m tools.report runs/batch-<time> [runs/batch-<time> ...] [--latex]

Runs are grouped by use case (hidden-bench: the evacuation tasks; e-commerce; renewal) and by scenario (M, S, C: no coordinator; O1, O2, O3:
with one), over the valid runs of every batch given. It reads the records, and calls no model. For each group:

  cost      tokens per run (mean, median, p95, max; input, cached input, cache writes and output all count, as in the first draft), time
            and model calls
  where     the share of the tokens that the coordinator spends (the forwarding of O1; the summary of O1 and O2) and the mix of the tokens
  waste     free-speech groups only, and only when the posts have labels (`python -m tools.metrics --judge` writes <run>/post_labels.json): messages per
            run, where the last fact is shared, the share of echo, and how much of the run comes after the last message that adds anything,
            as messages and as an estimate of tokens (from the size of the conversation at each model call, scaled to the tokens the
            records say the agent used)

`--latex` prints the rows of the cost table, to paste. The numbers are also written to report.json in the working directory.
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from tools import metrics
from blog_v2.scenario import AGENTS, set_roster

ADDS = ("FACT", "POSITION", "QUESTION")                # the labels of a message that adds something
SAYS = {"post", "send_to", "stay_silent", "done", "final_answer", "reply", "delegate_to"}      # the tools that end a turn with something said
USE_CASE = (("evacuation", "hidden-bench"), ("commerce", "e-commerce"), ("renewal", "renewal"))
ORDER = ("M", "S", "C", "O1", "O2", "O3")


def use_case(task):
    return next((name for key, name in USE_CASE if key in task), task)


def pct(values, p):
    """Percentile by linear interpolation between the closest ranks."""
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def stats(values):
    return {"mean": statistics.mean(values), "median": statistics.median(values), "p95": pct(values, 95), "max": max(values)}


def load(run, who):
    return metrics.load(run, who)


def usage_total(rec):
    u = (rec or {}).get("usage") or {}
    return sum(u.get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))


# ----------------------------------------------------------------------------------------------------------------------------------
# per run
# ----------------------------------------------------------------------------------------------------------------------------------

def run_numbers(run, row):
    """Tokens (by role and by kind), calls, time and outcome of one run."""
    set_roster(row.get("agents") or AGENTS)
    by_role = {who: usage_total(load(run, who)) for who in metrics.roles()}
    kinds = {k: sum(((load(run, who) or {}).get("usage") or {}).get(k, 0) for who in metrics.roles())
             for k in ("input", "cache_read", "cache_write", "output")}
    calls = sum(((load(run, who) or {}).get("usage") or {}).get("calls", 0) for who in metrics.roles())
    return {"run": row["run"], "tokens": sum(by_role.values()), "orchestrator": by_role.get("orchestrator", 0), "kinds": kinds, "calls": calls,
            "seconds": row["seconds"], "posts": row.get("posts")}


# ----------------------------------------------------------------------------------------------------------------------------------
# the waste of free speech
# ----------------------------------------------------------------------------------------------------------------------------------

def labelled_posts(run, row):
    """The posts of a run, oldest first, with the label of each (None when the run has no labels)."""
    set_roster(row.get("agents") or AGENTS)
    posts = metrics.posts_of(run)
    cache = run / "post_labels.json"
    labels = json.loads(cache.read_text()) if cache.exists() else None
    if labels is None or len(labels) != len(posts):
        return posts, None
    return posts, labels


def call_costs(run, who):
    """[(time, estimated tokens)] of the model calls of an agent that the record can place in time, and the estimated total.

    Each call is estimated as the size of the conversation it read plus what it wrote (4 characters a token). The time of a call is the time of
    the event that ended its turn (a post, a silence, a final answer, a delegation); the calls of a turn that only read data tools take the time
    of the turn."""
    rec = load(run, who)
    if not rec or not rec.get("transcript"):
        return [], 0
    routed = any(b.get("name") == "send_to" for m in rec["transcript"] if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use")
    says = SAYS - {"post"} if routed else SAYS      # a mesh: the message is sent when its recipients are chosen
    comm = [e["at"] for e in rec["events"] if e["event"] in ("posted", "silent", "final_answer", "post_lost", "delegated")]
    context = len((rec.get("config") or {}).get("system_prompt") or "")
    calls, used, pending = [], 0, []
    for m in rec["transcript"]:
        size = len(json.dumps(m["content"], ensure_ascii=False))
        if m["role"] == "assistant":
            names = [b.get("name") for b in m["content"] if b.get("type") == "tool_use"]
            pending.append(context / 4 + size / 4)
            ends = sum(n in says for n in names)
            if ends:                                              # the turn ends here: place the calls of the turn
                for _ in range(ends):
                    if used < len(comm):
                        t = comm[used]
                        used += 1
                if used <= len(comm):
                    calls += [(t, c) for c in pending]
                pending = []
        context += size
    calls += [(math.inf, c) for c in pending]
    return calls, sum(c for _, c in calls)


def tokens_after(run, row, cutoff):
    """The estimated share of the tokens of a run that were spent after `cutoff` (a time), over all the agents of the run."""
    after = total = 0.0
    for who in metrics.roles():
        calls, est = call_costs(run, who)
        actual = usage_total(load(run, who))
        if not est or not actual:
            continue
        scale = actual / est
        total += actual
        after += scale * sum(c for t, c in calls if t > cutoff)
    return after / total if total else None


def waste(run, row):
    posts, labels = labelled_posts(run, row)
    if labels is None or not posts:
        return None
    n = len(posts)
    last_fact = max((i for i, l in enumerate(labels, 1) if l == "FACT"), default=0)
    last_adds = max((i for i, l in enumerate(labels, 1) if l in ADDS), default=0)
    cutoff = posts[last_adds - 1]["t"] if last_adds else posts[0]["t"]
    shares = {f"label_{name}": sum(l == name for l in labels) / n for name in metrics.LABELS}      # the share of the messages with each label
    return {**shares, "posts": n, "last_fact": last_fact, "last_fact_early": last_fact <= 6, "after_fact": (n - last_fact) / n,
            "echo": sum(l == "ECHO" for l in labels) / n, "echo_runs_half": sum(l == "ECHO" for l in labels) / n >= 0.5,
            "after_adds": (n - last_adds) / n, "tokens_after_adds": tokens_after(run, row, cutoff)}


# ----------------------------------------------------------------------------------------------------------------------------------
# the report
# ----------------------------------------------------------------------------------------------------------------------------------

def collect(batches):
    groups = defaultdict(list)
    for batch in batches:
        for row in json.loads((batch / "summary.json").read_text()):
            if not row.get("valid"):
                continue
            scenario = metrics.scenario_of(row)
            if scenario == "ask":
                continue                                           # the retired scenario of a batch of scheme 3
            run = batch / row["run"]
            groups[(use_case(row["task"]), scenario)].append((run, row))
    return groups


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("batches", nargs="+", type=Path)
    p.add_argument("--latex", action="store_true", help="print the rows of the cost table")
    args = p.parse_args()
    groups = collect(args.batches)
    out = {}
    for (case, sc), runs in sorted(groups.items(), key=lambda kv: (kv[0][0], ORDER.index(kv[0][1]) if kv[0][1] in ORDER else 9)):
        nums = [run_numbers(run, row) for run, row in runs]
        tokens = stats([n["tokens"] for n in nums])
        total = sum(n["tokens"] for n in nums)
        entry = {"runs": len(nums), "tokens": tokens,
                 "seconds": {"mean": statistics.mean(n["seconds"] for n in nums), "max": max(n["seconds"] for n in nums)},
                 "calls": statistics.mean(n["calls"] for n in nums),
                 "orchestrator_share": sum(n["orchestrator"] for n in nums) / total,
                 "kinds": {k: sum(n["kinds"][k] for n in nums) / total for k in ("input", "cache_read", "cache_write", "output")},
                 "posts": statistics.mean(n["posts"] for n in nums if n["posts"] is not None)}
        if sc in ("M", "S", "O1", "O2"):
            w = [x for x in (waste(run, row) for run, row in runs) if x]
            entry["waste_runs"] = len(w)
            if w:
                entry["waste"] = {k: statistics.mean(x[k] for x in w if x[k] is not None) for k in w[0]}
        out[f"{case}/{sc}"] = entry
    Path("report.json").write_text(json.dumps(out, indent=2))

    for case in dict.fromkeys(k.split("/")[0] for k in out):
        print(f"\n=== {case}")
        print(f"{'':<5}{'runs':<6}{'tokens k: mean':<16}{'median':<9}{'p95':<9}{'max':<9}{'time s: mean':<14}{'max':<6}{'calls'}")
        for k, e in out.items():
            if k.split("/")[0] != case:
                continue
            t = e["tokens"]
            print(f"{k.split('/')[1]:<5}{e['runs']:<6}{t['mean'] / 1000:<16.0f}{t['median'] / 1000:<9.0f}"
                  f"{t['p95'] / 1000:<9.0f}{t['max'] / 1000:<9.0f}{e['seconds']['mean']:<14.0f}{e['seconds']['max']:<6.0f}{e['calls']:.0f}")
        print("where the tokens go (share of all the tokens of the scenario): coordinator | new input, cache read, cache write, output")
        for k, e in out.items():
            if k.split("/")[0] == case:
                kd = e["kinds"]
                print(f"  {k.split('/')[1]:<4}{100 * e['orchestrator_share']:>5.0f}%   |  {100 * kd['input']:>4.0f}% {100 * kd['cache_read']:>4.0f}% "
                      f"{100 * kd['cache_write']:>4.0f}% {100 * kd['output']:>4.0f}%")
        for k, e in out.items():
            if k.split("/")[0] == case and e.get("waste_runs"):
                w = e["waste"]
                print(f"waste, {k.split('/')[1]} ({e['waste_runs']} labelled runs): {w['posts']:.1f} messages; last fact at message {w['last_fact']:.1f} "
                      f"(within the first six in {100 * w['last_fact_early']:.0f}% of runs); {100 * w['after_fact']:.0f}% of the messages after the last fact; "
                      f"{100 * w['echo']:.0f}% echo; {100 * w['after_adds']:.0f}% of the messages after the last one that adds anything; "
                      f"~{100 * w['tokens_after_adds']:.0f}% of the tokens after it" if w["tokens_after_adds"] is not None else "")
                print("   labels: " + ", ".join(f"{name} {100 * w['label_' + name]:.0f}%" for name in metrics.LABELS))
            elif k.split("/")[0] == case and k.split("/")[1] in ("M", "S", "O1", "O2"):
                print(f"waste, {k.split('/')[1]}: no labels yet (run `python -m tools.metrics <batch> --judge`)")
    if args.latex:
        print("\n% rows of the cost table: scenario & tokens mean, median, p95, max (k) & time mean, max (s)")
        for k, e in out.items():
            t = e["tokens"]
            print(f"% {k}\n{k.split('/')[1]} & {t['mean'] / 1000:.0f} & {t['median'] / 1000:.0f} & {t['p95'] / 1000:.0f} & {t['max'] / 1000:.0f} & "
                  f"{e['seconds']['mean']:.0f} & {e['seconds']['max']:.0f} \\\\")
    print("\nwritten: report.json")


if __name__ == "__main__":
    sys.exit(main())
