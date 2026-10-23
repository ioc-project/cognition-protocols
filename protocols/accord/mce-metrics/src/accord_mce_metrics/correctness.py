"""Correctness metrics: did ACCORD lock the right terms, and were they used?

Ported from ``accord-correctness/accord_correctness_eval.py``. The scoring is
the script's (see ``terms.py``). What differs is where the facts come from: the
script reads ``context_assembled`` / ``tool_call_*`` events, this reads the
session's KG spans.

  system prompts   first LLM call of each agent (its ``system`` message)
  delegations      calls to an agent other than the lead (the agent of the earliest
                   call): the task is the callee's input, the reply its output. A
                   ``delegate_to_*`` tool call, when the KG has one, counts too
  lock time        start of the span that returned the kernel's contract
  final answer     the session's output, credited to the lead agent
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import convergence
from . import terms as T
from .base import NOT_ACTIVATED, AccordMetric
from .spans import decode, ordered, tool_arguments, tool_name, tool_output

# Field a delegation tool carries its instruction in, in order of preference.
_TASK_FIELDS = ("task", "query", "request", "instruction", "message", "input")
# The KG prefixes agent call content with its direction and agent id.
_STATE_PREFIX = re.compile(r"^(?:input|output):[\w.-]+:")


def _clean(text: Any) -> str:
    return _STATE_PREFIX.sub("", str(text or ""), count=1)


@dataclass
class Facts:
    contract: dict[str, Any] | None = None
    lock_time: float = 0.0
    system_prompts: dict[str, str] = field(default_factory=dict)
    produced: list[tuple[float, str, str]] = field(default_factory=list)
    lead_agent: str = ""


def _agent_names(agent_spans: list[dict[str, Any]]) -> dict[str, str]:
    """span id -> agent name."""
    return {str(s.get("spanId")): str(s.get("agentName")) for s in agent_spans if s.get("spanId") and s.get("agentName")}


def _system_prompts(llm_spans: list[dict[str, Any]], owner: dict[str, str]) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for span in ordered(llm_spans):
        agent = owner.get(str(span.get("parentSpanId")))
        if not agent or agent in prompts:
            continue
        messages = decode(span.get("prompt") or span.get("inputContent"))
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "system":
                    prompts[agent] = str(message.get("content") or "")
                    break
    return prompts


def delegations(agent_spans: list[dict[str, Any]]) -> tuple[str, list[tuple[float, float, str, str, str]]]:
    """The lead agent and the work it handed over, as (start, end, callee, task, reply).

    The KG does not record which agent made a call (an agent call's parent is the
    trace root), so delegations are inferred: the lead is the agent of the
    earliest call, and a call to any other agent is work the lead handed over.
    """
    owner = _agent_names(agent_spans)
    calls = [s for s in ordered(agent_spans) if owner.get(str(s.get("spanId")))]
    if not calls:
        return "", []
    lead = owner[str(calls[0].get("spanId"))]
    handed_over = []
    for span in calls:
        callee = owner[str(span.get("spanId"))]
        if callee == lead:
            continue
        start = float(span.get("startTime") or 0.0)
        handed_over.append(
            (start, start + float(span.get("duration") or 0.0), callee, _clean(span.get("inputContent")), _clean(span.get("outputContent")))
        )
    return lead, handed_over


def read_facts(context: dict[str, Any], contracts: convergence.Contracts) -> Facts:
    agents = context.get("agent_calls") or context.get("agent_spans") or []
    llms = context.get("llm_calls") or context.get("llm_spans") or []
    tools = context.get("tool_calls") or context.get("tool_spans") or []

    owner = _agent_names(agents)
    facts = Facts(contract=contracts.canonical, lock_time=contracts.canonical_at)
    facts.system_prompts = _system_prompts(llms, owner)

    delegators: dict[str, int] = {}
    roster = sorted(set(owner.values()) | set(facts.system_prompts))

    lead, handed_over = delegations(agents)
    facts.lead_agent = lead
    for start, end, callee, task, reply in handed_over:
        delegators[lead] = delegators.get(lead, 0) + 1
        facts.produced.append((start, lead, task))
        if reply:
            facts.produced.append((end, callee, reply))

    # Delegation tool calls, for runtimes that record them as tools.
    for span in ordered(tools):
        target = delegation_target(tool_name(span), roster)
        caller = owner.get(str(span.get("parentSpanId")), "")
        if not target or target == caller:
            continue
        arguments = tool_arguments(span)
        arguments = arguments if isinstance(arguments, dict) else {}
        task = next((str(arguments[f]) for f in _TASK_FIELDS if arguments.get(f)), json.dumps(arguments))
        start = float(span.get("startTime") or 0.0)
        delegators[caller] = delegators.get(caller, 0) + 1
        facts.produced.append((start, caller or "?", task))
        output = tool_output(span)
        if output not in (None, ""):
            facts.produced.append((start, target, output if isinstance(output, str) else json.dumps(output)))

    if not facts.lead_agent and delegators:
        facts.lead_agent = max(delegators.items(), key=lambda kv: kv[1])[0]
    final = str(context.get("output_text") or "")
    if final:
        last = max((float(s.get("startTime") or 0.0) for s in (*agents, *llms, *tools)), default=0.0)
        facts.produced.append((last + 1.0, facts.lead_agent or "?", final))
    return facts


def delegation_target(tool: str, agents: list[str]) -> str:
    """The agent a tool call hands work to ("" if not a delegation); longest agent id in the name wins."""
    name = tool.lower()
    best = ""
    for agent in agents:
        token = str(agent).lower()
        if token and token != "mas" and token in name and len(token) > len(best):
            best = token
    return best


def evaluate(session_id: str, facts: Facts, scorer, threshold: float) -> T.RunUtilization:
    """The script's ``evaluate``, over facts already read from the session."""
    run = T.RunUtilization(run_id=session_id)

    locked_defs: dict[str, dict[str, str]] = {}
    if facts.contract:
        for entry in facts.contract.get("glossary") or []:
            if not isinstance(entry, dict):
                continue
            term = T.normalize_term(entry.get("term", ""))
            if not term:
                continue
            variants = {}
            for variant in entry.get("variants") or []:
                if isinstance(variant, dict):
                    variants[str(variant.get("agent") or "?")] = str(variant.get("definition") or "")
            locked_defs[term] = variants
        run.has_contract = True
        if not locked_defs:
            run.notes.append("contract locked with an empty glossary, so no terms were selected")
    else:
        run.notes.append("no locked contract in this trace; nothing was selected")

    # What each agent says it means, independent of what the protocol asked.
    raw_declared: dict[str, dict[str, str]] = {}
    for agent, prompt in facts.system_prompts.items():
        for term, definition in T.extract_declarations(prompt).items():
            raw_declared.setdefault(term, {})[agent] = definition

    vocabulary = set(raw_declared) | set(locked_defs)
    for agent, prompt in facts.system_prompts.items():
        for term, definition in T.extract_with_vocabulary(prompt, vocabulary).items():
            raw_declared.setdefault(term, {}).setdefault(agent, definition)

    run.vocabulary_found = len(raw_declared)
    if not raw_declared:
        run.notes.append(
            "no agent vocabulary could be read from this trace, so under-"
            "selection is undetectable and recall is not evidence of anything"
        )

    canonical = T.unify_terms(sorted(set(raw_declared) | set(locked_defs)))
    declared: dict[str, dict[str, str]] = {}
    for term, agents in raw_declared.items():
        bucket = declared.setdefault(canonical.get(term, term), {})
        for agent, definition in agents.items():
            bucket.setdefault(agent, definition)

    locked_canonical = {canonical.get(t, t) for t in locked_defs}
    for term, variants in locked_defs.items():
        # Prefer the definitions the agents supplied to the protocol; fall back
        # to their declared vocabulary when the contract recorded only one.
        source = variants if len(variants) > 1 else declared.get(canonical.get(term, term), variants)
        low = T.min_pairwise_similarity(list(source.values()), scorer)
        run.locked_terms.append(
            T.TermFinding(
                term=term,
                locked=True,
                definitions=len(source),
                min_similarity=low,
                contested=low < threshold,
                referenced_after_lock=T._mentions(term, facts.produced, facts.lock_time),
                declaring_agents=sorted(source),
            )
        )

    for term, agents in declared.items():
        if term in locked_canonical or len(agents) < 2:
            continue
        low = T.min_pairwise_similarity(list(agents.values()), scorer)
        if low < threshold:
            run.missed_terms.append(
                T.TermFinding(
                    term=term,
                    locked=False,
                    definitions=len(agents),
                    min_similarity=low,
                    contested=True,
                    referenced_after_lock=T._mentions(term, facts.produced, 0.0),
                    declaring_agents=sorted(agents),
                )
            )
    return run


