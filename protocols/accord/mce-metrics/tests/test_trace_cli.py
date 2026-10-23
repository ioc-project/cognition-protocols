"""The trace CLI runs every metric the package registers, from a MAS Lab events.jsonl."""

import json
import tomllib
from pathlib import Path

from accord_mce_metrics import benefit as bf
from accord_mce_metrics import trace_cli as cli

CONTRACT = {
    "status": "INTENT-LOCKED",
    "roster": ["sre", "db"],
    "problem_frame": [{"id": "p1", "text": "x", "support": 2}],
    "verifiable_objective": {"owners": {"p1": "sre"}, "uncovered": [], "checks": [{"atom": "p1", "criterion": "c"}]},
    "flagged_items": ["ambiguous_term: severity"],
    "glossary": [
        {
            "term": "severity",
            "status": "flagged",
            "variants": [
                {"agent": "sre", "definition": "a scale from one to five, five being worst"},
                {"agent": "db", "definition": "letters A to D naming the incident class"},
            ],
        }
    ],
}


def _trace(path: Path) -> Path:
    def system(text):
        return [{"role": "system", "content": text}, {"role": "user", "content": "go"}]

    events = [
        {"kind": "execution_start", "agent_id": "sre", "call_id": "e1", "input": "an incident", "timestamp": 1},
        {"kind": "llm_call_start", "agent_id": "sre", "call_id": "l1", "parent_call_id": "e1", "timestamp": 2},
        {"kind": "context_assembled", "agent_id": "sre", "llm_call_id": "", "timestamp": 2.1,
         "messages": system("- severity: a scale from one to five, five being worst")},
        {"kind": "llm_call_end", "agent_id": "sre", "call_id": "l1", "timestamp": 3,
         "tokens_used": json.dumps({"prompt_tokens": 100, "cache_read_input_tokens": 40, "completion_tokens": 10})},
        {"kind": "tool_call_start", "agent_id": "sre", "call_id": "t1", "tool_name": "activate_skill",
         "arguments": json.dumps({"name": "l9-accord-initiator"}), "timestamp": 4},
        {"kind": "tool_call_end", "agent_id": "sre", "call_id": "t1", "output": "ok", "timestamp": 4.1},
        {"kind": "tool_call_start", "agent_id": "sre", "call_id": "t2", "tool_name": "accord_merge", "arguments": "{}", "timestamp": 5},
        {"kind": "tool_call_end", "agent_id": "sre", "call_id": "t2", "timestamp": 5.1,
         "output": json.dumps({"data": {"locked_intent_contract": CONTRACT}})},
        {"kind": "llm_call_start", "agent_id": "sre", "call_id": "l3", "parent_call_id": "e1", "timestamp": 4.5},
        {"kind": "llm_call_end", "agent_id": "sre", "call_id": "l3", "timestamp": 4.6,
         "tokens_used": json.dumps({"prompt_tokens": 200, "cache_read_input_tokens": 50, "completion_tokens": 20})},
        {"kind": "llm_call_start", "agent_id": "sre", "call_id": "l4", "parent_call_id": "e1", "timestamp": 5.5},
        {"kind": "context_assembled", "agent_id": "sre", "llm_call_id": "l4", "timestamp": 5.6,
         "messages": system("LOCKED_INTENT_CONTRACT: " + json.dumps(CONTRACT))},
        {"kind": "llm_call_end", "agent_id": "sre", "call_id": "l4", "timestamp": 5.7,
         "tokens_used": json.dumps({"prompt_tokens": 400, "completion_tokens": 30})},
        {"kind": "execution_start", "agent_id": "db", "call_id": "e2", "input": "check severity", "timestamp": 6},
        {"kind": "llm_call_start", "agent_id": "db", "call_id": "l2", "parent_call_id": "e2", "timestamp": 6.1},
        {"kind": "context_assembled", "agent_id": "db", "llm_call_id": "l2", "timestamp": 6.2,
         "messages": system("- severity: letters A to D naming the incident class")},
        {"kind": "llm_call_end", "agent_id": "db", "call_id": "l2", "timestamp": 7,
         "tokens_used": json.dumps({"prompt_tokens": 50, "completion_tokens": 5})},
        {"kind": "user_response", "agent_id": "db", "parent_call_id": "e2", "content": "severity is high", "timestamp": 8},
        {"kind": "user_response", "agent_id": "sre", "parent_call_id": "e1", "content": "severity is high", "timestamp": 9},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events))
    return path


def _registered() -> set[str]:
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    return set(pyproject["project"]["entry-points"]["metrics_computation_engine.plugins"])


def test_the_cli_covers_every_registered_metric():
    assert {c.NAME for c in cli.DETERMINISTIC + cli.JUDGED} == _registered()


def test_without_judge_it_runs_the_deterministic_metrics(tmp_path):
    rows = {r["metric"]: r for r in cli.run(_trace(tmp_path / "events.jsonl"))}
    assert set(rows) == {c.NAME for c in cli.DETERMINISTIC}
    assert not [r for r in rows.values() if (r["skipped"] or "").startswith("failed")]
    assert rows["AccordConverged"]["value"] == 1.0
    assert rows["AccordLockPrecision"]["value"] == 1.0
    assert rows["AccordProtocolPromptTokens"]["value"] == 200  # only the call inside the ACCORD window
    assert rows["AccordContractOverheadTokens"]["value"] > 0
    # every deterministic metric has a value on this session; none is skipped
    assert [m for m, r in rows.items() if r["skipped"]] == ["AccordRelayedValid"]


def test_with_judge_it_runs_all_of_them(tmp_path, monkeypatch):
    verdict = {"terms": [{"term": "severity", "handling": "explicit_definition", "evidence": "q",
                          "influenced_reasoning": True, "influence_evidence": "q"}]}
    monkeypatch.setattr(bf._BenefitMetric, "judge", lambda self, prompt, sample: verdict)
    rows = {r["metric"]: r for r in cli.run(_trace(tmp_path / "events.jsonl"), judge=True)}
    assert set(rows) == _registered()
    assert rows["AccordGlossaryUptake"]["value"] == 1.0 and rows["AccordGlossaryInfluence"]["value"] == 1.0
