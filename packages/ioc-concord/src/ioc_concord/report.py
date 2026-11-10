#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Where a run stands for an agent, and the report of the run: views of the state of a run that change nothing."""
from ioc_concord.engine import evaluate, lacking
from ioc_concord.protocol import ABORTED
from ioc_concord.state import INITIATOR, RUN_ABORTED, RUN_ENDED


def status_of(run):
    """The `status` of `run`, the `outcome` its agent has computed (None before), and the engine's view: `state` ("wait", "repair" or
    "done"), `x_star`, `floor`, `scores` (millionths), `bottleneck`, `round`, `lacking` (the ratings still missing, as [(agent,
    candidate)]), `anchors_in` and the deadline of each timer."""
    ev = evaluate(run["engine"])
    return {"run_id": run["run_id"], "role": run["role"], "status": run["status"], "outcome": run["outcome"],
            "state": ev["state"], "x_star": ev["x_star"], "floor": ev["floor"], "scores": ev["scores"],
            "bottleneck": ev["bottleneck"], "round": ev["round"], "reporter": ev["reporter"],
            "lacking": lacking(run["engine"]), "anchors_in": sorted(run["engine"]["anchors"]),
            "timers": {k: t["deadline"] for k, t in run["timers"].items()}}


def report_of(run, me):
    """The report of `run` for the agent `me`, as the spec describes it: `outcome`, `winner` ({id, candidate}), `scores` and `shortfall`
    (millionths), `rounds`, `reason`, `results_compared` (the reporter's result and whether it matches this agent's outcome; for the
    reporter, its own) and `status`: "pending" until the run has ended for this agent, then "confirmed", or "disputed" when a mismatch
    was found, sent or received or a cancel reached it after its run had ended. An aborted run has outcome ABORTED, no winner, its
    `reason` ({reason, agent, superseded_by, note}) and the status "aborted": the spec gives it none."""
    if run["role"] == INITIATOR:
        return _initiator_report(run)
    ev = evaluate(run["engine"])
    done = run["outcome"] not in (None, ABORTED)
    winner = next((c for c in run["engine"]["pool"] if c["id"] == ev["x_star"]), None) if done else None
    reporter = run["participants"][0]
    mine = (run["outcome"], ev["x_star"]) if done else None
    if me == reporter:
        compared = {"reporter": reporter, "result": {"outcome": run["outcome"], "winning_candidate_id": ev["x_star"]} if done else None,
                    "matches": True if done else None}
    else:
        rep = run["reported"]
        compared = {"reporter": reporter, "result": rep,
                    "matches": None if rep is None or not done else mine == (rep["outcome"], rep["winning_candidate_id"])}
    mismatches = run["mismatches"]
    if run["status"] == RUN_ABORTED:
        status = "aborted"
    elif mismatches["sent"] or mismatches["received"] or run["disputed"]:
        status = "disputed"
    else:
        status = "confirmed" if run["status"] == RUN_ENDED else "pending"
    return {"run_id": run["run_id"], "outcome": run["outcome"],
            "winner": {"id": winner["id"], "candidate": winner["candidate"]} if winner else None,
            "scores": ev["scores"] if done else {}, "shortfall": ev["shortfall"] if done else {}, "rounds": ev["t"],
            "reason": run["abort"], "results_compared": compared,
            "mismatches": {"sent": mismatches["sent"], "received": list(mismatches["received"])}, "status": status}


def _initiator_report(run):
    """The report of an initiator that is not a participant: it has no outcome of its own, and learns how the run ended from the result
    of the reporter. Its status is "unverified", since there is nothing of its own to compare, unless a mismatch error reaches it
    ("disputed"); "pending" before the result, "aborted" after a cancel or a give-up."""
    rep, mismatches = run["reported"], run["mismatches"]
    pool = {c["id"]: c for c in run["engine"]["pool"]}
    if run["status"] == RUN_ABORTED:
        status = "aborted"
    elif mismatches["received"] or run["disputed"]:
        status = "disputed"
    else:
        status = "unverified" if run["status"] == RUN_ENDED else "pending"
    winner = None
    if rep is not None and run["status"] != RUN_ABORTED:
        winner = {"id": rep["winning_candidate_id"], "candidate": pool.get(rep["winning_candidate_id"], {}).get("candidate")}
    return {"run_id": run["run_id"], "outcome": run["outcome"], "winner": winner, "scores": {}, "shortfall": {}, "rounds": None,
            "reason": run["abort"], "results_compared": {"reporter": run["participants"][0], "result": rep, "matches": None},
            "mismatches": {"sent": 0, "received": list(mismatches["received"])}, "status": status}