@dataclass
class Scored:
    run: T.RunUtilization
    backend: str
    threshold: float
    analysis: Any = None  # keeps the cache key's id() from being reused


_CACHE: dict[tuple, Scored] = {}


def scored_for(context: dict[str, Any]) -> Scored:
    """The session's scoring, computed once however many metrics ask for it."""
    analysis = convergence.analysis_for(context)
    session_id = str(context.get("session_id") or "")
    key = (session_id, id(analysis))
    if key not in _CACHE:
        if len(_CACHE) >= 16:
            _CACHE.pop(next(iter(_CACHE)))
        scorer, threshold, backend = T.load_scorer(None)
        facts = read_facts(context, analysis.contracts)
        _CACHE[key] = Scored(evaluate(session_id, facts, scorer, threshold), backend, threshold, analysis)
    return _CACHE[key]


class _CorrectnessMetric(AccordMetric):
    def compute(self, resource_id: str, context: dict[str, Any]):
        if not convergence.analysis_for(context).ran:
            return self._skip(resource_id, NOT_ACTIVATED)
        scored = scored_for(context)
        if not scored.run.selected:
            # Nothing was selected, so there is no selection to judge.
            return self._skip(resource_id, "; ".join(scored.run.notes) or "no terms were selected")
        return self.facet(resource_id, scored.run, scored)

    def facet(self, session_id, run, scored):  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _backend(scored: Scored) -> str:
        return f"similarity backend: {scored.backend}, threshold {scored.threshold:.2f}"


