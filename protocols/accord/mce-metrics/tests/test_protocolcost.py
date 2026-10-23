"""ACCORD-only token cost: the protocol window and the contract's overhead."""

import json

from accord_mce_metrics import convergence as cv
from accord_mce_metrics import protocolcost as pc

CONTRACT = {"status": "INTENT-LOCKED", "glossary": []}
BLOCK = "LOCKED_INTENT_CONTRACT: " + json.dumps(CONTRACT, indent=2)


def llm(span_id, agent_span, start, prompt, cache, completion, text="hello"):
    return {
        "id": span_id,
        "spanId": span_id,
        "parentSpanId": agent_span,
        "startTime": start,
        "prompt": json.dumps([{"role": "user", "content": text}]),
        "promptTokenCount": prompt,
        "cacheReadTokenCount": cache,
        "completionTokenCount": completion,
    }


def session(lock=True):
    tools = [{"id": "t1", "startTime": 10, "toolName": "activate_skill", "toolArguments": json.dumps({"name": "l9-accord-initiator"})}]
    if lock:
        tools.append({"id": "t2", "startTime": 20, "toolName": "accord_merge", "toolOutput": json.dumps({"data": {"locked_intent_contract": CONTRACT}})})
    return {
        "session_id": "s",
        "tool_spans": tools,
        "agent_spans": [{"id": "a1", "spanId": "s1", "startTime": 0, "agentName": "sre"}, {"id": "a2", "spanId": "s2", "startTime": 25, "agentName": "db"}],
        "llm_spans": [
            llm("l0", "s1", 5, 1000, 0, 10),  # before ACCORD
            llm("l1", "s1", 12, 2000, 500, 20),  # in the window
            llm("l2", "s1", 18, 3000, 2000, 30),  # in the window
            llm("l3", "db_unused", 22, 4000, 3000, 40),  # after the lock
            llm("l4", "s2", 26, 100, 0, 5, text="do the work\n" + BLOCK + "\nthanks"),  # carries the contract
        ],
    }


def fresh(ctx):
    cv._CACHE.clear()
    return ctx


def test_the_window_runs_from_the_first_activation_to_the_lock():
    w = pc.measure_window(fresh(session()))
    assert (w.start, w.end) == (10.0, 20.0)
    assert (w.llm_calls, w.prompt_tokens, w.cached_tokens, w.completion_tokens) == (2, 5000, 2500, 50)
    assert w.fresh_tokens == 2500 and list(w.agents) == ["sre"]


def test_with_no_lock_the_window_runs_to_the_end_of_the_session():
    w = pc.measure_window(fresh(session(lock=False)))
    assert w.end is None and w.llm_calls == 4 and w.prompt_tokens == 9100


def test_the_window_metrics_report_provider_usage_and_the_window_bounds():
    ctx = fresh(session())
    prompt = pc.AccordProtocolPromptTokens().compute("s", ctx)
    assert prompt.value == 5000 and not prompt.error
    detail = json.loads(prompt.reasoning)
    assert detail["locked"] and detail["window_start"] == 10 and detail["llm_calls"] == 2
    assert pc.AccordProtocolCachedTokens().compute("s", ctx).value == 2500
    assert pc.AccordProtocolFreshTokens().compute("s", ctx).value == 2500
    assert pc.AccordProtocolCompletionTokens().compute("s", ctx).value == 50


def test_a_session_where_accord_never_ran_is_skipped():
    ctx = fresh({"session_id": "s", "tool_spans": [], "agent_spans": [], "llm_spans": [llm("l", "s1", 1, 10, 0, 1)]})
    for metric in (pc.AccordProtocolPromptTokens(), pc.AccordContractOverheadTokens()):
        assert metric.compute("s", ctx).error


def test_the_contract_overhead_is_its_share_of_the_calls_text():
    calls = pc.measure_overhead(fresh(session()))
    assert len(calls) == 1 and calls[0].agent == "db" and calls[0].copies == 1
    text = "do the work\n" + BLOCK + "\nthanks"
    json_chars = len(json.dumps(CONTRACT, indent=2))  # the block is the JSON object after the marker
    assert calls[0].contract_chars == json_chars and calls[0].message_chars == len(text)
    assert abs(calls[0].estimated_tokens - 100 * json_chars / len(text)) < 1e-9


def test_the_overhead_metrics():
    ctx = fresh(session())
    total = pc.AccordContractOverheadTokens().compute("s", ctx)
    assert not total.error and 0 < total.value < 100
    assert json.loads(total.reasoning)["estimate"] is True
    assert pc.AccordContractOverheadPerCall().compute("s", ctx).value == total.value  # one call
    assert 0 < pc.AccordContractOverheadShare().compute("s", ctx).value < 1


def test_no_contract_in_any_prompt_means_no_overhead():
    ctx = session()
    ctx["llm_spans"] = ctx["llm_spans"][:4]
    assert pc.AccordContractOverheadTokens().compute("s", fresh(ctx)).error


def test_a_mention_of_the_marker_without_a_json_block_is_not_a_contract():
    ctx = session()
    ctx["llm_spans"][4] = llm("l4", "s2", 26, 100, 0, 5, text="LOCKED_INTENT_CONTRACT for this task already exists")
    assert pc.measure_overhead(fresh(ctx)) == []
