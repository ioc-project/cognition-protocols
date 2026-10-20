# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""The LLM-judged metrics, with the judge replaced by a canned answer."""

import json

from concord_mce_metrics import judged as jd
from fixtures import context, scores, tool_spans


def judge_with(metric, answers):
    """Make ``metric.call_llm`` return the canned answers in turn; returns the prompts it was asked."""
    prompts, queue = [], list(answers)

    def call_llm(prompt, **kwargs):
        prompts.append(prompt)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    metric.call_llm = call_llm
    return prompts


def fresh(**kw):
    jd._Judged._CACHE.clear()
    return context(**kw)


def test_parse_direction_needs_every_item_exactly_once():
    assert jd.parse_direction("1 | FOR\n2 | against", 2) == ["FOR", "AGAINST"]
    assert jd.parse_direction("1 | FOR", 2) is None
    assert jd.parse_direction("1 | FOR\n1 | MIXED", 2) is None
    assert jd.parse_direction("1 | MAYBE", 1) is None


def test_satisfied_when_every_agent_clears_tau_and_argues_for_the_winner():
    metric = jd.ConcordOutcomeSatisfied()
    prompts = judge_with(metric, ["1 | FOR\n2 | FOR"])
    result = metric.compute("s", fresh())
    assert result.value == 1 and not result.error and len(prompts) == 1
    assert "restart api" in prompts[0]  # the winner is what the agents' reasoning is about


def test_a_score_above_tau_with_reasoning_against_is_a_compromise():
    metric = jd.ConcordOutcomeSatisfied()
    judge_with(metric, ["1 | FOR\n2 | AGAINST"])
    result = metric.compute("s", fresh())
    assert result.value == 0 and result.reasoning.startswith("COMPROMISE")


def test_below_tau_needs_no_llm_call():
    metric = jd.ConcordOutcomeSatisfied()
    prompts = judge_with(metric, [])
    ctx = fresh(score_a=scores(**{"c1-a": (30, "no"), "c2-b": (90, "mine")}), score_b=scores(**{"c1-a": (30, "no"), "c2-b": (90, "mine")}))
    result = metric.compute("s", ctx)
    assert result.value == 0 and result.reasoning.startswith("NOT_SATISFIED") and prompts == []


def test_a_judge_that_never_answers_validly_is_unreliable_and_skipped_not_scored_zero():
    metric = jd.ConcordOutcomeSatisfied()
    prompts = judge_with(metric, ["I think so", RuntimeError("down"), "1 | FOR"])  # three attempts, none valid
    result = metric.compute("s", fresh())
    assert result.error and result.error.startswith("UNRELIABLE") and len(prompts) == 3


def test_a_malformed_answer_is_retried():
    metric = jd.ConcordOutcomeSatisfied()
    prompts = judge_with(metric, ["nonsense", "1 | FOR\n2 | FOR"])
    assert metric.compute("s", fresh()).value == 1 and len(prompts) == 2


def test_satisfaction_metrics_share_one_judge_call():
    ctx = fresh()
    first, second = jd.ConcordOutcomeSatisfied(), jd.ConcordSatisfiedAgents()
    judge_with(first, ["1 | FOR\n2 | AGAINST"])
    assert first.compute("s", ctx).value == 0
    prompts = judge_with(second, [])  # would fail on an empty queue if it asked again
    assert second.compute("s", ctx).value == 0.5 and prompts == []


def test_fairness_flags_a_score_against_the_scorers_own_reasoning():
    # a scored b's c2-b at 20 (LOW) with reasoning that argues FOR it; b scored a's c1-a 85 (HIGH).
    metric = jd.ConcordScoreFair()
    prompts = judge_with(metric, ["1 | FOR", "1 | FOR"])  # one question per agent, a then b
    result = metric.compute("s", fresh())
    assert result.value == 0 and result.reasoning.startswith("UNFAIR") and len(prompts) == 2
    detail = json.loads(result.reasoning.split("; ", 1)[1])
    assert detail["a"]["unfair"] == 1 and detail["b"]["unfair"] == 0


def test_fairness_is_fair_when_scores_follow_the_reasoning():
    metric = jd.ConcordScoreFair()
    judge_with(metric, ["1 | AGAINST", "1 | FOR"])
    assert metric.compute("s", fresh()).value == 1


def test_mixed_reasoning_is_never_unfair():
    metric = jd.ConcordUnfairScoreShare()
    judge_with(metric, ["1 | MIXED", "1 | MIXED"])
    assert metric.compute("s", fresh()).value == 0


def test_similar_proposals_scored_the_same_are_not_judged():
    spans = tool_spans(
        score_a=scores(**{"c1-a": (90, "mine"), "c2-b": (88, "fine too")}),  # a rates b's near-identical option as high as its own
    )
    # make the two candidates the same action
    for s in spans:
        out = json.loads(s["toolOutput"])
        out["stdout"] = out["stdout"].replace("enable debug logging", "restart api")
        s["toolOutput"] = json.dumps(out)
    metric = jd.ConcordUnfairScoreShare()
    prompts = judge_with(metric, ["1 | FOR"])  # only b's score of a's candidate is left to judge
    result = metric.compute("s", fresh(spans=spans))
    assert result.value == 0 and len(prompts) == 1


def test_skipped_when_not_used():
    metric = jd.ConcordScoreFair()
    assert "not used" in metric.compute("s", context(spans=[])).error
