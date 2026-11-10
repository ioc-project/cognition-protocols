#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CONCORD messages: build every one, and read every one.

Everything here works on text and plain dicts: it holds no process state, no clock and prints nothing.

A builder is the author's side: it validates what the spec makes the author responsible for (see Invalid messages), then
returns the message as text, with its `check` where it carries one. The text is the stored original: it is sent as it is
and never written again.

A reader is the receiver's side, in the layers of the Order of checks, so a caller can stop at the first that rejects:

    locate(text)         the one document in `text`, taken unchanged
    peek(doc)            the root attributes, without validating anything: enough to know the run, the sender, the `uuid`,
                         the `version`, and whom to ask when the document is damaged
    check_ok(doc)        the integrity check
    parse_message(doc)   the whole message, validated; raises InvalidMessage with the reason that goes into an
                         `<concord-error reason="invalid">`

The run-dependent rules are the caller's: whether the repair round `N` is the next one (a receiver with an incomplete copy
of the run state first asks for what it lacks), and who the participants are. `parse_message` takes the candidate schema of
the run, when the caller has it, and then also checks the candidates against it.
"""
import json
import re
import uuid
import xml.etree.ElementTree as ET

from ioc_concord.engine import check_params, parse_candidate_id
from ioc_concord.errors import ConcordError, InvalidMessage
from ioc_concord.protocol import (DEFAULT_PARAMS, MILLION, PROTOCOL_VERSION, RESULT_OUTCOMES, format_number, is_agent_id,
                                  is_uuid, parse_number, to_millionths)
from ioc_concord.schema import check_candidate, validate_schema
from ioc_concord.xmlutil import check_of, esc_attr, esc_text, verify_check, with_check

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------

INIT, INIT_REPLY, REPAIR, SCORE, RESULT = ("concord-init", "concord-init-reply", "concord-repair", "concord-score",
                                           "concord-result")
ERROR, RESEND, CANCEL = "concord-error", "concord-resend", "concord-cancel"

# The messages a run is made of, and the ones that carry a `check` (all of them): the others are control messages.
DATA_TAGS = (INIT, INIT_REPLY, REPAIR, SCORE, RESULT)
CONTROL_TAGS = (ERROR, RESEND, CANCEL)
KIND_OF_TAG = {INIT: "init", INIT_REPLY: "init-reply", REPAIR: "repair", SCORE: "score", RESULT: "result", ERROR: "error",
               RESEND: "resend", CANCEL: "cancel"}

ERROR_REASONS = ("missing", "check", "invalid", "mismatch", "busy", "version")
ERROR_WHAT = ("init", "init-reply", "score", "repair", "result")
CANCEL_REASONS = ("superseded", "version", "missing-agent")

_INIT_PARAMS = {"satisfaction_threshold": "satisfaction_threshold", "eps": "eps", "B": "budget", "timeout": "timeout",
                "retries": "retries"}

_INTEGER_RE = re.compile(r"[0-9]+")
_FORBIDDEN_RE = re.compile(r"<!|<\?")
_OPEN_RE = re.compile(r"<(concord-[a-z-]+)\b")
_FENCE_RE = re.compile(r"```(?:xml)?\s*\n(.*?)\n```", re.DOTALL)
_ROOT_TAG_RE = re.compile(r"<[^>]*>")
_ATTR_RE = re.compile(r'([A-Za-z_][\w.:-]*)\s*=\s*"([^"]*)"')


def new_uuid():
    """A new UUID, as text: the id of a run or of one transmission."""
    return str(uuid.uuid4())


def _unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def _score(value, what):
    """`value` (the text of a number, an int or a float) in millionths, as a raw score or an anchor: 0 to 100 in the form of the
    spec. Raises InvalidMessage."""
    m = to_millionths(value)
    if m is None or m > 100 * MILLION:
        raise InvalidMessage(f"{what} must be a number from 0 to 100 with at most 6 decimal places, not {value!r}")
    return m


def _need_agent(value, what):
    if not is_agent_id(value):
        raise InvalidMessage(f"{what} must be an agent id (1 to 64 letters, digits, _ or -), not {value!r}")
    return value


def _head(tag, from_, run_id, uid, more=()):
    _need_agent(from_, "from")
    if uid is not None:
        _need_uuid(uid, "uuid")
    attrs = [("version", PROTOCOL_VERSION), ("from", from_), ("run_id", run_id), ("uuid", uid or new_uuid()), *more]
    return f"<{tag}" + "".join(f' {k}="{esc_attr(v)}"' for k, v in attrs if v is not None)


def _need(value, what):
    if not str(value if value is not None else "").strip():
        raise InvalidMessage(f"{what} is required and cannot be empty")
    return str(value).strip()


def _need_uuid(value, what):
    if not is_uuid(value):
        raise InvalidMessage(f"{what} must be a UUID, not {value!r}")
    return value


def _param(key, value):
    """The value of the init parameter `key` as the node holds it: τ and eps in millionths, the others as integers."""
    if key in ("satisfaction_threshold", "eps"):
        m = to_millionths(value)
        if m is None:
            raise InvalidMessage(f"{key} must be a number with at most 6 decimal places, not {value!r}")
        return m
    return value


def _param_text(key, value):
    return format_number(value) if key in ("satisfaction_threshold", "eps") else str(value)


def _problem(fn, *args):
    """Run a validator of another module and make its refusal an InvalidMessage."""
    try:
        return fn(*args)
    except ConcordError as exc:
        if isinstance(exc, InvalidMessage):
            raise
        raise InvalidMessage(str(exc))


def build_init(from_, participants, context, x_reject, x_ideal, spec, params=None, run_id=None, uid=None):
    """<concord-init>: the initiator `from_` opens a run. `spec` is the candidate schema as a dict. `params` holds any of
    satisfaction_threshold, eps (a number as text, int or float), B, timeout, retries (DEFAULT_PARAMS fill the rest; a `timeout`
    of 0 switches the timers off, an extension of this library that is not part of the spec). A fresh run id is made when none is given. Raises InvalidMessage when a rule of the configuration is broken."""
    _need_agent(from_, "the initiator id")
    unknown = sorted(set(params or {}) - set(_INIT_PARAMS))
    if unknown:
        raise InvalidMessage(f"unknown parameters: {', '.join(unknown)}. Known: {', '.join(_INIT_PARAMS)}")
    full = {**DEFAULT_PARAMS, **{k: _param(k, v) for k, v in (params or {}).items()}}
    _problem(check_params, list(participants), full)
    _need(context, "context")
    _need(x_reject, "x_reject")
    _need(x_ideal, "x_ideal")
    spec = _problem(validate_schema, spec)
    lines = [_head(INIT, from_, _need_uuid(run_id or new_uuid(), "run_id"), uid) + ">", "  <participants>"]
    lines += [f"    <agent>{esc_text(a)}</agent>" for a in participants]
    lines += ["  </participants>", "  <params>"]
    lines += [f"    <{tag}>{esc_text(_param_text(key, full[key]))}</{tag}>" for key, tag in _INIT_PARAMS.items()]
    lines += ["  </params>", f"  <context>{esc_text(context)}</context>", "  <anchors>",
              f"    <x_reject>{esc_text(x_reject)}</x_reject>", f"    <x_ideal>{esc_text(x_ideal)}</x_ideal>", "  </anchors>",
              "  <proposal>",
              f"    <candidate_schema>{esc_text(json.dumps(spec, ensure_ascii=False, sort_keys=True))}</candidate_schema>",
              "  </proposal>", f"</{INIT}>"]
    return with_check("\n".join(lines))


def _proposal_lines(pid, from_, candidate, rationale, raw, reasoning, spec):
    parsed = parse_candidate_id(pid)
    if parsed is None or parsed[1] != from_:
        raise InvalidMessage(f"the proposal id {pid!r} is not seed-{from_} or r<N>-{from_}")
    candidate = _need(candidate, "the candidate")
    if spec is not None:
        why = check_candidate(spec, candidate)
        if why:
            raise InvalidMessage(f"the candidate {why}")
    raw = _score(raw, "raw")
    return [f'  <proposal id="{esc_attr(pid)}">', f"    <candidate>{esc_text(candidate)}</candidate>",
            f"    <rationale>{esc_text(_need(rationale, 'the rationale'))}</rationale>", "    <score>",
            f"      <raw>{format_number(raw)}</raw>", f"      <reasoning>{esc_text(_need(reasoning, 'the reasoning'))}</reasoning>",
            "    </score>", "  </proposal>"]


def build_init_reply(from_, run_id, raw_reject, raw_ideal, candidate, rationale, raw, reasoning, spec=None, uid=None):
    """<concord-init-reply>: the anchors, the seed `seed-<from_>` and the agent's own score for it. With the candidate
    schema `spec` of the run, the candidate is checked against it. The scores are numbers as text, int or float. Raises
    InvalidMessage for anything the spec makes invalid: a value outside 0 to 100 or in another form, `raw_ideal` not above
    `raw_reject`, a candidate that breaks the schema."""
    raw_reject, raw_ideal = _score(raw_reject, "raw_reject"), _score(raw_ideal, "raw_ideal")
    if not raw_ideal > raw_reject:
        raise InvalidMessage(f"raw_ideal ({format_number(raw_ideal)}) must be strictly greater than raw_reject "
                             f"({format_number(raw_reject)}). These "
                             "are satisfaction ratings of the two outcomes (higher = better for your role), not "
                             "measurements of the current state: re-rate the two outcomes, then answer again.")
    lines = [_head(INIT_REPLY, from_, _need_uuid(run_id, "run_id"), uid) + ">", "  <anchors>",
             f"    <raw_reject>{format_number(raw_reject)}</raw_reject>", f"    <raw_ideal>{format_number(raw_ideal)}</raw_ideal>",
             "  </anchors>"]
    lines += _proposal_lines(f"seed-{from_}", from_, candidate, rationale, raw, reasoning, spec)
    return with_check("\n".join(lines + [f"</{INIT_REPLY}>"]))


def build_repair(from_, run_id, n, candidate, rationale, raw, reasoning, spec=None, uid=None):
    """<concord-repair>: the proposal `r<n>-<from_>` and its proposer's own score. That `n` is the next round is the
    caller's to see (`engine.next_round`)."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise InvalidMessage(f"the repair round must be an integer, at least 1, not {n!r}")
    lines = [_head(REPAIR, from_, _need_uuid(run_id, "run_id"), uid) + ">"]
    lines += _proposal_lines(f"r{n}-{from_}", from_, candidate, rationale, raw, reasoning, spec)
    return with_check("\n".join(lines + [f"</{REPAIR}>"]))


