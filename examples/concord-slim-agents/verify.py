#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Verify a finished run: did it follow the CONCORD spec, and did the harness do what it should?

    uv run python verify.py runs/<run folder>

A referee, not a judge of the models. It reads the messages each agent kept and checks them with its own code, written from the spec
(it does not call the library's engine), so a bug in the library or in the harness shows as a failed check and not as a strange
result. A failed check means the run cannot be trusted. What the models did (refusals, repeated repairs, scores close to the bar) is
only reported.

    A  the protocol: who repaired, the incumbent, the outcome and the result message
    B  the messages: integrity check, ids, numbers, schema, who rated what
    C  everyone saw the same run
    D  the harness: the conversation of each model holds what its node recorded; the threshold is the one asked for
       (runs that saved their configuration)

Exit status: 0 if no check failed, 1 otherwise.
"""
import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

import show

AGENTS = show.AGENTS
SPEC_VERSION = "0.3.0"
MAIN = ("concord-init", "concord-init-reply", "concord-score", "concord-repair", "concord-result")
CONTROL = ("concord-error", "concord-resend", "concord-cancel")
NUMBER = re.compile(r"[0-9]{1,3}(\.[0-9]{1,6})?")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MILLION = 1_000_000


# --------------------------------------------------------------------------------------------------------------------------------
# small helpers, from the spec
# --------------------------------------------------------------------------------------------------------------------------------

def check_of(text):
    """The integrity check of a document (spec, Integrity check): the first 8 hex characters of the SHA-256 of its canonical text,
    which is the text without its `check` attribute, each text between a `>` and the next `<` without its whitespace (or collapsed
    to single spaces, without any at either end), as UTF-8."""
    t = re.sub(r' check="[0-9a-f]*"', "", text.strip(), count=1)

    def squeeze(m):
        body = m.group(1)
        return ">" + ("" if not body.strip(" \t\r\n") else re.sub(r"[ \t\r\n]+", " ", body).strip(" \t\r\n")) + "<"

    return hashlib.sha256(re.sub(r">([^<]*)<", squeeze, t).encode("utf-8")).hexdigest()[:8]


def millionths(text):
    """The value of a number of the spec in millionths, or None when it is not in the form of the spec."""
    if not isinstance(text, str) or not NUMBER.fullmatch(text):
        return None
    whole, _, frac = text.partition(".")
    return int(whole) * MILLION + int(frac.ljust(6, "0") or 0)


def normalize(raw, reject, ideal):
    """Spec, Raw vs Normalized: clamp((raw - reject) / (ideal - reject), 0, 1), exact, rounded to 6 places half up, in millionths."""
    s = min(max((Fraction(raw) - Fraction(reject)) / (Fraction(ideal) - Fraction(reject)), Fraction(0)), Fraction(1))
    return int((Decimal(s.numerator) / Decimal(s.denominator) * MILLION).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def txt(el, path):
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text is not None else ""


class Report:
    def __init__(self):
        self.rows = []                              # (group, id, status, title, detail)

    def add(self, group, cid, ok, title, detail=""):
        """ok: True (pass), False (FAIL), None (skip)."""
        self.rows.append((group, cid, "pass" if ok is True else "FAIL" if ok is False else "skip", title, detail))

    def failed(self):
        return [r for r in self.rows if r[2] == "FAIL"]


# --------------------------------------------------------------------------------------------------------------------------------
# what a run holds
# --------------------------------------------------------------------------------------------------------------------------------

def load_run(run_dir):
    run = {"dir": run_dir, "msgs": show.load(run_dir), "rec": {}, "state": {}}
    for who in AGENTS:
        try:
            run["rec"][who] = json.loads((run_dir / f"{who}.json").read_text())
            st = json.loads((run_dir / f"{who}-state.json").read_text())
            run["state"][who] = next(iter(st["runs"].values()))
        except (OSError, ValueError, StopIteration):
            pass
    for m in run["msgs"]:
        try:
            m["root"] = ET.fromstring(m["text"])
        except ET.ParseError:
            m["root"] = None
    return run


def by_kind(run, tag):
    return [m for m in run["msgs"] if m["root"] is not None and m["root"].tag == tag]


# --------------------------------------------------------------------------------------------------------------------------------
# A: the protocol, replayed from the messages
# --------------------------------------------------------------------------------------------------------------------------------

def replay(run):
    """Replay the run with the rules of the spec. Returns a dict with `stages`, `final` and what was found, or {"why": ...}."""
    inits = by_kind(run, "concord-init")
    if len(inits) != 1:
        return {"why": f"{len(inits)} init messages"}
    init = inits[0]["root"]
    agents = [a.text for a in init.findall("participants/agent")]
    tau, eps = millionths(txt(init, "params/satisfaction_threshold")), millionths(txt(init, "params/eps"))
    budget = int(txt(init, "params/budget"))
    anchors, raw, proposer = {}, {a: {} for a in agents}, {}
    repairs = []                                               # (id, author), in the order they were sent
    for m in run["msgs"]:
        r = m["root"]
        if r is None:
            continue
        who = r.get("from")
        if r.tag in ("concord-init-reply", "concord-repair"):
            p = r.find("proposal")
            proposer[p.get("id")] = who
            raw[who][p.get("id")] = txt(p, "score/raw")
            if r.tag == "concord-init-reply":
                anchors[who] = (txt(r, "anchors/raw_reject"), txt(r, "anchors/raw_ideal"))
            else:
                repairs.append((p.get("id"), who))
        elif r.tag == "concord-score":
            for s in r.findall("score"):
                raw[who].setdefault(s.get("candidate_id"), txt(s, "raw"))
    if set(anchors) != set(agents):
        return {"why": "not every participant sent an init reply", "agents": agents}
    seeds = [f"seed-{a}" for a in agents]
    pool = seeds + [rid for rid, _ in sorted(repairs, key=lambda x: int(x[0][1:].split("-")[0]))]

    def vec(c):
        if any(c not in raw[a] or millionths(raw[a][c]) is None for a in agents):
            return None
        return {a: normalize(Decimal(raw[a][c]), Decimal(anchors[a][0]), Decimal(anchors[a][1])) for a in agents}

    def accept(c, i):                                         # c, i: leximin vectors, sorted ascending
        if min(c) >= tau and min(i) < tau:
            return True
        return any(c[k] >= i[k] + eps and all(c[j] >= i[j] for j in range(k)) for k in range(len(c)))

    stages, xstar = [], None
    for n in range(len(repairs) + 1):
        upto = pool[: len(seeds) + n]
        vs = {c: vec(c) for c in upto}
        if any(v is None for v in vs.values()):
            return {"agents": agents, "tau": tau, "budget": budget, "stages": stages, "repairs": repairs, "proposer": proposer,
                    "anchors": anchors, "raw": raw, "pool": pool, "incomplete_at": n,
                    "missing": [f"{a[-1]} on {c}" for c in upto for a in agents if c not in raw[a] or millionths(raw[a][c]) is None]}
        cand = max(upto, key=lambda c: (sorted(vs[c].values()), -upto.index(c)))
        xstar = cand if xstar is None else (cand if accept(sorted(vs[cand].values()), sorted(vs[xstar].values())) else xstar)
        scores = vs[xstar]
        low = min(scores.values())
        stages.append({"n": n, "xstar": xstar, "scores": scores, "floor": low, "bottleneck": next(a for a in agents if scores[a] == low)})
    last = stages[-1]
    final = "SUCCESS" if last["floor"] >= tau else "BEST_EFFORT" if len(repairs) >= budget else None
    return {"agents": agents, "tau": tau, "budget": budget, "stages": stages, "repairs": repairs, "proposer": proposer, "anchors": anchors,
            "raw": raw, "pool": pool, "final": final, "winner": last["xstar"]}


def check_protocol(run, rep):
    rp = replay(run)
    if "why" in rp:
        rep.add("A", "A1", None, "repairs were sent by the bottleneck", rp["why"])
        return rp
    cancel = by_kind(run, "concord-cancel")
    aborted = bool(cancel) or any((run["rec"].get(a) or {}).get("report", {}).get("outcome") == "ABORTED" for a in AGENTS)
    rp["aborted"] = aborted
    titles = {"A1": "repairs were sent by the bottleneck", "A2": "the incumbent follows the ACCEPT rule",
              "A3": "the outcome and the result message are right"}
    if aborted:
        for cid in ("A1", "A2", "A3"):
            rep.add("A", cid, None, titles[cid], "the run was aborted: there is no outcome to check")
        return rp
    if "incomplete_at" in rp:
        # a run that was not aborted must have every rating in: a repair or a rating that does not fit the others is a fault
        rep.add("A", "A1", False, "every rating of every candidate is in", f"after {rp['incomplete_at']} repair(s) these ratings are "
                f"missing: {', '.join(rp['missing'][:8])}")
        for cid in ("A2", "A3"):
            rep.add("A", cid, None, titles[cid], "see A1")
        return rp
    stages, repairs, agents = rp["stages"], rp["repairs"], rp["agents"]
    # A1: the author of repair N is the bottleneck after stage N-1, and no repair is sent when none is owed
    wrong, owed_ok = [], True
    for n, (rid, who) in enumerate(repairs):
        st = stages[n]
        if who != st["bottleneck"]:
            wrong.append(f"{rid} by {who}, the bottleneck was {st['bottleneck']}")
        if st["floor"] >= rp["tau"] or n >= rp["budget"]:
            owed_ok = False
            wrong.append(f"{rid} sent when no repair was owed (floor {st['floor'] / MILLION:.3f}, round {n})")
        if rid != f"r{n + 1}-{who}":
            wrong.append(f"{rid} is not r{n + 1}-{who}")
    rep.add("A", "A1", not wrong, f"every repair was sent by the bottleneck ({len(repairs)} of {len(repairs)})" if not wrong
            else "repairs were sent by the bottleneck", "; ".join(wrong))
    # A2: the incumbent at each stage is the one the ACCEPT rule gives (it is how the stages were computed): show the path
    rep.add("A", "A2", True, "the incumbent follows the SELECT and ACCEPT rules",
            " -> ".join(dict.fromkeys(s["xstar"] for s in stages)) + f" (bar {rp['tau'] / MILLION}, eps from the init)")
    # A3: the outcome, the result message, the reporter, the reports of the agents
    last = stages[-1]
    problems = []
    if rp["final"] is None:
        problems.append("the run stopped but neither SUCCESS (floor at the bar) nor BEST_EFFORT (budget spent) holds")
    results = by_kind(run, "concord-result")
    if len(results) != 1:
        problems.append(f"{len(results)} result messages (one expected)")
    else:
        r = results[0]["root"]
        if r.get("from") != agents[0]:
            problems.append(f"the result is from {r.get('from')}, the reporter is {agents[0]}")
        if txt(r, "outcome") != rp["final"]:
            problems.append(f"result says {txt(r, 'outcome')}, the replay says {rp['final']}")
        if txt(r, "winning_candidate_id") != rp["winner"]:
            problems.append(f"result names {txt(r, 'winning_candidate_id')}, the replay says {rp['winner']}")
    for who, rec in run["rec"].items():
        rpt = rec.get("report") or {}
        if rpt.get("outcome") != rp["final"] or (rpt.get("winner") or {}).get("id") != rp["winner"]:
            problems.append(f"{who} reports {rpt.get('outcome')} / {(rpt.get('winner') or {}).get('id')}")
    rep.add("A", "A3", not problems, f"the outcome is {rp['final']} and the winner {rp['winner']}, as in the result message and the reports"
            if not problems else "the outcome and the result message are right", "; ".join(problems))
    return rp


# --------------------------------------------------------------------------------------------------------------------------------
# B: the messages
# --------------------------------------------------------------------------------------------------------------------------------

def check_messages(run, rep, rp):
    msgs = [m for m in run["msgs"] if m["root"] is not None]
    unreadable = [m["from"] for m in run["msgs"] if m["root"] is None]
    init = (by_kind(run, "concord-init") or [None])[0]
    run_id = init["root"].get("run_id") if init else None
    participants = rp.get("agents", [])
    bad = []
    seen = set()
    for m in msgs:
        r = m["root"]
        if r.tag in MAIN:
            if r.get("version") != SPEC_VERSION:
                bad.append(f"{r.tag} of {m['from']}: version {r.get('version')}")
            if check_of(m["text"]) != r.get("check"):
                bad.append(f"{r.tag} of {m['from']}: the integrity check is {r.get('check')}, the text gives {check_of(m['text'])}")
        if r.get("run_id") != run_id:
            bad.append(f"{r.tag} of {m['from']}: another run id")
        if not UUID.fullmatch(r.get("uuid", "")) or r.get("uuid") in seen:
            bad.append(f"{r.tag} of {m['from']}: the uuid is not a fresh UUID")
        seen.add(r.get("uuid"))
        if not AGENT_ID.fullmatch(r.get("from", "")) or (participants and r.get("from") not in participants):
            bad.append(f"{r.tag}: sender {r.get('from')} is not a participant")
    rep.add("B", "B1", not bad and not unreadable, f"{len(msgs)} messages: version, integrity check, run id, uuid and sender are right"
            if not bad and not unreadable else "version, integrity check, run id, uuid and sender", "; ".join(bad + [f"unreadable message of {u}" for u in unreadable]))

    # B2: ids, numbers, anchors, schema
    problems = []
    schema = {}
    if init:
        try:
            schema = json.loads(txt(init["root"], "proposal/candidate_schema"))
        except ValueError:
            problems.append("the candidate schema is not JSON")
    n_repair = 0
    for m in msgs:
        r = m["root"]
        who = r.get("from")
        if r.tag in ("concord-init-reply", "concord-repair"):
            p = r.find("proposal")
            if r.tag == "concord-init-reply":
                want = f"seed-{who}"
                rr, ri = millionths(txt(r, "anchors/raw_reject")), millionths(txt(r, "anchors/raw_ideal"))
                if rr is None or ri is None or not (0 <= rr <= 100 * MILLION and 0 <= ri <= 100 * MILLION) or not ri > rr:
                    problems.append(f"{who}: anchors {txt(r, 'anchors/raw_reject')}/{txt(r, 'anchors/raw_ideal')} are not valid (ideal must exceed reject)")
            else:
                n_repair += 1
                want = f"r{n_repair}-{who}"
            if p.get("id") != want:
                problems.append(f"{who}: the proposal id {p.get('id')} should be {want}")
            cand = txt(p, "candidate")
            if "enum" in schema and cand not in schema["enum"]:
                problems.append(f"{who}: the candidate {cand!r} is not one of the listed options")
            if not txt(p, "rationale") or not txt(p, "score/reasoning"):
                problems.append(f"{who}: a rationale or a reasoning is empty")
            v = millionths(txt(p, "score/raw"))
            if v is None or v > 100 * MILLION:
                problems.append(f"{who}: the score {txt(p, 'score/raw')} is not a number from 0 to 100")
        if r.tag == "concord-score":
            for s in r.findall("score"):
                v = millionths(txt(s, "raw"))
                if v is None or v > 100 * MILLION:
                    problems.append(f"{who}: the rating of {s.get('candidate_id')}, {txt(s, 'raw')}, is not a number from 0 to 100")
                if not txt(s, "reasoning"):
                    problems.append(f"{who}: no reasoning for {s.get('candidate_id')}")
    rep.add("B", "B2", not problems, "ids are in sequence, numbers and anchors are valid, candidates satisfy the schema" if not problems
            else "ids, numbers, anchors and the schema", "; ".join(problems))

    # B3: who rated what, exactly once, and what each score message covers
    problems = []
    if "pool" not in rp:
        rep.add("B", "B3", None, "each score message covers what was owed", "no pool to compare with")
        return
    pool, proposer = rp["pool"], rp["proposer"]
    for who in participants:
        rated = []
        for m in [x for x in msgs if x["from"] == who and x["root"].tag == "concord-score"]:
            ids = [s.get("candidate_id") for s in m["root"].findall("score")]
            rated.append(ids)
            if any(proposer.get(i) == who for i in ids):
                problems.append(f"{who} rates a candidate it proposed")
        flat = [i for ids in rated for i in ids]
        if len(flat) != len(set(flat)):
            problems.append(f"{who} rates a candidate twice")
        want = [c for c in pool if proposer.get(c) != who]
        unknown = sorted(set(flat) - set(pool))
        if unknown:
            problems.append(f"{who} rates {unknown}, which {'is' if len(unknown) == 1 else 'are'} not a candidate of the run")
        if not rp.get("aborted") and set(flat) != set(want):
            problems.append(f"{who} rated {sorted(set(flat) ^ set(want))} wrongly (missing or extra)")
        if rated and set(rated[0]) != {f"seed-{a}" for a in participants if a != who}:
            problems.append(f"{who}: the first score message covers {rated[0]}, the seeds of the others were owed")
        if any(len(ids) != 1 for ids in rated[1:]):
            problems.append(f"{who}: a score message after the seeds covers more than one candidate")
    rep.add("B", "B3", not problems, "each agent rated every candidate of the others exactly once, and each score message covers what was owed"
            if not problems else "each score message covers what was owed", "; ".join(problems))


# --------------------------------------------------------------------------------------------------------------------------------
# C: everyone saw the same run
# --------------------------------------------------------------------------------------------------------------------------------

def check_same_run(run, rep):
    problems = []
    for m in run["msgs"]:
        for who in AGENTS:
            st = run["state"].get(who)
            if who == m["from"] or st is None:
                continue
            if m["uuid"] not in st["uuids"]:
                problems.append(f"{who} did not record the {m['root'].tag if m['root'] is not None else '?'} of {m['from']}")
    reports = {who: (rec.get("report") or {}) for who, rec in run["rec"].items()}
    outcomes = {(r.get("outcome"), (r.get("winner") or {}).get("id")) for r in reports.values()}
    if len(outcomes) > 1:
        problems.append(f"the reports differ: {sorted(map(str, outcomes))}")
    for who, r in reports.items():
        if r.get("outcome") != "ABORTED" and r.get("status") != "confirmed":
            problems.append(f"{who}: status {r.get('status')}")
        if (r.get("mismatches") or {}).get("sent") or (r.get("mismatches") or {}).get("received"):
            problems.append(f"{who} has mismatch errors")
    missing = [a for a in AGENTS if a not in run["rec"] or a not in run["state"]]
    if missing:
        problems.append(f"no record of {', '.join(missing)}")
    rep.add("C", "C1", not problems, f"all {len(run['msgs'])} messages were recorded by the other two agents, and the three reports agree"
            if not problems else "everyone recorded every message and the reports agree", "; ".join(problems))


# --------------------------------------------------------------------------------------------------------------------------------
# D: the harness
# --------------------------------------------------------------------------------------------------------------------------------

def conversation(rec):
    """What the model read: the text of every user message of its conversation."""
    parts = []
    for m in rec.get("transcript") or []:
        if m["role"] == "user":
            parts += [b.get("text", "") for b in m["content"] if b.get("type") == "text"]
    return "\n".join(parts)


def check_harness(run, rep, rp):
    configs = {w: rec.get("config") for w, rec in run["rec"].items()}
    if not all(configs.values()):
        for cid, title in (("D1", "the model read every message the node recorded"), ("D2", "the threshold is the one asked for")):
            rep.add("D", cid, None, title, "this run saved no configuration (made before it was recorded)")
        return
    problems = []
    for who, rec in run["rec"].items():
        st = run["state"].get(who)
        if st is None:
            continue
        own = set(st["sent_by_uuid"])
        text = conversation(rec)
        for uid in st["uuids"]:
            if uid not in own and uid not in text:
                problems.append(f"{who}'s conversation lacks the message {uid[:8]} that its node recorded")
    rep.add("D", "D1", not problems, "every message the node of an agent recorded is in its conversation"
            if not problems else "the model read every message the node recorded", "; ".join(problems[:6]))
    asked = {c.get("threshold") for c in configs.values()}
    init = (by_kind(run, "concord-init") or [None])[0]
    got = txt(init["root"], "params/satisfaction_threshold") if init else None
    asked = {a for a in asked if a is not None}
    if not asked:
        rep.add("D", "D2", True, "the threshold was chosen by the initiator", f"it chose {got}")
    else:
        ok = len(asked) == 1 and got is not None and millionths(got) == millionths(str(next(iter(asked))))
        rep.add("D", "D2", ok, f"the threshold in the init, {got}, is the one asked for" if ok else "the threshold is the one asked for",
                "" if ok else f"asked for {sorted(asked)}, the init says {got}")


# --------------------------------------------------------------------------------------------------------------------------------
# information (never a failure)
# --------------------------------------------------------------------------------------------------------------------------------

def information(run, rp):
    out = []
    cfg = next((rec.get("config") for rec in run["rec"].values() if rec.get("config")), None)
    if cfg:
        v = cfg.get("versions", {})
        code = cfg.get("code") or {}
        out.append(f"config: model {cfg.get('model') or '(stand-in model)'}, temperature {cfg.get('temperature')}, threshold "
                   f"{cfg.get('threshold') if cfg.get('threshold') is not None else 'chosen by the initiator'}, fresh {cfg.get('fresh')}, "
                   f"endpoint {cfg.get('endpoint_host') or '-'}")
        out.append(f"        ioc-concord {v.get('ioc_concord')}, anthropic {v.get('anthropic')}, slim-bindings {v.get('slim_bindings')}, "
                   f"code {code.get('commit', '?')}{' + uncommitted changes' if code.get('uncommitted_changes') else ''}")
    else:
        out.append("config: not recorded (this run is older than the configuration record)")
    odd = [m["root"].tag for m in run["msgs"] if m["root"] is not None and m["root"].tag in CONTROL]
    cancelled = any((rec.get("report") or {}).get("outcome") == "ABORTED" for rec in run["rec"].values())
    out.append(f"clean run: {'yes' if not odd and not cancelled else 'NO: ' + (', '.join(odd) or 'aborted')}")
    ref = {}
    for who, rec in run["rec"].items():
        ref[who[-1]] = sum(1 for m in rec.get("transcript") or [] if m["role"] == "user" for b in m["content"]
                           if b.get("type") == "tool_result" and b.get("is_error"))
    out.append(f"refused tool calls: {ref}")
    if rp.get("repairs"):
        texts, rep_same = {}, 0
        for m in run["msgs"]:
            r = m["root"]
            if r is not None and r.tag == "concord-repair":
                key = (r.get("from"), txt(r, "proposal/candidate"))
                rep_same += key in texts
                texts[key] = 1
        out.append(f"repairs that propose a candidate the same agent already proposed: {rep_same} of {len(rp['repairs'])}")
    if rp.get("winner") and rp.get("tau"):
        near, vals = [], []
        for who in rp["agents"]:
            a = rp["anchors"][who]
            raw = rp["raw"][who].get(rp["winner"])
            if raw is not None:
                s = normalize(Decimal(raw), Decimal(a[0]), Decimal(a[1]))
                vals.append(f"{who[-1]} {raw} ({s / MILLION:.3f})")
                if rp["tau"] <= s < rp["tau"] + 30_000:
                    near.append(who[-1])
        out.append(f"ratings of the winner {rp['winner']}, raw (normalized): " + ", ".join(vals)
                   + (f"; within 0.03 above the bar {rp['tau'] / MILLION}: {', '.join(near)}" if near else ""))
    return out


# --------------------------------------------------------------------------------------------------------------------------------

def verify(run_dir):
    """Verify a run. Returns {"checks": [(group, id, status, title, detail)], "failed": n, "info": [...]}."""
    run = load_run(Path(run_dir))
    rep = Report()
    if not run["msgs"]:
        rep.add("A", "A0", False, "the run has messages", "no messages found: it needs the participant-X.json and -state.json files")
        return {"checks": rep.rows, "failed": 1, "info": []}
    rp = check_protocol(run, rep)
    check_messages(run, rep, rp)
    check_same_run(run, rep)
    check_harness(run, rep, rp)
    return {"checks": rep.rows, "failed": len(rep.failed()), "info": information(run, rp)}


def print_result(result, run_dir):
    print(f"verify {run_dir}")
    groups = {"A": "the protocol", "B": "the messages", "C": "everyone saw the same run", "D": "the harness"}
    last = None
    for g, cid, status, title, detail in result["checks"]:
        if g != last:
            print(f"\n{g}  {groups.get(g, g)}")
            last = g
        print(f"  [{status:>4}] {cid}  {title}" + (f"\n           {detail}" if detail and status != "pass" else ""))
        if status == "pass" and detail:
            print(f"           {detail}")
    print("\ninformation")
    for line in result["info"]:
        print(f"  {line}")
    n = Counter(r[2] for r in result["checks"])
    print(f"\nresult: {'VALID' if not result['failed'] else 'INVALID'}  ({n['pass']} passed, {n['FAIL']} failed, {n['skip']} skipped)")


from collections import Counter  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("run", help="a run folder, such as runs/manual or runs/batch-<time>/t0.7-r0")
    args = p.parse_args()
    result = verify(args.run)
    print_result(result, args.run)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
