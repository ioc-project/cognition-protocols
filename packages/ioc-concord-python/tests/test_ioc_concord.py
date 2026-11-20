#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Smoke test of the Python bindings: a whole run between two agents, as in the Rust quick start.

Run it with the package installed (see README.md).
"""
import importlib.metadata
import json
import tomllib
from pathlib import Path

import pytest

import ioc_concord as ic


SPEC = json.dumps({"type": "string", "description": "what one option looks like"})


def run_to_the_end(agents, opening):
    queue = list(opening.send)
    while queue or any(a.due() for a in agents):
        delivered, queue = queue, []
        for text in delivered:
            for agent in agents:
                step = agent.receive(text)
                queue.extend(step.send)
                queue.extend(step.forward)
        for agent in agents:
            for task in agent.due():
                if isinstance(task, ic.Task.INIT_REPLY):
                    step = agent.submit_init_reply(
                        task.run_id, ic.Number.INT(0), ic.Number.INT(100), "plan A", "the facts", ic.Number.INT(90), "why"
                    )
                elif isinstance(task, ic.Task.SCORE):
                    scores = [ic.ScoreInput(id=c.id, raw=ic.Number.INT(80), reasoning="it holds") for c in task.candidates]
                    step = agent.submit_score(task.run_id, scores)
                elif isinstance(task, ic.Task.REPAIR):
                    step = agent.submit_repair(task.run_id, "plan B", "the facts", ic.Number.INT(90), "why")
                else:
                    continue
                queue.extend(step.send)
                queue.extend(step.forward)


def test_versions():
    cargo = tomllib.loads((Path(__file__).parent.parent.parent / "ioc-concord-rs" / "Cargo.toml").read_text())
    assert ic.version() == cargo["package"]["version"] == importlib.metadata.version("ioc-concord")
    assert ic.protocol_version() == "0.3.0"


def test_a_run():
    agents = [ic.Agent("cost"), ic.Agent("security")]
    opening = agents[0].start(["cost", "security"], "which plan?", "no plan", "the best plan", SPEC)
    assert any(e.event == "started" for e in opening.events)
    run_to_the_end(agents, opening)
    report = json.loads(agents[1].report_json())
    assert report["outcome"] == "SUCCESS"
    assert "SUCCESS" in agents[1].ending()


def test_instructions_for_a_task():
    agent = ic.Agent("cost")
    agent.start(["cost", "security"], "which plan?", "no plan", "the best plan", SPEC, params=ic.RunParams(budget=4))
    (task,) = agent.due()
    assert isinstance(task, ic.Task.INIT_REPLY)
    assert "which plan?" in agent.instructions(task)


def test_errors_are_exceptions():
    with pytest.raises(ic.ConcordError.State):
        ic.Agent("not an agent id!")
    agent = ic.Agent("cost")
    with pytest.raises(ic.ConcordError.Schema):
        agent.start(["cost", "security"], "c", "r", "i", "not json")
    with pytest.raises(ic.ConcordError.InvalidMessage):
        agent.start(["cost", "security"], "c", "r", "i", "[]")


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "cost.json")
    ic.Agent("cost", path).start(["cost", "security"], "which plan?", "no plan", "the best plan", SPEC)
    again = ic.Agent("cost", path)
    assert [t.run_id for t in again.due()]


def test_relay():
    centre = ic.Agent.new_centre("hub", ["a", "b"])
    assert centre.spokes() == ["a", "b"]
    centre.add_spoke("c")
    assert centre.targets("a") == ["b", "c"]
    relay = ic.Relay()
    relay.add_spoke("a")
    relay.add_spoke("b")
    assert relay.targets("a") == ["b"]


def test_an_agent_that_is_not_a_centre_has_no_spokes():
    with pytest.raises(ic.ConcordError.State):
        ic.Agent("cost").spokes()


def test_a_refusal_is_the_one_of_the_library():
    error = ic.ConcordError.InvalidMessage(message="the score is out of range")
    assert ic.refusal(error, 1).startswith("refused: the score is out of range")
    assert not ic.settled(error)
