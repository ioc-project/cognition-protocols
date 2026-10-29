#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""CONCORD receiver: keeps this agent's picture of the run and says what to do next.

  concord_receiver.py --me ID handle [TEXT]    pass a message or a reply you got (stdin, an argument or @file; several at once,
                                               each preceded by a line `--- from: NAME`). Prints one of:
                                                 STATUS: NONE   nothing is due from you
                                                 STATUS: ASK    a prompt for your judgement; answer it with `answer`
                                                 STATUS: SEND   a reply to send, exactly as printed
  concord_receiver.py --me ID answer [JSON]    your judgement as JSON (the shape is in the ASK prompt); prints STATUS: SEND and
                                               the reply to send
  concord_receiver.py --me ID due              the current status, nothing recorded

The script writes every reply document itself. Stdlib only; no network, no dependencies.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

# >>> SHARED CORE: GENERATED from protocols/concord/shared/concord_core.py by protocols/concord/tools/sync_core.py. Do not edit it here: edit the source and run the tool.
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

_BARE_LT_RE = re.compile(r"<(?![a-zA-Z/?!])")
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")


def escape_bare_chars(xml_text):
    """Escape what an agent un-escaped while copying: a `<` that does not start a tag and an `&` that is not part of an
    entity. The scripts print `&lt;` and `&amp;`, but a model copying the text often turns them back into `<` and `&`.
    Applied to every document before it is parsed, so one such character never costs a whole reply; the meaning of
    the text is unchanged."""
    return _BARE_LT_RE.sub("&lt;", _BARE_AMP_RE.sub("&amp;", xml_text))


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

# <<< END SHARED CORE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(value: str) -> str:
    """Resolve @path references by reading the file."""
    if isinstance(value, str) and value.startswith("@"):
        with open(value[1:], encoding="utf-8") as fh:
            return fh.read()
    return value


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


_ME = [None]


def _emit(root: ET.Element) -> None:
    if _ME[0]:
        root.set("agent", _ME[0])          # the reply names its author, so whoever reads it knows who sent it
    if _STATE_ID[0]:
        root.set("state", _STATE_ID[0])    # and the run it belongs to
    if _ACK[0]:
        root.set("ack", str(_ACK[0]))      # and the last delivery it processes: a reply doubles as an acknowledgement
    try:
        ET.indent(root, space="  ")  # Python 3.9+
    except AttributeError:
        pass
    print(ET.tostring(root, encoding="unicode"))


# ---------------------------------------------------------------------------
# One forger per incoming message type
# ---------------------------------------------------------------------------

def forge_anchor(args: argparse.Namespace) -> None:
    """<concord-anchor> → <concord-anchor-reply>"""
    # `not (a > b)` rather than `a <= b`, so a NaN is refused too.
    if not (args.raw_ideal > args.raw_reject):
        sys.exit(
            f"error: raw_ideal ({args.raw_ideal:g}) must be strictly greater than raw_reject "
            f"({args.raw_reject:g}). These are satisfaction ratings of the two outcomes (higher = better "
            "for your role), not measurements of the current state or metrics quoted in the anchors: "
            "re-rate the two outcomes, then answer again."
        )
    root = ET.Element("concord-anchor-reply")
    _sub(root, "raw_reject", str(args.raw_reject))
    _sub(root, "raw_ideal", str(args.raw_ideal))
    if args.note:
        _sub(root, "note", _expand(args.note))
    _emit(root)


def forge_seed(args: argparse.Namespace) -> None:
    """<concord-propose-seed> → <concord-candidate-reply>"""
    root = ET.Element("concord-candidate-reply")
    if _ME[0]:
        root.set("id", f"seed-{_ME[0]}")
    _sub(root, "candidate", _expand(args.candidate))
    _sub(root, "rationale", _expand(args.rationale))
    _emit(root)


def forge_score(args: argparse.Namespace) -> None:
    """<concord-score> → <concord-score-reply>"""
    ids = args.candidate_id or []
    raws = args.raw or []
    reasonings = args.reasoning or []

    if not ids:
        sys.exit("error: at least one --candidate-id / --raw / --reasoning triplet is required")
    if not (len(ids) == len(raws) == len(reasonings)):
        sys.exit(
            "error: --candidate-id, --raw, and --reasoning must each appear "
            "the same number of times (one per candidate)"
        )

    root = ET.Element("concord-score-reply")
    for cid, raw, reasoning in zip(ids, raws, reasonings):
        score = ET.SubElement(root, "score", attrib={"candidate_id": cid})
        _sub(score, "raw", str(raw))
        _sub(score, "reasoning", _expand(reasoning))
    _emit(root)


def forge_repair(args: argparse.Namespace) -> None:
    """<concord-repair> → <concord-candidate-reply> (rationale required)"""
    root = ET.Element("concord-candidate-reply")
    cid = getattr(args, "cand_id", None) or _own_repair_id(args.me)
    if cid:
        root.set("id", cid)
    _sub(root, "candidate", _expand(args.candidate))
    _sub(root, "rationale", _expand(args.rationale))
    _emit(root)


def _own_repair_id(me):
    st = _load(me) if me else None
    m = st and st.get("mirror")
    return f"r{m['pending']['round']}-{me}" if m and m.get("pending") else None


# ---------------------------------------------------------------------------
# Ledger: the shared knowledge of the run, and what it makes due for this agent
# ---------------------------------------------------------------------------
#
# Every participant (the initiator included) that is fed the same messages, in order, runs the same decision engine
# (the shared core above). So every one of them knows the same anchors, proposals and ratings, and reaches the same
# conclusion: the ratings are complete, the run is over or a repair round starts, and which agent is the bottleneck.
# This script turns that into instructions for THIS agent. Nobody is asked: the agent that must move, moves.

STATE_DIR = ".concord-receiver"
_DOC_RE = re.compile(r"<(concord-[a-z-]+)\b[^>]*>.*?</\1\s*>", re.DOTALL)
_REQUEST_TAGS = ("concord-anchor", "concord-propose-seed", "concord-score", "concord-repair", "concord-commit")
_DELIVERY_TAG_RE = re.compile(r"<concord-delivery\b[^>]*>(?:\s*</concord-delivery>)?")


