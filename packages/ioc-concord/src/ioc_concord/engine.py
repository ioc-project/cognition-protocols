#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CONCORD decision engine: SELECT, ACCEPT, REPAIR and COMMIT over the ratings the agents give.

Every participant runs this engine over the same replies, so they all reach the same decision (the incumbent, the
bottleneck, the outcome). It holds no I/O and no process state: it works on the plain `st` dict that
`new_engine_state` returns.
"""
import hashlib
import re

from ioc_concord.errors import ReplyError

TOL = 1e-9

# --------------------------------------------------------------------------
# decision engine (SELECT / ACCEPT / REPAIR / COMMIT)
# --------------------------------------------------------------------------

def norm(st, agent, raw):
    a = st["anchors"][agent]
    span = a["raw_ideal"] - a["raw_reject"]
    return max(0.0, min(1.0, (raw - a["raw_reject"]) / span))


def score(st, agent, cid):
    raw = st["raw"].get(agent, {}).get(cid)
    if raw is None or raw == "MISSING":
        return None
    return norm(st, agent, raw)


def column(st, cid):
    return {a: score(st, a, cid) for a in st["agents"]}


def present(st, cid):
    return [s for s in column(st, cid).values() if s is not None]


def absent(st, cid):
    return [a for a in st["agents"] if score(st, a, cid) is None]


def floor_of(st, cid):
    vals = present(st, cid)
    return min(vals) if vals else None


def leximin(st, cid):
    return sorted(present(st, cid))


def lex_gt(v, u):
    for a, b in zip(v, u):
        if a > b + TOL:
            return True
        if b > a + TOL:
            return False
    return len(v) > len(u)


def scored_ids(st):
    return [c["id"] for c in st["pool"] if present(st, c["id"])]


def select(st):
    tau = st["params"]["satisfaction_threshold"]
    # Canonical order (round, then the agent's place in the roster), never arrival order: every participant must pick
    # the same incumbent, also when two candidates tie, whatever order it happened to receive the replies in.
    order = {c["id"]: (c.get("round", 0), st["agents"].index(c["proposer"])) for c in st["pool"]}
    ids = sorted(scored_ids(st), key=lambda cid: order[cid])
    if not ids:
        return None, False
    feasible = [c for c in ids
                if not absent(st, c) and floor_of(st, c) >= tau - TOL]
    pool = feasible if feasible else ids
    best = pool[0]
    for c in pool[1:]:
        if lex_gt(leximin(st, c), leximin(st, best)):
            best = c
    return best, bool(feasible)


def accept(v, u, eps):
    for k in range(min(len(v), len(u))):
        if v[k] >= u[k] + eps - TOL and all(v[i] >= u[i] - TOL for i in range(k)):
            return True, k
    return False, None


def advance(st):
    """Run SELECT/ACCEPT/REPAIR/COMMIT when all required data is in.

    Called after every state mutation. Modifies st in-place. After this
    returns, current_action(st) always resolves to a clean phase — there is
    no 'scoring complete but decision pending' intermediate state.
    """
    if st.get("outcome"):
        return

    # A repair has been requested and its candidate is not in yet: nothing to decide (running this again would
    # spend a round of the budget for nothing).
    if st.get("pending"):
        return

    # Not all anchors in yet
    if any(a not in st["anchors"] for a in st["agents"]):
        return

    # Not all seeds in yet
    if not st["pool"]:
        return

    # Still waiting for score data
    pool_ids = [c["id"] for c in st["pool"]]
    for a in st["agents"]:
        for cid in pool_ids:
            if cid not in st["raw"].get(a, {}):
                return

    # All scores collected — run SELECT + ACCEPT
    x_cand, feasible = select(st)
    eps = st["params"]["eps"]

    if st["x_star"] is None:
        st["x_star"] = x_cand
        why = "initial incumbent"
        changed = True
    else:
        ok, k = accept(leximin(st, x_cand), leximin(st, st["x_star"]), eps)
        if ok and x_cand != st["x_star"]:
            st["x_star"] = x_cand
            why = f"leximin position {k} gained >= {eps}"
            changed = True
        else:
            why = ("incumbent still best" if x_cand == st["x_star"]
                   else f"SELECT returned {x_cand}; gain below eps={eps}")
            changed = False

    st["log"].append({
        "t": st["t"], "x_star": st["x_star"],
        "select": x_cand, "feasible": feasible,
        "accepted": changed, "why": why,
        "floor": floor_of(st, st["x_star"]),
    })

    # STOP: success — blocked if ANY agent has a MISSING entry anywhere in the run
    tau = st["params"]["satisfaction_threshold"]
    floor = floor_of(st, st["x_star"])
    unknown = absent(st, st["x_star"])
    has_any_missing = any(
        v == "MISSING"
        for agent_raw in st["raw"].values()
        for v in agent_raw.values()
    )
    if floor is not None and floor >= tau - TOL and not unknown and not has_any_missing:
        st["outcome"] = "SUCCESS"
        return

    # STOP: budget
    if st["t"] >= st["params"]["B"]:
        st["outcome"] = "BEST_EFFORT"
        return

    # REPAIR: bottleneck agent
    st["t"] += 1
    rated = [(score(st, a, st["x_star"]), i, a)
             for i, a in enumerate(st["agents"])
             if score(st, a, st["x_star"]) is not None]
    b = min(rated)[2]
    st["pending"] = {"agent": b, "round": st["t"]}


def new_engine_state(agents, params):
    return {"params": dict(params), "agents": list(agents), "anchors": {}, "pool": [], "raw": {a: {} for a in agents},
            "score_reasoning": {}, "x_star": None, "t": 0, "pending": None, "outcome": None, "log": []}


def add_anchor(st, agent, raw_reject, raw_ideal, overwrite=False):
    """Record where an agent's own scale starts and ends: the raw rating of the worst (`raw_reject`) and the best
    (`raw_ideal`) outcome. Every later rating of that agent is read against it. Raises ReplyError for an inverted scale,
    and for an agent that is already anchored unless `overwrite` (which invalidates comparisons made before it)."""
    # `not (a > b)` rather than `a <= b`, so a NaN is refused too.
    if not (raw_ideal > raw_reject):
        raise ReplyError(f"raw_ideal ({raw_ideal}) must exceed raw_reject ({raw_reject}); {agent}'s scale is inverted")
    if agent in st["anchors"] and not overwrite:
        raise ReplyError(f"{agent} is already anchored; pass --overwrite to re-anchor "
                         f"(this invalidates comparisons made before it)")
    st["anchors"][agent] = {"raw_reject": raw_reject, "raw_ideal": raw_ideal}


def add_candidate(st, agent, text, rationale=None, cid=None):
    """Append a proposal. A proposal names its own id (seed-<agent>, r<k>-<agent>), so it can be taken in whatever
    order it arrives and is never lost for want of the rest of the picture. Without an id (an older reply), it is the
    pending repair round, else the seed round, exactly as the initiator assigns it."""
    if cid:
        if any(c["id"] == cid for c in st["pool"]):
            return None
        m = re.match(r"r(\d+)-", cid)
        st["pool"].append({"id": cid, "proposer": agent, "round": int(m.group(1)) if m else 0, "candidate": text,
                           "rationale": rationale})
        pend = st.get("pending")
        if pend and cid == f"r{pend['round']}-{pend['agent']}":
            st["pending"] = None
        return cid
    pending = st.get("pending")
    rnd = pending["round"] if pending else 0
    cid = f"seed-{agent}" if rnd == 0 else f"r{rnd}-{agent}"
    if any(c["id"] == cid for c in st["pool"]):
        return None
    if pending and pending["agent"] != agent:
        return None
    if rnd == 0 and any(c["proposer"] == agent for c in st["pool"]):
        return None
    st["pool"].append({"id": cid, "proposer": agent, "round": rnd, "candidate": text, "rationale": rationale})
    st["pending"] = None
    return cid


def add_ratings(st, agent, ratings):
    """ratings: [(candidate_id, raw, reasoning)]. Unknown ids and repeats are ignored. Returns the ids recorded."""
    known = {c["id"] for c in st["pool"]}
    done = []
    for cid, raw, why in ratings:
        if cid in known and cid not in st["raw"].get(agent, {}):
            st["raw"].setdefault(agent, {})[cid] = raw
            st.setdefault("score_reasoning", {}).setdefault(agent, {})[cid] = why
            done.append(cid)
    return done


# --------------------------------------------------------------------------
# reading CONCORD documents
# --------------------------------------------------------------------------

def parse_reply_doc(root):
    """-> ('anchor', (reject, ideal)) | ('candidate', (text, rationale)) | ('score', [(id, raw, reasoning)]) | None"""
    def txt(el, tag):
        c = el.find(tag)
        return (c.text or "").strip() if c is not None and c.text else ""
    try:
        if root.tag == "concord-anchor-reply":
            return "anchor", (float(txt(root, "raw_reject")), float(txt(root, "raw_ideal")))
        if root.tag == "concord-candidate-reply":
            return "candidate", (txt(root, "candidate"), txt(root, "rationale"), root.get("id"))
        if root.tag == "concord-score-reply":
            return "score", [(sc.get("candidate_id"), float(txt(sc, "raw")), txt(sc, "reasoning"))
                             for sc in root.findall("score")]
    except ValueError:
        return None
    return None

# --------------------------------------------------------------------------
# delivery check: the replies inside a delivery, fingerprinted by the initiator, verified by the receiver
# --------------------------------------------------------------------------

_CHECKED_RE = re.compile(r"<(concord-(?:anchor|candidate|score)-reply)\b[^>]*>.*?</\1\s*>", re.DOTALL)


def reply_check(text):
    """A short fingerprint of the replies of other agents inside a delivery, blind to whitespace. The initiator puts it
    on the delivery (`check="..."`); the receiver compares it with the text its agent passed in, so a reply that was
    shortened or reworded in a copy is caught, not silently used. None when there is no reply in `text`."""
    docs = [m.group(0) for m in _CHECKED_RE.finditer(text)]
    if not docs:
        return None
    return hashlib.sha256(re.sub(r"\s+", "", "".join(docs)).encode("utf-8")).hexdigest()[:8]
