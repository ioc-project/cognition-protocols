#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The state constructors, as imported and as bundled into each script (see conftest.py)."""
import json
import xml.etree.ElementTree as ET

import pytest

ENUM = {"enum": ["Plan A", "Plan B"]}
OPEN = {"type": "string", "description": "a plan"}


def new(state, spec=OPEN, **kw):
    return state.new_run_state(["a", "b"], "ctx", "bad", "good", spec, **kw)


def test_a_new_run_state_holds_the_engine_state_and_the_brief(state):
    st = new(state, ENUM, satisfaction_threshold=0.7, eps=0.1, B=4, relay=False, state_id="s1")
    assert list(st) == ["version", "params", "agents", "schema", "schema_json", "options", "context", "x_reject", "x_ideal",
                        "anchors", "pool", "raw", "score_reasoning", "x_star", "t", "pending", "outcome", "log", "relay",
                        "state_id"]
    assert st["version"] == 2 and st["agents"] == ["a", "b"]
    assert st["params"] == {"satisfaction_threshold": 0.7, "eps": 0.1, "B": 4, "W": "leximin"}
    assert (st["context"], st["x_reject"], st["x_ideal"], st["relay"], st["state_id"]) == ("ctx", "bad", "good", False, "s1")
    assert st["raw"] == {"a": {}, "b": {}} and st["anchors"] == {} and st["pool"] == [] and st["outcome"] is None


def test_the_schema_is_kept_three_ways(state):
    st = new(state, ENUM)
    assert st["options"] == ["Plan A", "Plan B"] and st["schema"] == "exactly one of: Plan A | Plan B"
    assert json.loads(st["schema_json"]) == ENUM
    open_st = new(state, OPEN)
    assert open_st["options"] is None and open_st["schema"] == "a plan"
    assert open_st["schema_json"] == json.dumps(OPEN, sort_keys=True)


def test_a_run_state_without_an_id_has_none_yet(state):
    st = new(state)
    assert "state_id" not in st and st["relay"] is True and st["params"]["B"] == 10


def test_a_run_state_survives_a_round_trip_through_json(state):
    st = new(state, ENUM, state_id="s1")
    assert json.loads(json.dumps(st)) == st


def test_a_schema_that_is_not_valid_is_refused(state):
    with pytest.raises(Exception, match="not a valid regular expression") as e:
        new(state, {"description": "d", "pattern": "("})
    assert e.value.code == "invalid_schema"


@pytest.mark.parametrize("kw, message", [
    ({"agents": ["a"]}, "needs at least 2 agents"),
    ({"agents": []}, "needs at least 2 agents"),
    ({"agents": ["a", "a"]}, "duplicate agent names"),
    ({"satisfaction_threshold": 0}, r"satisfaction_threshold must be in \(0, 1\]"),
    ({"satisfaction_threshold": 1.5}, r"satisfaction_threshold must be in \(0, 1\]"),
    ({"eps": 0}, "eps must be > 0"),
    ({"B": 0}, "B must be >= 1"),
])
def test_the_parameters_of_a_run_are_checked(state, kw, message):
    args = {"agents": ["a", "b"], "satisfaction_threshold": 0.6, "eps": 0.05, "B": 10, **kw}
    with pytest.raises(state.InvalidRun, match=message) as e:
        state.check_run(**args)
    assert e.value.code == "invalid_run"
    with pytest.raises(state.InvalidRun, match=message):
        state.new_run_state(args["agents"], "c", "r", "i", OPEN, args["satisfaction_threshold"], args["eps"], args["B"])


def test_a_new_run_state_builds_requests_and_runs_the_engine(state, wire, engine):
    st = new(state, ENUM, state_id="s1")
    req = wire.parse_request(ET.fromstring(wire.build_seed_msg(st, ["a", "b"])))
    assert req["options"] == ["Plan A", "Plan B"] and req["participants"] == ["a", "b"]
    for agent in ("a", "b"):
        st["anchors"][agent] = {"raw_reject": 0, "raw_ideal": 100}
    engine.add_candidate(st, "a", "Plan A", "r", cid="seed-a")
    engine.add_ratings(st, "a", [("seed-a", 90, "r")])
    engine.add_ratings(st, "b", [("seed-a", 80, "r")])
    engine.advance(st)
    assert st["outcome"] == "SUCCESS"


def test_a_new_agent_state_is_empty(state):
    assert state.new_agent_state("a", "s1") == {
        "me": "a", "id": "s1", "mirror": None, "asked": {}, "outbox": {}, "resend": None,
        "sent": {"anchor": False, "seed": False, "repair": [], "rated": []}, "lead": None}
    assert state.new_agent_state("a")["id"] is None
    assert state.new_agent_state("a")["sent"] is not state.new_agent_state("a")["sent"]