class AccordLockPrecision(_CorrectnessMetric):
    NAME = "AccordLockPrecision"
    DESCRIPTION = "Share of locked terms that agents genuinely defined differently (justified locks)."

    def facet(self, session_id, run, scored):
        return self._result(session_id, run.precision, self._backend(scored))


class AccordLockRecall(_CorrectnessMetric):
    NAME = "AccordLockRecall"
    DESCRIPTION = "Share of genuinely contested terms that ACCORD locked."

    def facet(self, session_id, run, scored):
        if not run.vocabulary_found:
            return self._skip(session_id, run.notes[-1] if run.notes else "no agent vocabulary could be read")
        return self._result(session_id, run.recall, self._backend(scored))


class AccordTermUtilization(_CorrectnessMetric):
    NAME = "AccordTermUtilization"
    DESCRIPTION = "Share of locked terms that agents mention again after the lock."

    def facet(self, session_id, run, scored):
        return self._result(session_id, run.utilization)


class AccordLockedTerms(_CorrectnessMetric):
    NAME = "AccordLockedTerms"
    DESCRIPTION = "Number of terms in the locked glossary. Reasoning carries the per-term verdicts as JSON."

    def facet(self, session_id, run, scored):
        return self._result(session_id, len(run.locked_terms), json.dumps(run.as_dict()["terms"]))


class AccordOverSelected(_CorrectnessMetric):
    NAME = "AccordOverSelected"
    DESCRIPTION = "Locked terms the agents already agreed on, which cost tokens and bought nothing."

    def facet(self, session_id, run, scored):
        return self._result(session_id, run.over_selected, ", ".join(t.term for t in run.locked_terms if not t.contested))


class AccordUnderSelected(_CorrectnessMetric):
    NAME = "AccordUnderSelected"
    DESCRIPTION = "Terms agents disagreed on that never reached the glossary."

    def facet(self, session_id, run, scored):
        return self._result(session_id, run.under_selected, ", ".join(t.term for t in run.missed_terms))
