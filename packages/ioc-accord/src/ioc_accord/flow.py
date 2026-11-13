#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The self-driving loop. An orchestrator hands over one reply at a time and is told what to send next: `handle_reply`
records the reply, runs the phase merge once everyone has answered, and returns the messages that are now due.

The phase is never stored. It is inferred from what has been merged (`_infer_phase_key`), so a state that was edited or
replayed cannot disagree with itself. `issued` stops the same message going out twice when a phase is re-entered."""
from ioc_accord.engine import (MAX_FRAME_ROUNDS, merge_roster, record_frame_round, record_glossary, record_verify,
                               store_contract)
from ioc_accord.errors import AccordError, AccordRefusal, PhaseError
from ioc_accord.ingest import ingest_frame_round, ingest_glossary, parse_responses
from ioc_accord.wire import (_CHECK_RE, build_clarify_msg, build_convene_msg, build_frame_msg, build_ground_msg,
                             build_verify_msg)


def _infer_phase_key(st: dict) -> str:
    """Infer current phase from merged-data indicators."""
    if not st["roster"]:
        return "convene"
    if st["frame_rounds"] < MAX_FRAME_ROUNDS:
        return f"frame_{st['frame_rounds']}"
    if not st["glossary"]:
        return "ground"
    if st.get("clarify_terms"):
        return "clarify"
    if not st.get("locked_contract"):
        return "verify"
    return "locked"


def current_action(st: dict) -> dict:
    """Return {phase, recipients, messages} for the current phase.

    Every handle call uses this to know who still needs to receive a message.
    Phase is inferred from merged-data indicators, not stored explicitly.
    """
    if st.get("locked_contract"):
        return {"phase": "locked", "recipients": [], "messages": {}}

    phase_key = _infer_phase_key(st)
    specialists = st["specialists"]

    replied = st["handle_replies"].get(phase_key, {})

    if phase_key == "convene":
        pending = [a for a in specialists if a not in replied]
        msg = build_convene_msg(st)
        return {
            "phase": phase_key,
            "recipients": pending,
            "messages": {a: msg for a in pending},
        }

    if phase_key.startswith("frame_"):
        pending = [a for a in specialists if a not in replied]
        return {
            "phase": phase_key,
            "recipients": pending,
            "messages": {a: build_frame_msg(st, a) for a in pending},
        }

    if phase_key == "ground":
        pending = [a for a in specialists if a not in replied]
        msg = build_ground_msg(st)
        return {
            "phase": phase_key,
            "recipients": pending,
            "messages": {a: msg for a in pending},
        }

    if phase_key == "clarify":
        pairs = st.get("clarify_terms", [])
        pending = [a for a in specialists if a not in replied]
        return {
            "phase": phase_key,
            "recipients": pending,
            "messages": {a: build_clarify_msg(st, a, pairs) for a in pending},
        }

    if phase_key == "verify":
        pending = [a for a in specialists if a not in replied]
        msg = build_verify_msg(st)
        return {
            "phase": phase_key,
            "recipients": pending,
            "messages": {a: msg for a in pending},
        }

    return {"phase": phase_key, "recipients": [], "messages": {}}


def _is_issued(st: dict, phase: str, agent: str) -> bool:
    return st["issued"].get(f"{phase}:{agent}", False)


def _mark_issued(st: dict, phase: str, agents: list[str], messages: dict[str, str] | None = None) -> None:
    for a in agents:
        st["issued"][f"{phase}:{a}"] = True
        if messages and a in messages:
            match = _CHECK_RE.search(messages[a])
            if match:
                st["issued_checks"][f"{phase}:{a}"] = match.group(1)


_PHASE_SHAPE = {
    "convene": 'an agent card: `agent`, plus `roles`, `capabilities`, `access_scopes` and `holdings_summary`',
    "frame": 'an `agent` and an `atoms` list of {"text": ..., "type": ...} objects (`[]` withdraws every atom)',
    "ground": 'an `agent` and a `units` map carrying a unit for a term in KEY_TERMS',
    "clarify": 'an `agent`, the `term`, and an `extended_definition` for it',
    "verify": 'an `agent`, its `holds` list and its proposed `checks`',
}


def validate_reply(st: dict, phase: str, agent: str, reply: str) -> None:
    """Refuse a reply this phase's merge would take nothing from.

    The merges are deliberately lenient — they skip what they cannot read and carry on — so a summary, a refusal or
    an answer in the wrong shape used to be recorded as that agent having contributed, and the phase closed without
    it. This says so instead.

    The bar is the reply's *shape*, not how much it says: the keys that make it an answer to this phase have to be
    there, and may be empty. `{"agent": a, "holds": [], "checks": []}` is a complete VERIFY reply from an agent that
    holds nothing, while `{"agent": a, "summary": "..."}` is not a reply at all — which is the difference a summary
    cannot fake, and the delivery check cannot see.
    """
    kind = phase.split("_")[0]
    shape = _PHASE_SHAPE.get(kind)
    if shape is None:
        return

    def refuse(why: str) -> None:
        raise AccordError(f"handle: the reply from {agent} {why}. {kind.upper()} needs {shape}. Nothing was recorded.")

    parsed = parse_responses([reply])
    if not parsed:
        refuse("is not a JSON object")
    body = parsed[0]

    named = str(body.get("agent") or body.get("identity") or "").strip()
    card_keys = ("roles", "capabilities", "access_scopes", "accessScopes", "holdings_summary", "holdingsSummary")
    verify_keys = ("holds", "checks", "proposed_checks")

    if kind == "convene":
        if not named:
            refuse("names no agent")
        if not any(k in body for k in card_keys):
            refuse("carries none of the agent-card fields")
    elif kind == "frame":
        if not isinstance(body.get("atoms"), list):
            refuse("carries no `atoms` list")
    elif kind in ("ground", "clarify"):
        if not ingest_glossary([reply], st["key_terms"]):
            refuse("grounds none of the key terms")
    elif kind == "verify":
        if not named:
            refuse("names no agent")
        if not any(k in body for k in verify_keys):
            refuse("carries neither `holds` nor `checks`")


def verify_reply_check(st: dict, phase: str, agent: str, reply: str) -> None:
    """Refuse a reply that does not carry the check of the message that was sent.

    A reply the orchestrator summarized or wrote itself loses the `<accord-delivery>` line, so it cannot present the
    right check. The two failures are not equally suspicious, and are not treated alike:

    * A **wrong** check is always refused. It says this text belongs to some other message, so recording it would
      file the wrong content under this phase. The real reply exists; the orchestrator has to go back for it.
    * A **missing** check is refused once and then recorded anyway, with a defect. It usually means a receiver that
      did not echo the line rather than a substitution, and a receiver that never echoes must not stall the session.

    That leniency covers the missing line only. What it lets through still has to be a reply the phase can read:
    `validate_reply` runs next and refuses a body the merge would take nothing from, however many attempts it took.
    """
    key = f"{phase}:{agent}"
    expected = st["issued_checks"].get(key)
    if not expected:
        return                                  # nothing was stamped (a session started before checks existed)
    found = _CHECK_RE.search(reply)
    if found and found.group(1) == expected:
        st["check_fail"].pop(key, None)
        return
    if found:
        raise AccordError(
            f"handle: the reply from {agent} does not match its check (expected {expected}, got "
            f"{found.group(1)}). Nothing was recorded.")

    st["check_fail"][key] = st["check_fail"].get(key, 0) + 1
    if st["check_fail"][key] < 2:
        raise AccordRefusal(
            f"handle: the reply from {agent} carried no `<accord-delivery>` check line. Nothing was recorded.")
    st["check_fail"].pop(key, None)
    st["defects"].append({
        "kind": "unverified_reply", "agent": agent, "phase": phase,
        "detail": "recorded on a second attempt without a matching delivery check",
    })


def _run_phase_advance(st: dict, phase_key: str) -> dict:
    """Run the deterministic merge for a completed phase, mutate state, return extras."""
    replies_map: dict[str, str] = st["handle_replies"].get(phase_key, {})
    raw = list(replies_map.values())

    if phase_key == "convene":
        merge_roster(st, raw)
        return {"roster": st["roster"], "flagged_items": st["defects"]}

    if phase_key.startswith("frame_"):
        by_agent, truncated, _ = ingest_frame_round(raw)
        record_frame_round(st, by_agent)
        return {"frame": st["frame"], "round": st["frame_rounds"], "truncated": truncated}

    if phase_key == "ground":
        _, ambiguous = record_glossary(st, ingest_glossary(raw, st["key_terms"]))
        if ambiguous:
            st["clarify_terms"] = ambiguous
        return {"glossary": st["glossary"], "ambiguous": ambiguous}

    if phase_key == "clarify":
        _, ambiguous = record_glossary(st, ingest_glossary(raw, st["key_terms"]))
        st["clarify_terms"] = ambiguous  # empty when resolved
        return {"glossary": st["glossary"], "remaining_ambiguous": ambiguous}

    if phase_key == "verify":
        record_verify(st, raw, st.get("required_knowledge") or [], st.get("objective_clauses") or [])
        contract = store_contract(st)
        return {"locked_intent_contract": contract, "gaps": list(st["uncovered"])}

    return {}


def handle_reply(st: dict, agent: str, reply: str) -> dict:
    """Record one agent reply and advance the phase when all replies are in.

    The reply is checked before anything is recorded: `verify_reply_check` says it is the answer to the message that
    was actually sent, and `validate_reply` says this phase's merge can read it. Either one refusing leaves the
    session exactly as it was, so the orchestrator can go back for the real reply and try again.

    Raises PhaseError when the session was not started with a task, so there is no loop to drive.
    """
    if not st.get("task"):
        raise PhaseError("handle: no task in state — run init --task first")

    if st.get("locked_contract"):
        return {"status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {}}

    phase_key = _infer_phase_key(st)
    verify_reply_check(st, phase_key, agent, reply)
    if reply:
        validate_reply(st, phase_key, agent, reply)

    # Record reply (empty string is still a valid placeholder — agent responded)
    phase_replies: dict[str, str] = st["handle_replies"].setdefault(phase_key, {})
    phase_replies[agent] = reply

    specialists = st["specialists"]
    pending_after = [a for a in specialists if a not in phase_replies]

    extras: dict = {}
    if not pending_after:
        # All specialists replied — run the deterministic merge.
        extras = _run_phase_advance(st, phase_key)

    # Compute next action after possible advance
    action = current_action(st)
    new_phase = action["phase"]
    new_pending = action.get("recipients", [])
    messages: dict[str, str] = {}

    if action.get("messages"):
        # Only emit messages for agents we haven't already issued this phase.
        for recipient, msg in action["messages"].items():
            if not _is_issued(st, new_phase, recipient):
                messages[recipient] = msg
        _mark_issued(st, new_phase, list(messages), messages)

    if new_phase == "locked":
        return {
            "status": "LOCKED",
            "phase": "locked",
            "awaiting": [],
            "messages": {},
            "locked_intent_contract": st.get("locked_contract"),
            **extras,
        }

    status = "SEND" if messages else "NONE"
    return {
        "status": status,
        "phase": new_phase,
        "awaiting": new_pending,
        "messages": messages,
        "still_waiting_this_phase": pending_after,
        **extras,
    }
