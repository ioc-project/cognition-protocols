#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The documents. One builder per request a specialist receives (CONVENE, FRAME, GROUND, CLARIFY, VERIFY, EXECUTION), plus
the envelopes an orchestrator reads: `protocol_control` says what to run next and that execution is not yet allowed, and
`next_step` spells out the phase that follows a locked contract.

Every self-driving message opens with a delivery tag naming the session, and tells the receiver which handler of the
`accord-receiver` skill to follow. A builder only renders: it reads the session state and returns text."""
import json


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _esc(text: str) -> str:
    """XML-escape text for embedding in message attributes or content."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _header(st: dict) -> str:
    return f'<accord-delivery state="{_esc(st["state_id"])}"></accord-delivery>'


def build_convene_msg(st: dict) -> str:
    specialists = ", ".join(st["specialists"])
    return (
        f"{_header(st)}\n\n"
        "CONVENE_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its CONVENE_REQUEST handler.\n\n"
        f"TASK: {_esc(st['task'])}\n\n"
        f"SPECIALISTS: {specialists}"
    )


def build_frame_msg(st: dict, agent: str) -> str:
    current = json.dumps(st["frame"], indent=2) if st["frame"] else "[]"
    previous: list[str] = [
        obs["text"] for obs in st["obs"] if obs.get("agent") == agent
    ]
    parts = [
        f"{_header(st)}\n\n",
        "FRAME_REQUEST\n\n",
        "Activate your `accord-receiver` skill and follow its FRAME_REQUEST handler.\n\n",
        f"TASK: {_esc(st['task'])}\n\n",
        f"CURRENT_FRAME: {current}",
    ]
    if previous:
        parts.append(f"\n\nYOUR_PREVIOUS_ATOMS: {json.dumps(previous, indent=2)}")
    return "".join(parts)


def build_ground_msg(st: dict) -> str:
    key_terms = json.dumps(st["key_terms"], indent=2)
    return (
        f"{_header(st)}\n\n"
        "GROUND_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its GROUND_REQUEST handler.\n\n"
        f"KEY_TERMS: {key_terms}"
    )


def build_clarify_msg(st: dict, agent: str, pairs: list[dict]) -> str:
    return (
        f"{_header(st)}\n\n"
        "CLARIFY_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its CLARIFY_REQUEST handler.\n\n"
        f"AMBIGUOUS_PAIR: {json.dumps(pairs, indent=2)}\n\n"
        f"DEFINITIONS_SO_FAR: {json.dumps(st['glossary'], indent=2)}"
    )


def build_verify_msg(st: dict) -> str:
    rk = json.dumps(st["required_knowledge"], indent=2)
    oc = json.dumps(st["objective_clauses"], indent=2)
    frame = json.dumps(st["frame"], indent=2)
    return (
        f"{_header(st)}\n\n"
        "VERIFY_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its VERIFY_REQUEST handler.\n\n"
        f"REQUIRED_KNOWLEDGE: {rk}\n\n"
        f"OBJECTIVE_CLAUSES: {oc}\n\n"
        f"PROBLEM_FRAME: {frame}"
    )


def render_execution_message(agent: str, contract: dict, assignment: str) -> str:
    return (
        "EXECUTION_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its EXECUTION_REQUEST\n"
        "handler before starting the assignment. Report values in the units fixed by the\n"
        "contract Glossary, complete the acceptance checks assigned to you in the\n"
        "Verifiable Objective, and begin your reply with `LOCK_STATUS: RECEIVED`.\n\n"
        f"LOCKED_INTENT_CONTRACT: {canonical_json(contract)}\n\n"
        f"ASSIGNMENT: {assignment}"
    )


def protocol_control(next_command: str) -> dict:
    return {
        "next_command": next_command,
        "protocol_status": "IN_PROGRESS",
        "locked_contract_exists": False,
        "normal_execution_allowed": False,
        "app_execution_allowed": False,
    }


def next_step(st: dict) -> dict:
    """Spell out Phase 5 in the verify payload.

    A contract with flagged items reads like a conclusion, and orchestrators
    have ended the run there with a written recommendation. Fresh tool output
    outranks skill text recalled from many turns ago, so the follow-up command
    ships with the contract itself.
    """
    action_tool = st["action_tool"] or "the action tool"
    return {
        "command": "execution-message",
        "agents": list(st["specialists"]),
        "note": (
            "flagged_items do not end the protocol: render one execution message "
            "per agent, delegate data.message unchanged, then call "
            f"{action_tool} once the replies arrive"
        ),
    }
