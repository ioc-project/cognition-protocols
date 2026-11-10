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
    assert run["received"] == {"init|alice": doc} and run["uuids"] == [init["uuid"]] and run["sent"] == {}
    assert run["engine"]["agents"] == ["cost", "security"] and run["params"] == init["params"]
    assert json.loads(json.dumps(run)) == run


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
