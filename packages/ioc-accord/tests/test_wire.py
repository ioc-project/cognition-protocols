#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The documents a specialist receives, and the envelopes an orchestrator reads."""
import json

import pytest

from conftest import wire  # noqa: F401

BUILDERS = ["build_convene_msg", "build_ground_msg", "build_verify_msg"]


@pytest.mark.parametrize("builder", BUILDERS)
def test_every_request_names_its_handler_and_its_session(wire, session, builder):  # noqa: F811
    session["state_id"] = "abc12345"
    msg = getattr(wire, builder)(session)
    kind = builder.removeprefix("build_").removesuffix("_msg").upper() + "_REQUEST"
    assert msg.startswith('<accord-delivery state="abc12345"></accord-delivery>')
    assert kind in msg
    assert f"follow its {kind} handler" in msg


def test_the_task_is_escaped_before_it_reaches_a_message(wire, session):  # noqa: F811
    session["task"] = 'fix <b>"checkout" & co</b>'
    assert "fix &lt;b&gt;&quot;checkout&quot; &amp; co&lt;/b&gt;" in wire.build_convene_msg(session)


def test_convene_lists_the_specialists(wire, session):  # noqa: F811
    assert "SPECIALISTS: sre, dba, net" in wire.build_convene_msg(session)


def test_the_first_frame_request_carries_an_empty_frame_and_no_history(wire, session):  # noqa: F811
    msg = wire.build_frame_msg(session, "sre")
    assert "CURRENT_FRAME: []" in msg
    assert "YOUR_PREVIOUS_ATOMS" not in msg


def test_a_later_frame_request_reminds_an_agent_of_its_own_atoms_only(wire, session):  # noqa: F811
    session["obs"] = [{"text": "latency spike", "agent": "sre", "kind": "term"},
                      {"text": "pool exhausted", "agent": "dba", "kind": "term"}]
    session["frame"] = [{"id": "s1", "text": "latency spike", "support": 1}]
    msg = wire.build_frame_msg(session, "sre")
    assert "YOUR_PREVIOUS_ATOMS" in msg and "latency spike" in msg and "pool exhausted" not in msg


def test_ground_and_clarify_carry_the_terms_under_discussion(wire, session):  # noqa: F811
    assert "severity" in wire.build_ground_msg(session)
    pairs = [{"term": "severity", "agents": ["sre", "net"], "sim": 0.6}]
    assert "AMBIGUOUS_PAIR" in wire.build_clarify_msg(session, "sre", pairs)


def test_verify_carries_the_universe_the_clauses_and_the_frame(wire, session):  # noqa: F811
    session["required_knowledge"] = ["alerts"]
    session["objective_clauses"] = ["latency is fine"]
    msg = wire.build_verify_msg(session)
    assert "REQUIRED_KNOWLEDGE" in msg and "alerts" in msg
    assert "OBJECTIVE_CLAUSES" in msg and "latency is fine" in msg
    assert "PROBLEM_FRAME" in msg


def test_an_execution_message_carries_the_contract_verbatim(wire):  # noqa: F811
    contract = {"status": "INTENT-LOCKED", "flagged_items": []}
    msg = wire.render_execution_message("sre", contract, "restore checkout latency")
    assert msg.startswith("EXECUTION_REQUEST")
    assert "LOCK_STATUS: RECEIVED" in msg
    assert json.loads(msg.split("LOCKED_INTENT_CONTRACT: ")[1].split("\n\n")[0]) == contract
    assert msg.endswith("ASSIGNMENT: restore checkout latency")


def test_canonical_json_is_stable_and_compact(wire):  # noqa: F811
    assert wire.canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_a_phase_payload_says_execution_is_not_allowed_yet(wire):  # noqa: F811
    control = wire.protocol_control("frame")
    assert control["next_command"] == "frame"
    assert control["protocol_status"] == "IN_PROGRESS"
    assert not any([control["locked_contract_exists"], control["normal_execution_allowed"],
                    control["app_execution_allowed"]])


def test_the_next_step_names_the_action_tool_and_every_agent(wire, session):  # noqa: F811
    step = wire.next_step(session)
    assert step["command"] == "execution-message" and step["agents"] == ["sre", "dba", "net"]
    assert "deploy" in step["note"]
    session["action_tool"] = ""
    assert "the action tool" in wire.next_step(session)["note"]