def build_score(from_, run_id, scores, uid=None):
    """<concord-score>: `scores` is [(candidate id, raw, reasoning)], the candidates of the others that the sender has not rated.
    Invalid when empty, when a candidate is rated twice or is one the sender proposed."""
    if not scores:
        raise InvalidMessage("a score message must rate at least one candidate")
    lines, seen = [_head(SCORE, from_, _need_uuid(run_id, "run_id"), uid) + ">"], set()
    for cid, raw, reasoning in scores:
        parsed = parse_candidate_id(cid)
        if parsed is None:
            raise InvalidMessage(f"{cid!r} is not a candidate id")
        if parsed[1] == from_:
            raise InvalidMessage(f"{from_} rates {cid}, which it proposed: its score is in the proposal")
        if cid in seen:
            raise InvalidMessage(f"{cid} is rated twice")
        seen.add(cid)
        raw = _score(raw, f"the score for {cid}")
        lines += [f'  <score candidate_id="{esc_attr(cid)}">', f"    <raw>{format_number(raw)}</raw>",
                  f"    <reasoning>{esc_text(_need(reasoning, 'the reasoning for ' + cid))}</reasoning>", "  </score>"]
    return with_check("\n".join(lines + [f"</{SCORE}>"]))


def build_result(from_, run_id, outcome, winning_candidate_id, uid=None):
    """<concord-result>: the outcome of the reporter, SUCCESS or BEST_EFFORT, with the winning candidate. An aborted run sends
    no result. That the sender is the reporter and the winner a candidate of the run is the caller's to see."""
    if outcome not in RESULT_OUTCOMES:
        raise InvalidMessage(f"outcome must be one of {', '.join(RESULT_OUTCOMES)}, not {outcome!r}")
    if parse_candidate_id(winning_candidate_id) is None:
        raise InvalidMessage(f"a {outcome} result needs the id of the winning candidate, not {winning_candidate_id!r}")
    lines = [_head(RESULT, from_, _need_uuid(run_id, "run_id"), uid) + ">", f"  <outcome>{outcome}</outcome>",
             f"  <winning_candidate_id>{esc_text(winning_candidate_id)}</winning_candidate_id>"]
    return with_check("\n".join(lines + [f"</{RESULT}>"]))


