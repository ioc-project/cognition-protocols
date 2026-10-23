"""Run the ACCORD metrics on MAS Lab trace files.

A development tool: the metrics read the KG's span dicts, so this builds those
from a trace's events, in memory, and runs the very same metric classes the
worker runs. The LLM-judged metrics (benefit) are left out unless ``--judge``
is given; they need an LLM, configured with ``LLM_BASE_MODEL_URL``,
``LLM_API_KEY`` and ``LLM_MODEL_NAME``.

    python -m accord_mce_metrics.trace_cli TRACE.jsonl [TRACE.jsonl ...] [--judge] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import benefit as bf
from . import convergence as cv
from . import correctness as cr
from . import protocolcost as pc

DETERMINISTIC = (
    cv.AccordConverged,
    cv.AccordPhaseCoverage,
    cv.AccordCanonicalValid,
    cv.AccordRelayedValid,
    cv.AccordFabricatedLock,
    cv.AccordContractWarnings,
    cr.AccordLockPrecision,
    cr.AccordLockRecall,
    cr.AccordTermUtilization,
    cr.AccordLockedTerms,
    cr.AccordOverSelected,
    cr.AccordUnderSelected,
    pc.AccordProtocolPromptTokens,
    pc.AccordProtocolCachedTokens,
    pc.AccordProtocolFreshTokens,
    pc.AccordProtocolCompletionTokens,
    pc.AccordContractOverheadTokens,
    pc.AccordContractOverheadPerCall,
    pc.AccordContractOverheadShare,
)
JUDGED = (
    bf.AccordGlossaryUptake,
    bf.AccordGlossaryInfluence,
)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def spans_from_trace(path: Path) -> dict[str, Any]:
    """The session context: each call's start and end events paired into one KG-style span.

    ``tool_spans`` from ``tool_call_*``, ``llm_spans`` from ``llm_call_*`` (the prompt from the agent's
    ``context_assembled``, usage from ``tokens_used``), ``agent_spans`` from ``execution_*`` and ``user_response``,
    and the mission and final answer as ``input_text`` / ``output_text``.
    """
    tools: dict[str, dict[str, Any]] = {}
    llms: dict[str, dict[str, Any]] = {}
    agents: dict[str, dict[str, Any]] = {}
    last_llm: dict[str, str] = {}  # agent -> its latest LLM call still without a prompt
    replies: list[tuple[str, str, str]] = []  # (agent, execution call, content)

    with path.open(encoding="utf-8", errors="replace") as handle:
        for position, line in enumerate(handle):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, call_id = event.get("kind"), event.get("call_id")
            agent = str(event.get("agent_id") or "")
            when = float(event.get("timestamp") or position)
            if kind == "tool_call_start":
                tools[call_id] = {
                    "id": call_id,
                    "startTime": when,
                    "toolName": event.get("tool_name") or event.get("name"),
                    "toolArguments": event.get("arguments"),
                    "parentSpanId": event.get("parent_call_id"),
                }
            elif kind == "tool_call_end" and call_id in tools:
                tools[call_id].update(endTime=when, toolOutput=event.get("output"))
            elif kind == "llm_call_start":
                llms[call_id] = {"id": call_id, "startTime": when, "parentSpanId": event.get("parent_call_id")}
                last_llm[agent] = call_id
            elif kind == "llm_call_end" and call_id in llms:
                usage = _json(event.get("tokens_used"))
                llms[call_id].update(endTime=when)
                if isinstance(usage, dict) and usage:
                    llms[call_id].update(
                        promptTokenCount=usage.get("prompt_tokens") or 0,
                        cacheReadTokenCount=usage.get("cache_read_input_tokens") or 0,
                        completionTokenCount=usage.get("completion_tokens") or 0,
                    )
            elif kind == "context_assembled" and event.get("messages"):
                target = str(event.get("llm_call_id") or "") or last_llm.pop(agent, "")
                if target in llms:
                    llms[target]["prompt"] = event["messages"]
            elif kind == "execution_start":
                agents[call_id] = {
                    "spanId": call_id,
                    "agentName": agent,
                    "startTime": when,
                    "inputContent": _text(event.get("input") or ""),
                }
            elif kind == "execution_end" and call_id in agents:
                agents[call_id]["duration"] = when - agents[call_id]["startTime"]
            elif kind == "user_response":
                replies.append((agent, str(event.get("parent_call_id") or ""), str(event.get("content") or "")))

    for agent, execution, content in replies:
        if execution in agents:
            agents[execution]["outputContent"] = content

    spans = sorted(agents.values(), key=lambda s: s["startTime"])
    return {
        "tool_spans": list(tools.values()),
        "llm_spans": list(llms.values()),
        "agent_spans": spans,
        "input_text": spans[0]["inputContent"] if spans else "",
        "output_text": spans[0].get("outputContent", "") if spans else "",
    }


def tool_spans_from_trace(path: Path) -> list[dict[str, Any]]:
    return spans_from_trace(path)["tool_spans"]


def configure_judge() -> None:
    """Point MCE's LLM service at the endpoint named by the environment."""
    from mce.engine.llm import LLMService

    try:
        import openai  # noqa: F401  (MCE's LLM service needs it, and fails late without it)
    except ImportError:
        raise SystemExit("--judge needs the openai package: run with `uv run --extra judge ...` or `pip install openai`")
    missing = [n for n in ("LLM_API_KEY", "LLM_MODEL_NAME") if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"--judge needs {' and '.join(missing)} set (and LLM_BASE_MODEL_URL for a non-OpenAI endpoint)")
    LLMService.configure(
        mode="live",
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL_NAME"],
        base_url=os.environ.get("LLM_BASE_MODEL_URL") or None,
    )


def run(path: Path, judge: bool = False) -> list[dict[str, Any]]:
    session_id = path.parent.name if path.name == "events.jsonl" else path.stem
    context = {"session_id": session_id, **spans_from_trace(path)}
    cv._CACHE.clear()
    cr._CACHE.clear()
    bf._UptakeMetric._CACHE.clear()
    out = []
    for cls in DETERMINISTIC + (JUDGED if judge else ()):
        try:
            result = cls().compute(session_id, context)
            row = {
                "metric": result.metric_id,
                "value": None if result.error else result.value,
                "skipped": result.error,
                "reasoning": result.reasoning if not result.error else "",
            }
        except Exception as exc:  # a judge outage must not hide the other metrics
            row = {"metric": cls.NAME, "value": None, "skipped": f"failed: {exc}", "reasoning": ""}
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--judge", action="store_true", help="also run the LLM-judged metrics")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    if args.judge:
        configure_judge()

    results = {str(p): run(p, args.judge) for p in args.traces}
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    for path, metrics in results.items():
        print(path)
        for m in metrics:
            shown = f"skipped ({m['skipped']})" if m["skipped"] else f"{m['value']:g}"
            note = f"  - {m['reasoning']}" if m["reasoning"] and not m["skipped"] else ""
            print(f"  {m['metric']:<32} {shown}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
