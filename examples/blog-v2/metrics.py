#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Cost of a batch: time, tokens, messages, repair rounds (CONCORD) and useless posts (free speech).

    uv run python metrics.py runs/batch-<time> [--judge]

It reads the records of the runs, never calls a model unless `--judge` is given. Prints a table per scenario (mean and
standard deviation over the runs), and writes metrics.json (one row per run) and metrics.csv next to summary.json.

`--judge` asks the model of $LLM_MODEL (endpoint $LLM_BASE_URL) to label every post of the free-speech runs: one call per run,
the labels are kept in <run>/post_labels.json and reused the next time. Without it a rough word-overlap test is used.
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
from pathlib import Path

import show

HERE = Path(__file__).resolve().parent
from blog_v2.scenario import AGENTS, set_roster       # the roster of the run being read (set per row)
CORRECT = "West City"                    # for the batches before summary.json had the correct answer of each run


def roles():
    """The agents of the run, and (O1 to O3) the coordinator, which has no facts."""
    return AGENTS + ("orchestrator",)

STOP = set("that this with have from they will what which their there about would could should these those been were your more than "
           "also into only just very some other each both then them when where while because still like make made".split())
LABELS = ("FACT", "POSITION", "QUESTION", "ECHO", "OTHER")
JUDGE = """You read the posts of {group}. Label each post with exactly one word:
FACT      it brings information the others did not have yet (something the author knows or has checked)
POSITION  it states or changes which option the author prefers, or proposes how to decide, with a new reason
QUESTION  it asks the others for something they have not given yet
ECHO      it adds nothing new: it agrees, thanks, confirms, summarizes or repeats what was already written
OTHER     anything else
Answer with one line per post, "<number>: <label>", for every post, nothing else.

Posts:
"""


def load(run, who):
    try:
        return json.loads((run / f"{who}.json").read_text())
    except (OSError, ValueError):
        return None


def words(text):
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


FREE, CONCORD = ("M", "S", "O1", "O2"), ("C", "O3")


def scenario_of(summary_row):
    """The scenario of a row of summary.json (M free mesh, S free slim, C concord slim, O1 to O3 with a coordinator). Batches of scheme 3 had
    O1 = ask and decide (retired: `scenario_of` gives "ask" and the rows are left out) and O2 to O4 = today's O1 to O3."""
    if summary_row.get("scheme") == 3:
        return {"O1": "ask", "O2": "O1", "O3": "O2", "O4": "O3"}.get(summary_row["scenario"], summary_row["scenario"])
    return summary_row["scenario"]


def posts_of(run):
    """The free-text posts of a run (the discussion of free speech, step 1 of CONCORD), oldest first: [{"t", "from", "text"}], from the
    `posted` events of the agents."""
    out = [{"t": e["at"], "from": who, "text": e["text"]} for who in AGENTS for e in (load(run, who) or {}).get("events") or []
           if e["event"] == "posted"]
    return sorted(out, key=lambda p: p["t"])


def tokens(run):
    """Tokens of the run: in and out, per role, and the number of model calls."""
    out = {}
    for who in roles():
        u = (load(run, who) or {}).get("usage") or {}
        read, write = u.get("cache_read", 0), u.get("cache_write", 0)          # not in the records of a batch before the cache was counted
        out[who] = {"in": u.get("input", 0) + read + write, "fresh": u.get("input", 0), "cache_read": read, "cache_write": write,
                    "out": u.get("output", 0), "calls": u.get("calls", 0), "slowest": u.get("max_seconds", 0)}
    return out


def concord(run, row):
    """Messages, repair rounds and the time of the protocol, from the events of the agents."""
    ev = {who: (load(run, who) or {}).get("events") or [] for who in roles()}
    sent = [e for who in AGENTS for e in ev[who] if e["event"] == "sent"]
    kinds = {k: sum(e.get("kind") == k for e in sent) for k in ("init-reply", "score", "repair", "result")}
    report = (load(run, AGENTS[0]) or {}).get("report") or {}
    start = min((e["at"] for who in roles() for e in ev[who] if e["event"] == "started"), default=None)      # the initiator's
    end = max((e["at"] for who in AGENTS for e in ev[who] if e["event"] in ("outcome", "ended")), default=None)
    rounds = report.get("rounds") or 0
    row.update(rounds=rounds, repairs=kinds["repair"], scores=kinds["score"], messages=len(sent) + 1,     # +1: the init (the initiator's `started`)
               protocol_seconds=round(end - start, 1) if start and end else None,
               seconds_per_round=round((end - start) / max(rounds, 1), 1) if start and end else None)
    times = {k: [e["at"] - start for who in AGENTS for e in ev[who] if e["event"] in ("sent", "recorded") and e.get("kind") == k]
             for k in ("init-reply", "score")} if start else {}
    row.update(first_score=round(min(times["score"]), 1) if times.get("score") else None,
               init_done=round(max(times["init-reply"]), 1) if times.get("init-reply") else None)
    wire = text = 0                                                  # how much of what goes on the wire is the reasoning and how much format
    for m in show.load(run):
        wire += len(m["text"])
        try:
            text += sum(len(t) for _, t in show.texts(m["text"]))
        except Exception:                                            # a message that cannot be read has no text to count
            pass
    row.update(wire_chars=wire, text_chars=text, format_share=round(1 - text / wire, 2) if wire else None)
    step1 = posts_of(run)                                           # step 1 (C): the free-text posts before the run
    if step1:
        row.update(share_posts=len(step1), share_seconds=round(max(p["t"] for p in step1) - step1[0]["t"], 1),
                   share_chars=sum(len(p["text"]) for p in step1))