def _check_error_fields(what, reason, candidate, ref):
    if reason not in ERROR_REASONS:
        raise InvalidMessage(f"reason must be one of {', '.join(ERROR_REASONS)}, not {reason!r}")
    if what not in ERROR_WHAT:
        raise InvalidMessage(f"what must be one of {', '.join(ERROR_WHAT)}, not {what!r}")
    if what == "init" and reason not in ("busy", "invalid", "version"):
        raise InvalidMessage('what="init" goes only with reason="busy", "invalid" or "version"')
    if reason == "mismatch" and what != "result":
        raise InvalidMessage('reason="mismatch" is about a result: what must be "result"')
    if reason in ("busy", "invalid", "version") and what != "init":
        raise InvalidMessage(f'reason="{reason}" is about the init: what must be "init"')
    if reason == "check" and not ref:
        raise InvalidMessage('reason="check" needs ref, the uuid of the damaged document')
    if ref and not is_uuid(ref):
        raise InvalidMessage(f"ref must be a UUID, not {ref!r}")
    if reason != "check" and ref:
        raise InvalidMessage('ref goes only with reason="check"')
    if what in ("score", "repair") and reason != "check" and not candidate:
        raise InvalidMessage(f'what="{what}" needs candidate, the id of the candidate concerned')
    if what not in ("score", "repair") and candidate:
        raise InvalidMessage(f'candidate goes only with what="score" or what="repair", not "{what}"')


