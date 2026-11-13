#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The documents. One builder per request a specialist receives (CONVENE, FRAME, GROUND, CLARIFY, VERIFY, EXECUTION), plus
the envelopes an orchestrator reads: `protocol_control` says what to run next and that execution is not yet allowed, and
`next_step` spells out the phase that follows a locked contract.

Every self-driving message opens with a delivery tag naming the session and fingerprinting the body that follows, and
tells the receiver which handler of the `accord-receiver` skill to follow. A builder only renders: it reads the session
state and returns text."""
import hashlib
import json
import re


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _esc(text: str) -> str:
    """XML-escape text for embedding in message attributes or content."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_CHECK_RE = re.compile(r'<accord-delivery\b[^>]*\bcheck="([0-9a-f]+)"')


def reply_check(text: str) -> str:
    """A short fingerprint of a phase message, blind to whitespace.

    The initiator puts it on the delivery (`check="..."`); the specialist echoes that line back on its reply, so a
    reply that was summarized, rewritten or invented in place of the real one no longer carries the right check.
    It binds a reply to the message that was actually sent — it does not prove the reply's own body is untouched,
    which nothing the initiator holds could establish.
    """
    return hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:8]


def _header(st: dict, body: str = "") -> str:
    return (f'<accord-delivery state="{_esc(st["state_id"])}" check="{reply_check(body)}">'
            "</accord-delivery>")


def _wrap_msg(st: dict, body: str) -> str:
    """A phase message: its delivery header, fingerprinting the body that follows."""
    return f"{_header(st, body)}\n\n{body}"


def build_convene_msg(st: dict) -> str:
    specialists = ", ".join(st["specialists"])
    return _wrap_msg(st, (
        "CONVENE_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its CONVENE_REQUEST handler.\n\n"
        f"TASK: {_esc(st['task'])}\n\n"
        f"SPECIALISTS: {specialists}"
    ))


def build_frame_msg(st: dict, agent: str) -> str:
    current = json.dumps(st["frame"], indent=2) if st["frame"] else "[]"
    previous: list[str] = [
        obs["text"] for obs in st["obs"] if obs.get("agent") == agent
    ]
    parts = [
        "FRAME_REQUEST\n\n",
        "Activate your `accord-receiver` skill and follow its FRAME_REQUEST handler.\n\n",
        f"TASK: {_esc(st['task'])}\n\n",
        f"CURRENT_FRAME: {current}",
    ]
    if previous:
        parts.append(f"\n\nYOUR_PREVIOUS_ATOMS: {json.dumps(previous, indent=2)}")
    return _wrap_msg(st, "".join(parts))


def build_ground_msg(st: dict) -> str:
    key_terms = json.dumps(st["key_terms"], indent=2)
    return _wrap_msg(st, (
        "GROUND_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its GROUND_REQUEST handler.\n\n"
        f"KEY_TERMS: {key_terms}"
    ))


def build_clarify_msg(st: dict, agent: str, pairs: list[dict]) -> str:
    return _wrap_msg(st, (
        "CLARIFY_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its CLARIFY_REQUEST handler.\n\n"
        f"AMBIGUOUS_PAIR: {json.dumps(pairs, indent=2)}\n\n"
        f"DEFINITIONS_SO_FAR: {json.dumps(st['glossary'], indent=2)}"
    ))


def build_verify_msg(st: dict) -> str:
    rk = json.dumps(st["required_knowledge"], indent=2)
    oc = json.dumps(st["objective_clauses"], indent=2)
    frame = json.dumps(st["frame"], indent=2)
    return _wrap_msg(st, (
        "VERIFY_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its VERIFY_REQUEST handler.\n\n"
        f"REQUIRED_KNOWLEDGE: {rk}\n\n"
        f"OBJECTIVE_CLAUSES: {oc}\n\n"
        f"PROBLEM_FRAME: {frame}"
    ))


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
