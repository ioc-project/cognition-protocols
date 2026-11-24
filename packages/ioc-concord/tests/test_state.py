#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The state of an agent, as imported and as bundled into each script (see conftest.py)."""
import json

import pytest

from ioc_concord import wire as W

RUN = "11111111-1111-4111-8111-111111111111"
SPEC = {"type": "string", "description": "a plan"}


def parsed_init(**kw):
    args = dict(from_="alice", participants=["cost", "security"], context="pick one", x_reject="nothing", x_ideal="all agree",
                spec=SPEC, run_id=RUN)
    doc = W.build_init(**{**args, **kw})
    return W.parse_message(doc), doc


def test_a_new_agent_state_is_empty_and_the_agent_id_is_checked(state):
    assert state.new_agent_state("cost") == {"me": "cost", "runs": {}, "active": None}
    for bad in ("", "a b", "x" * 65, None):
        with pytest.raises(state.StateError, match="agent id"):
            state.new_agent_state(bad)


def test_item_keys_name_what_a_message_is_about(state):
    assert state.item_key("init-reply", "cost") == "init-reply|cost"
    assert state.item_key("score", "cost", "seed-security") == "score|cost|seed-security"
    assert state.item_key("result", "cost", None) == "result|cost"


def test_a_run_holds_what_the_init_says_and_is_json_able(state):
    init, doc = parsed_init()
    run = state.new_run("cost", init, doc)
    assert (run["run_id"], run["initiator"], run["participants"], run["role"], run["status"], run["outcome"]) == (
        RUN, "alice", ["cost", "security"], "participant", "active", None)
    assert run["received"] == {"init|alice": doc} and list(run["uuids"]) == [init["uuid"]] and run["sent"] == {}
    assert run["engine"]["agents"] == ["cost", "security"] and run["params"] == init["params"]
    assert json.loads(json.dumps(run)) == run


def saved(state):
    """A state as the agent `cost` saves it: JSON that has been through a file."""
    init, doc = parsed_init()
    s = state.new_agent_state("cost")
    s["runs"][RUN] = state.new_run("cost", init, doc)
    s["active"] = RUN
    return json.loads(json.dumps(s))


def test_the_keys_of_a_run_that_a_saved_state_must_hold_are_those_new_run_makes(state):
    init, doc = parsed_init()
    assert set(state.RUN_KEYS) == set(state.new_run("cost", init, doc)) == set(state.new_run("alice", init, doc))


def test_a_state_that_the_library_made_passes_the_check_and_is_returned_as_it_is(state):
    s = saved(state)
    assert state.check_agent_state(s, "cost") is s
    empty = state.new_agent_state("cost")
    assert state.check_agent_state(empty, "cost") is empty


@pytest.mark.parametrize("change, why", [
    (lambda s: s.pop("runs"), '"runs" is not an object'),
    (lambda s: s.update(runs=[]), '"runs" is not an object'),
    (lambda s: s.update(runs=5), '"runs" is not an object'),
    (lambda s: s.update(active="22222222-2222-4222-8222-222222222222"), '"active" names a run'),
    (lambda s: s["runs"].update({"not-a-uuid": s["runs"][RUN]}), "is not a run id"),
    (lambda s: s["runs"].update({RUN: None}), "is not an object"),
    (lambda s: s["runs"].update({RUN: []}), "is not an object"),
    (lambda s: s["runs"][RUN].pop("engine"), "lacks engine"),
    (lambda s: s["runs"][RUN].pop("held"), "lacks held"),
    (lambda s: s["runs"][RUN].update(run_id="22222222-2222-4222-8222-222222222222"), "says it is"),
    (lambda s: s["runs"][RUN].update(status="paused"), "a role or a status"),
    (lambda s: s["runs"][RUN].update(role="spectator"), "a role or a status"),
    (lambda s: s["runs"][RUN].update(participants="cost"), '"participants" of the run'),
    (lambda s: s["runs"][RUN].update(timers=[]), '"timers" of the run'),
    (lambda s: s["runs"][RUN].update(uuids=None), '"uuids" of the run'),
    (lambda s: s["runs"][RUN].update(participants=["cost", "a b"]), "no valid id"),
    (lambda s: s["runs"][RUN].update(initiator=None), "no valid id"),
    (lambda s: s["runs"][RUN]["params"].pop("retries"), "no parameters or no engine"),
    (lambda s: s["runs"][RUN]["engine"].pop("pool"), "no parameters or no engine"),
])
def test_a_state_of_the_wrong_shape_is_refused_with_the_reason(state, change, why):
    s = saved(state)
    change(s)
    with pytest.raises(state.StateError, match="not the state of an agent") as e:
        state.check_agent_state(s, "cost")
    assert why in str(e.value)


@pytest.mark.parametrize("value", [None, [], "state", 5, True])
def test_a_saved_state_that_is_not_an_object_is_refused(state, value):
    with pytest.raises(state.StateError, match="not a JSON object"):
        state.check_agent_state(value, "cost")


def test_the_state_of_another_agent_is_refused(state):
    with pytest.raises(state.StateError, match="belongs to 'cost', not to 'security'"):
        state.check_agent_state(saved(state), "security")


def test_the_members_of_a_run_are_its_participants_and_its_initiator(state):
    init, doc = parsed_init()
    assert state.members(state.new_run("cost", init, doc)) == {"alice", "cost", "security"}


def test_the_active_run_is_the_one_that_is_still_running(state):
    s = state.new_agent_state("cost")
    assert state.active_run(s) is None
    init, doc = parsed_init()
    s["runs"][RUN] = state.new_run("cost", init, doc)
    s["active"] = RUN
    assert state.active_run(s) is s["runs"][RUN]
    s["runs"][RUN]["status"] = state.RUN_ENDED
    assert state.active_run(s) is None


def test_a_sent_original_is_kept_by_item_and_by_uuid_and_an_accepted_uuid_is_remembered(state):
    init, doc = parsed_init()
    run = state.new_run("cost", init, doc)
    reply = W.build_init_reply("cost", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    state.keep_sent(run, "init-reply|cost", reply)
    assert run["sent"] == {"init-reply|cost": reply} and run["sent_by_uuid"] == {"55555555-5555-4555-8555-555555555555": reply}
    assert "x" not in run["uuids"]
    state.keep_uuid(run, "x")
    state.keep_uuid(run, "x")
    assert list(run["uuids"]) == [init["uuid"], "x"] and json.loads(json.dumps(run)) == run


def test_the_initiator_keeps_the_init_it_sent_in_the_same_way(state):
    init, doc = parsed_init()
    run = state.new_run("alice", init, doc)
    assert run["role"] == "initiator" and run["sent"] == {"init|alice": doc} and run["received"] == {}
    assert run["sent_by_uuid"] == {init["uuid"]: doc}
