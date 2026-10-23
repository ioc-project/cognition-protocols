#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""CONCORD — self-driving protocol initiator.

init        → starts the run and outputs the first set of messages to send
record-reply → records one agent reply; auto-advances to the next phase when
               all expected replies are in
mark-missing → marks an agent non-responsive on a candidate (any run with a
               MISSING entry can only terminate BEST_EFFORT)
status      → re-prints the current phase status at any time

All state lives in one JSON file. `init` always mints its own unique path and
reports it back; every later command must be given that exact path via --state.
Set CONCORD_RUN_KEY to make that suffix deterministic instead of random
(see `unique_state_path`) — needed for LLM response caching across a
record/replay pair. Stdlib only; no network, no dependencies.

Any text argument may be given as @path to read it from a file.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

TOL = 1e-9
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
    ids = scored_ids(st)
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
# reply, so the message is self-contained: the orchestrator can forward it
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
        "Reply with the unchanged output of: concord_receiver.py forge anchor --raw-reject N --raw-ideal N"
    ),
    "seed": (
        "Propose the single candidate that best satisfies your own role, in the candidate_schema format. "
        "Reply with the unchanged output of: concord_receiver.py forge seed --candidate TEXT --rationale TEXT "
        "(always forge seed for this message, never forge score)."
    ),
    "score": (
        "Rate every candidate on your own raw 0-100 scale, the same scale you used for your anchor. "
        "Copy each candidate id exactly as given, one entry per candidate, in the order listed, with your full reasoning for each. "
        "Reply with the unchanged output of: concord_receiver.py forge score "
        "--candidate-id ID --raw N --reasoning TEXT (repeat the triplet for each candidate)."
    ),
    "repair": (
        "You are the least-satisfied agent on the incumbent. Read the incumbent, its rationale and every agent's score and reasoning, "
        "then propose ONE new candidate in the candidate_schema format that you would rate close to your x_ideal and that does not push "
        "other agents below the threshold. Your rationale must answer the strongest reasoning against your position. "
        "Reply with the unchanged output of: concord_receiver.py forge repair --candidate TEXT --rationale TEXT"
    ),
}


def instructions_line(kind):
    return f"  <instructions>{esc(INSTRUCTIONS[kind])}</instructions>"


# --------------------------------------------------------------------------
# XML message builders
# --------------------------------------------------------------------------

def build_anchor_msg(st):
    p = st["params"]
    return "\n".join([
        "<concord-anchor>",
        f"  <satisfaction_threshold>{esc(p['satisfaction_threshold'])}</satisfaction_threshold>",
        f"  <x_reject>{esc(st['x_reject'])}</x_reject>",
        f"  <x_ideal>{esc(st['x_ideal'])}</x_ideal>",
        instructions_line("anchor"),
        "</concord-anchor>",
    ])


def build_seed_msg(st):
    lines = [
        "<concord-propose-seed>",
        f"  <candidate_schema>{esc(st['schema'])}</candidate_schema>",
        f"  <satisfaction_threshold>{esc(st['params']['satisfaction_threshold'])}</satisfaction_threshold>",
        f"  <context>{esc(st['context'])}</context>",
    ]
    lines.append(instructions_line("seed"))
    lines.append("</concord-propose-seed>")
    return "\n".join(lines)