def build_error(from_, run_id, to, what, reason, candidate=None, ref=None, text="", uid=None):
    """<concord-error>: ask `to` for a message again (`missing`, `check`), or tell it that its init is refused (`busy`,
    `version`), its init is invalid (`invalid`), or its result differs (`mismatch`). It carries no `check`. `text` is
    for readers only."""
    _check_error_fields(what, reason, candidate, ref)
    head = _head(ERROR, from_, _need_uuid(run_id, "run_id"), uid,
                 [("to", _need_agent(to, "to")), ("what", what), ("candidate", candidate), ("ref", ref),
                  ("reason", reason)])
    return f"{head}>\n  {esc_text(text.strip())}\n</{ERROR}>" if text and text.strip() else head + "/>"


def build_resend(from_, run_id, original, reply_to=None, uid=None):
    """<concord-resend>: carry the stored message `original` again, word for word. `from_` is the author of the message
    inside. `reply_to` is the `uuid` of the error it answers, when it answers one."""
    inner = original.strip()
    info = peek(inner)
    if info is None or info["tag"] not in DATA_TAGS:
        raise InvalidMessage("a resend carries a message of the run: an init, init reply, repair, score or result")
    if info["attrs"].get("from") != from_:
        raise InvalidMessage(f"{from_} can only send again its own messages, not one from {info['attrs'].get('from')!r}")
    if reply_to is not None:
        _need_uuid(reply_to, "reply_to")
    head = _head(RESEND, from_, _need_uuid(run_id, "run_id"), uid, [("reply_to", reply_to)])
    return f"{head}>\n{inner}\n</{RESEND}>"


