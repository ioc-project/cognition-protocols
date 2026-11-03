#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""CONCORD — self-driving protocol initiator.

init          starts the run and prints the state path and what to run next
handle        the loop: pass it what arrived (replies, or nothing); it records them and prints the next message to send
              (STATUS: SEND), says that nothing is due (STATUS: NONE), or asks for a decision (STATUS: ASK)
watchdog      for a host that can run it in the background: waits for a stall, then prints a nudge
mark-missing  gives up on a silent agent on a candidate (a run with a MISSING entry can only end BEST_EFFORT)
status        shows the phase, who is awaited and who has acknowledged; changes nothing

All state lives in one JSON file. `init` always mints its own unique path and
reports it back; every later command must be given that exact path via --state.
Set CONCORD_RUN_KEY to make that suffix deterministic instead of random
(see `unique_state_path`) — needed for LLM response caching across a
record/replay pair. Stdlib only; no network, no dependencies.

Any text argument may be given as @path to read it from a file.
"""

import argparse
import fcntl
import contextlib
import io
import json
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

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
# <<< END SHARED CORE

DEFAULT_STATE = "concord-state.json"

# --------------------------------------------------------------------------
# state I/O
# --------------------------------------------------------------------------

def unique_state_path(path):
    """Append a suffix to `path`'s filename so concurrent runs that reuse the
    same --state value (or its default) never collide on the same file.
    Only called at `init` time — every later command just uses whatever
    path `init` reports back.

    The suffix is a fresh random UUID by default. When the caller sets
    CONCORD_RUN_KEY, the suffix is instead a deterministic hash of that
    key, so every `init` under the same run key always mints the same state
    path. That matters for LLM response caching: the state path appears in
    the text sent to the model (`state_file` / `State:`), so a random
    suffix makes a recorded run and a replayed run hash to different cache
    keys from the second call onward, even though nothing else differs. A
    caller doing record/replay sets CONCORD_RUN_KEY to something stable
    across both runs (for example, a hash of the LLM cache path) to get
    byte-identical state paths and real cache hits.
    CONCORD_STATE_DIR optionally relocates the file; it is ignored
    unless CONCORD_RUN_KEY is also set."""
    p = Path(path)
    run_key = os.environ.get("CONCORD_RUN_KEY")

    if run_key:
        state_dir = Path(os.environ.get("CONCORD_STATE_DIR") or p.parent)
        state_dir.mkdir(parents=True, exist_ok=True)

        target_dir = state_dir
        suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_key}:{p.name}").hex[:8]
    else:
        target_dir = p.parent
        suffix = uuid.uuid4().hex[:8]

    return str(target_dir / f"{p.stem}-{suffix}{p.suffix}")


def load(path):
    p = Path(path)
    if not p.exists():
        die(f"no state file at {path} — run `concord_initiator.py init` first")
    return json.loads(p.read_text())


def save(path, st):
    Path(path).write_text(json.dumps(st, indent=2) + "\n")


@contextmanager
def locked_state(path):
    """Exclusive file lock held across load → mutate → save.

    Parallel record-reply calls (N agents replying simultaneously) each do a
    read-modify-write on the state file. Without a lock the last writer wins
    and earlier mutations are silently lost. flock(LOCK_EX) serialises them:
    each call waits until the previous one has finished and saved before
    reading, so every reply is recorded regardless of arrival order.
    """
    p = Path(path)
    if not p.exists():
        die(f"no state file at {path} — run `concord_initiator.py init` first")
    with open(path, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            st = json.loads(fh.read())
            yield st
            fh.seek(0)
            fh.write(json.dumps(st, indent=2) + "\n")
            fh.truncate()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


_LAST_ERROR = None


def die(msg):
    global _LAST_ERROR
    _LAST_ERROR = str(msg)
    sys.stderr.write(f"error: {msg}\n")
    raise SystemExit(2)


def textarg(v):
    if v is not None and str(v).startswith("@"):
        return Path(str(v)[1:]).read_text().rstrip("\n")
    return v


# --------------------------------------------------------------------------
# anchor-leak guard
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset("""
    a an the this that these those is are was were be been being
    to of in on for and or but if then than so not no nor
    it its as at by from with without into onto over under
    do does did done doing have has had having will would should could can
    may might must shall we you they he she i our your their his her
    all any both each few more most other some such only own same