def _verify_delivery(st, text):
    """Refuse a delivery whose replies are not the ones the initiator sent. Nothing is recorded; the agent still has
    the original and gives it again. A second failure on the same delivery is reported to the initiator instead
    (a `concord-error`), who sends it again. Each delivery is checked against its own text only (up to the next
    delivery tag), so several deliveries passed in one call are checked one by one. Returns the payload of that
    report, or None when every delivery is intact."""
    tags = list(_DELIVERY_TAG_RE.finditer(text))
    fails = st.setdefault("check_fail", {})
    for k, tag in enumerate(tags):
        want = re.search(r'\bcheck="([0-9a-f]+)"', tag.group(0))
        seq_m = re.search(r'\bseq="(\d+)"', tag.group(0))
        seq = seq_m.group(1) if seq_m else str(st.get("delivery", 0))
        if not want:
            continue                                    # no check on it (an older initiator): nothing to compare
        got = reply_check(text[tag.end():tags[k + 1].start() if k + 1 < len(tags) else len(text)])
        if got == want.group(1):
            fails.pop(seq, None)
            continue
        fails[seq] = fails.get(seq, 0) + 1
        if fails[seq] < 2:
            _save(st)
            sys.exit(f"error: delivery {seq} does not match its check (expected {want.group(1)}, got {got}): part of a "
                     "reply was left out or changed when it was copied. Nothing was recorded. Give the script the "
                     "complete text again, exactly as received, without shortening or rewording it.")
        fails.pop(seq, None)
        _save(st)
        return (f'<concord-error agent="{st["me"]}" state="{st.get("id") or ""}" seq="{seq}">the delivery could not be '
                "copied intact (its replies do not match its check): send it again</concord-error>")
    return None


def _close_request(text: str) -> str:
    """Give a request message back the closing tags a copy dropped.

    A request ends with its instructions, a prose sentence, and a model copying the message often stops after that
    sentence and leaves out the last closing tags. Only that is repaired: the last CONCORD document of the text is a
    request, it opens `<instructions>`, and what is missing is the end of the instructions and of the document itself.
    A message cut anywhere else (a reply, or a request without its instructions) is left as it is: it stays unreadable.
    """
    starts = [m for m in re.finditer(r"<(concord-[a-z-]+)\b", text)]
    if not starts:
        return text
    last = starts[-1]
    tag = last.group(1)
    if tag not in _REQUEST_TAGS or re.search(rf"</{tag}\s*>", text[last.end():]):
        return text
    tail = text[last.start():]
    if "<instructions>" not in tail:
        return text
    if "</instructions>" not in tail:
        text += "</instructions>"
    return text.rstrip() + f"\n</{tag}>"


def _project_root() -> str:
    """Skills live in <project>/.claude/skills/<skill>/scripts/: the project is the folder that holds `.claude`."""
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        if os.path.basename(d) == ".claude":
            return os.path.dirname(d)
        d = os.path.dirname(d)
    return os.getcwd()


_SID = [None]          # the run's state id, from --state-id; otherwise taken from the message, else the newest run
_STATE_ID = [None]     # echoed on every reply this process builds
_ACK = [None]          # the last delivery number seen (relay mode); echoed on every reply


def _state_dir() -> str:
    root = os.environ.get("CONCORD_STATE_DIR") or os.path.join(_project_root(), STATE_DIR)
    os.makedirs(root, exist_ok=True)
    return root


