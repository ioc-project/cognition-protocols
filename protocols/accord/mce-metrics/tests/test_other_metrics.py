"""Correctness and glossary-benefit metrics over a small synthetic session."""

import json

from accord_mce_metrics import benefit as bf
from accord_mce_metrics import convergence as cv
from accord_mce_metrics import correctness as cr

CONTRACT = {
    "status": "INTENT-LOCKED",
    "glossary": [
        {
            "term": "severity",
            "variants": [
                {"agent": "sre", "definition": "a scale from one to five, five being worst"},
                {"agent": "db", "definition": "letters A to D naming the incident class"},
            ],
        }
    ],
}


def context(activated=True, final="severity is high"):
    tools = []
    if activated:
        tools.append({"id": "t1", "startTime": 1, "toolName": "activate_skill", "toolArguments": json.dumps({"name": "l9-accord-initiator"})})
        tools.append(
            {
                "id": "t2",
                "startTime": 3,
                "toolName": "accord_merge",
                "toolOutput": json.dumps({"data": {"locked_intent_contract": CONTRACT}}),
            }
        )
    return {
        "session_id": "s",
        "tool_spans": tools,
        "agent_spans": [
            {"id": "a1", "spanId": "s1", "startTime": 0, "agentName": "sre", "inputContent": "input:sre:triage this"},
            {"id": "a2", "spanId": "s2", "startTime": 5, "duration": 1, "agentName": "db", "inputContent": "check severity", "outputContent": "severity is high"},
        ],
        "llm_spans": [
            {"id": "l1", "spanId": "x1", "parentSpanId": "s1", "startTime": 0.1, "prompt": json.dumps([{"role": "system", "content": "- severity: a scale from one to five"}]),
             "promptTokenCount": 100, "cacheReadTokenCount": 40, "completionTokenCount": 10},
            {"id": "l2", "spanId": "x2", "parentSpanId": "s2", "startTime": 5.1, "prompt": json.dumps([{"role": "system", "content": "- severity: letters A to D naming the incident class"}]),
             "promptTokenCount": 50, "cacheReadTokenCount": 0, "completionTokenCount": 5},
        ],
        "input_text": "an incident",
        "output_text": final,
    }


def fresh(ctx):
    cv._CACHE.clear()
    cr._CACHE.clear()
    bf._UptakeMetric._CACHE.clear()
    return ctx


def test_correctness_scores_a_justified_and_used_term():
    ctx = fresh(context())
    assert cr.AccordLockedTerms().compute("s", ctx).value == 1
    assert cr.AccordLockPrecision().compute("s", ctx).value == 1.0
    assert cr.AccordOverSelected().compute("s", ctx).value == 0
    assert cr.AccordUnderSelected().compute("s", ctx).value == 0
    assert cr.AccordTermUtilization().compute("s", ctx).value == 1.0


def test_term_mentioned_only_before_the_lock_is_not_used():
    ctx = fresh(context(final=""))
    ctx["agent_spans"][1]["startTime"] = 2  # the delegation now precedes the lock at t=3
    assert cr.AccordTermUtilization().compute("s", ctx).value == 0.0


def test_correctness_skips_without_activation_and_without_a_lock():
    result = cr.AccordLockPrecision().compute("s", fresh(context(activated=False)))
    assert result.error and "No evidence of ACCORD activation" in result.error
    ctx = fresh(context())
    ctx["tool_spans"] = ctx["tool_spans"][:1]  # activated, but no contract came back
    assert "no locked contract" in cr.AccordLockPrecision().compute("s", ctx).error


def stub_judge(monkeypatch, replies):
    prompts = []

    def call_llm(self, prompt, **kwargs):
        prompts.append((prompt, kwargs))
        return replies.pop(0)

    monkeypatch.setattr(bf._BenefitMetric, "call_llm", call_llm)
    return prompts


def test_uptake_needs_a_quote_and_is_judged_once_for_both_metrics(monkeypatch):
    reply = json.dumps({"terms": [{"term": "severity", "handling": "explicit_definition", "evidence": "", "influenced_reasoning": True, "influence_evidence": "x"}]})
    prompts = stub_judge(monkeypatch, [reply])
    ctx = fresh(context())
    uptake = bf.AccordGlossaryUptake().compute("s", ctx)
    influence = bf.AccordGlossaryInfluence().compute("s", ctx)
    assert uptake.value == 0.0 and influence.value == 0.0  # no quote, so downgraded to "unqualified"
    assert len(prompts) == 1


def test_uptake_credits_a_quoted_definition(monkeypatch):
    reply = json.dumps({"terms": [{"term": "severity", "handling": "sense_qualified", "evidence": "severity (1-5)", "influenced_reasoning": True, "influence_evidence": "so we escalate"}]})
    stub_judge(monkeypatch, [reply])
    ctx = fresh(context())
    assert bf.AccordGlossaryUptake().compute("s", ctx).value == 1.0
    assert bf.AccordGlossaryInfluence().compute("s", ctx).value == 1.0


def test_benefit_skips_without_activation(monkeypatch):
    stub_judge(monkeypatch, [])
    assert "No evidence of ACCORD activation" in bf.AccordGlossaryUptake().compute("s", fresh(context(activated=False))).error
