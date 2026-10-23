"""Benefit metrics: did the agents use the glossary ACCORD is meant to give them?

Ported from ``accord-benefit/accord_benefit_eval.py``, as per-session single-arm
metrics. The paired control/treatment statistics need two runs and have no home
on one session, so they stay in the offline script.

Judging is the script's: same prompts, same blinding (protocol vocabulary is
redacted before the judge sees anything), same code-enforced evidence rule (an
uptake claim without a quote is downgraded). The transport differs. The script
calls an HTTP endpoint itself; this goes through ``Metric.call_llm`` so MCE
records the judge's tokens and cost on the result (``evaluation_*`` metadata).

What the KG cannot give, and what stands in for it:

  mission             the session's input
  transcript          what the lead handed to each agent and what came back
  terms at issue      terms the agents define differently (the correctness
                      metrics' contested terms), instead of a hand-supplied list
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from mce.core.metadata import MetricLayer, MetricNature

from . import convergence, correctness
from . import judging as J
from .base import NOT_ACTIVATED, AccordMetric
from .correctness import delegations

PASSES = 1  # judge samples per verdict; the script defaults to 1 as well


def record_from_context(context: dict[str, Any], terms: list[str]) -> J.RunRecord:
    agents = context.get("agent_calls") or context.get("agent_spans") or []
    lead, handed_over = delegations(agents)

    transcript: list[J.Message] = []
    for _start, _end, callee, task, reply in handed_over:
        transcript.append(J.Message(lead, callee, task))
        if reply:
            transcript.append(J.Message(callee, lead, reply))

    session_id = str(context.get("session_id") or "")
    return J.RunRecord(
        run_id=session_id,
        scenario_id=session_id,
        arm="session",
        mission=str(context.get("input_text") or ""),
        transcript=transcript,
        terms_at_issue=terms,
    )


def contested_terms(context: dict[str, Any]) -> list[str]:
    """Terms the roster defines differently: contested locked terms plus the ones never locked."""
    run = correctness.scored_for(context).run
    return sorted({t.term for t in run.locked_terms if t.contested} | {t.term for t in run.missed_terms})


class _BenefitMetric(AccordMetric):
    LAYER = MetricLayer.GOVERNANCE
    NATURE = MetricNature.STOCHASTIC

    def compute(self, resource_id: str, context: dict[str, Any]):
        if not convergence.analysis_for(context).ran:
            return self._skip(resource_id, NOT_ACTIVATED)
        return self.facet(resource_id, context)

    def facet(self, session_id: str, context: dict[str, Any]):  # pragma: no cover
        raise NotImplementedError

    def judge(self, prompt: str, sample: int) -> dict[str, Any]:
        text = self.call_llm(prompt, system_prompt=J.JUDGE_SYSTEM, temperature=0.0, sample=sample)
        return J.parse_json_object(text)


class _UptakeMetric(_BenefitMetric):
    """Both uptake metrics read one judge verdict, computed once per session."""

    _CACHE: dict[tuple, dict[str, Any]] = {}

    def _uptake(self, context: dict[str, Any]) -> dict[str, Any] | None:
        terms = contested_terms(context)
        if not terms:
            return None
        key = (str(context.get("session_id") or ""), tuple(terms))
        cache = _UptakeMetric._CACHE
        if key not in cache:
            if len(cache) >= 16:
                cache.pop(next(iter(cache)))
            cache[key] = self._judge_uptake(context, terms)
        return cache[key]

    def _judge_uptake(self, context: dict[str, Any], terms: list[str]) -> dict[str, Any]:
        run = record_from_context(context, terms)
        prompt = J.GLOSSARY_UPTAKE_PROMPT.format(
            mission=J.redact(run.mission),
            terms="\n".join(f"- {term}" for term in terms),
            transcript=J.render_transcript(run),
        )
        uptake_rates, influence_rates = [], []
        handling: dict[str, int] = defaultdict(int)
        for sample in range(PASSES):
            result = self.judge(prompt, sample)
            present = took_up = influenced = 0
            for entry in (e for e in (result.get("terms") or []) if isinstance(e, dict)):
                level = str(entry.get("handling", "absent"))
                if level not in J.HANDLING_LEVELS:
                    level = "unqualified"
                # Uptake is a claim about the agent's words, so it needs a quote.
                if level in J.UPTAKE_LEVELS and not str(entry.get("evidence", "")).strip():
                    level = "unqualified"
                handling[level] += 1
                if level == "absent":
                    continue
                present += 1
                if level in J.UPTAKE_LEVELS:
                    took_up += 1
                    if entry.get("influenced_reasoning") is True and str(entry.get("influence_evidence", "")).strip():
                        influenced += 1
            uptake_rates.append(took_up / present if present else 0.0)
            influence_rates.append(influenced / present if present else 0.0)
        return {
            "terms": terms,
            "uptake_rate": round(statistics.fmean(uptake_rates), 3),
            "influence_rate": round(statistics.fmean(influence_rates), 3),
            "handling": dict(handling),
        }

    def facet(self, session_id, context):
        uptake = self._uptake(context)
        if uptake is None:
            return self._skip(session_id, "no terms are defined differently across the roster, so there is nothing to take up")
        return self._result(session_id, uptake[self.FIELD], f"terms={uptake['terms']}; handling={uptake['handling']}")

    FIELD = ""


class AccordGlossaryUptake(_UptakeMetric):
    NAME = "AccordGlossaryUptake"
    DESCRIPTION = "Share of contested terms the agents visibly pinned down (defined, qualified, translated or flagged), LLM-judged."
    FIELD = "uptake_rate"


class AccordGlossaryInfluence(_UptakeMetric):
    NAME = "AccordGlossaryInfluence"
    DESCRIPTION = "Share of contested terms whose handling visibly changed an agent's reasoning, LLM-judged."
    FIELD = "influence_rate"
