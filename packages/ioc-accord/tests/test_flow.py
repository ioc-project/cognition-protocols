#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The self-driving loop: one reply at a time, from CONVENE to a locked contract."""
import json
import re

import pytest

from conftest import flow  # noqa: F401

AGENTS = ["sre", "dba", "net"]
DELIVERY_RE = re.compile(r"<accord-delivery[^>]*></accord-delivery>")

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


def reply_from(flow, st, agent, body):  # noqa: F811
    """A reply as a receiver sends one: the delivery line it was given, then its answer.

    `handle_reply` refuses a reply that does not carry the check of the message that was sent, so a test driving the
    loop has to echo the line the way the `accord-receiver` skill is asked to.
    """
    msg = flow.current_action(st)["messages"].get(agent, "")
    found = DELIVERY_RE.search(msg)
    text = json.dumps(body)
    return f"{found.group(0)}\n\n{text}" if found else text


def issue_convene(flow, st):  # noqa: F811
    """Send the first CONVENE and remember what went out, as `init --task` does."""
    action = flow.current_action(st)
    flow._mark_issued(st, action["phase"], action["recipients"], action["messages"])


def round_trip(flow, st, replies):  # noqa: F811
    """Hand every agent's reply over, and return what the last one was told."""
    out = None
    for agent in AGENTS:
        out = flow.handle_reply(st, agent, reply_from(flow, st, agent, replies[agent]))
    return out


def drive_to_ground(flow, st):  # noqa: F811
    """CONVENE, then every FRAME round the protocol asks for."""
    issue_convene(flow, st)
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
    issue_convene(flow, session)
    first = flow.handle_reply(session, "sre", reply_from(flow, session, "sre", ROSTER["sre"]))
    assert first["phase"] == "convene" and first["still_waiting_this_phase"] == ["dba", "net"]
    assert first["status"] == "NONE" and first["messages"] == {}

    for agent in ("dba", "net"):
        out = flow.handle_reply(session, agent, reply_from(flow, session, agent, ROSTER[agent]))
    assert out["phase"] == "frame_0"
    assert [c["identity"] for c in out["roster"]] == AGENTS


def test_a_message_is_never_issued_to_the_same_agent_twice_in_a_phase(flow, session):  # noqa: F811
    issue_convene(flow, session)
    out = round_trip(flow, session, ROSTER)
    assert set(out["messages"]) == set(AGENTS)                       # frame round 1 goes out
    again = flow.handle_reply(session, "sre", reply_from(flow, session, "sre", FRAME["sre"]))
    assert again["messages"] == {}


def test_a_run_reaches_a_locked_contract(flow, session):  # noqa: F811
    session["required_knowledge"] = [f"{a} knowledge" for a in AGENTS]
    session["objective_clauses"] = ["latency is fine"]

    issue_convene(flow, session)
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


def test_what_went_out_is_remembered_by_its_check(flow, session):  # noqa: F811
    issue_convene(flow, session)
    msg = flow.current_action(session)["messages"]["sre"]
    assert session["issued_checks"]["convene:sre"] == DELIVERY_RE.search(msg).group(0).split('check="')[1][:8]
    assert set(session["issued_checks"]) == {f"convene:{a}" for a in AGENTS}


def test_a_reply_carrying_another_messages_check_is_always_refused(flow, session):  # noqa: F811
    issue_convene(flow, session)
    wrong = f'<accord-delivery state="x" check="deadbeef"></accord-delivery>\n\n{json.dumps(ROSTER["sre"])}'
    with pytest.raises(Exception) as exc:
        flow.handle_reply(session, "sre", wrong)
    assert "does not match its check" in str(exc.value)
    assert session["handle_replies"] == {}                           # nothing was recorded
    assert session["check_fail"] == {}                               # and a wrong check is never counted


