"""Run the Concord metrics on MAS Lab ``events.jsonl`` traces.

A development tool: the metrics read the KG's span dicts, so this builds those
from a trace's tool and LLM call events, in memory, and runs the very same
metric classes the worker runs. The LLM-judged metrics are left out unless
``--judge`` is given (they need the MCE LLM service configured).

    python -m concord_mce_metrics.trace_cli TRACE.jsonl [TRACE.jsonl ...] [--judge] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import compliance, judged, protocolcost, rounds, satisfaction
from . import session as S

DETERMINISTIC = (
    compliance.ConcordReachedEnd,
    compliance.ConcordCommitted,
    compliance.ConcordUsedAllCandidates,
    compliance.ConcordAllSteps,
    rounds.ConcordRounds,
    rounds.ConcordRepairRounds,
    rounds.ConcordRepairsAfterMarkMissing,
    rounds.ConcordBudgetUsed,
    satisfaction.ConcordWorstOffSatisfaction,
    satisfaction.ConcordMeanSatisfaction,
    satisfaction.ConcordWorstOffAboveTau,
    satisfaction.ConcordSelfPreferenceGap,
    protocolcost.ConcordRunTokens,
    protocolcost.ConcordTokensBeforeConcord,
    protocolcost.ConcordTokensAnchors,
    protocolcost.ConcordTokensSeeds,
    protocolcost.ConcordTokensScores,
    protocolcost.ConcordTokensRepair,
    protocolcost.ConcordTokensAfterCommit,
    protocolcost.ConcordProtocolTokens,
    protocolcost.ConcordProtocolShare,
)
JUDGED = (
    judged.ConcordOutcomeSatisfied,
    judged.ConcordSatisfiedAgents,
    judged.ConcordScoreFair,
    judged.ConcordUnfairScoreShare,
)


def _usage(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def spans_from_trace(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(tool spans, LLM spans): each call's start and end events paired into one KG-style span."""
    tools: dict[str, dict[str, Any]] = {}
    llms: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, call_id = event.get("kind"), event.get("call_id")
            when = float(event.get("timestamp") or 0.0)
            if kind == "tool_call_start":
                tools[call_id] = {"id": call_id, "startTime": when, "toolName": event.get("tool_name") or event.get("name"), "toolArguments": event.get("arguments")}
            elif kind == "tool_call_end" and call_id in tools:
                tools[call_id].update(endTime=when, toolOutput=event.get("output"))
            elif kind == "llm_call_start":
                llms[call_id] = {"id": call_id, "startTime": when}
            elif kind == "llm_call_end" and call_id in llms:
                usage = _usage(event.get("tokens_used"))
                if usage:
                    llms[call_id].update(
                        endTime=when,
                        promptTokenCount=usage.get("prompt_tokens") or 0,
                        cacheReadTokenCount=usage.get("cache_read_input_tokens") or 0,
                        completionTokenCount=usage.get("completion_tokens") or 0,
                    )
    return list(tools.values()), list(llms.values())


def run(path: Path, judge: bool = False) -> list[dict[str, Any]]:
    session_id = path.parent.name if path.name == "events.jsonl" else path.stem
    tools, llms = spans_from_trace(path)
    context = {"session_id": session_id, "tool_spans": tools, "llm_spans": llms}
    S._CACHE.clear()
    protocolcost._CACHE.clear()
    out = []
    for cls in DETERMINISTIC + (JUDGED if judge else ()):
        result = cls().compute(session_id, context)
        out.append({"metric": cls.NAME, "value": None if result.error else result.value, "skipped": result.error or None, "reasoning": result.reasoning})
    return out


def configure_judge() -> None:
    """Point MCE's LLM service at the LLM_* settings (environment, or a ``.env`` in the working directory)."""
    import os

    from dotenv import load_dotenv
    from mce.engine.llm import LLMService

    load_dotenv()
    missing = [k for k in ("LLM_BASE_MODEL_URL", "LLM_API_KEY", "LLM_MODEL_NAME") if not os.environ.get(k)]
    if missing:
        sys.exit(f"--judge needs {', '.join(missing)} (set them in the environment or in a .env)")
    LLMService.configure(
        mode="live",
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL_NAME"],
        base_url=os.environ["LLM_BASE_MODEL_URL"],
    )


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
        json.dump(results, sys.stdout, indent=1)
        return 0
    for path, rows in results.items():
        print(path)
        for row in rows:
            shown = f"SKIPPED: {row['skipped']}" if row["skipped"] else f"{row['value']:g}"
            print(f"  {row['metric']:32s} {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
