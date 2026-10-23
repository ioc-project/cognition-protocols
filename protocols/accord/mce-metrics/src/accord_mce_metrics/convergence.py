"""Convergence metrics: did the run lock a usable intent contract?

Ported from ``accord-convergence/accord_convergence_eval.py``. The script reads
trace events; this reads the session's tool spans. Each metric reports one
facet of the same analysis, which is computed once per session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import contract as C
from . import diagnostics
from .base import NOT_ACTIVATED, PROVIDER, AccordMetric
from .spans import ordered, output_payloads, tool_arguments, tool_name, tool_output

_PHASE_IN_TEXT = re.compile(r'"args"\s*:\s*\[\s*"(\w+)"')


@dataclass
class Contracts:
    """What a session had to say about its lock."""

    canonical: dict[str, Any] | None = None
    canonical_at: float = 0.0  # start time of the span that returned it
    relayed: dict[str, Any] | None = None
    relay_attempted: bool = False  # a contract block appeared in some prompt
    activated: bool = False  # the orchestrator loaded l9-accord-initiator
    activated_at: float = 0.0  # start time of the first span that showed it
    phases: set[str] = field(default_factory=set)  # phase scripts it then ran


def _note_activation(span: dict[str, Any], found: Contracts) -> None:
    """Record that the protocol ran, and how far it got.

    A run that never activates the initiator is not a failed lock, it is a run
    where ACCORD was available and the orchestrator ignored it.
    """
    args = tool_arguments(span)
    name = tool_name(span)
    if isinstance(args, dict):
        if name == "activate_skill" and args.get("name") == C.INITIATOR_SKILL:
            found.activated = True
        elif name == "run_skill_script" and args.get("skill") == C.INITIATOR_SKILL:
            found.activated = True
            phase_args = args.get("args")
            if isinstance(phase_args, list) and phase_args and phase_args[0] in C.PHASES:
                found.phases.add(phase_args[0])
        return
    # Arguments that did not decode: fall back to the raw text.
    text = str(args or "")
    if C.INITIATOR_SKILL in text and name in ("activate_skill", "run_skill_script"):
        found.activated = True
        match = _PHASE_IN_TEXT.search(text)
        if name == "run_skill_script" and match and match.group(1) in C.PHASES:
            found.phases.add(match.group(1))


def _note_relay(value: Any, found: Contracts) -> None:
    """Record a contract block pasted into a prompt (first one that parses wins)."""
    if found.relayed is not None or not isinstance(value, str) or C.MARKER not in value:
        return
    found.relay_attempted = True
    found.relayed = C._locked(value, after_marker=True)


def contracts_from_spans(tool_spans: list[dict[str, Any]], agent_spans: list[dict[str, Any]] | None = None) -> Contracts:
    """Pull the kernel's contract and the orchestrator's relayed copy."""
    found = Contracts()
    for span in ordered(tool_spans):
        was_activated = found.activated
        _note_activation(span, found)
        if found.activated and not was_activated:
            found.activated_at = float(span.get("startTime") or 0.0)

        # The kernel's own output, returned by the verify phase.
        if found.canonical is None:
            for payload in output_payloads(tool_output(span)):
                candidate = (payload.get("data") or {}).get("locked_intent_contract")
                if isinstance(candidate, dict) and candidate.get("status") == C.LOCKED_STATUS:
                    found.canonical = candidate
                    found.canonical_at = float(span.get("startTime") or 0.0)
                    break

        # The orchestrator's retyped copy, if it was pasted into a tool argument.
        args = tool_arguments(span)
        for value in args.values() if isinstance(args, dict) else [args]:
            _note_relay(value, found)

    # Delegation prompts are stored on the delegated agent's call, not on the
    # delegate_to_* tool call, so the relayed copy is usually found here.
    for span in ordered(agent_spans):
        _note_relay(span.get("inputContent") or span.get("input"), found)
    return found


def _outcome(report: C.Report | None, missing_reason: str) -> dict[str, Any]:
    if report is None:
        return {"found": False, "reason": missing_reason}
    return {"found": True, "valid": report.ok, "errors": report.errors, "warnings": report.warnings}


@dataclass
class Analysis:
    ran: bool
    activated: bool
    phases: list[str]
    canonical: dict[str, Any]
    relayed: dict[str, Any]
    fabricated: bool
    converged: bool
    contracts: Contracts = field(default_factory=Contracts)  # the raw findings, for other metrics


def analyse(
    session_id: str,
    tool_spans: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    agent_spans: list[dict[str, Any]] | None = None,
) -> Analysis:
    found = contracts_from_spans(tool_spans, agent_spans)
    ran = found.activated or found.canonical is not None or found.relay_attempted
    canonical = _outcome(
        C.validate(found.canonical, session_id) if found.canonical else None,
        "the kernel never emitted a locked contract",
    )
    relayed = _outcome(
        C.validate(found.relayed, session_id) if found.relayed else None,
        "truncated mid-JSON or malformed" if found.relay_attempted else "no contract was relayed to specialists",
    )
    analysis = Analysis(
        ran=ran,
        activated=found.activated,
        phases=sorted(found.phases),
        canonical=canonical,
        relayed=relayed,
        # A relayed lock with no canonical one: the orchestrator announced a
        # lock the kernel never produced.
        fabricated=bool(found.relay_attempted and found.canonical is None),
        converged=bool(found.activated and canonical["found"] and canonical["valid"]),
        contracts=found,
    )
    if context is not None:
        diagnostics.log_context(session_id, context)
    diagnostics.log_analysis(session_id, tool_spans, found, analysis)
    return analysis


_CACHE: dict[tuple, Analysis] = {}


def analysis_for(context: dict[str, Any]) -> Analysis:
    """The session's analysis, computed once however many metrics ask for it."""
    session_id = str(context.get("session_id") or "")
    spans = context.get("tool_calls") or context.get("tool_spans") or []
    agents = context.get("agent_calls") or context.get("agent_spans") or []
    key = (
        session_id,
        tuple((str(s.get("id") or ""), s.get("startTime"), len(str(s.get("toolOutput") or ""))) for s in spans),
        tuple((str(s.get("id") or ""), len(str(s.get("inputContent") or ""))) for s in agents),
    )
    if key not in _CACHE:
        if len(_CACHE) >= 16:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = analyse(session_id, spans, context, agents)
    return _CACHE[key]


