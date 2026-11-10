#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Script tests: the script's complete output on scripted runs must not change.

    python3 -m pytest protocols/accord/tests        compare against the recorded transcripts
    UPDATE_TRANSCRIPTS=1 python3 -m pytest protocols/accord/tests    re-record them (review the diff before committing)

A transcript changes only when the script's behavior does, so a refactor that must not change behavior leaves them as they are.
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import driver  # noqa: E402
import scenarios  # noqa: E402

TRANSCRIPTS = Path(__file__).resolve().parent / "transcripts"


@pytest.mark.parametrize("name", sorted(scenarios.SCENARIOS))
def test_transcript(name):
    got = driver.dump(scenarios.SCENARIOS[name]())
    assert "Traceback" not in got, "a script crashed with a traceback"
    path = TRANSCRIPTS / f"{name}.txt"
    if os.environ.get("UPDATE_TRANSCRIPTS"):
        TRANSCRIPTS.mkdir(exist_ok=True)
        path.write_text(got)
        return
    assert path.exists(), f"no transcript for {name}: run with UPDATE_TRANSCRIPTS=1"
    assert got == path.read_text(), f"{name}: output differs from the recorded transcript"


@pytest.mark.parametrize("name", sorted(scenarios.SCENARIOS))
def test_runs_are_deterministic(name):
    """Every scenario, not just the reference one: a hash-ordered dict in the output only shows up in some of them."""
    run = scenarios.SCENARIOS[name]
    assert driver.dump(run()) == driver.dump(run())


def test_no_action_is_allowed_before_the_contract_is_locked():
    """The protocol's central claim: every phase result before LOCK forbids app execution."""
    run = scenarios.split_term_triggers_clarify()
    results = [json.loads(e["stdout"]) for e in run.log if e["stdout"].startswith("{")]
    before = [r["data"] for r in results if not r["data"].get("locked_contract_exists")]
    assert before, "no pre-lock results in the run"
    assert not any(r.get("app_execution_allowed") for r in before)
    assert not any(r.get("normal_execution_allowed") for r in before)


def test_today_lock_reports_intent_locked_on_a_session_that_ran_no_phases():
    """Recorded, not endorsed. `lock` has no precondition, so an empty session yields an empty INTENT-LOCKED contract.

    That status is what the skill gates every app action on, so this is the one place a premature call produces a
    green light over nothing. Change the assertion when `lock` learns a precondition.
    """
    run = driver.Run("test-empty-lock")
    run.initiator(["init", *scenarios.init_args()])
    contract = json.loads(run.initiator(["lock"]).stdout)["data"]
    assert contract["status"] == "INTENT-LOCKED"
    assert contract["roster"] == [] and contract["glossary"] == [] and contract["problem_frame"] == []


def test_a_wrong_check_is_refused_however_often_it_is_retried():
    """The leniency that keeps a non-echoing receiver from stalling the session must not extend to a wrong check:
    that text belongs to another message, so recording it would file the wrong content under this phase."""
    run = driver.Run("test-wrong-check")
    d = driver.data(run.initiator(["init", *scenarios.init_args()]))
    tampered = re.sub(r'check="[0-9a-f]+"', 'check="deadbeef"', d["messages"]["telemetry"].split("\n", 1)[0])
    card = json.dumps({"agent": "telemetry", "roles": ["r"], "capabilities": ["c"], "access_scopes": ["s"],
                       "holdings_summary": "h"})

    for _ in range(3):
        out = json.loads(run.initiator(["handle", "--agent", "telemetry", "--reply", f"{tampered}\n\n{card}"]).stdout)
        assert out["ok"] is False and out["status"] == "ERROR"
        assert "does not match its check" in out["error"]