def _check_cancel_fields(reason, superseded_by, agent):
    if reason not in CANCEL_REASONS:
        raise InvalidMessage(f"reason must be one of {', '.join(CANCEL_REASONS)}, not {reason!r}")
    if (reason == "superseded") != bool(superseded_by):
        raise InvalidMessage('superseded_by goes with reason="superseded", and only with it')
    if (reason == "missing-agent") != bool(agent):
        raise InvalidMessage('agent goes with reason="missing-agent", and only with it')
    if superseded_by and not is_uuid(superseded_by):
        raise InvalidMessage(f"superseded_by must be a UUID, not {superseded_by!r}")
    if agent and not is_agent_id(agent):
        raise InvalidMessage(f"agent must be an agent id, not {agent!r}")


def build_cancel(from_, run_id, reason, superseded_by=None, agent=None, uid=None):
    """<concord-cancel>: ends the run for every agent. `superseded_by` is the run id that continues, for `superseded`; `agent` is
    the agent that was found silent, for `missing-agent`. It carries no `check`."""
    _check_cancel_fields(reason, superseded_by, agent)
    more = [("reason", reason), ("superseded_by", superseded_by), ("agent", agent)]
    return _head(CANCEL, from_, _need_uuid(run_id, "run_id"), uid, more) + "/>"


# --------------------------------------------------------------------------
# reading, layer by layer
# --------------------------------------------------------------------------

def locate(text):
    """The CONCORD document in `text`, taken unchanged, from its first `<` to its last `>`. The message may be handed over
    inside a markdown fence or prose; what is returned is the sender's own text, byte for byte. Raises InvalidMessage when
    there is none, or it is not complete."""
    s = text.strip()
    for m in _FENCE_RE.finditer(s):
        if "<concord-" in m.group(1):
            s = m.group(1).strip()
            break
    m = _OPEN_RE.search(s)
    if not m:
        raise InvalidMessage("no CONCORD message in the text")
    tag_end = s.find(">", m.start())
    if tag_end < 0:
        raise InvalidMessage("the CONCORD message is cut off inside its first tag")
    if s[tag_end - 1] == "/":
        return s[m.start():tag_end + 1]
    close = s.rfind(f"</{m.group(1)}")
    end = s.find(">", close) if close > tag_end else -1
    if end < 0:
        raise InvalidMessage(f"the <{m.group(1)}> message is not closed")
    return s[m.start():end + 1]


def peek(doc):
    """The root of `doc`, read from its text without validating anything: {"tag", "attrs", "inner", "text"}, or None when it is
    not a CONCORD element. `attrs` holds every attribute of the start tag (`version`, `from`, `run_id`, `uuid`, `check`,
    `reply_to`, ...), unescaped. `inner` is the peek of the document inside a <concord-resend>. `text` is the text of an
    <concord-error>: an error of another version is read by its root attributes and this text alone."""
    doc = doc.strip()
    start = _ROOT_TAG_RE.match(doc)
    m = _OPEN_RE.match(start.group(0)) if start else None
    if not m:
        return None
    out = {"tag": m.group(1), "attrs": {k: _unescape(v) for k, v in _ATTR_RE.findall(start.group(0))}, "inner": None,
           "text": None}
    body = doc[start.end():doc.rfind(f"</{m.group(1)}")] if not start.group(0).endswith("/>") else ""
    if m.group(1) == RESEND:
        out["inner"] = peek(body)
    elif m.group(1) == ERROR:
        out["text"] = " ".join(_unescape(re.sub(r"<[^>]*>", " ", body)).split())
    return out


def check_ok(doc):
    """Whether `doc` carries a `check` and it is correct (the integrity check, on the text)."""
    return verify_check(doc)


def _txt(el):
    return (el.text or "").strip() if el is not None else ""


def _child(parent, tag):
    el = parent.find(tag)
    if el is None:
        raise InvalidMessage(f"<{tag}> is missing in <{parent.tag}>")
    return el


def _required(parent, tag):
    t = _txt(_child(parent, tag))
    if not t:
        raise InvalidMessage(f"<{tag}> in <{parent.tag}> is empty")
    return t


def _number(text, what):
    """The number in `text`, in millionths. Raises InvalidMessage unless it is written in the form of the spec."""
    m = parse_number(text)
    if m is None:
        raise InvalidMessage(f"{what} is not a number in the form of the spec (digits, an optional point, at most 6 decimal "
                             f"places): {text!r}")
    return m