class _ConvergenceMetric(AccordMetric):
    def compute(self, resource_id: str, context: dict[str, Any]) -> MetricResult:
        analysis = analysis_for(context)
        if not analysis.ran:
            return self._skip(resource_id, NOT_ACTIVATED)
        return self.facet(resource_id, analysis)

    def facet(self, session_id: str, a: Analysis) -> MetricResult:  # pragma: no cover
        raise NotImplementedError


class AccordConverged(_ConvergenceMetric):
    NAME = "AccordConverged"
    DESCRIPTION = "1 if the initiator ran and the kernel's contract is INTENT-LOCKED and valid, else 0."

    def facet(self, session_id, a):
        why = "; ".join(a.canonical.get("errors", [])) or a.canonical.get("reason", "")
        return self._result(session_id, a.converged, why)


class AccordPhaseCoverage(_ConvergenceMetric):
    NAME = "AccordPhaseCoverage"
    DESCRIPTION = "Share of the initiator phases (init, roster, frame, glossary, verify) the orchestrator ran."

    def facet(self, session_id, a):
        missing = [p for p in C.PHASES if p not in a.phases]
        return self._result(session_id, len(set(a.phases) & set(C.PHASES)) / len(C.PHASES), "missing: " + ", ".join(missing))


class AccordCanonicalValid(_ConvergenceMetric):
    NAME = "AccordCanonicalValid"
    DESCRIPTION = "1 if the contract the kernel produced passes validation, else 0."

    def facet(self, session_id, a):
        if not a.canonical["found"]:
            return self._skip(session_id, a.canonical["reason"])
        return self._result(session_id, a.canonical["valid"], "; ".join(a.canonical["errors"]))


class AccordRelayedValid(_ConvergenceMetric):
    NAME = "AccordRelayedValid"
    DESCRIPTION = "1 if the contract copy the orchestrator relayed to specialists passes validation, else 0."

    def facet(self, session_id, a):
        if a.relayed["found"]:
            return self._result(session_id, a.relayed["valid"], "; ".join(a.relayed["errors"]))
        if a.relayed["reason"].startswith("truncated"):
            return self._result(session_id, 0.0, a.relayed["reason"])
        return self._skip(session_id, a.relayed["reason"])


class AccordFabricatedLock(_ConvergenceMetric):
    NAME = "AccordFabricatedLock"
    DESCRIPTION = "1 if the orchestrator relayed a lock the kernel never produced, else 0."

    def facet(self, session_id, a):
        return self._result(session_id, a.fabricated)


class AccordContractWarnings(_ConvergenceMetric):
    NAME = "AccordContractWarnings"
    DESCRIPTION = "Number of validation warnings on the kernel's contract (fragile frame, drifted references)."

    def facet(self, session_id, a):
        if not a.canonical["found"]:
            return self._skip(session_id, a.canonical["reason"])
        return self._result(session_id, len(a.canonical["warnings"]), "; ".join(a.canonical["warnings"]))