def test_a_missing_delivery_line_is_refused_once_then_recorded_with_a_defect():
    """A receiver that does not echo the line must not stall the session, so the second attempt is taken — with a
    defect recorded against it. The body still has to be readable; that is `validate_reply`'s job, not this one."""
    run = driver.Run("test-unusable")
    run.initiator(["init", *scenarios.init_args()])
    card = json.dumps({"agent": "telemetry", "roles": ["r"], "capabilities": ["c"], "access_scopes": ["s"],
                       "holdings_summary": "h"})

    first = json.loads(run.initiator(["handle", "--agent", "telemetry", "--reply", card]).stdout)
    assert first["ok"] is False and first["status"] == "ERROR"
    assert "carried no" in first["error"] and first["next"]

    second = run.initiator(["handle", "--agent", "telemetry", "--reply", card])
    assert driver.data(second)["still_waiting_this_phase"] == ["database"]
    state = json.loads(next(p for p in (run.tmp / "state").iterdir()).read_text())
    assert [d["kind"] for d in state["defects"]] == ["unverified_reply"]


def test_a_summary_is_refused_however_often_it_is_sent():
    """What actually stops summarization: a body the phase's merge would take nothing from is never recorded.

    The delivery check alone could not do this — an orchestrator holding the request can copy the line onto anything.
    """
    run = driver.Run("test-summary")
    d = driver.data(run.initiator(["init", *scenarios.init_args()]))
    delivery = d["messages"]["telemetry"].split("\n", 1)[0]

    for _ in range(3):
        out = json.loads(run.initiator(
            ["handle", "--agent", "telemetry", "--reply", f"{delivery}\n\nTelemetry sees elevated errors."]).stdout)
        assert out["ok"] is False and out["status"] == "ERROR"
        assert "is not a JSON object" in out["error"]
        assert "CONVENE needs" in out["error"]

    # and the phase is still open: nothing was recorded for that agent
    state = json.loads(next(p for p in (run.tmp / "state").iterdir()).read_text())
    assert state["handle_replies"] == {}


def test_the_bar_is_the_shape_not_how_much_the_reply_says():
    """An empty-but-shaped reply is a legitimate answer and must pass; the same emptiness without the shape is not."""
    run = driver.Run("test-readable")
    d = driver.data(run.initiator(["init", *scenarios.init_args()]))
    delivery = d["messages"]["telemetry"].split("\n", 1)[0]

    def send(agent, body):
        return json.loads(run.initiator(["handle", "--agent", agent, "--reply", f"{delivery}\n\n{body}"]).stdout)

    assert send("telemetry", '{"agent": "telemetry", "roles": [], "capabilities": []}')["ok"] is True
    refused = send("database", '{"agent": "database", "summary": "database is healthy"}')
    assert refused["ok"] is False and "none of the agent-card fields" in refused["error"]


def test_today_a_default_self_driving_run_locks_an_empty_verifiable_objective():
    """Recorded, not endorsed. `handle` accepts --required-knowledge and --objective-clauses, so the self-driving
    path *can* populate the objective — but nothing asks for them: `init` does not take them, no phase message
    mentions them, and no `next_command` hint names them. A run that does not know to pass them locks an empty one.
    """
    self_driven = driver.contract_of(scenarios.split_term_triggers_clarify())["verifiable_objective"]
    assert self_driven == {"owners": {}, "uncovered": [], "checks": []}

    stepwise = driver.contract_of(scenarios.step_by_step_phase_commands())["verifiable_objective"]
    assert stepwise["checks"], "the step-by-step path should keep checks that cover a clause"
    assert stepwise["owners"], "knowledge an agent holds should be recorded against it"
    assert stepwise["uncovered"] == ["checkout error budget policy"], "a gap held by nobody must be reported"


def test_a_term_two_agents_measure_differently_is_not_silently_merged():
    """GROUND must flag the fraction/percent split rather than pick one, and the locked Glossary must record it."""
    contract = driver.contract_of(scenarios.split_term_triggers_clarify())
    entry = next(e for e in contract["glossary"] if e["term"] == "error_rate")
    assert entry["status"] != "agreed", "a real unit split was merged into a single definition"

    agreed = driver.contract_of(scenarios.agreed_terms_skip_clarify())
    entry = next(e for e in agreed["glossary"] if e["term"] == "error_rate")
    assert entry["status"] == "agreed", "agents reporting the same unit should ground to one definition"
