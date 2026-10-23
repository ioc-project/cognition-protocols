import json

from accord_mce_metrics import convergence as cv

CONTRACT = {
    "status": "INTENT-LOCKED",
    "roster": ["a", "b"],
    "problem_frame": [{"id": "p1", "text": "x", "support": 2}],
    "glossary": [{"term": "t", "status": "agreed", "definition": "d"}],
    "verifiable_objective": {"owners": {"p1": "a"}, "uncovered": [], "checks": [{"atom": "p1", "criterion": "c"}]},
    "flagged_items": [],
}


def span(i, name, args="{}", out=""):
    return {"id": f"t{i}", "startTime": i, "toolName": name, "toolArguments": args, "toolOutput": out}


def spans(relay=True, canonical=True):
    out = [span(1, "activate_skill", json.dumps({"name": "l9-accord-initiator"}))]
    if canonical:
        out.append(span(2, "accord_merge", out=json.dumps({"data": {"locked_intent_contract": CONTRACT}})))
    if relay:
        out.append(span(3, "delegate_to_x", json.dumps({"task": "LOCKED_INTENT_CONTRACT\n" + json.dumps(CONTRACT)})))
    return out


def run(metric_cls, tool_spans, sid="s"):
    cv._CACHE.clear()
    return metric_cls().compute(sid, {"session_id": sid, "tool_spans": tool_spans})


def test_converged_session():
    assert run(cv.AccordConverged, spans()).value == 1.0
    assert run(cv.AccordCanonicalValid, spans()).value == 1.0
    assert run(cv.AccordRelayedValid, spans()).value == 1.0
    assert run(cv.AccordFabricatedLock, spans()).value == 0.0


def test_fabricated_lock_and_missing_canonical_not_scored_as_invalid():
    s = spans(canonical=False)
    assert run(cv.AccordConverged, s).value == 0.0
    assert run(cv.AccordFabricatedLock, s).value == 1.0
    skipped = run(cv.AccordCanonicalValid, s)
    assert skipped.error and skipped.value == 0.0  # error results are not persisted


def test_session_without_accord_is_skipped_by_every_metric():
    for cls in (cv.AccordConverged, cv.AccordFabricatedLock, cv.AccordPhaseCoverage):
        assert run(cls, []).error


def test_phase_coverage_and_stdout_wrapped_canonical():
    s = [
        span(1, "run_skill_script", json.dumps({"skill": "l9-accord-initiator", "args": ["init"]})),
        span(2, "run_skill_script", json.dumps({"skill": "l9-accord-initiator", "args": ["verify"]}),
             json.dumps({"stdout": json.dumps({"data": {"locked_intent_contract": CONTRACT}})})),
    ]
    assert run(cv.AccordPhaseCoverage, s).value == 2 / 5
    assert run(cv.AccordConverged, s).value == 1.0


def test_undecodable_arguments_fall_back_to_text():
    s = [span(1, "activate_skill", "name=l9-accord-initiator")]
    assert run(cv.AccordConverged, s).value == 0.0  # activated, no contract
    assert not run(cv.AccordConverged, s).error


def test_spans_are_read_in_execution_order():
    s = list(reversed(spans()))
    assert run(cv.AccordConverged, s).value == 1.0


def test_diagnostics_log_what_the_session_carried(caplog):
    import logging

    cv._CACHE.clear()
    s = spans()
    s[1]["toolOutput"] = '{"data": {"locked_intent_contra'  # truncated, as a KG field might be
    with caplog.at_level(logging.INFO, logger="accord_mce_metrics.diagnostics"):
        cv.AccordConverged().compute("s", {"session_id": "s", "tool_spans": s})
    text = caplog.text
    assert "3 tool spans" in text and "accord_merge" in text
    assert "canonical_found=False" in text
    assert "str[" in text  # the raw output's type and length are shown, which exposes truncation
    assert "phases never seen" in text
    assert "canonical contract not found" in text


def test_relayed_contract_is_found_on_the_delegated_agent_span():
    cv._CACHE.clear()
    agent = {"id": "a1", "startTime": 5, "inputContent": "LOCKED_INTENT_CONTRACT\n" + json.dumps(CONTRACT)}
    context = {"session_id": "s", "tool_spans": spans(relay=False), "agent_spans": [agent]}
    assert cv.AccordRelayedValid().compute("s", context).value == 1.0
    assert cv.AccordFabricatedLock().compute("s", context).value == 0.0