def _safe(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", x)


def _state_path(me: str, sid: str) -> str:
    # one file per run and per agent: the agents of a run share a folder, and runs must never mix
    return os.path.join(_state_dir(), f"state-{_safe(sid)}.{_safe(me)}.json")


def _latest_path(me: str):
    import glob
    files = glob.glob(os.path.join(_state_dir(), f"state-*.{_safe(me)}.json"))
    return max(files, key=os.path.getmtime) if files else None


def _sid_in(text: str):
    m = re.search(r"<state_id>\s*([A-Za-z0-9_-]+)\s*</state_id>", text) or re.search(r'\bstate="([A-Za-z0-9_-]+)"', text)
    return m.group(1) if m else None


def _load(me: str, sid: str = None) -> dict:
    sid = sid or _SID[0]
    p = _state_path(me, sid) if sid else _latest_path(me)
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {"me": me, "id": sid, "mirror": None, "asked": {}, "outbox": {}, "resend": None,
            "sent": {"anchor": False, "seed": False, "repair": [], "rated": []}, "lead": None}


def _save(st: dict) -> None:
    with open(_state_path(st["me"], st.get("id") or "default"), "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)


def _agents_of(root, tag):
    box = root.find(tag)
    return [a.text.strip() for a in box.findall("agent")] if box is not None else []


def _ingest_doc(st, sender, root):
    """Fold one CONCORD document (a lead message or an agent reply) into the shared picture."""
    tag = root.tag
    if tag == "concord-delivery":
        # relay mode: the initiator numbers each delivery to this agent. Remember the number, and that an
        # acknowledgement is owed if nothing else is said about it.
        try:
            seq = int(root.get("seq") or 0)
            seen = set(st.get("seen_seq", [])) | {seq}
            covered = st.get("covered", 0)
            if root.get("full"):
                covered = max(covered, seq)            # a full delivery holds everything recorded so far: earlier
                                                       # deliveries, lost or not, add nothing to it
            w = covered
            while w + 1 in seen:                       # the acknowledged position is contiguous: 1..w all received
                w += 1
            if st["me"] in [x.strip() for x in (root.get("awaiting") or "").split(",")]:
                st["awaited"] = True                   # the initiator still waits for a reply of mine
            st["seen_seq"] = sorted(s for s in seen if s > w)[:50]
            st["covered"] = w
            st["delivery"] = w
            st["ack_due"] = True
        except ValueError:
            pass
        return
    if tag in ("concord-anchor", "concord-propose-seed", "concord-score", "concord-repair", "concord-commit"):
        st["lead"] = st["lead"] or sender
        if st["mirror"] is None and root.find("participants") is not None:
            p = root.find("params")
            def num(t, d):
                el = p.find(t) if p is not None else None
                return float(el.text) if el is not None and el.text else d
            st["mirror"] = new_engine_state(
                _agents_of(root, "participants"),
                {"satisfaction_threshold": num("satisfaction_threshold", 0.6), "eps": num("eps", 0.05),
                 "B": int(num("budget", 8)), "W": "leximin"})
        mine = st["me"] in _agents_of(root, "addressed_to")
        brief = st.setdefault("brief", {})
        for t in ("x_reject", "x_ideal", "candidate_schema", "context"):
            el = root.find(t)
            if el is not None and el.text and t not in brief:
                brief[t] = el.text.strip()
        ob = st.setdefault("outbox", {})
        if mine:
            asked_keys = []
            if tag == "concord-anchor":
                asked_keys = ["anchor"]
            elif tag == "concord-propose-seed":
                asked_keys = ["seed"]
            elif tag == "concord-repair" and root.find("new_candidate_id") is not None:
                asked_keys = ["repair:" + (root.find("new_candidate_id").text or "").strip()]
            elif tag == "concord-score":
                asked_keys = ["score:" + (c.get("id") or "") for c in root.findall("candidates/candidate")]
            for k in asked_keys:
                if k in ob:
                    st["resend"] = ob[k]       # asked again for something already answered: it did not arrive
        # A request that carries candidates (a Score message, the incumbent of a Repair) is self-sufficient: whatever
        # proposals this agent missed are learned from it, so it can answer even with an incomplete picture.
        if st["mirror"] is not None:
            cands = [(c.get("id"), c.find("text"), c.find("rationale")) for c in root.findall("candidates/candidate")]
            inc = root.find("incumbent")
            if inc is not None and inc.get("id"):
                cands.append((inc.get("id"), inc.find("text"), inc.find("rationale")))
            for cid, t_el, r_el in cands:
                if cid and "-" in cid and t_el is not None:
                    add_candidate(st["mirror"], cid.split("-", 1)[1], (t_el.text or "").strip(),
                                  (r_el.text or "").strip() if r_el is not None and r_el.text else None, cid)
        if tag == "concord-anchor":
            st["asked"]["anchor"] = st["asked"].get("anchor") or mine
        elif tag == "concord-propose-seed":
            st["asked"]["seed"] = st["asked"].get("seed") or mine
        elif tag == "concord-repair":
            nid = root.find("new_candidate_id")
            if mine and nid is not None and nid.text:
                st["asked"]["repair"] = nid.text.strip()
        elif tag == "concord-commit":
            st["asked"]["commit"] = True
            # The commit is the initiator's announcement of the outcome: whatever this agent's own picture holds,
            # the run is over, with this outcome and this winner.
            o, w = root.find("outcome"), root.find("winning_candidate_id")
            if st["mirror"] is not None and o is not None and o.text:
                st["mirror"]["outcome"] = o.text.strip()
                if w is not None and w.text:
                    st["mirror"]["x_star"] = w.text.strip()
        return
    m = st["mirror"]
    if tag == "concord-missing" and m is not None:
        # the initiator tells us an agent never rated a candidate: our engine must see the same
        who, cid = root.get("agent"), root.get("candidate")
        if who in m["agents"] and cid and cid not in m["raw"].get(who, {}):
            m["raw"].setdefault(who, {})[cid] = "MISSING"
            advance(m)
        return
    parsed = parse_reply_doc(root)
    sender = root.get("agent") or sender
    if m is None or parsed is None or not sender:
        return
    kind, val = parsed
    if kind == "anchor":
        if val[1] > val[0]:
            m["anchors"].setdefault(sender, {"raw_reject": val[0], "raw_ideal": val[1]})
    elif kind == "candidate":
        add_candidate(m, sender, val[0], val[1], val[2])
    elif kind == "score":
        add_ratings(m, sender, val)
    advance(m)


def _ingest_text(st, sender, text):
    """Fold every CONCORD document in `text` into the picture. Returns (documents used, documents ignored)."""
    n = bad = 0
    for d in _DOC_RE.finditer(text):
        try:
            root = ET.fromstring(escape_bare_chars(d.group(0)))
        except ET.ParseError:
            bad += 1                                       # not well-formed: ignored, and said so
            continue
        sid = (root.find("state_id").text.strip() if root.find("state_id") is not None and root.find("state_id").text
               else root.get("state"))
        if sid and st.get("id") and sid != st["id"]:
            continue                                   # a message of another run
        if sid and not st.get("id"):
            st["id"] = sid
        _ingest_doc(st, sender, root)
        n += 1
    return n, bad


def _due_items(st) -> list:
    """What this agent must do now, as data. Derived from the shared picture, never from a request."""
    me, m = st["me"], st["mirror"]
    if m is None:
        return []
    if m["outcome"]:
        return []
    items = []
    have_anchor = me in m["anchors"] or st["sent"]["anchor"]
    have_seed = any(c["id"] == f"seed-{me}" for c in m["pool"]) or st["sent"]["seed"]
    if st["asked"].get("anchor") and not have_anchor:
        items.append({"kind": "anchor"})
        return items                                    # anchor before anything else
    if st["asked"].get("seed") and not have_seed:
        items.append({"kind": "seed"})
        return items                                    # propose before reading anyone's proposals to rate
    rated = set(m["raw"].get(me, {})) | set(st["sent"]["rated"])
    pool = {c["id"]: c for c in m["pool"]}
    pend = m["pending"]
    repair_id = None
    if pend and pend["agent"] == me:
        repair_id = f"r{pend['round']}-{me}"
        if repair_id not in pool and repair_id not in st["sent"]["repair"]:
            items.append({"kind": "repair", "id": repair_id,
                          "why": f"every rating is in, the incumbent {m['x_star']} is below the bar for you "
                                 f"(you are the least satisfied agent)"})
    seeds_in = all(f"seed-{a}" in pool for a in m["agents"])
    todo = []
    for cid, c in pool.items():
        if cid in rated:
            continue
        if cid.startswith("seed-"):
            if seeds_in:
                todo.append(cid)
        else:
            todo.append(cid)                            # a repair candidate that has been posted: rate it
    if todo and have_anchor:
        items.append({"kind": "score", "ids": todo})
    return items


def _waiting_for(st) -> str:
    m = st["mirror"]
    if m is None:
        return "the lead's first message"
    if m["outcome"]:
        return "nothing"
    parts = []
    miss_anchor = [a for a in m["agents"] if a not in m["anchors"]]
    if miss_anchor:
        return "anchors from " + ", ".join(miss_anchor)
    miss_seed = [a for a in m["agents"] if not any(c["id"] == f"seed-{a}" for c in m["pool"])]
    if miss_seed:
        return "seeds from " + ", ".join(miss_seed)
    if m["pending"]:
        return f"the repair from {m['pending']['agent']} (round {m['pending']['round']})"
    for a in m["agents"]:
        owed = [c["id"] for c in m["pool"] if c["id"] not in m["raw"].get(a, {})]
        if owed and a != st["me"]:
            parts.append(f"{a} to rate {', '.join(owed)}")
    return "; ".join(parts) or "the lead"


def _fmt(x):
    return f"{x:g}"


def _who(st) -> str:
    """`--me <you>` plus the run it is for: several runs may share a folder (parallel runs), and `answer` has no message
    to read the run from, so the command the task shows names it."""
    return f"--me {st['me']}" + (f" --state-id {st['id']}" if st.get("id") else "")


def _flags_for(items) -> str:
    """The command-line flags that answer these due tasks. The script knows what is due, so the answer needs no key naming
    the task: just the values."""
    flags = []
    for it in items:
        k = it["kind"]
        if k == "anchor":
            flags.append("--reject N --ideal N")
        elif k == "seed":
            flags.append('--candidate "TEXT" --rationale "TEXT with the evidence for it"')
        elif k == "repair":
            flags.append('--candidate "TEXT" --rationale "TEXT"')
            flags.append(f'--score {it["id"]} N "reasons"')
        elif k == "score":
            flags.extend(f'--score {c} N "reasons"' for c in it["ids"])
    return " ".join(flags)


# Guidance only, never checked: a reply that runs over is accepted. The only length error is a message cut mid-XML.
_BUDGET = "Be detailed and informative, but stay within 1000 characters for each part of your answer."
# Said wherever the script ends what the agent has to do: whatever the agent writes after that may reach the others.
_NOTHING_MORE = ("When you are done, write nothing more: not even a confirmation of what you sent or decided. Your "
                 "environment may pass anything you write to the other participants.")


def _ask_text(st, items) -> str:
    """The prompt for the LLM: everything it needs to decide, and the exact shape of its answer."""
    me, m, brief = st["me"], st["mirror"], st.get("brief", {})
    pool = {c["id"]: c for c in m["pool"]} if m else {}
    out = [f"You are the {me} agent. CONCORD needs your judgement. Decide from YOUR role's criteria only; other "
           "agents' reasoning is evidence you may weigh (a specific, checkable fact may change your own rating or "
           "repair, and you should be able to say what changed), their numbers are not a reason. Be honest: never "
           "inflate to seem agreeable or deflate for leverage, and never soften your criteria to make something look "
           "acceptable. If you cannot be satisfied, say so."]
    shape = {}
    for it in items:
        k = it["kind"]
        if k == "anchor":
            out += ["", "TASK: anchor",
                    "Rate two outcomes on your own raw 0-100 scale, as satisfaction of your role if each happened "
                    "(not how true it is today: answering how true it is now inverts the scale and is refused). "
                    "x_reject should get a LOW number, x_ideal a HIGH one strictly above it. These are satisfaction "
                    "ratings, not metrics: no error rate or latency in these fields. The anchors are rated once and "
                    "stay fixed for the whole run. Rate your raw scale, never a normalised 0-1 score.",
                    f"x_reject: {brief.get('x_reject', '?')}", f"x_ideal: {brief.get('x_ideal', '?')}"]
            shape["anchor"] = {"raw_reject": "N", "raw_ideal": "N"}
        elif k == "seed":
            out += ["", "TASK: seed",
                    "Propose the single candidate that best satisfies your own role, in this format. Do not hedge, "
                    "do not offer alternatives, do not pre-compromise for other roles: this round represents your "
                    "role's viewpoint undiluted. The rationale carries the concrete facts and evidence from your "
                    "criteria, why the other options in the candidate format are less suitable from your role's "
                    "perspective (any domain knowledge you hold that argues against them, even if none has been "
                    "proposed yet), the trade-offs you considered and why it will hold up under scrutiny: the other "
                    "agents use it to detect incorrect logic. Your rationale is your only voice here: anything you "
                    "know and do not write in it is invisible to every other participant. " + _BUDGET,
                    f"candidate format: {brief.get('candidate_schema', '?')}", f"context: {brief.get('context', '?')}"]
            shape["seed"] = {"candidate": "TEXT", "rationale": "TEXT with the evidence for it"}
        elif k == "repair":
            inc = pool.get(m["x_star"], {})
            out += ["", f"TASK: repair {it['id']}", f"REPAIR ROUND, YOU ALONE: {it['why']}.",
                    "Read the incumbent's ratings and their reasoning, not just the numbers. Propose ONE new candidate "
                    "that: (1) clears your own bar, something you would rate close to your x_ideal and not marginally "
                    "above what you just rejected; (2) does not knowingly push any other agent below the threshold: "
                    "infer from their reasoning what they are protecting and keep it; (3) is really new, not a "
                    "cosmetic variant of a candidate already scored; (4) follows the candidate format; (5) answers the "
                    "strongest reasoning against your position, in your rationale: agree with part of it, refute it, "
                    "or say why it does not apply; (6) states any domain knowledge of your role that has not yet appeared "
                    "and bears on why this candidate is the right one: it stays invisible to everyone if you do not "
                    "write it. " + _BUDGET + " Rate your own new candidate, honestly, in the same answer. If you "
                    "believe no such candidate exists, still give your best attempt and state the conflict plainly in "
                    "the rationale (which agent, over what property). Do not lower the threshold, do not weaken your "
                    "criteria, and do not propose something you are not satisfied by. The ratings of the incumbent "
                    "below are normalised 0-1 for you to read: never echo one back as your own rating.",
                    f"candidate format: {brief.get('candidate_schema', '?')}", f"context: {brief.get('context', '?')}",
                    f"incumbent {m['x_star']}: {inc.get('candidate', '')}",
                    f"its rationale: {inc.get('rationale') or ''}", "ratings of the incumbent:"]
            for ag in m["agents"]:
                raw = m["raw"].get(ag, {}).get(m["x_star"])
                sc = score(m, ag, m["x_star"]) if ag in m["anchors"] and raw is not None else None
                why = m.get("score_reasoning", {}).get(ag, {}).get(m["x_star"], "")
                out.append(f"  - {ag}: {'%.2f' % sc if sc is not None else 'n/a'} ({why})")
            shape["repair"] = {"candidate": "TEXT", "rationale": "TEXT"}
            shape.setdefault("scores", []).append({"id": it["id"], "raw": "N", "reasoning": "TEXT"})
        elif k == "score":
            a = m["anchors"].get(me)
            out += ["", "TASK: score " + ",".join(it["ids"]),
                    "Rate each candidate listed, including one you proposed yourself, on your own raw 0-100 scale, "
                    "the same scale as your anchor, for the whole run: the same quality gets the same number in round "
                    "7 as at anchoring. Move a number only for a real change (a rationale that answers a concern you "
                    "raised), and be able to say in one sentence what changed your mind; refusing to budge is inertia, "
                    "not honesty. Never refuse to score: a candidate you consider terrible gets a low number with "
                    "honest reasons"
                    + (f" (raw_reject={_fmt(a['raw_reject'])}, raw_ideal={_fmt(a['raw_ideal'])})" if a else "")
                    + ". Give the concrete reasons for each number (the properties that drove it, the evidence from "
                    "your criteria, both for and against it: \"too expensive\" is not a reason, \"adds about $400 a "
                    "month, over the budget\" is), and say if a candidate rests on a false claim or a flawed step. "
                    "Write the facts that matter, even if they seem obvious to you: the repair agent and the "
                    "others see only your reasoning, and what you leave out is invisible to the rest of the run. "
                    + _BUDGET]
            for cid in it["ids"]:
                c = pool.get(cid, {})
                out += [f"candidate {cid}: {c.get('candidate', '')}", f"  its rationale: {c.get('rationale') or ''}"]
            shape.setdefault("scores", []).extend({"id": c, "raw": "N", "reasoning": "TEXT"} for c in it["ids"])
    out += ["", f"Answer by running: python3 <this script> {_who(st)} answer {_flags_for(items)}",
            "(N is a number; each text is one argument, and may have several lines.)",
            "It prints STATUS: SEND followed by your reply: send exactly that, nothing else. Your judgement goes in "
            "these values only: never write the reply yourself, and never add text before or after what the script prints. "
            + _NOTHING_MORE]
    return "\n".join(out)


def _status(st):
    """-> (status, payload): SEND (a reply to send again), ASK (a prompt for the LLM) or NONE (nothing to send)."""
    # Asked again for something already answered: that wins over everything, even over "the run is over" in this
    # agent's own picture. The agent may be done while the initiator has not heard from it, and it waits for it.
    if st.get("resend"):
        docs, st["resend"] = st["resend"], None
        return "SEND", docs
    if st.pop("awaited", False) and st.get("outbox") and not _due_items(st):
        # the initiator says it still waits for me, and nothing is due: what I answered last did not arrive, or was
        # not accepted. Send it again, exactly as it was.
        return "SEND", list(st["outbox"].values())[-1]
    m = st["mirror"]
    if m is not None and m["outcome"]:
        return "NONE", (f"the run is over: outcome {m['outcome']}, winning candidate {m['x_star']}. "
                        "Do not re-open the decision.\nNEXT: Nothing is due from you: send nothing (no "
                        "acknowledgement either) and do not re-propose. " + _NOTHING_MORE + " If the outcome is SUCCESS, proceed with the "
                        "winning candidate. If it is BEST_EFFORT, say so when you report to whoever triggered the "
                        "run, naming the agents below their floor.")
    items = _due_items(st)
    if not items:
        return "NONE", (f"nothing is due from you; waiting for {_waiting_for(st)}. Send no message.\n"
                        "NEXT: Send nothing: never write an acknowledgement or any message yourself (when one is due, "
                        "`handle` prints it as a SEND). Pass the next CONCORD message or reply you get to `handle`. "
                        + _NOTHING_MORE)
    return "ASK", _ask_text(st, items)


_SEND_REMINDER = ("NEXT: Send the text printed under STATUS: SEND exactly as it is, and nothing else: no words before or "
                  "after it, no summary of your own. Then pass the next CONCORD message you get to `handle`. "
                  + _NOTHING_MORE)


def _print_status(st) -> None:
    status, payload = _status(st)
    print(f"STATUS: {status}")
    print(payload)
    if status == "SEND":
        print(_SEND_REMINDER, file=sys.stderr)


def cmd_observe(args) -> None:
    """Fold messages into the shared picture and print what is due.

    Input (stdin, an argument, or @file): one message, or several, each preceded by a line `--- from: NAME`
    (the sender; needed for another agent's reply, since a reply does not name its author).
    """
    text = args.text
    if text is None or text == "-":
        text = _read_stdin_if_any(wait=1.0 if text is None else 30.0)
    st = _load(args.me, _SID[0] or _sid_in(text))
    if text.startswith("@") or (text and "\n" not in text and "<" not in text and os.path.isfile(text)):
        with open(text.lstrip("@"), encoding="utf-8") as fh:
            text = fh.read()
    closed = _close_request(text)
    if closed != text:
        print("note: the closing tags of the request were missing and were added", file=sys.stderr)
        text = closed
    broken = _verify_delivery(st, text)
    if broken:
        print("STATUS: SEND")
        print(broken)
        print(_SEND_REMINDER, file=sys.stderr)
        return
    blocks = re.split(r"^--- from: *(\S+) *$", text, flags=re.M)
    items = [(args.sender, blocks[0])] + [(blocks[i], blocks[i + 1]) for i in range(1, len(blocks) - 1, 2)]
    if not _DOC_RE.search(text):
        # Nothing to record: not an error (a runner must never be wedged by one odd message). Say so, and what is due.
        st = _load(args.me, _SID[0])
        status, payload = _status(st)
        print(f"STATUS: {status}")
        print(payload)
        if status == "SEND":
            print(_SEND_REMINDER, file=sys.stderr)
        if status != "SEND":
            print("[note: no CONCORD message was found in what you passed: nothing was recorded]")
        return
    mine = False
    ignored = 0
    before = (list(st.get("seen_seq", [])), st.get("covered", 0), st.get("delivery", 0))
    for sender, body in items:
        mine = mine or sender == st["me"] or f'agent="{st["me"]}"' in body
        _, bad = _ingest_text(st, sender, body)
        ignored += bad
    # a CONCORD tag that opens but never closes (a message cut short) is unreadable too
    ignored += max(0, len(re.findall(r"<concord-[a-z-]+\b", text)) - len(list(_DOC_RE.finditer(text))))
    unreadable_seq = None
    if ignored:
        # Part of what was sent could not be read, so the delivery did not arrive: it must not be acknowledged, and the
        # sender has to hear it.
        m_seq = re.search(r'<concord-delivery\b[^>]*\bseq="(\d+)"', text)
        unreadable_seq = m_seq.group(1) if m_seq else str(st.get("delivery", 0))
        st["seen_seq"], st["covered"], st["delivery"] = before
        st.pop("ack_due", None)
    if mine:
        # What I sent is now part of the shared picture, so the picture is the truth about it: a document I forged
        # that did not reach the others (left out, or never delivered) is due again.
        st["sent"] = {"anchor": False, "seed": False, "repair": [], "rated": []}
    status, payload = _status(st)
    ack_due = st.pop("ack_due", False)
    if unreadable_seq is not None and status == "NONE":
        status = "SEND"
        payload = (f'<concord-error agent="{st["me"]}" state="{st.get("id") or ""}" seq="{unreadable_seq}">'
                   f"{ignored} document(s) in the message were not well-formed XML and were not read: send the message "
                   "again exactly as its script printed it</concord-error>")
    elif ack_due and status == "NONE":
        # In relay mode a delivery that needs no answer is acknowledged, so that silence never means "lost". When a
        # reply is due (ASK) that reply carries the delivery number instead, and when a stored reply is resent it goes out.
        status = "SEND"
        payload = (f'<concord-ack agent="{st["me"]}" state="{st.get("id") or ""}" seq="{st.get("delivery", 0)}">'
                   "</concord-ack>")
    elif ack_due and status == "SEND" and payload.lstrip().startswith("<concord-") and "<concord-error" not in payload:
        # a reply sent again goes together with the acknowledgement of the delivery that asked for it
        payload += (f'\n\n<concord-ack agent="{st["me"]}" state="{st.get("id") or ""}" seq="{st.get("delivery", 0)}">'
                    "</concord-ack>")
    _save(st)
    print(f"STATUS: {status}")
    print(payload)
    if status == "SEND":
        print(_SEND_REMINDER, file=sys.stderr)
    if ignored and status != "SEND":
        print(f"[note: {ignored} document(s) that are not well-formed XML were ignored]")


_MIN_REASON = 20            # characters: "too expensive" is not a reason


def _num(x):
    """x as a finite float, or None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _same_text(a, b):
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def _check_answer(st, items, data):
    """The simple rules an answer must meet, checked before anything is built, so that one slip is named and the model
    corrects it itself. Returns a list of problems (empty if the answer is fine); a structurally broken answer is left to
    the caller's own handling."""
    if not isinstance(data, dict):
        return []
    # the answer must have the keys the task asks for (a model tends to name them after the message it was sent, such as
    # "propose" for a propose-seed message): say which keys it has, which it needs, and the exact shape
    shape, need = {}, []
    me_ = st["me"]
    for it in items:
        k = it["kind"]
        if k == "anchor":
            shape["anchor"] = {"raw_reject": "N", "raw_ideal": "N"}
        elif k == "seed":
            shape["seed"] = {"candidate": "TEXT", "rationale": "TEXT with the evidence for it"}
        elif k == "repair":
            shape["repair"] = {"candidate": "TEXT", "rationale": "TEXT"}
            shape.setdefault("scores", []).append({"id": it["id"], "raw": "N", "reasoning": "TEXT"})
        elif k == "score":
            shape.setdefault("scores", []).extend({"id": c, "raw": "N", "reasoning": "TEXT"} for c in it["ids"])
    need = list(shape)
    missing = [k for k in need if k not in data]
    if missing:
        return [f"your answer is missing what this task needs ({', '.join(need)}). Answer with exactly: python3 <this "
                f"script> {_who(st)} answer {_flags_for(items)}"]
    me, m = st["me"], st["mirror"]
    anchor = (m or {}).get("anchors", {}).get(me)
    pool = (m or {}).get("pool", [])
    out = []

    def text_ok(label, v, minimum=1):
        if not isinstance(v, str) or len(v.strip()) < minimum:
            out.append(f"{label} must be text" + (f" of at least {minimum} characters" if minimum > 1 else "")
                       + (" (the concrete reasons: the properties that drove it, the evidence from your criteria)"
                          if minimum > 1 else ""))
            return False
        return True

    def rating_ok(cid, raw, reasoning):
        v = _num(raw)
        if v is None:
            out.append(f"the rating of {cid} must be a number")
            return
        if not (0 <= v <= 100):
            out.append(f"the rating of {cid} ({v:g}) is outside your 0-100 scale")
        elif anchor and not (anchor["raw_reject"] - TOL <= v <= anchor["raw_ideal"] + TOL):
            rr, ri = anchor["raw_reject"], anchor["raw_ideal"]
            hint = (" It looks like a 0-1 score: use your raw scale, never a normalised one." if 0 <= v <= 1 < ri else "")
            out.append(f"your rating of {cid} is {v:g}, outside your anchors (raw_reject={rr:g}, raw_ideal={ri:g}). Rate "
                       "between them: a candidate is no worse than the outcome you called unacceptable and no better than "
                       "the one you called fully satisfying (use the nearest anchor value if you think it is at the "
                       f"edge, and say so in the reasoning).{hint}")
        text_ok(f"the reasoning for {cid}", reasoning, _MIN_REASON)

    for it in items:
        k = it["kind"]
        try:
            if k == "anchor":
                a_ = data["anchor"]
                for key in ("raw_reject", "raw_ideal"):
                    v = _num(a_[key])
                    if v is None:
                        out.append(f"anchor {key} must be a number")
                    elif not (0 <= v <= 100):
                        out.append(f"anchor {key} ({v:g}) is outside the 0-100 scale: these are satisfaction ratings of "
                                   "the two outcomes, not metrics or percentages of today's state")
            elif k in ("seed", "repair"):
                d = data[k]
                if text_ok(f"{k} candidate", d["candidate"]) and k == "repair" and any(
                        _same_text(d["candidate"], c["candidate"]) for c in pool):
                    out.append("the repair candidate is the same as one already in the run: propose something new that "
                               "answers the objection")
                text_ok(f"{k} rationale", d["rationale"], _MIN_REASON)
                if k == "repair":
                    own = [x for x in data.get("scores", []) if x.get("id") == it["id"]]
                    if own:
                        rating_ok(it["id"], own[0].get("raw"), own[0].get("reasoning"))
            elif k == "score":
                given = {x.get("id"): x for x in data.get("scores", []) if isinstance(x, dict)}
                for cid in it["ids"]:
                    if cid in given:
                        rating_ok(cid, given[cid].get("raw"), given[cid].get("reasoning"))
        except (KeyError, TypeError, AttributeError):
            pass                                    # a missing or malformed field: the caller names it
    return out


def cmd_answer(args) -> None:
    """The LLM's judgement, as JSON on stdin. Prints STATUS: SEND and the reply to send, or STATUS: ASK again."""
    import contextlib
    import io
    st = _load(args.me)
    items = _due_items(st)
    given_flags = any(v is not None for v in (args.reject, args.ideal, args.candidate, args.rationale, args.score))
    if given_flags:
        data = {}
        if args.reject is not None or args.ideal is not None:
            data["anchor"] = {"raw_reject": args.reject, "raw_ideal": args.ideal}
        if args.candidate is not None or args.rationale is not None:
            kinds = [it["kind"] for it in items]
            data["repair" if "repair" in kinds else "seed"] = {"candidate": args.candidate, "rationale": args.rationale}
        if args.score:
            data["scores"] = [{"id": i, "raw": n, "reasoning": r} for i, n, r in args.score]
    else:
        text = args.text if args.text is not None else _read_stdin_if_any(2.0)
        if not text.strip():
            if items:
                print("STATUS: ASK")
                print("You gave no answer. Run: python3 <this script> " + _who(st) + " answer " + _flags_for(items)
                      + "\n\n" + _ask_text(st, items))
                return
            data = {}
        else:
            try:
                data = json.loads(text)
            except ValueError as e:
                print("STATUS: ASK")
                print(f"Your answer could not be read ({e}). Give it with the flags the task shows: python3 <this script> "
                      f"{_who(st)} answer {_flags_for(items)}\n\n" + (_ask_text(st, items) if items else ""))
                return
    if not items:
        print("STATUS: NONE")
        print("nothing is due from you any more; send nothing.\nNEXT: Send nothing and pass the next CONCORD message "
              "you get to `handle`.")
        return
    docs, problems = [], []
    problems += _check_answer(st, items, data)
    if problems:
        print("STATUS: ASK")
        print("Your answer was not usable: " + "; ".join(problems) + ". Nothing was recorded or sent. Fix these and "
              "answer again as the task shows below (what is due may differ from what you answered).\n")
        print(_ask_text(st, items))
        return

    def run(fn, **kw):
        ns = argparse.Namespace(me=args.me, **kw)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(ns)
        docs.append(buf.getvalue().strip())

    try:
        for it in items:
            k = it["kind"]
            if k == "anchor":
                a_ = data["anchor"]
                _gate = argparse.Namespace(me=args.me, msg_type="anchor")
                _gate_forge(_gate)
                run(forge_anchor, raw_reject=float(a_["raw_reject"]), raw_ideal=float(a_["raw_ideal"]), note="")
                _record_forge(_gate)
            elif k == "seed":
                _gate = argparse.Namespace(me=args.me, msg_type="seed")
                _gate_forge(_gate)
                run(forge_seed, candidate=str(data["seed"]["candidate"]), rationale=str(data["seed"]["rationale"]))
                _record_forge(_gate)
            elif k == "repair":
                _gate = argparse.Namespace(me=args.me, msg_type="repair")
                _gate_forge(_gate)
                run(forge_repair, candidate=str(data["repair"]["candidate"]), rationale=str(data["repair"]["rationale"]),
                    cand_id=it["id"])
                _record_forge(_gate)
                own = [x for x in data.get("scores", []) if x["id"] == it["id"]]
                if not own:
                    problems.append(f"scores must include a rating of your own new candidate {it['id']}")
                else:
                    x = own[0]
                    _gate = argparse.Namespace(me=args.me, msg_type="score", candidate_id=[x["id"]])
                    run(forge_score, candidate_id=[x["id"]], raw=[float(x["raw"])], reasoning=[str(x["reasoning"])])
                    _record_forge(_gate)
            elif k == "score":
                given = {x["id"]: x for x in data.get("scores", [])}
                missing = [c for c in it["ids"] if c not in given]
                if missing:
                    problems.append("scores is missing: " + ", ".join(missing))
                    continue
                ids = list(it["ids"])
                _gate = argparse.Namespace(me=args.me, msg_type="score", candidate_id=ids)
                _gate_forge(_gate)
                run(forge_score, candidate_id=ids, raw=[float(given[c]["raw"]) for c in ids],
                    reasoning=[str(given[c]["reasoning"]) for c in ids])
                _record_forge(_gate)
    except (KeyError, TypeError, ValueError) as e:
        problems.append(f"missing or malformed field: {e}")
    except SystemExit as e:
        problems.append(str(e.code))
    if problems:
        print("STATUS: ASK")
        print("Your answer was not usable: " + "; ".join(problems) + ". Answer again as the task shows below "
              "(what is due may differ from what you answered).\n")
        print(_ask_text(_load(args.me), _due_items(_load(args.me)) or items))
        return
    # My own reply is part of the picture of the run from now on, whether or not anyone passes it back to me.
    st = _load(args.me)
    reply = "\n\n".join(d for d in docs if d)
    _ingest_text(st, args.me, reply)
    for it in items:
        if it["kind"] in ("anchor", "seed"):
            st["outbox"][it["kind"]] = reply
        elif it["kind"] == "repair":
            st["outbox"]["repair:" + it["id"]] = reply
        elif it["kind"] == "score":
            for c in it["ids"]:
                st["outbox"]["score:" + c] = reply
    _save(st)
    print("STATUS: SEND")
    print("\n\n".join(d for d in docs if d))
    print(_SEND_REMINDER, file=sys.stderr)


def cmd_due(args) -> None:
    _print_status(_load(args.me))


def _read_stdin_if_any(wait: float) -> str:
    """Read piped input, but never hang on a terminal or an open pipe that nobody writes to."""
    import select
    if sys.stdin.isatty():
        return ""
    ready, _, _ = select.select([sys.stdin], [], [], wait)
    return sys.stdin.read() if ready else ""


def _gate_forge(args) -> None:
    """A reply is forged only if it is not a duplicate of what you already sent, and a repair only if the shared picture
    shows it is yours to send (this is what stops two agents repairing at once)."""
    t = args.msg_type
    if t == "commit":
        return
    if not args.me:
        sys.exit("error: pass --me <your agent id>.")
    st = _load(args.me)
    m = st["mirror"]
    why = None
    if t == "anchor" and (st["sent"]["anchor"] or (m and args.me in m["anchors"])):
        why = "you already sent your anchor"
    elif t == "seed" and (st["sent"]["seed"] or (m and any(c["id"] == f"seed-{args.me}" for c in m["pool"]))):
        why = "you already sent your seed"
    elif t == "repair":
        items = {i["kind"]: i for i in _due_items(st)}
        if "repair" not in items:
            why = "no repair is due from you"
    elif t == "score":
        already = set(st["sent"]["rated"]) | (set(m["raw"].get(args.me, {})) if m else set())
        bad = [c for c in (args.candidate_id or []) if c in already]
        if bad:
            why = f"already rated: {', '.join(bad)}"
    if why:
        sys.exit(f"error: {why}. The shared picture shows: {_status(st)[0]} {_status(st)[1].splitlines()[-1] if _status(st)[0] == 'NONE' else 'a reply is due; run handle'}." +
                 f"\nRun `observe` on the messages you have not recorded yet to refresh it first.")


def _record_forge(args) -> None:
    """Remember what this agent itself sent, so it is never sent twice before the shared picture shows it."""
    if not args.me:
        return
    st = _load(args.me)
    t = args.msg_type
    if t == "anchor":
        st["sent"]["anchor"] = True
    elif t == "seed":
        st["sent"]["seed"] = True
    elif t == "repair":
        m = st["mirror"]
        if m and m["pending"]:
            st["sent"]["repair"].append(f"r{m['pending']['round']}-{args.me}")
    elif t == "score":
        st["sent"]["rated"] += list(args.candidate_id or [])
    _save(st)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------



def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="concord_receiver.py",
        description="CONCORD receiver: pass what you got to `handle`; give your judgement to `answer`.",
    )
    parser.add_argument("--me", default=None, help="your agent id, as in the initiator's --agents list")
    parser.add_argument("--state-id", default=None, dest="state_id",
                        help="the run's state id (normally read from the message; only needed with several runs)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser("observe", aliases=["handle"],
                        help="Pass a CONCORD message or reply you got; prints what is due, or NOTHING DUE.")
    op.add_argument("--from", dest="sender", default=None,
                    help="who sent it (only needed for a reply that does not name its author)")
    op.add_argument("text", nargs="?", default=None, help="message text (stdin if omitted)")
    ap_ = sub.add_parser("answer", help="Give your judgement with the flags the task shows; prints STATUS: SEND and the "
                                         "reply to send.")
    ap_.add_argument("--reject", type=str, default=None, help="anchor: your rating of x_reject")
    ap_.add_argument("--ideal", type=str, default=None, help="anchor: your rating of x_ideal")
    ap_.add_argument("--candidate", default=None, help="seed or repair: the candidate")
    ap_.add_argument("--rationale", default=None, help="seed or repair: why")
    ap_.add_argument("--score", nargs=3, action="append", default=None, metavar=("ID", "N", "REASONS"),
                     help="a rating: the candidate id, your raw number, your reasons (repeat for each candidate)")
    ap_.add_argument("text", nargs="?", default=None, help="(older form) the answer as JSON, if no flag is given")
    sub.add_parser("due", help="Print the current status, change nothing.")

    return parser


def _hoist_globals(argv):
    """Accept --me ID in any position, so `observe --me x` works as well as `--me x observe`."""
    out, hoisted, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--me" and i + 1 < len(argv):
            hoisted += [a, argv[i + 1]]
            i += 2
        elif a == "--state-id" and i + 1 < len(argv):
            hoisted += [a, argv[i + 1]]
            i += 2
        elif a.startswith("--me="):
            hoisted.append(a)
            i += 1
        else:
            out.append(a)
            i += 1
    return hoisted + out


class _UsageError(Exception):
    """A command line the parser refused (an unknown command, a missing argument)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _hint_for(msg):
    """What to do about an error: always a way back onto the path, never just the complaint."""
    m = msg.lower()
    if "--me" in m:
        return ("Pass your own agent id: `--me <id>`, the one the initiator used in its list of agents (it is in the "
                "message you received). Then run the same command again.")
    if "does not match its check" in m:
        return ("Give the script the whole delivery again, exactly as you received it: every reply in full, none shortened or "
                "reworded, then give it to the script again. Nothing was recorded. Do not write anything of your own.")
    if "already sent" in m or "already rated" in m or "no repair is due" in m:
        return ("That is already answered, or not yours to answer: nothing is lost. Pass the CONCORD message you have not "
                "recorded yet to `handle`, and if nothing is due send nothing.")
    if "raw_ideal" in m and "raw_reject" in m:
        return ("Rate the two outcomes again, as satisfaction of your role if each happened (reject LOW, ideal HIGH), and "
                "run `answer` again with `--reject N --ideal N`.")
    if "usage" in m or "invalid choice" in m or "unrecognized arguments" in m or "required" in m:
        return ("You need two commands only: `--me <you> handle` with each CONCORD message or reply (on stdin), and "
                "`--me <you> answer` with the flags the task shows, when `handle` printed STATUS: ASK (`due` prints the task and its "
                "exact command again). Copy the command as the task printed it.")
    return ("Fix what the message says and run the same command again. Never work around it by writing a reply, a "
            "rating or XML yourself.")


def _report_error(message, hint=None):
    """Put an error on stdout in the same shape as every other output: a status, what happened, what to do."""
    print("STATUS: ERROR")
    print(str(message).strip())
    print("NEXT: " + (hint or _hint_for(str(message))))


def main() -> None:
    try:
        _main()
    except _UsageError as exc:
        _report_error(f"usage error: {exc}")
        sys.exit(2)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            _report_error(exc.code)
        elif exc.code not in (0, None):
            _report_error("the command failed")
        raise
    except Exception as exc:                                  # a bug or a damaged state file: never a bare traceback
        _report_error(f"the script failed unexpectedly: {type(exc).__name__}: {exc}",
                      "Run the same command once more. If it fails again, tell whoever sent you the message that you "
                      "could not process it, quoting this message. Do not write a reply of your own.")
        sys.exit(1)


def _main() -> None:
    parser = build_parser()
    argv = _hoist_globals(sys.argv[1:])
    if not any(a in ("observe", "handle", "answer", "due") for a in argv) and not any(a in ("-h", "--help") for a in argv):
        argv.append("handle")                              # the command was left out: `handle` is the usual one
    args = parser.parse_args(argv)

    _ME[0] = args.me
    _SID[0] = getattr(args, "state_id", None)
    if args.cmd == "answer" and args.me:
        _st0 = _load(args.me)
        _STATE_ID[0] = _st0.get("id")
        _ACK[0] = _st0.get("delivery") or None
    if args.cmd == "handle":
        args.cmd = "observe"
    if args.cmd in ("observe", "due", "answer") and not args.me:
        sys.exit("error: --me <your agent id> is required before handle/due/answer")
    if args.cmd == "observe":
        cmd_observe(args)
    elif args.cmd == "answer":
        cmd_answer(args)
    elif args.cmd == "due":
        cmd_due(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