def _raw_number(text, what):
    m = _number(text, what)
    if m > 100 * MILLION:
        raise InvalidMessage(f"{what} must be a number from 0 to 100, not {text!r}")
    return m


def _integer(text, what):
    if not _INTEGER_RE.fullmatch(text):
        raise InvalidMessage(f"{what} is not an integer: {text!r}")
    return int(text)


def _agents(parent, tag):
    return [_txt(a) for a in _child(parent, tag).findall("agent")]


def _proposal(root, from_, spec, seed):
    p = _child(root, "proposal")
    pid = p.get("id") or ""
    parsed = parse_candidate_id(pid)
    if parsed is None or parsed[1] != from_ or (parsed[0] == 0) != seed:
        raise InvalidMessage(f"the proposal id {pid!r} is not {'seed-' + from_ if seed else 'r<N>-' + from_}")
    candidate = _required(p, "candidate")
    if spec is not None:
        why = check_candidate(spec, candidate)
        if why:
            raise InvalidMessage(f"the candidate {why}")
    s = _child(p, "score")
    raw = _raw_number(_required(s, "raw"), "<raw>")
    return {"id": pid, "round": parsed[0], "candidate": candidate, "rationale": _required(p, "rationale"), "raw": raw,
            "reasoning": _required(s, "reasoning")}


def _parse_init(root, spec):
    participants = _agents(root, "participants")
    params = {**DEFAULT_PARAMS}
    box = root.find("params")
    for key, tag in _INIT_PARAMS.items():
        el = box.find(tag) if box is not None else None
        if el is not None and _txt(el):
            params[key] = _number(_txt(el), f"<{tag}>") if key in ("satisfaction_threshold", "eps") else \
                _integer(_txt(el), f"<{tag}>")
    _problem(check_params, participants, params)
    anchors = _child(root, "anchors")
    text = _required(_child(root, "proposal"), "candidate_schema")
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        raise InvalidMessage(f"<candidate_schema> is not valid JSON ({exc})")
    return {"participants": participants, "params": params, "context": _required(root, "context"),
            "x_reject": _required(anchors, "x_reject"), "x_ideal": _required(anchors, "x_ideal"),
            "schema": _problem(validate_schema, loaded)}


def _parse_init_reply(root, from_, spec):
    anchors = _child(root, "anchors")
    reject, ideal = (_raw_number(_required(anchors, t), f"<{t}>") for t in ("raw_reject", "raw_ideal"))
    if not ideal > reject:
        raise InvalidMessage(f"raw_ideal ({format_number(ideal)}) must exceed raw_reject ({format_number(reject)})")
    return {"raw_reject": reject, "raw_ideal": ideal, "proposal": _proposal(root, from_, spec, True)}


def _parse_score(root, from_):
    """The entries of a score message. An entry that rates a candidate the sender proposed, or one that an earlier entry already
    rated, is ignored and the others are kept: `ignored` lists their candidate ids."""
    scores, ignored, seen, found = [], [], set(), False
    for sc in root.findall("score"):
        found = True
        cid = sc.get("candidate_id") or ""
        parsed = parse_candidate_id(cid)
        if parsed is None:
            raise InvalidMessage(f"{cid!r} is not a candidate id")
        raw = _raw_number(_required(sc, "raw"), f"the <raw> of {cid}")
        reasoning = _required(sc, "reasoning")
        if parsed[1] == from_ or cid in seen:
            ignored.append(cid)
            continue
        seen.add(cid)
        scores.append({"id": cid, "raw": raw, "reasoning": reasoning})
    if not found:
        raise InvalidMessage("a score message must rate at least one candidate")
    return {"scores": scores, "ignored": ignored}


def _parse_result(root):
    outcome = _required(root, "outcome")
    if outcome not in RESULT_OUTCOMES:
        raise InvalidMessage(f"outcome must be one of {', '.join(RESULT_OUTCOMES)}, not {outcome!r}")
    winner = _required(root, "winning_candidate_id")
    if parse_candidate_id(winner) is None:
        raise InvalidMessage(f"<winning_candidate_id> is not a candidate id: {winner!r}")
    return {"outcome": outcome, "winning_candidate_id": winner}


