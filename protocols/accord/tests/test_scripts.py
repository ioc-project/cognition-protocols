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


def test_today_an_unusable_reply_counts_as_that_agent_having_answered():
    """Recorded, not endorsed. Prose and truncated JSON are accepted silently: the agent leaves `awaiting` having
    contributed nothing, and the phase merges without it rather than re-asking."""
    run = driver.Run("test-unusable")
    run.initiator(["init", *scenarios.init_args()])
    before = driver.data(run.initiator(["handle", "--agent", "telemetry", "--reply", "prose, not a reply"]))
    assert before["still_waiting_this_phase"] == ["database"], "telemetry was counted as having replied"
    assert "error" not in before


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
