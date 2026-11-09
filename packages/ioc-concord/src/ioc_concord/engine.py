#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CONCORD decision engine: SELECT, ACCEPT, bottleneck and termination over the ratings the agents give.

Every participant runs this engine over the same messages, so they all reach the same decision. It holds no I/O, no clock
and no process state: it works on the plain `st` dict that `new_engine_state` returns, and `evaluate` reads it without
changing it. The decision is never kept: `evaluate` goes through the evaluations again from the seeds each time it is
called, so a participant marked missing changes the result of every evaluation at once, as the spec asks.

The state:

    params    satisfaction_threshold, eps, B (the other parameters are the node's business)
    agents    the participants, in the order of <participants>
    anchors   {agent: {"raw_reject", "raw_ideal"}}, the first one an agent sent
    pool      [{"id", "proposer", "round", "candidate", "rationale"}], the seeds and every repair, in arrival order
    raw       {agent: {candidate id: raw score}}
    reasoning {agent: {candidate id: text}}
    missing   the participants marked missing, in the order they were marked
"""
import math
import re

from ioc_concord.errors import InvalidMessage, InvalidRun
from ioc_concord.protocol import ABORTED, BEST_EFFORT, SUCCESS

TOL = 1e-9

_SEED_RE = re.compile(r"^seed-(.+)$")
_REPAIR_RE = re.compile(r"^r([1-9][0-9]*)-(.+)$")

# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_params(agents, params):
    """Raises InvalidRun unless these are the participants and parameters of a run that can be played. `params` holds
    satisfaction_threshold, eps, B, timeout and retries. A `timeout` of 0 is valid: it switches the timers off."""
    if len(agents) < 2:
        raise InvalidRun("a negotiating set needs at least 2 agents")
    if len(set(agents)) != len(agents):
        raise InvalidRun("duplicate agent names")
    if any(not str(a).strip() for a in agents):
        raise InvalidRun("an agent name cannot be empty")
    tau, eps, budget = params["satisfaction_threshold"], params["eps"], params["B"]
    if not (_number(tau) and 0 < tau <= 1):
        raise InvalidRun("satisfaction_threshold must be in (0, 1]")
    if not (_number(eps) and eps > 0):
        raise InvalidRun("eps must be > 0")
    if not (isinstance(budget, int) and not isinstance(budget, bool) and budget >= 1):
        raise InvalidRun("budget must be an integer, at least 1")
    if not (_number(params["timeout"]) and params["timeout"] >= 0):
        raise InvalidRun("timeout must be 0 (no timers) or above 0")
    retries = params["retries"]
    if not (isinstance(retries, int) and not isinstance(retries, bool) and retries >= 0):
        raise InvalidRun("retries must be an integer, at least 0")


def timers_enabled(params):
    """Whether the run has timers: a `timeout` of 0 means nobody ever asks again or marks an agent missing on a clock."""
    return params["timeout"] > 0

# --------------------------------------------------------------------------
# candidate ids
# --------------------------------------------------------------------------


def seed_id(agent):
    return f"seed-{agent}"


def repair_id(n, agent):
    return f"r{n}-{agent}"


def parse_candidate_id(cid):
    """(round, agent) of a candidate id: round 0 for `seed-<agent>`, N for `r<N>-<agent>`. None for any other text."""
    m = _SEED_RE.match(cid or "")
    if m:
        return 0, m.group(1)
    m = _REPAIR_RE.match(cid or "")
    return (int(m.group(1)), m.group(2)) if m else None

# --------------------------------------------------------------------------
# the state and what is recorded in it
# --------------------------------------------------------------------------


def new_engine_state(agents, params):
    return {"params": {"satisfaction_threshold": params["satisfaction_threshold"], "eps": params["eps"], "B": params["B"]},
            "agents": list(agents), "anchors": {}, "pool": [], "raw": {a: {} for a in agents},
            "reasoning": {a: {} for a in agents}, "missing": []}


def _raw_value(raw, what):
    if not _number(raw) or not 0 <= raw <= 100:
        raise InvalidMessage(f"{what} must be a number from 0 to 100, not {raw!r}")
    return raw


def add_anchor(st, agent, raw_reject, raw_ideal):
    """Record where an agent's own scale starts and ends. The first anchors of an agent stand: later ones are ignored, since
    replacing them would invalidate every earlier comparison. Returns whether they were recorded. Raises InvalidMessage for
    a value outside 0 to 100 and for an inverted scale."""
    _raw_value(raw_reject, "raw_reject")
    _raw_value(raw_ideal, "raw_ideal")
    if not raw_ideal > raw_reject:
        raise InvalidMessage(f"raw_ideal ({raw_ideal:g}) must exceed raw_reject ({raw_reject:g}). These are satisfaction "
                             "ratings of the two outcomes (higher = better for your role), not measurements of the "
                             "current state.")
    if agent in st["anchors"]:
        return False
    st["anchors"][agent] = {"raw_reject": raw_reject, "raw_ideal": raw_ideal}
    return True


def active(st):
    """The participants that are not marked missing, in the order of <participants>."""
    return [a for a in st["agents"] if a not in st["missing"]]


def mark_missing(st, agent):
    """Mark `agent` missing. Final for the run. Returns whether it was newly marked."""
    if agent not in st["agents"] or agent in st["missing"]:
        return False
    st["missing"].append(agent)
    return True


def reporter(st):
    """The first participant of <participants> that is not marked missing (None when all are)."""
    return next(iter(active(st)), None)


def ordered_pool(st):
    """The pool in its canonical order: the earlier round first, and within a round the proposer that comes first in
    <participants>. Never arrival order: every participant must pick the same candidate, whatever order it received them."""
    return sorted(st["pool"], key=lambda c: (c["round"], st["agents"].index(c["proposer"])))


def repairs_in_pool(st):
    """`t`: the number of repair proposals so far, whether or not they replaced the incumbent."""
    return sum(1 for c in st["pool"] if c["round"] > 0)


def next_round(st):
    """The number the next repair must carry: `t + 1`."""
    return repairs_in_pool(st) + 1


def add_proposal(st, agent, cid, candidate, rationale, raw, reasoning):
    """Record a seed or a repair together with its proposer's own score for it. The id must be `seed-<agent>` or
    `r<N>-<agent>` for this agent; that `N` is `next_round` is for the caller to judge, since a copy of the run state that is
    incomplete must first ask for what it lacks. The first proposal for an id stands. Returns whether it was recorded.
    Raises InvalidMessage for a wrong id or a score outside 0 to 100."""
    parsed = parse_candidate_id(cid)
    if parsed is None or parsed[1] != agent or agent not in st["agents"]:
        raise InvalidMessage(f"the proposal id {cid!r} is not seed-{agent} or r<N>-{agent}")
    _raw_value(raw, "raw")
    if any(c["id"] == cid for c in st["pool"]):
        return False
    st["pool"].append({"id": cid, "proposer": agent, "round": parsed[0], "candidate": candidate, "rationale": rationale})
    st["raw"][agent][cid] = raw
    st["reasoning"][agent][cid] = reasoning
    return True


def add_ratings(st, agent, ratings):
    """Record a <concord-score>: `ratings` is [(candidate id, raw, reasoning)]. Returns (recorded ids, unknown ids): a rating
    of a candidate that is not in the pool is not invalid, the proposal was lost, so the caller asks for it, and that rating
    is not recorded. A rating the agent already gave stands. Raises InvalidMessage, recording nothing, when a raw score is
    outside 0 to 100, a candidate is rated twice or the agent rates a candidate it proposed."""
    seen, owner = set(), {c["id"]: c["proposer"] for c in st["pool"]}
    for cid, raw, _ in ratings:
        _raw_value(raw, f"the score for {cid}")
        if cid in seen:
            raise InvalidMessage(f"{cid} is rated twice")
        seen.add(cid)
        if (owner.get(cid) or (parse_candidate_id(cid) or (None, None))[1]) == agent:
            raise InvalidMessage(f"{agent} rates {cid}, which it proposed: its score is in the proposal")
    recorded, unknown = [], []
    for cid, raw, why in ratings:
        if cid not in owner:
            unknown.append(cid)
        elif cid not in st["raw"][agent]:
            st["raw"][agent][cid] = raw
            st["reasoning"][agent][cid] = why
            recorded.append(cid)
    return recorded, unknown

# --------------------------------------------------------------------------
# scores
# --------------------------------------------------------------------------


def norm(st, agent, raw):
    """`raw` on the scale of `agent`, in [0, 1], clamped."""
    a = st["anchors"][agent]
    return max(0.0, min(1.0, (raw - a["raw_reject"]) / (a["raw_ideal"] - a["raw_reject"])))


def score(st, agent, cid):
    """The normalized score of `agent` on `cid`, or None when it has none (or its anchors are not in)."""
    raw = st["raw"].get(agent, {}).get(cid)
    if raw is None or agent not in st["anchors"]:
        return None
    return norm(st, agent, raw)


def scores_of(st, cid):
    """{agent: normalized score} of the participants that are not missing and have rated `cid`."""
    return {a: s for a in active(st) if (s := score(st, a, cid)) is not None}


def floor_of(st, cid):
    vals = scores_of(st, cid).values()
    return min(vals) if vals else None


def leximin(st, cid):
    """The normalized scores of `cid` from the participants that are not missing, sorted from the lowest."""
    return sorted(scores_of(st, cid).values())


def lex_gt(v, u):
    """Whether vector `v` beats `u`: the larger value at the first position where they differ."""
    for a, b in zip(v, u):
        if a > b + TOL:
            return True
        if b > a + TOL:
            return False
    return False


def select(st, ids):
    """SELECT: the candidate of `ids` (in pool order) with the best leximin vector; the first one wins an exact tie."""
    best = ids[0]
    for cid in ids[1:]:
        if lex_gt(leximin(st, cid), leximin(st, best)):
            best = cid
    return best


def accept(st, cand, inc):
    """ACCEPT: whether candidate `cand` replaces the incumbent `inc`. True if `cand` clears τ for everyone and `inc` does
    not, or if some position k of its leximin vector gains at least eps while no position before it loses."""
    tau, eps = st["params"]["satisfaction_threshold"], st["params"]["eps"]
    if floor_of(st, cand) >= tau - TOL and floor_of(st, inc) < tau - TOL:
        return True
    c, i = leximin(st, cand), leximin(st, inc)
    return any(c[k] >= i[k] + eps - TOL and all(c[j] >= i[j] - TOL for j in range(k)) for k in range(len(c)))


def bottleneck(st, cid):
    """The participant that is not missing with the lowest normalized score on `cid`; a tie goes to the one that comes first
    in <participants>."""
    return min(scores_of(st, cid).items(), key=lambda kv: (kv[1], st["agents"].index(kv[0])))[0]


def lacking(st):
    """[(agent, candidate id)]: the ratings of participants that are not missing that are not in yet, over the whole pool."""
    return [(a, c["id"]) for c in ordered_pool(st) for a in active(st) if c["id"] not in st["raw"][a]]

# --------------------------------------------------------------------------
# the evaluation
# --------------------------------------------------------------------------


def _complete(st, ids):
    return all(a in st["anchors"] and cid in st["raw"][a] for a in active(st) for cid in ids)


def evaluate(st):
    """Go through the evaluations from the first, for the seeds and then for each repair in the order of the pool, with only the
    ratings of the participants that are not missing, and say what follows. Reads `st`, changes nothing. Returns a dict:

    state       "wait" (ratings or init replies are still owed), "repair" (the bottleneck must propose) or "done"
    outcome     SUCCESS, BEST_EFFORT or ABORTED once the state is "done", else None
    reason      for ABORTED, "too few participants"
    x_star      the incumbent: the winner when done, the incumbent so far while waiting (None before the seeds are rated)
    t           the number of repairs in the pool
    floor       floor(x*) over the participants that are not missing
    scores      {agent: normalized score on x*} of the participants that are not missing
    shortfall   {agent: how far below τ} for each of them under τ
    bottleneck  the participant that must propose, when the state is "repair"
    round       the number its repair must carry, when the state is "repair"
    reporter    the first participant that is not missing
    log         one entry per evaluation: the stage, the candidate SELECT gave, whether it replaced the incumbent
    """
    agents = active(st)
    out = {"state": "wait", "outcome": None, "reason": None, "x_star": None, "t": repairs_in_pool(st), "floor": None,
           "scores": {}, "shortfall": {}, "bottleneck": None, "round": None, "reporter": reporter(st), "log": []}
    if len(agents) < 2:
        return {**out, "state": "done", "outcome": ABORTED, "reason": "too few participants"}
    if any(a not in st["anchors"] and a not in st["missing"] for a in st["agents"]):
        return out                                   # an init reply is still owed
    pool = ordered_pool(st)
    seeds, repairs = [c["id"] for c in pool if c["round"] == 0], [c["id"] for c in pool if c["round"] > 0]
    tau, x_star = st["params"]["satisfaction_threshold"], None
    for k in range(len(repairs) + 1):
        ids = seeds + repairs[:k]
        if not ids or not _complete(st, ids):
            break
        x_cand = select(st, ids)
        replaced = x_star is None or (x_cand != x_star and accept(st, x_cand, x_star))
        out["log"].append({"stage": k, "select": x_cand, "replaced": replaced, "floor": floor_of(st, x_cand)})
        if replaced:
            x_star = x_cand
    else:
        out["state"] = "done"                        # every evaluation is made: the pool is rated through
    out["x_star"] = x_star
    if x_star is None:
        return out
    out["floor"] = floor_of(st, x_star)
    out["scores"] = scores_of(st, x_star)
    out["shortfall"] = {a: tau - s for a, s in out["scores"].items() if s < tau - TOL}
    if out["state"] != "done":
        return out
    if out["floor"] >= tau - TOL:
        out["outcome"] = BEST_EFFORT if st["missing"] else SUCCESS
    elif out["t"] >= st["params"]["B"]:
        out["outcome"] = BEST_EFFORT
    else:
        out.update(state="repair", bottleneck=bottleneck(st, x_star), round=out["t"] + 1)
    return out