def _parse_error(root):
    f = {k: root.get(k) or None for k in ("to", "what", "candidate", "ref", "reason")}
    if not f["to"]:
        raise InvalidMessage("<concord-error> needs the attribute to")
    _need_agent(f["to"], "to")
    _check_error_fields(f["what"], f["reason"], f["candidate"], f["ref"])
    return {**f, "text": " ".join((root.text or "").split())}


def _parse_resend(doc, root):
    kids = list(root)
    if len(kids) != 1 or kids[0].tag not in DATA_TAGS:
        raise InvalidMessage("a <concord-resend> carries exactly one message of the run")
    start = _ROOT_TAG_RE.match(doc)
    inner = doc[start.end():doc.rfind(f"</{RESEND}")].strip()
    reply_to = root.get("reply_to")
    if reply_to is not None and not is_uuid(reply_to):
        raise InvalidMessage(f"reply_to must be a UUID, not {reply_to!r}")
    return {"reply_to": reply_to, "inner": inner}


def _parse_cancel(root):
    reason, by, agent = root.get("reason"), root.get("superseded_by"), root.get("agent")
    _check_cancel_fields(reason, by, agent)
    return {"reason": reason, "superseded_by": by, "agent": agent}


def parse_message(doc, spec=None):
    """The message in `doc`, validated, as a dict: "kind" (`init`, `init-reply`, `repair`, `score`, `result`, `error`,
    `resend` or `cancel`), the common attributes `version`, `from`, `run_id`, `uuid` and `check`, and the fields of the
    kind. The `version` is read, not compared, and the `check` is not verified: those are the earlier steps of the Order of
    checks. With the candidate schema `spec` of the run, every candidate is checked against it. Raises InvalidMessage with
    the reason a receiver puts in its error.

    init        participants, params (satisfaction_threshold, eps, B, timeout, retries), context, x_reject, x_ideal, schema
    init-reply  raw_reject, raw_ideal, proposal
    repair      proposal; proposal is {id, round, candidate, rationale, raw, reasoning}
    score       scores: [{id, raw, reasoning}], ignored: the ids of the entries that were ignored
    result      outcome, winning_candidate_id
    error       to, what, candidate, ref, reason, text
    resend      reply_to, inner (the stored message, as text)
    cancel      reason, superseded_by, agent

    Every number is an integer count of millionths (see `protocol.MILLION`): the anchors, the raw scores, and the `satisfaction_threshold`
    and `eps` of the init.
    """
    doc = doc.strip()
    if _FORBIDDEN_RE.search(doc):
        raise InvalidMessage("a message holds no comments, CDATA sections, processing instructions or declarations")
    try:
        root = ET.fromstring(doc)
    except ET.ParseError as exc:
        raise InvalidMessage(f"the message is not well-formed XML: {exc}")
    if root.tag not in KIND_OF_TAG:
        raise InvalidMessage(f"unknown message <{root.tag}>")
    out = {"kind": KIND_OF_TAG[root.tag], "tag": root.tag, "check": check_of(doc)}
    for k in ("version", "from", "run_id", "uuid"):
        if not root.get(k):
            raise InvalidMessage(f"<{root.tag}> needs the attribute {k}")
        out[k] = root.get(k)
    for k in ("run_id", "uuid"):
        if not is_uuid(out[k]):
            raise InvalidMessage(f"{k} must be a UUID, not {out[k]!r}")
    _need_agent(out["from"], "from")
    kind, from_ = out["kind"], out["from"]
    if kind == "init":
        out.update(_parse_init(root, spec))
    elif kind == "init-reply":
        out.update(_parse_init_reply(root, from_, spec))
    elif kind == "repair":
        out["proposal"] = _proposal(root, from_, spec, False)
    elif kind == "score":
        out.update(_parse_score(root, from_))
    elif kind == "result":
        out.update(_parse_result(root))
    elif kind == "error":
        out.update(_parse_error(root))
    elif kind == "resend":
        out.update(_parse_resend(doc, root))
    else:
        out.update(_parse_cancel(root))
    return out