def test_a_reply_missing_its_check_is_refused_once_then_recorded_with_a_defect(flow, session):  # noqa: F811
    """A receiver that never echoes the line must not stall the session, but the contract has to say so."""
    issue_convene(flow, session)
    bare = json.dumps(ROSTER["sre"])

    with pytest.raises(Exception) as exc:
        flow.handle_reply(session, "sre", bare)
    assert exc.value.code == "refused"                               # saved on its way out: the count must survive
    assert session["check_fail"]["convene:sre"] == 1
    assert session["handle_replies"] == {}

    flow.handle_reply(session, "sre", bare)
    assert session["handle_replies"]["convene"]["sre"] == bare
    assert session["check_fail"] == {}                               # the count is spent, not carried forward
    assert session["defects"] == [{"kind": "unverified_reply", "agent": "sre", "phase": "convene",
                                   "detail": "recorded on a second attempt without a matching delivery check"}]


def test_a_good_reply_clears_an_earlier_failure(flow, session):  # noqa: F811
    issue_convene(flow, session)
    with pytest.raises(Exception):
        flow.handle_reply(session, "sre", json.dumps(ROSTER["sre"]))
    assert session["check_fail"]["convene:sre"] == 1

    flow.handle_reply(session, "sre", reply_from(flow, session, "sre", ROSTER["sre"]))
    assert session["check_fail"] == {} and session["defects"] == []


def test_a_session_from_before_checks_existed_is_left_alone(flow, session):  # noqa: F811
    """Nothing was stamped, so there is nothing to check against: the reply is taken as it comes."""
    out = flow.handle_reply(session, "sre", json.dumps(ROSTER["sre"]))
    assert out["still_waiting_this_phase"] == ["dba", "net"]
    assert session["check_fail"] == {}


@pytest.mark.parametrize("body, missing", [
    ({"agent": "sre", "summary": "I reviewed the request and agree"}, "carries none of the agent-card fields"),
    ({"roles": ["owner"], "capabilities": []}, "names no agent"),
    ("a sentence, not an object", "is not a JSON object"),
])
def test_a_reply_convene_could_take_nothing_from_is_refused(flow, session, body, missing):  # noqa: F811
    issue_convene(flow, session)
    with pytest.raises(Exception) as exc:
        flow.handle_reply(session, "sre", reply_from(flow, session, "sre", body))
    assert missing in str(exc.value)
    assert "CONVENE needs an agent card" in str(exc.value)
    assert session["handle_replies"] == {}


def test_each_phase_says_what_it_needs_instead(flow, session):  # noqa: F811
    issue_convene(flow, session)
    round_trip(flow, session, ROSTER)
    summary = {"agent": "sre", "summary": "looks fine to me"}

    with pytest.raises(Exception) as exc:                            # FRAME
        flow.handle_reply(session, "sre", reply_from(flow, session, "sre", summary))
    assert "carries no `atoms` list" in str(exc.value)

    round_trip(flow, session, FRAME)
    round_trip(flow, session, FRAME)
    assert flow._infer_phase_key(session) == "ground"
    with pytest.raises(Exception) as exc:                            # GROUND
        flow.handle_reply(session, "sre", reply_from(flow, session, "sre", summary))
    assert "grounds none of the key terms" in str(exc.value)

    round_trip(flow, session, GROUND)
    assert flow._infer_phase_key(session) == "verify"
    with pytest.raises(Exception) as exc:                            # VERIFY
        flow.handle_reply(session, "sre", reply_from(flow, session, "sre", summary))
    assert "carries neither `holds` nor `checks`" in str(exc.value)


def test_an_agent_that_holds_nothing_still_has_a_complete_verify_reply(flow, session):  # noqa: F811
    """The bar is the reply's shape, not how much it says."""
    drive_to_ground(flow, session)
    round_trip(flow, session, GROUND)
    flow.validate_reply(session, "verify", "sre", json.dumps({"agent": "sre", "holds": [], "checks": []}))


def test_a_placeholder_reply_is_recorded_as_an_answer(flow, session):  # noqa: F811
    """An empty reply is the agent responding with nothing, which no shape check should second-guess."""
    out = flow.handle_reply(session, "sre", "")
    assert session["handle_replies"]["convene"]["sre"] == ""
    assert out["still_waiting_this_phase"] == ["dba", "net"]


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