def novelty(posts):
    """For each post, the share of its words that no earlier post had used."""
    seen, out = set(), []
    for text in posts:
        w = words(text)
        out.append(len(w - seen) / len(w) if w else 0)
        seen |= w
    return out


def group_of(task):
    """Who discusses what, for the judge: the leaders of the evacuation task, or the roles of an app of the mas-lab library."""
    from blog_v2.scenario import Scenario, scenario_files
    for f in scenario_files():
        s = Scenario(f)
        if s.id == task:
            if s.kind == "mas":
                return f"{len(s.agents)} colleagues with different roles ({', '.join(s.agents)}) who discuss {s.decision}"
            return "four leaders who discuss where to evacuate"
    return "a group that discusses a decision"


def judged(run, posts, client, model, group="four leaders who discuss where to evacuate"):
    cache = run / "post_labels.json"
    if cache.exists():
        labels = json.loads(cache.read_text())
        if len(labels) == len(posts):
            return labels
    body = JUDGE.format(group=group) + "\n".join(f"[{i + 1}] {p['from']}: {p['text']}" for i, p in enumerate(posts))
    for attempt in range(3):                                         # numbered answers: a missing line is seen, not shifted
        got = client.messages.create(model=model, max_tokens=2000, extra_body={"temperature": 0}, messages=[{"role": "user", "content": body}])
        text = "".join(b.text for b in got.content if b.type == "text")
        said = {int(n): l for n, l in re.findall(r"^\W*(\d+)\W*[:\-]\s*([A-Z]+)", text, re.M)}
        if all(i in said for i in range(1, len(posts) + 1)):
            break
    else:
        print(f"{run.name}: no label for posts {[i for i in range(1, len(posts) + 1) if i not in said]}, counted as OTHER", file=sys.stderr)
    labels = [said.get(i + 1) if said.get(i + 1) in LABELS else "OTHER" for i in range(len(posts))]
    cache.write_text(json.dumps(labels))
    return labels


def free(run, row, client, model):
    posts = posts_of(run)
    first = posts[0]["t"] if posts else None
    last = posts[-1]["t"] if posts else None
    orch = load(run, "orchestrator") or {}
    closes = [(load(run, a) or {}).get("close") or {} for a in AGENTS]
    if (orch.get("report") or {}).get("close"):                     # with a coordinator, it closes the session
        closes = [orch["report"]["close"]]
    delegated = [e for e in orch.get("events") or [] if e["event"] == "delegated"]
    if delegated:                                                   # O1: every post goes in to the coordinator and out again
        row["hub_deliveries"] = sum(len(e["to"]) for e in delegated)
    silent = sum(e["event"] == "silent" for who in AGENTS for e in (load(run, who) or {}).get("events") or [])
    spoke = {a: next((p["t"] - first for p in posts if p["from"] == a), None) for a in AGENTS}
    row.update(everyone_spoke=round(max(spoke.values()), 1) if None not in spoke.values() and first else None)
    nov = novelty([p["text"] for p in posts])
    ends = [c["seconds"] for c in closes if c.get("seconds")]         # the seconds each agent ran before it stopped
    row.update(posts=len(posts), silent=silent, closed_by=",".join(sorted({str(c.get("reason")) for c in closes})),
               chars=sum(len(p["text"]) for p in posts), protocol_seconds=round(last - first, 1) if first and last else None,
               idle_tail=round(max(ends) - (last - first), 1) if ends and first and last else None,
               per_agent={a: sum(p["from"] == a for p in posts) for a in AGENTS})
    if client:
        labels = judged(run, posts, client, model, group_of(row.get("task")))
        row["labels"] = {l: labels.count(l) for l in LABELS}
        row["useless"] = labels.count("ECHO")
    else:                                                           # a post that brings fewer than a quarter of new words
        row["useless"] = sum(n < 0.25 for n in nov[3:])             # the first post of each leader always counts
        row["novelty_mean"] = round(statistics.mean(nov), 2) if nov else None


