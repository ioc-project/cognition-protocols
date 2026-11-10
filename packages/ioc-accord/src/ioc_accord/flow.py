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
from ioc_accord.errors import PhaseError
from ioc_accord.ingest import ingest_frame_round, ingest_glossary
from ioc_accord.wire import build_clarify_msg, build_convene_msg, build_frame_msg, build_ground_msg, build_verify_msg


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


def _mark_issued(st: dict, phase: str, agents: list[str]) -> None:
    for a in agents:
        st["issued"][f"{phase}:{a}"] = True


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

    Raises PhaseError when the session was not started with a task, so there is no loop to drive.
    """
    if not st.get("task"):
        raise PhaseError("no task in state — run init --task first")

    if st.get("locked_contract"):
        return {"status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {}}

    phase_key = _infer_phase_key(st)

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
        _mark_issued(st, new_phase, list(messages))

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
