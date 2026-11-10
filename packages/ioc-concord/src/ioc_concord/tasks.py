#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the agent's model must write now, worked out from the state of the run, never queued: asking twice gives the same tasks, and
after a restart from the store they are still right."""
from ioc_concord.engine import evaluate
from ioc_concord.liveness import lacking_by
from ioc_concord.protocol import format_number
from ioc_concord.state import PARTICIPANT, item_key


def to_rate(run, me):
    """The candidates of the others that `me` has not rated, once every init reply is in."""
    return lacking_by(run).get(me, [])


def tasks_of(run, me):
    """The tasks `me` owes in `run` (None when it has no active run), as dicts with a `kind`: `init-reply`, `score` or `repair`. See
    `Agent.due` for what each holds."""
    if run is None or run["role"] != PARTICIPANT:
        return []
    st = run["engine"]
    if item_key("init-reply", me) not in run["sent"]:
        return [{"kind": "init-reply", "run_id": run["run_id"], "initiator": run["initiator"], "context": run["context"],
                 "x_reject": run["x_reject"], "x_ideal": run["x_ideal"], "schema": run["schema"]}]
    owed = to_rate(run, me)
    if not owed:
        return _repair_task(run, me)
    by_id = {c["id"]: c for c in st["pool"]}
    return [{"kind": "score", "run_id": run["run_id"], "candidates": [
        {"id": cid, "from": by_id[cid]["proposer"], "candidate": by_id[cid]["candidate"],
         "rationale": by_id[cid]["rationale"], "reasoning": st["reasoning"][by_id[cid]["proposer"]].get(cid, "")}
        for cid in owed]}]


def _repair_task(run, me):
    ev = evaluate(run["engine"])
    if ev["state"] != "repair" or ev["bottleneck"] != me or item_key("repair", me, f"r{ev['round']}-{me}") in run["sent"]:
        return []
    st, x = run["engine"], ev["x_star"]
    inc = next(c for c in st["pool"] if c["id"] == x)
    text = lambda d: {a: format_number(v) for a, v in d.items()}
    return [{"kind": "repair", "run_id": run["run_id"], "round": ev["round"],
             "incumbent": {"id": x, "candidate": inc["candidate"], "rationale": inc["rationale"]},
             "threshold": format_number(st["params"]["satisfaction_threshold"]), "floor": format_number(ev["floor"]),
             "scores": text(ev["scores"]), "shortfall": text(ev["shortfall"]),
             "reasoning": {a: st["reasoning"][a].get(x, "") for a in st["agents"]},
             "repairs_left": st["params"]["B"] - ev["t"]}]