def mean(values):
    values = [v for v in values if v is not None]
    return (statistics.mean(values), statistics.pstdev(values)) if values else (None, None)


def cell(values, digits=0):
    m, s = mean(values)
    return "-" if m is None else f"{m:.{digits}f}±{s:.{digits}f}"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("batch", type=Path)
    p.add_argument("--judge", action="store_true", help="label the posts of the free-speech runs with the model (one call per run)")
    args = p.parse_args()
    client = model = None
    if args.judge:
        from anthropic import Anthropic                              # a plain client: this script has no event loop
        client = Anthropic(base_url=os.environ.get("LLM_BASE_URL") or None, api_key=os.environ.get("LLM_API_KEY") or None,
                           timeout=float(os.environ.get("LLM_TIMEOUT", 120)), default_headers={"Cache-Control": "no-cache"})
        model = os.environ.get("LLM_MODEL")
        if not model:
            sys.exit("--judge needs LLM_MODEL (and LLM_BASE_URL / LLM_API_KEY)")
    rows = []
    for r in json.loads((args.batch / "summary.json").read_text()):
        run = args.batch / r["run"]
        if not r.get("valid") or scenario_of(r) == "ask":             # "ask": the retired scenario of a batch of scheme 3
            continue
        set_roster(r.get("agents") or AGENTS)                         # the agents of this run (a scenario can have three, four or more)
        by = tokens(run)
        row = {"run": r["run"], "scenario": scenario_of(r), "seconds": r.get("seconds"), "tokens_by_role": by,
               "tokens": sum(v["in"] + v["out"] for v in by.values()), "tokens_in": sum(v["in"] for v in by.values()),
               "tokens_cache_read": sum(v["cache_read"] for v in by.values()), "tokens_cache_write": sum(v["cache_write"] for v in by.values()),
               "tokens_out": sum(v["out"] for v in by.values()), "calls": sum(v["calls"] for v in by.values()),
               "slowest_call": max(v["slowest"] for v in by.values())}
        row.update(task=r.get("task"), right=r.get("winner") == r.get("correct", CORRECT))
        (free(run, row, client, model) if r["mode"] == "free" else concord(run, row))
        row["orchestrator_tokens"] = by["orchestrator"]["in"] + by["orchestrator"]["out"]
        row["tokens_per_message"] = round(row["tokens"] / (row.get("posts") or row.get("messages") or 1))
        rows.append(row)
    (args.batch / "metrics.json").write_text(json.dumps(rows, indent=2))
    keys = ["run", "scenario", "seconds", "protocol_seconds", "tokens", "tokens_in", "tokens_cache_read", "tokens_cache_write", "tokens_out", "calls", "rounds", "repairs", "scores",
            "messages", "posts", "silent", "useless", "idle_tail", "orchestrator_tokens", "tokens_per_message", "right", "slowest_call",
            "first_score", "init_done", "everyone_spoke", "format_share", "share_posts", "share_seconds", "share_chars"]
    with open(args.batch / "metrics.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        w.writerows([[r.get(k) for k in keys] for r in rows])

    names = {"M": "M free mesh", "S": "S free slim", "C": "C concord slim", "O1": "O1 orch free star",
             "O2": "O2 orch free slim", "O3": "O3 orch concord"}
    names = {s: n for s, n in names.items() if any(r["scenario"] == s for r in rows)}          # only the scenarios of this batch
    print("cost per run, mean±sd over the valid runs\n")
    print(f"{'':<20}{'runs':<6}{'seconds':<11}{'in protocol':<13}{'tokens':<15}{'in':<14}{'out':<12}{'calls':<10}{'tokens/msg'}")
    for s, name in names.items():
        rs = [r for r in rows if r["scenario"] == s]
        print(f"{name:<20}{len(rs):<6}{cell([r['seconds'] for r in rs]):<11}{cell([r['protocol_seconds'] for r in rs]):<13}"
              f"{cell([r['tokens'] for r in rs]):<15}{cell([r['tokens_in'] for r in rs]):<14}{cell([r['tokens_out'] for r in rs]):<12}"
              f"{cell([r['calls'] for r in rs]):<10}{cell([r['tokens_per_message'] for r in rs])}")
    print("\ninput tokens: new, read from the cache, written to the cache (mean per run; 0 for a batch run before the cache was counted)")
    for s, name in names.items():
        rs = [r for r in rows if r["scenario"] == s]
        print(f"  {name:<20}new {cell([r['tokens_in'] - r['tokens_cache_read'] - r['tokens_cache_write'] for r in rs]):<14}"
              f"cache read {cell([r['tokens_cache_read'] for r in rs]):<14}cache write {cell([r['tokens_cache_write'] for r in rs])}")
    print("\ntokens by agent (mean per run)")
    for s, name in names.items():
        rs = [r for r in rows if r["scenario"] == s]
        for a in rs[0]["tokens_by_role"]:
            used = [r['tokens_by_role'][a]['in'] + r['tokens_by_role'][a]['out'] for r in rs]
            if any(used):
                print(f"  {name:<20}{a} {cell(used)}")
    print("\ncost of a right answer (all the tokens and seconds of the scenario / the runs that chose the right option)")
    print(f"  {'':<20}{'right':<8}{'tokens per right answer':<26}{'seconds per right answer'}")
    for s, name in names.items():
        rs = [r for r in rows if r["scenario"] == s]
        ok = sum(r["right"] for r in rs)
        per = lambda key: f"{sum(r[key] for r in rs) / ok:.0f}" if ok else "never right"
        print(f"  {name:<20}{ok}/{len(rs):<6}{per('tokens'):<26}{per('seconds')}")
    print("\nCONCORD: rounds and messages")
    print(f"  {'':<20}{'repair rounds':<15}{'repairs sent':<14}{'scores sent':<13}{'messages':<11}{'sec/round'}")
    for s in (s for s in CONCORD if s in names):
        rs = [r for r in rows if r["scenario"] == s]
        zero = sum(r["rounds"] == 0 for r in rs)
        print(f"  {names[s]:<20}{cell([r['rounds'] for r in rs], 1):<15}{cell([r['repairs'] for r in rs], 1):<14}"
              f"{cell([r['scores'] for r in rs], 1):<13}{cell([r['messages'] for r in rs], 1):<11}{cell([r['seconds_per_round'] for r in rs], 1):<11}"
              f"   (closed at round 0: {zero}/{len(rs)})")
    print("\nCONCORD timing and format (seconds from the start of the run)")
    print(f"  {'':<20}{'all init replies':<19}{'first scores':<15}{'format share of the messages (the rest is the reasoning text)'}")
    for s in (s for s in CONCORD if s in names):
        rs = [r for r in rows if r["scenario"] == s]
        print(f"  {names[s]:<20}{cell([r['init_done'] for r in rs], 1):<19}{cell([r['first_score'] for r in rs], 1):<15}{cell([r['format_share'] for r in rs], 2)}")
    for s in (s for s in CONCORD if s in names):
        rs = [r for r in rows if r["scenario"] == s]
        print(f"\nstep 1 ({names[s]}): the free-text posts before the run (not in the CONCORD messages, in the seconds and tokens above)")
        print(f"  posts {cell([r['share_posts'] for r in rs], 1)}   seconds until the last post {cell([r['share_seconds'] for r in rs], 1)}   "
              f"characters {cell([r['share_chars'] for r in rs])}")
    how = "model labels (ECHO)" if client else "word-overlap test: under a quarter of new words"
    for s in (s for s in FREE if s in names):
        rs = [r for r in rows if r["scenario"] == s]
        print(f"\nfree speech, {names[s]} ({how})")
        print(f"  posts {cell([r['posts'] for r in rs], 1)}   silent turns {cell([r['silent'] for r in rs], 1)}   useless {cell([r['useless'] for r in rs], 1)} "
              f"({100 * sum(r['useless'] for r in rs) / max(sum(r['posts'] for r in rs), 1):.0f}% of the posts)")
        print(f"  everyone has spoken after {cell([r['everyone_spoke'] for r in rs], 1)} s")
        print(f"  seconds with posts {cell([r['protocol_seconds'] for r in rs])}, then waiting for the idle close {cell([r['idle_tail'] for r in rs])}")
        if any(r.get("hub_deliveries") for r in rs):
            print(f"  the coordinator made {cell([r.get('hub_deliveries') for r in rs], 1)} deliveries per run")
        if client:
            total = {l: sum(r["labels"][l] for r in rs) for l in LABELS}
            print("  labels: " + ", ".join(f"{l} {n}" for l, n in total.items()))
    print(f"\nwritten: {args.batch / 'metrics.json'}, {args.batch / 'metrics.csv'}")


if __name__ == "__main__":
    main()