""".split())


def _tokens(text):
    return {w for w in _WORD_RE.findall((text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _non_numeric(tokens):
    return {t for t in tokens if not any(c.isdigit() for c in t)}




# --------------------------------------------------------------------------
# scoring  (spec §4–5)
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------

def esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def cdata(s):
    return "<![CDATA[" + str(s).replace("]]>", "]]]]><![CDATA[>") + "]]>"


# --------------------------------------------------------------------------
# receiver instructions embedded in every message
#
# Each message tells its recipient what to do and which command produces the
# reply, so the message is self-contained: the initiator can forward it
# unchanged without adding its own instructions or reply template. Text is
# broadcast-safe (identical for every recipient) and avoids quote characters so
# it stays readable after XML escaping.
# --------------------------------------------------------------------------

INSTRUCTIONS = {
    "anchor": (
        "Rate the two outcomes above on your own raw 0-100 scale, from your role's perspective. "
        "raw_reject is how satisfied your role would be if x_reject happened (it should be LOW). "
        "raw_ideal is how satisfied your role would be if x_ideal happened (it should be HIGH, above raw_reject). "
        "Rate the two outcomes, not the current state. "
        "Your answer to this message is only the reply the script tells you to send, with nothing before or after it. Pass this message to concord_receiver.py handle and do what it says; never write the reply yourself."
    ),
    "seed": (
        "Propose the single candidate that best satisfies your own role, in the candidate_schema format. "
        "Your answer to this message is only the reply the script tells you to send, with nothing before or after it. Pass this message to concord_receiver.py handle and do what it says; never write the reply yourself."
    ),
    "score": (
        "Rate every candidate on your own raw 0-100 scale, the same scale you used for your anchor, with your full "
        "reasoning for each. "
        "Your answer to this message is only the reply the script tells you to send, with nothing before or after it. Pass this message to concord_receiver.py handle and do what it says; never write the reply yourself."
    ),
    "repair": (
        "You are the least-satisfied agent on the incumbent. Read the incumbent, its rationale and every agent's score "
        "and reasoning, then propose ONE new candidate in the candidate_schema format that you would rate close to "
        "your x_ideal and that does not push other agents below the threshold. Your rationale must answer the "
        "strongest reasoning against your position. Rate your own new candidate in the same reply. "
        "Your answer to this message is only the reply the script tells you to send, with nothing before or after it. Pass this message to concord_receiver.py handle and do what it says; never write the reply yourself."
    ),
}


def instructions_line(kind):
    return f"  <instructions>{esc(INSTRUCTIONS[kind])}</instructions>"


# --------------------------------------------------------------------------
# XML message builders
# --------------------------------------------------------------------------

def _routing_lines(st, addressed):
    """Who takes part, and who must answer this message.

    The message may be posted to a channel every agent reads (a shared endpoint) or sent to each agent alone.
    Either way the text says which agents it asks for a reply (<addressed_to>); the others only read it.
    """
    lines = [f"  <state_id>{esc(st.get('state_id', ''))}</state_id>", "  <participants>"]
    lines += [f"    <agent>{esc(a)}</agent>" for a in st["agents"]]
    lines.append("  </participants>")
    p = st["params"]
    lines.append(f"  <params><satisfaction_threshold>{esc(p['satisfaction_threshold'])}</satisfaction_threshold>"
                 f"<eps>{esc(p['eps'])}</eps><budget>{esc(p['B'])}</budget></params>")
    lines.append("  <addressed_to>")
    lines += [f"    <agent>{esc(a)}</agent>" for a in addressed]
    lines.append("  </addressed_to>")
    return lines


def build_anchor_msg(st, addressed):
    p = st["params"]
    return "\n".join([
        "<concord-anchor>",
        f"  <satisfaction_threshold>{esc(p['satisfaction_threshold'])}</satisfaction_threshold>",
        f"  <x_reject>{esc(st['x_reject'])}</x_reject>",
        f"  <x_ideal>{esc(st['x_ideal'])}</x_ideal>",
        *_routing_lines(st, addressed),
        instructions_line("anchor"),
        "</concord-anchor>",
    ])


def build_seed_msg(st, addressed):
    lines = [
        "<concord-propose-seed>",
        f"  <candidate_schema>{esc(st['schema'])}</candidate_schema>",
        f"  <satisfaction_threshold>{esc(st['params']['satisfaction_threshold'])}</satisfaction_threshold>",
        f"  <context>{esc(st['context'])}</context>",
    ]
    lines += _routing_lines(st, addressed)
    lines.append(instructions_line("seed"))
    lines.append("</concord-propose-seed>")
    return "\n".join(lines)


def build_score_msg(st, addressed):
    lines = [
        "<concord-score>",
        f"  <context>{esc(st['context'])}</context>",
        "  <candidates>",
    ]
    # Only candidates that some agent has not rated yet: after a repair that is the new candidate. Ratings already
    # on record are never asked for again (record-reply would refuse to overwrite them).
    to_score = [c for c in st["pool"] if any(c["id"] not in st["raw"].get(a, {}) for a in st["agents"])] or st["pool"]
    for cand in to_score:
        cid = cand["id"]
        lines.append(f'    <candidate id="{esc(cid)}">')
        lines.append(f"      <text>{esc(cand['candidate'])}</text>")
        if cand.get("rationale"):
            lines.append(f"      <rationale>{esc(cand['rationale'])}</rationale>")
        lines.append("    </candidate>")
    lines += ["  </candidates>", *_routing_lines(st, addressed), instructions_line("score"), "</concord-score>"]
    return "\n".join(lines)


def build_repair_msg(st, agent):
    tau = st["params"]["satisfaction_threshold"]
    s_b = score(st, agent, st["x_star"])
    cid = st["x_star"]
    inc = next(c for c in st["pool"] if c["id"] == cid)
    reasonings = st.get("score_reasoning", {})

    lines = [
        "<concord-repair>",
        f"  <satisfaction_threshold>{esc(tau)}</satisfaction_threshold>",
        f"  <candidate_schema>{esc(st['schema'])}</candidate_schema>",
        f"  <context>{esc(st['context'])}</context>",
        f"  <new_candidate_id>r{st['pending']['round']}-{esc(agent)}</new_candidate_id>",
        f'  <incumbent id="{esc(cid)}"'
        f' your_score="{s_b:.2f}" shortfall="{tau - s_b:.2f}">',
        f"    <text>{esc(inc['candidate'])}</text>",
    ]
    if inc.get("rationale"):
        lines.append(f"    <rationale>{esc(inc['rationale'])}</rationale>")
    lines.append("    <scores>")
    for a in st["agents"]:
        s = score(st, a, cid)
        s_attr = "" if s is None else f' score="{s:.2f}"'
        r = reasonings.get(a, {}).get(cid)
        if r:
            lines.append(f'      <score agent="{esc(a)}"{s_attr}>')
            lines.append(f"        <reasoning>{esc(r)}</reasoning>")
            lines.append("      </score>")
        else:
            lines.append(f'      <score agent="{esc(a)}"{s_attr}/>')
    lines += ["    </scores>", "  </incumbent>", *_routing_lines(st, addressed=[agent]), instructions_line("repair"), "</concord-repair>"]
    return "\n".join(lines)


def build_commit_msg(st, outcome):
    tau = st["params"]["satisfaction_threshold"]
    winner = next(c for c in st["pool"] if c["id"] == st["x_star"])
    col = column(st, st["x_star"])
    short = {a: s for a, s in col.items() if s is not None and s < tau - TOL}
    unknown = absent(st, st["x_star"])

    lines = [
        "<concord-commit>",
        f"  <outcome>{esc(outcome)}</outcome>",
        f"  <winning_candidate_id>{esc(st['x_star'])}</winning_candidate_id>",
        f"  <winning_candidate>{esc(winner['candidate'])}</winning_candidate>",
        "  <final_scores>",
    ]
    for a in st["agents"]:
        if col[a] is None:
            lines.append(f'    <score agent="{esc(a)}" value="no-reply"/>')
        else:
            lines.append(f'    <score agent="{esc(a)}" value="{col[a]:.2f}"/>')
    lines.append("  </final_scores>")
    if short:
        lines.append("  <shortfall>")
        for a, s in short.items():
            lines.append(f'    <agent name="{esc(a)}" score="{s:.2f}"'
                         f' short_by="{tau - s:.2f}"/>')
        lines.append("  </shortfall>")
    if unknown:
        lines.append("  <unknown>")
        for a in unknown:
            lines.append(f'    <agent name="{esc(a)}"/>')
        lines.append("  </unknown>")
    lines += _routing_lines(st, [])
    lines.append("</concord-commit>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# decision engine
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# phase resolver
# --------------------------------------------------------------------------

def current_action(st):
    """Return the current phase action dict. Always resolves — no edge cases.

    Keys always present:
      phase     str
      messages  dict[agent → XML string]  — identical content for all agents in
                                            Anchor/Seed/Score/Complete phases

    Additional keys per phase:
      outcome   (Complete only)
      recipients list[agent]  — who we are waiting for
      owed      dict[agent → list[cid]]  (Score only — tracks per-agent gaps)
    """
    agents = st["agents"]

    # Terminal
    if st.get("outcome"):
        outcome = st["outcome"]
        msg = build_commit_msg(st, outcome)
        return {
            "phase": "Complete",
            "outcome": outcome,
            "recipients": [],
            "messages": {a: msg for a in agents},
        }

    # Awaiting repair proposal
    if st.get("pending"):
        b = st["pending"]["agent"]
        return {
            "phase": "Repair",
            "recipients": [b],
            "messages": {b: build_repair_msg(st, b)},
        }

    # Awaiting anchor calibrations
    need_anchor = [a for a in agents if a not in st.get("anchors", {})]
    if need_anchor:
        msg = build_anchor_msg(st, need_anchor)
        return {
            "phase": "Anchor",
            "recipients": need_anchor,
            "messages": {a: msg for a in need_anchor},
        }

    # Awaiting seed proposals
    seeded = {e["proposer"] for e in st["pool"] if e.get("round", -1) == 0}
    need_seed = [a for a in agents if a not in seeded]
    if need_seed:
        msg = build_seed_msg(st, need_seed)
        return {
            "phase": "Seed",
            "recipients": need_seed,
            "messages": {a: msg for a in need_seed},
        }

    # Awaiting scores
    pool_ids = [c["id"] for c in st["pool"]]
    owed = {}
    for a in agents:
        gaps = [cid for cid in pool_ids if cid not in st["raw"].get(a, {})]
        if gaps:
            owed[a] = gaps
    if owed:
        msg = build_score_msg(st, list(owed))
        return {
            "phase": "Score",
            "recipients": list(owed),
            "messages": {a: msg for a in owed},
            "owed": owed,
        }

    # advance() guarantees this is never reached in normal operation
    die("internal: current_action called in unresolvable state — "
        "this is a bug; please report it")


# --------------------------------------------------------------------------
# reply parsing  (copied from the reference implementation)
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:xml)?\s*\n(.*?)\n```", re.DOTALL)
# A complete <concord-...-reply> element anywhere in the text (a reply embedded in prose, unfenced).
_EMBEDDED_REPLY_RE = re.compile(r"<(concord-[a-z-]+-reply)\b[^>]*>.*?</\1\s*>", re.DOTALL)
# Any complete <concord-...> element: last resort, so an invented reply tag still gets a precise error.
_EMBEDDED_ANY_RE = re.compile(r"<(concord-[a-z-]+)\b[^>]*>.*?</\1\s*>", re.DOTALL)


def _locate_reply_xml(text):
    """Return the XML document that holds the agent's reply, taken unchanged from `text`.

    The agent's whole message may be handed over: the reply can sit inside a markdown fence or inside surrounding
    prose, and is extracted here, deterministically, instead of by the initiator. The extracted text is the
    agent's own, byte for byte. A message that contains no CONCORD XML at all is
    refused: prose is not a reply, and numbers or a candidate must never be lifted out of it.
    """
    stripped = text.strip()
    for m in _FENCE_RE.finditer(stripped):
        if "<concord-" in m.group(1):
            return m.group(1).strip()
    if stripped.startswith("<concord-"):
        return stripped
    # Prefer a *-reply element: an agent may quote the incoming message before replying to it.
    for pattern in (_EMBEDDED_REPLY_RE, _EMBEDDED_ANY_RE):
        m = pattern.search(stripped)
        if m:
            return m.group(0)
    if "<concord-" not in stripped:
        die("reply contains no CONCORD XML — the agent replied in prose, so nothing was recorded. "
            "That prose is not a reply: do not pull numbers or a candidate out of it and do not write the XML "
            "yourself.")
    return stripped


def parse_reply(text):
    stripped = escape_bare_chars(_locate_reply_xml(text))
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        die(f"reply is not valid XML: {exc}\n"
            "Expected: <concord-anchor-reply>, <concord-candidate-reply>, "
            "or <concord-score-reply>.")
    return _parse_reply_root(_normalize_reply_root(root))


def _normalize_reply_root(root):
    if root.tag == "concord-score":
        root.tag = "concord-score-reply"
    if root.tag != "concord-score-reply":
        return root
    for sc in list(root.findall("score")):
        if sc.get("candidate_id") and sc.find("raw") is not None:
            continue
        replacements = []
        for child in list(sc):
            cid = child.get("candidate_id")
            raw = child.get("raw")
            if cid and raw is not None:
                new_sc = ET.Element("score", {"candidate_id": cid})
                ET.SubElement(new_sc, "raw").text = raw
                ET.SubElement(new_sc, "reasoning").text = (child.text or "").strip()
                replacements.append(new_sc)
        if replacements:
            idx = list(root).index(sc)
            root.remove(sc)
            for offset, new_sc in enumerate(replacements):
                root.insert(idx + offset, new_sc)
    return root


def _parse_reply_root(root):
    tag = root.tag
    if tag == "concord-anchor-reply":
        rej_el = root.find("raw_reject")
        ide_el = root.find("raw_ideal")
        if rej_el is None or ide_el is None:
            die("anchor reply missing <raw_reject> or <raw_ideal>")
        return {"kind": "anchor",
                "raw_reject": float(rej_el.text.strip()),
                "raw_ideal": float(ide_el.text.strip())}
    if tag == "concord-candidate-reply":
        cand_el = root.find("candidate")
        if cand_el is None:
            die("candidate reply missing <candidate>")
        if not (cand_el.text or "").strip():
            # An empty candidate would enter the pool and could even win, leaving a certificate with no text.
            die("candidate reply has an empty <candidate> — the candidate must be the proposal itself, "
                "in the candidate_schema format")
        rat_el = root.find("rationale")
        if rat_el is None:
            die("candidate reply missing <rationale>")
        return {"kind": "candidate",
                "candidate": (cand_el.text or "").strip(),
                "rationale": (rat_el.text or "").strip(),
                "id": root.get("id")}
    if tag == "concord-score-reply":
        scores = []
        for sc in root.findall("score"):
            cid = sc.get("candidate_id")
            if not cid:
                die("a <score> element is missing candidate_id attribute")
            raw_el = sc.find("raw")
            if raw_el is None:
                die(f"no <raw> found for candidate_id '{cid}'")
            reas_el = sc.find("reasoning")
            if reas_el is None:
                die(f"no <reasoning> found for candidate_id '{cid}'")
            scores.append({
                "id": cid,
                "raw": float(raw_el.text.strip()),
                "reasoning": (reas_el.text or "").strip(),
            })
        if not scores:
            die("score reply contains no <score> elements")
        return {"kind": "scores", "scores": scores}
    die(f"unrecognised reply root tag <{tag}>; expected one of: "
        "concord-anchor-reply, concord-candidate-reply, concord-score-reply")


# --------------------------------------------------------------------------
# output renderer
# --------------------------------------------------------------------------

SEND_NOTE = (
    "Send each message below to its recipient, exactly as printed between the\n"
    "BEGIN/END lines: no text before or after it, no summary, and no reply template of your own\n"
    "(the message already says how to reply). Do not include the BEGIN/END lines themselves."
)


def _marked(to, xml):
    return (f"----- BEGIN MESSAGE (to: {to}) -----\n"
            f"{xml}\n"
            "----- END MESSAGE -----")


def _issue_key(st, action, agent):
    phase = action["phase"]
    if phase == "Score":
        return "score:" + ",".join(action.get("owed", {}).get(agent, []))
    if phase == "Repair":
        return f"repair:{(st.get('pending') or {}).get('round')}"
    return phase.lower()


def _mark_issued(state_path, action, resend=False):
    """Remember which message each agent was told about, so the same message is printed once.

    Returns the agents whose message is new (to be sent now). After every recorded reply the tool used to print the
    messages again for everyone still owing a reply, and initiators obeyed: the same Score message went out two
    to eight times per agent. Now a message is shown once; later outputs only say who is awaited.
    """
    new = []
    with locked_state(state_path) as st:
        issued = st.setdefault("issued", {})
        for a in action.get("recipients", []):
            key = _issue_key(st, action, a)
            if resend or key not in issued.get(a, []):
                issued.setdefault(a, []).append(key)
                new.append(a)
    return new


def _mark_issued_locked(st, action, resend=False):
    new = []
    issued = st.setdefault("issued", {})
    for a in action.get("recipients", []):
        key = _issue_key(st, action, a)
        if resend or key not in issued.get(a, []):
            issued.setdefault(a, []).append(key)
            new.append(a)
    return new


def render_status(st, state_path, fmt, resend=False, issue=True, show_messages=True):
    action = current_action(st)
    phase = action["phase"]
    messages = action["messages"]
    recipients = action.get("recipients", [])
    owed = action.get("owed", {})

    if fmt == "json":
        data = {
            "phase": phase,
            "state_file": state_path,
            "awaiting": recipients,
            "messages": messages,
        }
        if phase == "Complete":
            data["outcome"] = action["outcome"]
        data["finished"] = phase == "Complete"
        print(json.dumps(data, indent=2))
        return

    # Text format
    outcome_suffix = f" — {action['outcome']}" if phase == "Complete" else ""
    print(f"Phase: {phase}{outcome_suffix}")
    if phase != "Complete":
        print("NOT FINISHED: there is no outcome yet. Do not run any action and do not report a result until this "
              "tool prints `Phase: Complete`.")
    print()

    # Messages to Send
    new_agents = list(recipients) if not (issue and state_path) else _mark_issued(state_path, action, resend)
    if not show_messages:
        messages = {}
    if messages and phase != "Complete" and not new_agents:
        print("Messages to Send:")
        print()
        print("NONE. The message for this phase was already printed, and you were told to send it. Do not send it "
              "again. Wait for the replies listed under Awaiting replies and record each as it arrives. (Only if "
              "you know it was never delivered: `status --resend`.)")
        print()
        messages = {}
    elif messages and phase != "Complete":
        messages = {a: m for a, m in messages.items() if a in new_agents}
    all_msgs = list(messages.values())
    unique_msgs = len(set(all_msgs)) == 1 if all_msgs else True

    if messages:
        print("Messages to Send:")
        print()
        if phase != "Complete":
            print(SEND_NOTE)
            print()
        if phase in ("Anchor", "Seed", "Score") or (unique_msgs and phase == "Complete"):
            # All agents get the same message — print once
            if phase == "Complete":
                print(next(iter(messages.values())))
            else:
                print(_marked("every agent under Awaiting replies", next(iter(messages.values()))))
            print()
        else:
            for a in (recipients if recipients else list(messages)):
                if a in messages:
                    print(_marked(a, messages[a]))
                    print()

    parked = st.get("deferred", [])
    if parked:
        print("Parked replies (kept, applied automatically once what they refer to is on record):")
        for d in parked:
            print(f"* {d['agent']} rated {', '.join(sc['id'] for sc in d['reply']['scores'])}")
        print("If one of these ids will never exist (the agent used a wrong id), ignore it: it does no harm, and "
              "the agent still owes the ratings the Awaiting list shows.")
        print()

    # Awaiting replies
    print("Awaiting replies:")
    print()
    if phase == "Complete":
        print("(none — broadcast to all participants)")
    elif recipients:
        for a in recipients:
            if owed and a in owed:
                cids = ", ".join(owed[a])
                print(f"* {a} (candidates: {cids})")
            else:
                print(f"* {a}")
    else:
        print("(none)")
    print()

    if phase == "Complete" and state_path:
        print(f"State: {state_path}")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args):
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    if len(agents) < 2:
        die("a negotiating set needs at least 2 agents")
    if len(set(agents)) != len(agents):
        die("duplicate agent names")
    if not (0 < args.satisfaction_threshold <= 1):
        die("satisfaction_threshold must be in (0, 1]")
    if args.eps <= 0:
        die("eps must be > 0")
    if args.B < 1:
        die("B must be >= 1")

    context = textarg(args.context)
    x_reject = textarg(args.x_reject)
    x_ideal = textarg(args.x_ideal)
    schema = textarg(args.schema)

    st = {
        "version": 2,
        "params": {
            "satisfaction_threshold": args.satisfaction_threshold,
            "eps": args.eps,
            "B": args.B,
            "W": "leximin",
        },
        "agents": agents,
        "schema": schema,
        "context": context,
        "x_reject": x_reject,
        "x_ideal": x_ideal,
        "anchors": {},
        "pool": [],
        "raw": {a: {} for a in agents},
        "score_reasoning": {},
        "x_star": None,
        "t": 0,
        "pending": None,
        "outcome": None,
        "log": [],
    }

    st["relay"] = not getattr(args, "no_relay", False)
    state_path = unique_state_path(args.state)
    # The run's id is the random suffix of its state file. Every message carries it and every reply echoes it, so
    # each agent keeps the state of this run apart from any other run, and a stale reply is recognised.
    st["state_id"] = Path(state_path).stem.rsplit("-", 1)[-1]
    save(state_path, st)

    if args.format == "json":
        action = current_action(st)
        data = {
            "state_file": state_path,
            "agents": agents,
            "satisfaction_threshold": args.satisfaction_threshold,
            "budget": args.B,
            "phase": action["phase"],
            "awaiting": action.get("recipients", []),
            "messages": action["messages"],
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"State: {state_path}")
        print(f"NEXT: Run `concord_initiator.py --state {state_path} handle` with no input to get the first message to "
              "send. Use exactly this state path on every call of this run.")
        print()
        if not args.quiet:
            render_status(st, state_path, args.format)


def reject_hint(state_path, agent, err):
    """Text printed after a rejected record-reply: what to do next, and the exact message to re-send.

    A rejected reply leaves the state untouched. The initiator's only legitimate move is to ask the
    agent again; editing the reply (re-tagging, swapping or choosing numbers) would put words in the
    agent's mouth, so the hint says so and hands over the message to re-send.
    """
    lines = [
        f"REJECTED: nothing was recorded for {agent}.",
        "Do not edit this reply and do not change, swap or invent any number or tag: a corrected reply is "
        "no longer the agent's own reply.",
    ]
    if err and "must exceed raw_reject" in err:
        lines.append("An inverted anchor must be re-rated by the agent itself; never swap the two numbers yourself.")
    try:
        st = json.loads(Path(state_path).read_text())
        action = current_action(st)
    except Exception:
        return "\n".join(lines)
    phase = action["phase"]
    if phase == "Complete":
        lines.append("The run is already complete; there is nothing to re-send.")
    elif agent in action["messages"]:
        lines += [
            "",
            f"Send {agent} the message below again, exactly as printed (it already carries the rejection reason "
            "and the reply instructions), then record the new reply:",
            "",
            _marked(agent, f"Your previous reply was rejected: {(err or 'invalid reply').split(' — ')[0]}. "
                           "Reply again, exactly as instructed below.\n\n"
                           f"{action['messages'][agent]}"),
            "",
        ]
        if phase == "Score":
            cids = action.get("owed", {}).get(agent, [])
            cmds = "; ".join(f"concord_initiator.py --state {state_path} mark-missing {agent} {c}" for c in cids)
            lines.append("If the second reply is also rejected, treat the agent as silent (the run can then only end "
                         f"BEST_EFFORT): {cmds}")
        else:
            lines.append(f"If the second reply is also rejected there is no way to skip an agent in the {phase} phase "
                         "(mark-missing only covers scores). Stop and report that the run could not be completed and "
                         "is NOT certified, together with the error above. Do not fill in the reply yourself.")
    else:
        lines.append(f"{agent} is not awaited in the current {phase} phase (already recorded, or not a participant), "
                     "so there is nothing to re-send. Do not record it again.")
    return "\n".join(lines)


_REPLY_DOC_RE = re.compile(r"<(concord-(?:anchor|candidate|score)-reply|concord-ack|concord-error)\b[^>]*>.*?</\1\s*>", re.DOTALL)


def _split_reply_docs(text):
    """An agent that was sent several documents at once (anchor + seed) may reply with several, in order."""
    docs = [m.group(0) for m in _REPLY_DOC_RE.finditer(text)]
    return docs if len(docs) > 1 else [text]


def _apply_scores(st, agent, r, overwrite, replay=False):
    """Record the ratings in a score reply. Returns (kept, deferred).

    A reply can arrive before what it rates exists (a rating of a repair candidate sent before the candidate was
    recorded; a rating sent before the agent's own anchor was recorded). It is parked in st["deferred"] and applied
    as soon as that is on record. A reply that rates only what the agent already rated changes nothing and is not an
    error: when agents read each other's replies, the same rating can legitimately arrive twice.
    """
    known = {c["id"] for c in st["pool"]}
    unknown = [sc["id"] for sc in r["scores"] if sc["id"] not in known]
    if unknown or agent not in st["anchors"]:
        if replay:
            return [], True
        # an id that will never exist (altered or invented) cannot be told apart from one that has not arrived yet:
        # the run's status keeps showing the agent as owing it, and the initiator can retry or mark-missing.
        st.setdefault("deferred", []).append({"agent": agent, "reply": r})
        what = ", ".join(unknown) if unknown else f"{agent}'s anchor"
        return [], what
    anchor = st["anchors"][agent]
    lo = min(anchor["raw_reject"], anchor["raw_ideal"])
    hi = max(anchor["raw_reject"], anchor["raw_ideal"])
    out_of_range = []
    on_record = st["raw"].get(agent, {})
    fresh, kept = [], []
    for sc in r["scores"]:
        cid = sc["id"]
        if cid in on_record and not overwrite:
            if on_record[cid] == "MISSING":
                die(f"{agent} was marked MISSING on {cid}; pass --overwrite to record a late score")
            kept.append(cid)          # already rated: the rating on record stays
        else:
            fresh.append(sc)
    for sc in fresh:
        cid = sc["id"]
        st["raw"].setdefault(agent, {})[cid] = sc["raw"]
        st.setdefault("score_reasoning", {}).setdefault(agent, {})[cid] = sc["reasoning"]
        raw = sc["raw"]
        if raw < lo or raw > hi:
            direction = "below raw_reject" if raw < lo else "above raw_ideal"
            out_of_range.append(
                f"  {cid}: raw={raw} ({direction} {lo if raw < lo else hi})"
                f" → normalises to {'0.00' if raw < lo else '1.00'}"
            )
    if out_of_range:
        print(
            f"WARNING: {agent} scored {len(out_of_range)} candidate(s) outside their "
            f"calibrated range [{lo}, {hi}] — these collapse to 0 or 1 and lose all "
            f"discrimination. Check whether the anchor needs --overwrite:\n"
            + "\n".join(out_of_range),
            file=sys.stderr,
        )
    return kept, False


def _replay_deferred(st):
    """Apply parked score replies whose candidate and anchor are now on record."""
    left = []
    for d in st.get("deferred", []):
        _, still = _apply_scores(st, d["agent"], d["reply"], False, replay=True)
        if still:
            left.append(d)
    st["deferred"] = left


def _apply_reply(st, agent, r, overwrite):
    """Apply one parsed reply to the run state. Returns (kept, deferred) for score replies."""
    kept, deferred = [], False
    if agent not in st["agents"]:
        die(f"{agent} is not in the negotiating set")

    if r["kind"] == "anchor":
        if r["raw_ideal"] <= r["raw_reject"]:
            die(f"raw_ideal ({r['raw_ideal']}) must exceed raw_reject "
                f"({r['raw_reject']}); {agent}'s scale is inverted")
        for name, val in [("raw_reject", r["raw_reject"]), ("raw_ideal", r["raw_ideal"])]:
            if val < 0 or val > 100:
                print(
                    f"WARNING: {agent}'s {name} ({val}) is outside the expected "
                    f"0..100 raw scale — scores may not be comparable across agents",
                    file=sys.stderr,
                )
        if agent in st["anchors"] and not overwrite:
            die(f"{agent} is already anchored; pass --overwrite to re-anchor "
                f"(this invalidates comparisons made before it)")
        st["anchors"][agent] = {
            "raw_reject": r["raw_reject"],
            "raw_ideal": r["raw_ideal"],
        }

    elif r["kind"] == "candidate":
        pending = st.get("pending")
        if pending and pending["agent"] != agent:
            die(f"REPAIR was issued to {pending['agent']}, not {agent}")
        rnd = pending["round"] if pending else 0
        cid = (f"seed-{agent}" if rnd == 0 else f"r{rnd}-{agent}")
        if r.get("id") and r["id"] != cid:
            die(f"candidate id {r['id']} does not match the expected {cid}")
        if any(c["id"] == cid for c in st["pool"]):
            die(f"candidate id {cid} already exists")
        if rnd == 0 and any(c["proposer"] == agent for c in st["pool"]):
            die(f"{agent} already contributed a seed")
        st["pool"].append({
            "id": cid,
            "proposer": agent,
            "round": rnd,
            "candidate": r["candidate"],
            "rationale": r.get("rationale"),
        })
        st["pending"] = None

    else:  # scores
        kept, deferred = _apply_scores(st, agent, r, overwrite)
    _replay_deferred(st)
    advance(st)
    return kept, deferred


def _record_reply(args):
    state_path = args.state
    text = args.text if args.text is not None else sys.stdin.read()
    overwrite = getattr(args, "overwrite", False)
    agent = args.agent

    r = parse_reply(text)  # parse before acquiring lock — pure computation

    with locked_state(state_path) as st:
        kept, deferred = _apply_reply(st, agent, r, overwrite)


    if r["kind"] == "scores" and deferred:
        print(f"Note: {agent}'s ratings arrived before {deferred}. The reply is kept and is recorded automatically "
              f"once that is on record. Nothing to re-send.")
        print()
    if r["kind"] == "scores" and kept:
        print(f"Note: {agent} also sent ratings for {', '.join(kept)}, which it had already rated. The ratings on record "
              f"were kept; only the new ones were recorded.")
        print()
    render_status(st, state_path, args.format)


def _final_report(st):
    """The result, for the initiator to report to whoever asked for the run. Not a message to the agents: every
    agent reaches the same outcome from the same ratings."""
    tau = st["params"]["satisfaction_threshold"]
    winner = next(c for c in st["pool"] if c["id"] == st["x_star"])
    col = column(st, st["x_star"])
    lines = [f"RUN COMPLETE: outcome {st['outcome']}.",
             f"Winning candidate {winner['id']} (proposed by {winner['proposer']}): {winner['candidate']}",
             "Final scores: " + ", ".join(f"{a} {'no reply' if col[a] is None else '%.2f' % col[a]}"
                                         for a in st["agents"]) + f" (threshold {tau:g})"]
    short = {a: v for a, v in col.items() if v is not None and v < tau - TOL}
    if short:
        lines.append("Below the threshold: " + ", ".join(f"{a} by {tau - v:.2f}" for a, v in short.items()))
    unknown = absent(st, st["x_star"])
    if unknown:
        lines.append("Never rated (not certified): " + ", ".join(unknown))
    return "\n".join(lines)


def _blocks(per):
    """per: agent -> text. One message when all agents get the same, else `--- to:` blocks."""
    groups = {}
    for a, text in per.items():
        groups.setdefault(text, []).append(a)
    return groups


_RULES = "Never write or change a message or a reply yourself."


def _next_send(phase, nudge=False, complete=False):
    """The reminder printed under `STATUS: SEND`: what to do with the blocks, and what happens next."""
    lead = ("This is a nudge: it makes each agent check what it has to do, and carries what it may have missed. "
            if nudge else "")
    tail = ("Pass every reply you receive to `handle`, exactly as received (several replies in one call is fine). "
            "Do not stop before the script prints RUN COMPLETE. If sending to an agent fails (an error, or the "
            "message did not go out), try once more with the full block as the message; if it fails again, stop and "
            "report which agent could not be reached and quote the error: do not keep retrying.")
    if phase in ("Score", "Repair"):
        tail = ("From here the agents rate and repair among themselves: you only pass things on. " + tail)
    if complete:
        tail = ("The run is over: after sending, report the RUN COMPLETE result printed on the error output, "
                "exactly as printed and with nothing added.")
    return ("NEXT: " + lead + "Send each block below to the agent or agents named on its `--- to:` line, exactly as "
            "printed and without that line. " + _RULES + " " + tail)


def _next_none(report=False, hold=False, awaiting="-"):
    """The reminder printed under `STATUS: NONE`."""
    if report:
        return ("NEXT: The run is over. Report exactly the result below (outcome, winning candidate, final scores) to "
                "whoever asked for it, and add nothing: no steps, recommendations or commentary of your own. Run no "
                "more commands.")
    wait = ("The run is not over, so do not end your turn just because nothing is due now. If no reply is still on its "
            "way (you have already collected what the agents answered), run `handle` with no input now: it prints the "
            "nudge to send, as it would after a lost message. Do not ask the agents for anything yourself (no request "
            "for a proposal, a rating or a reply, not even to speed things up): each agent works out what it owes.")
    if hold:
        return ("NEXT: Send nothing now. The agents rate and repair among themselves. Pass each reply you receive to "
                "`handle`, exactly as received. " + wait)
    return (f"NEXT: Send nothing. Wait for a reply from: {awaiting}. Pass each reply to `handle` as it arrives, "
            "exactly as received. " + wait)


def cmd_mark_missing(args):
    """An agent stayed silent: record it as MISSING on a candidate, and print the notice every other agent needs so
    that their own picture reaches the same outcome (BEST_EFFORT). Only the initiator knows this, so it is always
    passed on, whatever the relay setting."""
    state_path = args.state
    agent = args.agent
    cid = args.candidate

    with locked_state(state_path) as st:
        if agent not in st["agents"]:
            die(f"{agent} is not in the negotiating set")
        if not any(c["id"] == cid for c in st["pool"]):
            die(f"no candidate {cid} in the pool")
        if cid in st["raw"].get(agent, {}):
            die(f"{agent} already has a score for {cid}")
        st["raw"].setdefault(agent, {})[cid] = "MISSING"
        doc = (f'<concord-missing agent="{esc(agent)}" candidate="{esc(cid)}" state="{esc(st.get("state_id", ""))}">'
               "</concord-missing>")
        st.setdefault("docs", []).append([agent, doc])
        advance(st)
        agents = [a for a in st["agents"] if a != agent]
        outcome = st.get("outcome")
        report = _final_report(st) if outcome else None
    print("STATUS: SEND")
    print("NEXT: Send the notice below to the agents named, exactly as printed, then continue with `handle`. "
          + ("The run is over: report the RUN COMPLETE result printed on the error output, exactly as printed. " if report else "")
          + _RULES)
    print(f"--- to: {', '.join(agents)}")
    print(doc)
    print(f"[{agent} marked MISSING on {cid}: the run can now only end BEST_EFFORT]", file=sys.stderr)
    if report:
        print(report, file=sys.stderr)


def cmd_handle(args):
    """Pass whatever arrived (replies, one or several, from any agent; or nothing). Prints the next message to send
    as plain XML, or nothing when there is nothing to send. A reply names its author (`agent` attribute); for one
    that does not, a line `--- from: NAME` before it, or --from, says who sent it."""
    text = args.text if getattr(args, "text", None) is not None else _read_stdin_if_any(1.0)
    blocks = re.split(r"^--- from: *(\S+) *$", text, flags=re.M)
    items = [(args.sender, blocks[0])] + [(blocks[i], blocks[i + 1]) for i in range(1, len(blocks) - 1, 2)]
    n, notes, applied = 0, [], []
    err_agents = set()
    prose_only = bool(text.strip()) and not _REPLY_DOC_RE.search(text)
    with locked_state(args.state) as st:
        for sender, body in items:
            for m in _REPLY_DOC_RE.finditer(body):
                doc = m.group(0)
                doc = escape_bare_chars(doc)
                try:
                    root_ = ET.fromstring(doc)
                except ET.ParseError as exc:
                    notes.append(f"a document that is not well-formed XML was skipped ({exc}); the sender must answer "
                                 "again")
                    continue
                agent = root_.get("agent") or sender
                if agent in st["agents"] and root_.get("ack", root_.get("seq") if root_.tag == "concord-ack" else None):
                    # an agent says it processed our delivery number N (an ack, or any reply that carries `ack`)
                    try:
                        seq = int(root_.get("ack") or root_.get("seq"))
                        ak = st.setdefault("acked", {})
                        ak[agent] = max(ak.get(agent, 0), seq)
                    except (TypeError, ValueError):
                        pass
                if root_.tag == "concord-error":
                    # the agent could not read what it was sent: that delivery is not acknowledged, and it is sent again
                    if agent in st["agents"]:
                        err_agents.add(agent)
                        notes.append(f"{agent} could not read a message it was sent ({(root_.text or '').strip()[:160]}); "
                                     "it is sent again below")
                    continue
                if root_.tag == "concord-ack":
                    continue                                   # an ack is recorded, never relayed or counted as a reply
                if not agent:
                    notes.append("a reply that does not name its author and has no `--- from:` line was skipped")
                    continue
                if root_.get("state") and root_.get("state") != st.get("state_id"):
                    notes.append(f"from {agent}: a reply of another run (state {root_.get('state')}) was skipped")
                    continue
                try:
                    rr = parse_reply(doc)
                    if rr["kind"] == "candidate" and any(
                            c["proposer"] == agent and c["candidate"] == rr["candidate"] for c in st["pool"]):
                        continue                               # an exact repeat of a proposal already recorded: nothing to do
                    kept, _ = _apply_reply(st, agent, rr, False)
                    n += 1
                    if not (rr["kind"] == "scores" and kept and len(kept) == len(rr["scores"])):
                        named = _name_author(doc, agent)
                        applied.append((agent, named))     # a repeated rating adds nothing to relay
                        st.setdefault("docs", []).append([agent, named])
                except SystemExit:
                    notes.append(f"from {agent}: {_LAST_ERROR}")
        advance(st)
        action = current_action(st)
        phase = action["phase"]
        relay_enabled = st.get("relay", True)
        agents_all = list(st["agents"])
        all_docs = [tuple(d) for d in st.get("docs", [])]
        st_deliv = dict(st.get("deliv", {}))          # delivery numbers so far; updated below, saved after
        new_deliv = {}
        state_id_ = st.get("state_id", "")
        acked_ = dict(st.get("acked", {}))
        dcount_ = json.loads(json.dumps(st.get("dcount", {})))      # per agent: delivery number -> replies recorded then
        awaiting_ = list(action.get("recipients", []))
        # nothing came in: the run may be stuck. A finished run is never nudged: it only reports.
        if prose_only:
            notes.append("what you passed holds no CONCORD reply: it is prose (an agent answered in words instead of "
                         "sending the reply its script printed). Nothing was recorded, and the agents still owing a reply "
                         "are sent their request again below")
        nudge = ((not applied and not text.strip()) or prose_only or bool(err_agents)) and phase != "Complete"
        hold = phase in ("Score", "Repair") and n > 0 and not args.resend
        if hold:
            fresh, msgs = [], {}
        elif relay_enabled and phase in ("Score", "Repair"):
            fresh, msgs = [], {}                            # the agents know from the relay what they owe: no request
        elif phase == "Complete":
            fresh, msgs = [], {}                            # no commit: only the last replies are relayed
        else:
            # on a nudge the current request goes again to the agents still awaited: it may never have reached them
            fresh = _mark_issued_locked(st, action, args.resend or nudge)
            msgs = action["messages"]
    for note in notes:
        print("NOT RECORDED: " + note, file=sys.stderr)
    if notes:
        print("NEXT: Do not fix or rewrite a reply yourself. A reply that was not recorded is as if it had not "
              "arrived: the agent has to answer again. Send it the message it was asked again, exactly as printed "
              "(`handle` with no input prints it), and pass its new reply as received.", file=sys.stderr)
    awaiting = ", ".join(action.get("recipients", [])) or "-"
    summary = f"recorded {n}; phase {phase}; awaiting: {awaiting}"
    report = _final_report(load(args.state)) if phase == "Complete" else None
    # Relay: every reply recorded now is passed, as received and naming its author, to every other agent, so they all
    # build the same picture without any group communication. Together with the next message, if there is one.
    relay_on = relay_enabled
    carry = all_docs if nudge else applied              # a nudge re-sends everything recorded, whatever the relay setting
    per = {}
    new_dcount = {}
    # In relay mode a nudge goes only to the agents that owe a reply or have not acknowledged their latest delivery: the
    # others are up to date and have nothing to do. (Without acks, everyone gets it.)
    targets = set(agents_all)
    if nudge and relay_on:
        targets = set(awaiting_) | {a for a in agents_all if acked_.get(a, 0) < st_deliv.get(a, 0)}
    if err_agents:
        targets = set(err_agents)                         # only the agent that could not read it is sent it again
    for a in agents_all:
        if a not in targets:
            continue
        docs_a = carry
        if nudge and relay_on:
            # only what this agent has not acknowledged: the replies recorded since its last acknowledged delivery
            ak = acked_.get(a, 0)
            docs_a = all_docs[dcount_.get(a, {}).get(str(ak), 0) if ak else 0:]
        parts = [d for (x, d) in docs_a if x != a] if (relay_on or nudge) else []
        if a in fresh:
            parts.append(msgs.get(a) or next(iter(msgs.values())))
        # a nudge in Score or Repair may be a bare delivery: it only makes the agent check what is due
        if parts or (nudge and relay_on and phase in ("Score", "Repair")):
            if relay_on:
                # In relay mode every delivery carries a number, so the agent can acknowledge it (a reply carries it too)
                dv = st_deliv.get(a, 0) + 1
                new_deliv[a] = dv
                new_dcount[a] = len(all_docs)
                # a nudge carries all that is unacknowledged: it closes any gap. With nothing delivered yet there is
                # no gap, and the first message is no nudge
                full = ' full="1"' if nudge and st_deliv else ""
                # a nudge names the agents whose reply is still awaited: one that already answered sends it again
                # (it may have been lost, or refused for a change made on the way)
                aw = f' awaiting="{",".join(awaiting_)}"' if nudge and st_deliv and awaiting_ else ""
                parts.insert(0, f'<concord-delivery seq="{dv}" state="{state_id_}"{full}{aw}></concord-delivery>')
            per[a] = "\n\n".join(parts)
    if new_deliv:
        with locked_state(args.state) as st3:
            st3.setdefault("deliv", {}).update(new_deliv)
            for a_, n_ in new_dcount.items():
                st3.setdefault("dcount", {}).setdefault(a_, {})[str(new_deliv[a_])] = n_
    unacked = [a for a in agents_all if acked_.get(a, 0) < {**st_deliv, **new_deliv}.get(a, 0) and relay_on]
    summary += (f"; not yet acknowledged: {', '.join(unacked)}" if unacked else "")
    if not per:
        print("STATUS: NONE")
        print(_next_none(report=bool(report), hold=bool(hold), awaiting=awaiting))
        if report:
            print(report)
        elif hold:
            print(f"{summary}. Agents that hold every proposal rate on their own, and the one who must repair does "
                  "so on its own: send nothing yet. If nothing arrives, run `handle` again with no input to get the "
                  "message.")
        else:
            print(f"{summary}. Nothing new to send: wait for the replies still awaited.")
        return
    groups = _blocks(per)
    if report:
        print(report, file=sys.stderr)                  # the result, for the initiator to report
    print("STATUS: SEND")
    print(_next_send(phase, nudge=nudge and bool(st_deliv), complete=bool(report)))   # the first call is no nudge
    for text, who in groups.items():
        print(f"--- to: {', '.join(who)}")
        print(text)


def _progress(st):
    """A fingerprint of everything that moves a run forward: if it does not change, nothing happened."""
    return json.dumps([len(st.get("docs", [])), len(st["anchors"]), [c["id"] for c in st["pool"]],
                       sum(len(v) for v in st["raw"].values()), st.get("outcome"), st.get("pending"),
                       sorted((st.get("acked") or {}).items())])


def cmd_watchdog(args):
    """Block until the run has made no progress for --timeout seconds, then return what to do about it, and exit.

    Nothing arrives when a message is lost or an agent has stopped, so nothing wakes the initiator: this does.
    On a stall it returns the nudge (`handle` with no input: the current request again to the agents still awaited,
    and the replies the others may have missed). After --max-nudges nudges in a row with no progress it returns
    STATUS: ASK: a decision for the initiator. It returns at once, with the report, if the run is complete.
    Run it again after acting on what it printed."""
    import contextlib
    import io
    last_fp, since = None, time.time()
    while True:
        st = load(args.state)
        if st.get("outcome"):
            print("STATUS: NONE")
            print(_next_none(report=True))
            print(_final_report(st))
            return
        fp = _progress(st)
        if last_fp is None:
            last_fp = fp
            since = time.time()
        if fp != last_fp:
            last_fp, since = fp, time.time()
            with locked_state(args.state) as st2:
                st2["watch"] = {"fp": fp, "nudges": 0}
        elif time.time() - since >= args.timeout:
            with locked_state(args.state) as st2:
                w = st2.get("watch") or {}
                nudges = (w.get("nudges", 0) if w.get("fp") == fp else 0) + 1
                st2["watch"] = {"fp": fp, "nudges": nudges}
                awaiting = ", ".join(current_action(st2).get("recipients", [])) or "-"
                phase = current_action(st2)["phase"]
            if nudges > args.max_nudges:
                print("STATUS: ASK")
                print("NEXT: Decide as described below and act on it. Do not invent a step, and do not write anything "
                      "on an agent's behalf.")
                print(f"The run is stuck: no progress after {args.max_nudges} nudges (phase {phase}; still awaiting: "
                      f"{awaiting}). Decide: wait longer (run `watchdog` again); or, in the Score phase, give up on an "
                      "agent with `mark-missing <agent> <candidate-id>` for each candidate it owes (the run then can "
                      "only end BEST_EFFORT); or stop and report that the run could not be completed. An agent that "
                      "owes an anchor, a seed or a repair cannot be skipped.")
                return
            buf = io.StringIO()
            ns = argparse.Namespace(state=args.state, text="", sender=None, resend=False, format="text")
            with contextlib.redirect_stdout(buf):
                cmd_handle(ns)
            out = buf.getvalue()
            print(f"[watchdog: no progress for {args.timeout:g}s (phase {phase}, awaiting {awaiting}); "
                  f"nudge {nudges} of {args.max_nudges}]", file=sys.stderr)
            print(out, end="")
            return
        time.sleep(min(1.0, max(0.1, args.timeout / 10)))


def _name_author(doc, agent):
    """The reply as received, with its author named (added only if the reply does not already name it)."""
    m = re.match(r"<([A-Za-z0-9_-]+)([^>]*)>", doc)
    if m and 'agent="' not in m.group(2):
        return f'<{m.group(1)} agent="{agent}"{m.group(2)}>' + doc[m.end():]
    return doc


def _read_stdin_if_any(wait):
    import select
    if sys.stdin.isatty():
        return ""
    ready, _, _ = select.select([sys.stdin], [], [], wait)
    return sys.stdin.read() if ready else ""


def cmd_status(args):
    """Look at a run without changing it: no message is printed or marked as issued."""
    render_status(load(args.state), args.state, args.format, issue=False, show_messages=False)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _add_global_opts(parser):
    parser.add_argument("--state", default=argparse.SUPPRESS)
    parser.add_argument("--format", choices=["text", "json"],
                        default=argparse.SUPPRESS)


INIT_GUIDE = """\
How to fill the arguments:

  --agents                 the ids of the agents whose satisfaction counts, exactly as your tool for reaching each of
                           them names it. Use these same ids everywhere in this run.
  --satisfaction-threshold the standard the outcome genuinely has to meet, not a level you are sure is reachable (0.6 is
                           a starting point). If it sits below the best seed, the run ends with no negotiation at all.
  --x-reject / --x-ideal   the objective, never the means: the state the context is trying to produce, as a plain
                           before/after in its own numbers (for example error rate above 40%: ideal "error rate returns
                           to 0", reject "stays above 40% or rises"). Never a proposal, and no word about how it is
                           reached. If an anchor could pass as something an agent might propose, rewrite it one level up.
  --quiet                  always add it: init then prints only the state path and what to run next.

Pass every value as a plain argument (multi-line is fine). `init` mints the state path and prints it: use that exact
path on every later call, never one you made up.
"""


class _UsageError(Exception):
    """A command line the parser refused (an unknown command, a missing argument)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _hint_for(msg):
    """What to do about an error: always a way back onto the path, never just the complaint."""
    m = msg.lower()
    if "no state file" in m:
        return ("Use the exact state path `init` printed (its `State:` line) in --state. If this run has no state yet, "
                "run `init` first: `init --help` says how to fill each argument.")
    if "not in the negotiating set" in m or "unknown agent" in m:
        return "Use the agent ids exactly as you passed them to `init --agents`."
    if "reads as a description" in m or ("anchor" in m and "rejected" in m):
        return ("Rewrite --x-reject and --x-ideal one level up: the objective, never the means or a proposal. Then run "
                "`init` again (`init --help` has the rules).")
    if any(k in m for k in ("negotiating set needs", "duplicate agent", "satisfaction_threshold must", "eps must",
                            "b must be")):
        return "Fix that argument (`init --help` shows what each one means) and run `init` again."
    if any(k in m for k in ("--schema", "--context", "--x-reject", "--x-ideal", "--agents")) and "required" in m:
        return "Run `init --help` to see every argument and how to fill it, then run `init` again with all of them."
    if "already" in m and any(k in m for k in ("exists", "contributed", "anchored", "has a score")):
        return ("That agent has already answered: this is a repeat, and nothing is lost. Carry on: run `handle` with the "
                "next reply, or with no input if you are waiting.")
    if "was marked missing" in m:
        return "That agent was given up on, so its late score is not recorded. Carry on with `handle`."
    if "repair was issued to" in m:
        return "Only the agent asked for the repair counts. Ignore this reply and pass that agent's reply instead."
    if "does not match the expected" in m or "no candidate" in m:
        return ("The reply names the wrong candidate. Do not edit it: send that agent the message it was asked, again, "
                "and pass its new reply as received.")
    if "contains no concord xml" in m or "not valid xml" in m or "missing <" in m or "empty <candidate>" in m or \
            "no <" in m or "unrecognised reply root" in m or "no <score>" in m:
        return ("That reply is prose, incomplete or broken. Never repair it by hand, never lift numbers out of it, never "
                "write the XML yourself: ask that agent to answer again from the message it was sent, and pass its new "
                "reply to `handle` exactly as received.")
    if "usage" in m or "invalid choice" in m or "unrecognized arguments" in m or "required" in m:
        return ("You need two commands only: `init` once (see `init --help`), then `handle`: with no input for the first "
                "message, then with each reply on stdin. Use the state path `init` printed. `--help` lists the rest.")
    return ("Fix what the message says and run the same command again. Never work around it by writing a message, a "
            "rating or a reply yourself.")


def _report_error(message, hint=None):
    """Put an error on stdout in the same shape as every other output: a status, what happened, what to do."""
    print("STATUS: ERROR")
    print(str(message).strip())
    print("NEXT: " + (hint or _hint_for(str(message))))


def main():
    try:
        _main()
    except _UsageError as exc:
        _report_error(f"usage error: {exc}")
        sys.exit(2)
    except SystemExit as exc:
        if exc.code not in (0, None):
            _report_error(_LAST_ERROR or (exc.code if isinstance(exc.code, str) else "the command failed"))
        raise
    except Exception as exc:                                  # a bug or a damaged state file: never a bare traceback
        _report_error(f"the script failed unexpectedly: {type(exc).__name__}: {exc}",
                      "Run the same command once more. If it fails again, stop and report that the run could not be "
                      "completed, quoting this message. Do not write anything on an agent's behalf.")
        sys.exit(1)


def _main():
    ap = _Parser(
        prog="concord_initiator.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--format", choices=["text", "json"], default="text",
                    help="output format (default: text)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # init
    p = sub.add_parser("init", help="start a new run", formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog=INIT_GUIDE)
    _add_global_opts(p)
    p.add_argument("--agents", required=True,
                   help="comma-separated agent names, e.g. security,cost,latency")
    p.add_argument("--satisfaction-threshold", type=float, default=0.6,
                   dest="satisfaction_threshold")
    p.add_argument("--eps", type=float, default=0.05)
    p.add_argument("-B", "--B", "--budget", type=int, default=10, dest="B")
    p.add_argument("--schema", required=True, help="candidate format description (or @file)")
    p.add_argument("--context", required=True, help="problem statement (or @file)")
    p.add_argument("--x-reject", required=True, dest="x_reject",
                   help="reject anchor — outcome that fails all criteria (or @file)")
    p.add_argument("--x-ideal", required=True, dest="x_ideal",
                   help="ideal anchor — outcome that fully satisfies all criteria (or @file)")
    p.add_argument("--no-relay", action="store_true", dest="no_relay",
                   help="do not pass each reply on to the other agents (when they already see each other's replies)")
    p.add_argument("--quiet", action="store_true",
                   help="print only the state path and what to run next: the first message comes from `handle`")
    p.set_defaults(fn=cmd_init)

    # mark-missing
    p = sub.add_parser("mark-missing",
                       help="mark agent non-responsive on a candidate (forces BEST_EFFORT)")
    _add_global_opts(p)
    p.add_argument("agent", help="agent name")
    p.add_argument("candidate", help="candidate id the agent failed to score")
    p.set_defaults(fn=cmd_mark_missing)

    p = sub.add_parser("handle", help="pass what arrived (replies, or nothing); prints the next message to send, "
                                      "or nothing")
    _add_global_opts(p)
    p.add_argument("--from", dest="sender", default=None,
                   help="who sent it (only for a reply that does not name its author)")
    p.add_argument("--resend", action="store_true", help="print the pending message again")
    p.set_defaults(fn=cmd_handle)

    p = sub.add_parser("watchdog", help="wait for a stall; then print the nudge, or STATUS: ASK if nudges do not help")
    _add_global_opts(p)
    p.add_argument("--timeout", type=float, default=60, help="seconds without progress that count as a stall")
    p.add_argument("--max-nudges", type=int, default=3, dest="max_nudges",
                   help="nudges in a row without progress before it asks the initiator to decide")
    p.set_defaults(fn=cmd_watchdog)

    # status
    p = sub.add_parser("status", help="show the phase, who is awaited and who has acknowledged (changes nothing)")
    _add_global_opts(p)
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
