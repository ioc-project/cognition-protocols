#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The self-driving loop: one reply at a time, from CONVENE to a locked contract."""
import json

import pytest

from conftest import flow  # noqa: F401

AGENTS = ["sre", "dba", "net"]

ROSTER = {a: {"agent": a, "roles": [a], "holdings_summary": f"{a} knowledge"} for a in AGENTS}
FRAME = {
    "sre": {"agent": "sre", "atoms": [{"text": "latency spike on checkout", "type": "observation"}]},
    "dba": {"agent": "dba", "atoms": [{"text": "latency spike on checkout"}]},
    "net": {"agent": "net", "atoms": [{"text": "packet loss on the edge link"}]},
}
GROUND = {
    "sre": {"agent": "sre", "units": {"severity": "sev1 is an outage", "error_rate": "errors per second"}},
    "dba": {"agent": "dba", "units": {"severity": "sev1 is an outage", "error_rate": "errors per second"}},
    "net": {"agent": "net", "units": {"severity": "sev1 is an outage", "error_rate": "errors per second"}},
}
VERIFY = {
    "sre": {"agent": "sre", "holds": ["sre knowledge"],
            "checks": [{"criterion": "p99 under 300ms", "covers": ["latency is fine"]}]},
    "dba": {"agent": "dba", "holds": ["dba knowledge"]},
    "net": {"agent": "net", "holds": ["net knowledge"]},
}


def round_trip(flow, st, replies):  # noqa: F811
    """Hand every agent's reply over, and return what the last one was told."""
    out = None
    for agent in AGENTS:
        out = flow.handle_reply(st, agent, json.dumps(replies[agent]))
    return out


def drive_to_ground(flow, st):  # noqa: F811
    """CONVENE, then every FRAME round the protocol asks for."""
    round_trip(flow, st, ROSTER)
    while flow._infer_phase_key(st).startswith("frame_"):
        round_trip(flow, st, FRAME)


def test_a_session_without_a_task_has_no_loop_to_drive(flow, session):  # noqa: F811
    session["task"] = ""
    with pytest.raises(Exception) as exc:
        flow.handle_reply(session, "sre", "{}")
    assert exc.value.code == "invalid_phase"


def test_a_fresh_session_is_waiting_on_convene(flow, session):  # noqa: F811
    action = flow.current_action(session)
    assert action["phase"] == "convene" and action["recipients"] == AGENTS
    assert set(action["messages"]) == set(AGENTS)


def test_the_phase_only_turns_once_everyone_has_answered(flow, session):  # noqa: F811
    flow._mark_issued(session, "convene", AGENTS)                    # as `init` does when it sends CONVENE
    first = flow.handle_reply(session, "sre", json.dumps(ROSTER["sre"]))
    assert first["phase"] == "convene" and first["still_waiting_this_phase"] == ["dba", "net"]
    assert first["status"] == "NONE" and first["messages"] == {}

    out = round_trip(flow, session, ROSTER)
    assert out["phase"] == "frame_0"
    assert [c["identity"] for c in out["roster"]] == AGENTS


def test_a_message_is_never_issued_to_the_same_agent_twice_in_a_phase(flow, session):  # noqa: F811
    out = round_trip(flow, session, ROSTER)
    assert set(out["messages"]) == set(AGENTS)                       # frame round 1 goes out
    again = flow.handle_reply(session, "sre", json.dumps(FRAME["sre"]))
    assert again["messages"] == {}


def test_a_run_reaches_a_locked_contract(flow, session):  # noqa: F811
    session["required_knowledge"] = [f"{a} knowledge" for a in AGENTS]
    session["objective_clauses"] = ["latency is fine"]

    round_trip(flow, session, ROSTER)
    assert flow._infer_phase_key(session) == "frame_0"

    round_trip(flow, session, FRAME)
    assert flow._infer_phase_key(session) == "frame_1"

    round_trip(flow, session, FRAME)
    assert flow._infer_phase_key(session) == "ground"
    assert session["frame_rounds"] == flow.MAX_FRAME_ROUNDS
    assert [s["support"] for s in session["frame"]] == [2, 1]

    out = round_trip(flow, session, GROUND)
    assert out["ambiguous"] == []
    assert flow._infer_phase_key(session) == "verify"

    out = round_trip(flow, session, VERIFY)
    assert out["status"] == "LOCKED" and out["phase"] == "locked"
    contract = out["locked_intent_contract"]
    assert contract["status"] == "INTENT-LOCKED"
    assert contract["verifiable_objective"]["owners"] == {f"{a} knowledge": a for a in AGENTS}
    assert contract["verifiable_objective"]["checks"][0]["assigned_to"] == "sre"
    assert out["gaps"] == []


def test_a_locked_session_answers_locked_and_asks_for_nothing(flow, session):  # noqa: F811
    session["locked_contract"] = {"status": "INTENT-LOCKED"}
    out = flow.handle_reply(session, "sre", "anything")
    assert out == {"status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {}}
    assert flow.current_action(session) == {"phase": "locked", "recipients": [], "messages": {}}


def test_disagreeing_definitions_open_a_clarify_round(flow, session):  # noqa: F811
    ground = {a: {"agent": a, "units": dict(GROUND[a]["units"])} for a in AGENTS}
    ground["net"]["units"]["severity"] = "sev1 means a user facing outage"

    drive_to_ground(flow, session)
    out = round_trip(flow, session, ground)
    assert out["ambiguous"] and session["clarify_terms"]
    assert flow._infer_phase_key(session) == "clarify"
    assert "CLARIFY_REQUEST" in out["messages"]["sre"]

    agreed = {a: {"agent": a, "term": "severity", "extended_definition": "sev1 is an outage"} for a in AGENTS}
    out = round_trip(flow, session, agreed)
    assert out["remaining_ambiguous"] == [] and session["clarify_terms"] == []
    assert flow._infer_phase_key(session) == "verify"
