#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CONCORD documents: what the initiator sends, what an agent replies, and how each is read.

One vocabulary for both roles. The initiator builds requests (`build_*_msg`) and reads replies (`parse_reply`); an agent
reads requests (`parse_request`) and builds replies (`build_*_reply`). Everything here works on text and plain dicts: it
holds no process state and prints nothing.

A request is built from the run state `st`: the engine state (see `ioc_concord.engine.new_engine_state`) plus the brief
`x_reject`, `x_ideal`, `context`, `schema` (the format as the agents read it), `schema_json`, `options` (a closed list or
None) and `state_id`.
"""
import json
import re
import xml.etree.ElementTree as ET

from ioc_concord.engine import TOL, absent, column, score
from ioc_concord.errors import ReplyError
from ioc_concord.xmlutil import DOC_RE, REPLY_RE, esc, escape_bare_chars, text_element

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------

ANCHOR, SEED, SCORE, REPAIR, COMMIT = ("concord-anchor", "concord-propose-seed", "concord-score", "concord-repair",
                                       "concord-commit")
REQUEST_TAGS = (ANCHOR, SEED, SCORE, REPAIR, COMMIT)
KIND_OF_REQUEST = {ANCHOR: "anchor", SEED: "seed", SCORE: "score", REPAIR: "repair", COMMIT: "commit"}

ANCHOR_REPLY, CANDIDATE_REPLY, SCORE_REPLY = "concord-anchor-reply", "concord-candidate-reply", "concord-score-reply"
REPLY_TAGS = (ANCHOR_REPLY, CANDIDATE_REPLY, SCORE_REPLY)

DELIVERY, MISSING, ACK, ERROR = "concord-delivery", "concord-missing", "concord-ack", "concord-error"

DEFAULT_PARAMS = {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 8}

# --------------------------------------------------------------------------
# requests: what the initiator sends
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
        "and reasoning, then propose ONE candidate in the candidate_schema format that you would rate close to "
        "your x_ideal and that does not push other agents below the threshold. It may be a new candidate, or one "
        "already in the run that you now rate differently because of a specific fact another agent shared: say which "
        "fact and from whom. Your rationale must answer the strongest reasoning against your position. Rate your own "
        "candidate in the same reply. "
        "Your answer to this message is only the reply the script tells you to send, with nothing before or after it. Pass this message to concord_receiver.py handle and do what it says; never write the reply yourself."
    ),
}


CLOSED_NOTE = ("The candidate must be exactly one of the listed options: detail about it goes in the rationale, and "
               "added wording does not make a different option. ")


FORMAT_NOTE = ("The candidate must satisfy the candidate_schema exactly: detail about it goes in the rationale. ")


def instructions_line(kind, st=None):
    text = INSTRUCTIONS[kind]
    note = ""
    if st and kind in ("seed", "repair"):
        spec = json.loads(st.get("schema_json") or "{}")
        note = CLOSED_NOTE if st.get("options") else (
            FORMAT_NOTE if {"pattern", "minLength", "maxLength"} & set(spec) else "")
    if note:
        head, sep, tail = text.partition("Your answer to this message")
        text = head + note + sep + tail
    return f"  <instructions>{esc(text)}</instructions>"


def schema_lines(st):
    """The enforced part of the schema, as the receivers read it: the schema itself as JSON (an enum is the closed list)."""
    lines = []
    if st.get("schema_json"):
        lines.append(f"  <schema_json>{esc(st['schema_json'])}</schema_json>")
    return lines


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
        *schema_lines(st),
        f"  <satisfaction_threshold>{esc(st['params']['satisfaction_threshold'])}</satisfaction_threshold>",
        f"  <context>{esc(st['context'])}</context>",
    ]
    lines += _routing_lines(st, addressed)
    lines.append(instructions_line("seed", st))
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
        *schema_lines(st),
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
    lines += ["    </scores>", "  </incumbent>", *_routing_lines(st, addressed=[agent]), instructions_line("repair", st), "</concord-repair>"]
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
# reading a reply  (the initiator)
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:xml)?\s*\n(.*?)\n```", re.DOTALL)


def locate_reply_xml(text):
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
    for pattern in (REPLY_RE, DOC_RE):
        m = pattern.search(stripped)
        if m:
            return m.group(0)
    if "<concord-" not in stripped:
        raise ReplyError("reply contains no CONCORD XML — the agent replied in prose, so nothing was recorded. "
            "That prose is not a reply: do not pull numbers or a candidate out of it and do not write the XML "
            "yourself.")
    return stripped


def parse_reply(text):
    stripped = escape_bare_chars(locate_reply_xml(text))
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise ReplyError(f"reply is not valid XML: {exc}\n"
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
            raise ReplyError("anchor reply missing <raw_reject> or <raw_ideal>")
        return {"kind": "anchor",
                "raw_reject": float(rej_el.text.strip()),
                "raw_ideal": float(ide_el.text.strip())}
    if tag == "concord-candidate-reply":
        cand_el = root.find("candidate")
        if cand_el is None:
            raise ReplyError("candidate reply missing <candidate>")
        if not (cand_el.text or "").strip():
            # An empty candidate would enter the pool and could even win, leaving a certificate with no text.
            raise ReplyError("candidate reply has an empty <candidate> — the candidate must be the proposal itself, "
                "in the candidate_schema format")
        rat_el = root.find("rationale")
        if rat_el is None:
            raise ReplyError("candidate reply missing <rationale>")
        return {"kind": "candidate",
                "candidate": (cand_el.text or "").strip(),
                "rationale": (rat_el.text or "").strip(),
                "id": root.get("id")}
    if tag == "concord-score-reply":
        scores = []
        for sc in root.findall("score"):
            cid = sc.get("candidate_id")
            if not cid:
                raise ReplyError("a <score> element is missing candidate_id attribute")
            raw_el = sc.find("raw")
            if raw_el is None:
                raise ReplyError(f"no <raw> found for candidate_id '{cid}'")
            reas_el = sc.find("reasoning")
            if reas_el is None:
                raise ReplyError(f"no <reasoning> found for candidate_id '{cid}'")
            scores.append({
                "id": cid,
                "raw": float(raw_el.text.strip()),
                "reasoning": (reas_el.text or "").strip(),
            })
        if not scores:
            raise ReplyError("score reply contains no <score> elements")
        return {"kind": "scores", "scores": scores}
    raise ReplyError(f"unrecognised reply root tag <{tag}>; expected one of: "
        "concord-anchor-reply, concord-candidate-reply, concord-score-reply")


# --------------------------------------------------------------------------
# replies: what an agent sends back
# --------------------------------------------------------------------------

def _reply(root, agent, state, ack):
    """The reply as text. It names its author, the run it belongs to and the last delivery it processes (a reply doubles
    as an acknowledgement), so whoever reads it knows where it comes from."""
    if agent:
        root.set("agent", agent)
    if state:
        root.set("state", state)
    if ack:
        root.set("ack", str(ack))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def build_anchor_reply(raw_reject, raw_ideal, note=None, agent=None, state=None, ack=None):
    """<concord-anchor-reply>: how satisfied the agent would be with each anchor outcome, on its own scale."""
    # `not (a > b)` rather than `a <= b`, so a NaN is refused too.
    if not (raw_ideal > raw_reject):
        raise ReplyError(
            f"raw_ideal ({raw_ideal:g}) must be strictly greater than raw_reject ({raw_reject:g}). These are "
            "satisfaction ratings of the two outcomes (higher = better for your role), not measurements of the current "
            "state or metrics quoted in the anchors: re-rate the two outcomes, then answer again.")
    root = ET.Element(ANCHOR_REPLY)
    text_element(root, "raw_reject", str(raw_reject))
    text_element(root, "raw_ideal", str(raw_ideal))
    if note:
        text_element(root, "note", note)
    return _reply(root, agent, state, ack)


def build_candidate_reply(candidate, rationale, cid=None, agent=None, state=None, ack=None):
    """<concord-candidate-reply>: a proposal (a seed or a repair) with the reasons for it."""
    root = ET.Element(CANDIDATE_REPLY)
    if cid:
        root.set("id", cid)
    text_element(root, "candidate", candidate)
    text_element(root, "rationale", rationale)
    return _reply(root, agent, state, ack)


def build_score_reply(scores, agent=None, state=None, ack=None):
    """<concord-score-reply>: `scores` is [(candidate_id, raw, reasoning)]."""
    root = ET.Element(SCORE_REPLY)
    for cid, raw, reasoning in scores:
        sc = ET.SubElement(root, "score", attrib={"candidate_id": cid})
        text_element(sc, "raw", str(raw))
        text_element(sc, "reasoning", reasoning)
    return _reply(root, agent, state, ack)


def build_ack(agent, state, seq):
    """<concord-ack>: the agent has processed delivery number `seq`."""
    return f'<{ACK} agent="{esc(agent)}" state="{esc(state)}" seq="{esc(seq)}"></{ACK}>'


def build_error(agent, state, seq, text):
    """<concord-error>: the agent could not read delivery `seq`; `text` says what to do about it."""
    return f'<{ERROR} agent="{esc(agent)}" state="{esc(state)}" seq="{esc(seq)}">{esc(text)}</{ERROR}>'


# --------------------------------------------------------------------------
# notices from the initiator
# --------------------------------------------------------------------------

def build_missing(agent, candidate, state):
    """<concord-missing>: `agent` never rated `candidate`; every participant records it as missing."""
    return f'<{MISSING} agent="{esc(agent)}" candidate="{esc(candidate)}" state="{esc(state)}"></{MISSING}>'


def build_delivery_tag(seq, state, check=None, full=False, awaiting=()):
    """<concord-delivery>: numbers a delivery so the agent can acknowledge it. `check` fingerprints the replies it carries
    (see `ioc_concord.engine.reply_check`), `full` says it holds everything unacknowledged, and `awaiting` names the agents
    whose reply is still due."""
    ck = f' check="{check}"' if check else ""
    fl = ' full="1"' if full else ""
    aw = f' awaiting="{",".join(awaiting)}"' if awaiting else ""
    return f'<{DELIVERY} seq="{seq}" state="{state}"{ck}{fl}{aw}></{DELIVERY}>'


def parse_delivery(root):
    """{"seq", "full", "awaiting"} of a <concord-delivery> element, or None when its number is not a number."""
    try:
        seq = int(root.get("seq") or 0)
    except ValueError:
        return None
    return {"seq": seq, "full": bool(root.get("full")),
            "awaiting": [x.strip() for x in (root.get("awaiting") or "").split(",")]}


# --------------------------------------------------------------------------
# reading a request
# --------------------------------------------------------------------------

def _agents_of(root, tag):
    box = root.find(tag)
    return [a.text.strip() for a in box.findall("agent")] if box is not None else []


def parse_request(root):
    """The fields of a request element (anchor, seed, score, repair or commit), or None for any other element.

    kind          "anchor", "seed", "score", "repair" or "commit"
    participants  the agents of the run, or None when the request does not list them
    params        satisfaction_threshold, eps and B (DEFAULT_PARAMS where the request is silent)
    addressed_to  the agents asked to reply
    brief         what the request says about the task, only the fields it carries: x_reject, x_ideal, candidate_schema, context
    schema_spec   the schema as a dict, and options: the closed list, if it has one (None when the request has none)
    new_candidate_id  the id a repair must give its candidate
    candidates    [(id, text, rationale)] a score request asks to rate
    incumbent     (id, text, rationale) of a repair request
    outcome, winning_candidate_id   what a commit announces
    """
    if root.tag not in REQUEST_TAGS:
        return None

    def text(tag):
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    def candidate(el):
        t, r = el.find("text"), el.find("rationale")
        return (el.get("id"), (t.text or "").strip() if t is not None else None,
                (r.text or "").strip() if r is not None and r.text else None)

    p = root.find("params")

    def num(tag, default):
        el = p.find(tag) if p is not None else None
        if el is None or not el.text:
            return default
        try:
            return float(el.text)
        except ValueError:
            raise ReplyError(f"<{tag}> in <{root.tag}> is not a number: {el.text.strip()!r}")

    spec, options = None, None
    js = root.find("schema_json")
    if js is not None and js.text:
        try:
            loaded = json.loads(js.text)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            spec = loaded
            options = list(loaded["enum"]) if "enum" in loaded else None
    nid, inc = root.find("new_candidate_id"), root.find("incumbent")
    return {
        "kind": KIND_OF_REQUEST[root.tag],
        "participants": _agents_of(root, "participants") if root.find("participants") is not None else None,
        "params": {"satisfaction_threshold": num("satisfaction_threshold", DEFAULT_PARAMS["satisfaction_threshold"]),
                   "eps": num("eps", DEFAULT_PARAMS["eps"]), "B": int(num("budget", DEFAULT_PARAMS["B"]))},
        "addressed_to": _agents_of(root, "addressed_to"),
        "brief": {t: text(t) for t in ("x_reject", "x_ideal", "candidate_schema", "context") if text(t)},
        "schema_spec": spec,
        "options": options,
        "new_candidate_id": None if nid is None else (nid.text or "").strip(),
        "candidates": [candidate(c) for c in root.findall("candidates/candidate")],
        "incumbent": candidate(inc) if inc is not None and inc.get("id") else None,
        "outcome": text("outcome"),
        "winning_candidate_id": text("winning_candidate_id"),
    }