def build_score_msg(st):
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
    lines += ["  </candidates>", instructions_line("score"), "</concord-score>"]
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
    lines += ["    </scores>", "  </incumbent>", instructions_line("repair"), "</concord-repair>"]
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
    lines.append("</concord-commit>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# decision engine
# --------------------------------------------------------------------------

def advance(st):
    """Run SELECT/ACCEPT/REPAIR/COMMIT when all required data is in.

    Called after every state mutation. Modifies st in-place. After this
    returns, current_action(st) always resolves to a clean phase — there is
    no 'scoring complete but decision pending' intermediate state.
    """
    if st.get("outcome"):
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
    missing_anchor = [a for a in agents if a not in st.get("anchors", {})]
    if missing_anchor:
        msg = build_anchor_msg(st)
        return {
            "phase": "Anchor",
            "recipients": missing_anchor,
            "messages": {a: msg for a in missing_anchor},
        }

    # Awaiting seed proposals
    seeded = {e["proposer"] for e in st["pool"] if e.get("round", -1) == 0}
    missing_seed = [a for a in agents if a not in seeded]
    if missing_seed:
        msg = build_seed_msg(st)
        return {
            "phase": "Seed",
            "recipients": missing_seed,
            "messages": {a: msg for a in missing_seed},
        }

    # Awaiting scores
    pool_ids = [c["id"] for c in st["pool"]]
    owed = {}
    for a in agents:
        gaps = [cid for cid in pool_ids if cid not in st["raw"].get(a, {})]
        if gaps:
            owed[a] = gaps
    if owed:
        msg = build_score_msg(st)
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
_BARE_LT_RE = re.compile(r"<(?![a-zA-Z/?!])")
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")


def _escape_bare_chars(xml_text):
    """Escape what an agent un-escaped while copying: a `<` that does not start a tag and an `&` that is not part of an
    entity. `concord_receiver.py forge` prints `&lt;` and `&amp;`, but models often turn them back into `<` and `&`.
    Done on every reply, so the stored text is always well-formed; the meaning of the text is unchanged."""
    return _BARE_LT_RE.sub("&lt;", _BARE_AMP_RE.sub("&amp;", xml_text))


# A complete <concord-...-reply> element anywhere in the text (a reply embedded in prose, unfenced).
_EMBEDDED_REPLY_RE = re.compile(r"<(concord-[a-z-]+-reply)\b[^>]*>.*?</\1\s*>", re.DOTALL)
# Any complete <concord-...> element: last resort, so an invented reply tag still gets a precise error.
_EMBEDDED_ANY_RE = re.compile(r"<(concord-[a-z-]+)\b[^>]*>.*?</\1\s*>", re.DOTALL)


def _locate_reply_xml(text):
    """Return the XML document that holds the agent's reply, taken unchanged from `text`.

    The agent's whole message may be handed over: the reply can sit inside a markdown fence or inside surrounding
    prose, and is extracted here, deterministically, instead of by the orchestrator. The extracted text is the
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
    stripped = _escape_bare_chars(_locate_reply_xml(text))
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
                "rationale": (rat_el.text or "").strip()}
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


def render_status(st, state_path, fmt):
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

    state_path = unique_state_path(args.state)
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
        print()
        render_status(st, state_path, args.format)


def reject_hint(state_path, agent, err):
    """Text printed after a rejected record-reply: what to do next, and the exact message to re-send.

    A rejected reply leaves the state untouched. The orchestrator's only legitimate move is to ask the
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


def cmd_record_reply(args):
    try:
        _record_reply(args)
    except SystemExit as e:
        if e.code not in (0, None):
            sys.stderr.write(reject_hint(args.state, args.agent, _LAST_ERROR) + "\n")
        raise


def _record_reply(args):
    state_path = args.state
    text = args.text if args.text is not None else sys.stdin.read()
    overwrite = getattr(args, "overwrite", False)
    agent = args.agent

    r = parse_reply(text)  # parse before acquiring lock — pure computation

    with locked_state(state_path) as st:
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
            known = {c["id"] for c in st["pool"]}
            for sc in r["scores"]:
                if sc["id"] not in known:
                    die(f"reply scores unknown candidate '{sc['id']}' — "
                        f"the agent altered or invented an id; re-send the SCORE message")
            if agent not in st["anchors"]:
                die(f"{agent} has no anchors yet — send the anchor message first")
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
                    kept.append(cid)          # already rated in an earlier round: the rating on record stays
                else:
                    fresh.append(sc)
            if not fresh:
                todo = [c["id"] for c in st["pool"] if c["id"] not in on_record]
                die(f"{agent} already rated every candidate in this reply ({', '.join(kept)}), so there is nothing new to record. "
                    f"Candidates {agent} still has to rate: {', '.join(todo) or 'none'}. Use --overwrite only to correct a transcription error")
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

        advance(st)

    if r["kind"] == "scores" and kept:
        print(f"Note: {agent} also sent ratings for {', '.join(kept)}, which it had already rated. The ratings on record "
              f"were kept; only the new ones were recorded.")
        print()
    render_status(st, state_path, args.format)


def cmd_mark_missing(args):
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
        advance(st)

    if args.format != "json":
        print(f"Note: {agent} marked MISSING on {cid}. "
              f"This run can now only terminate as BEST_EFFORT.")
        print()

    render_status(st, state_path, args.format)


def cmd_status(args):
    st = load(args.state)
    render_status(st, args.state, args.format)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _add_global_opts(parser):
    parser.add_argument("--state", default=argparse.SUPPRESS)
    parser.add_argument("--format", choices=["text", "json"],
                        default=argparse.SUPPRESS)


def main():
    ap = argparse.ArgumentParser(
        prog="concord_initiator.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--format", choices=["text", "json"], default="text",
                    help="output format (default: text)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # init
    p = sub.add_parser("init", help="start a new run")
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
    p.set_defaults(fn=cmd_init)

    # record-reply
    p = sub.add_parser("record-reply",
                       help="record a sub-agent reply; auto-advances protocol")
    _add_global_opts(p)
    p.add_argument("agent", help="agent name")
    p.add_argument("text", nargs="?",
                   help="reply XML (reads stdin if omitted)")
    p.add_argument("--overwrite", action="store_true",
                   help="allow re-recording an existing anchor or score")
    p.set_defaults(fn=cmd_record_reply)

    # mark-missing
    p = sub.add_parser("mark-missing",
                       help="mark agent non-responsive on a candidate (forces BEST_EFFORT)")
    _add_global_opts(p)
    p.add_argument("agent", help="agent name")
    p.add_argument("candidate", help="candidate id the agent failed to score")
    p.set_defaults(fn=cmd_mark_missing)

    # status
    p = sub.add_parser("status",
                       help="show current phase, messages to send, and awaiting replies")
    _add_global_opts(p)
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
