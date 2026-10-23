"""What ACCORD itself costs in tokens, as opposed to what the whole session costs.

Whole-session token cost is not measured here. These two families isolate the protocol:

  protocol window    the calls made while the protocol was running: from the
                     first activation of the initiator skill to the lock (the
                     span that returned the locked contract). Prompt, cached,
                     fresh and completion tokens, from the provider's usage.
                     With no lock, the window runs to the end of the session.

  contract overhead  what the locked contract adds to each later call, the
                     price paid for carrying it. The provider does not say how
                     much of a prompt is the contract, so it is estimated: the
                     contract's share of the call's message text, applied to
                     the call's provider-reported prompt tokens. An estimate,
                     and labelled as one.

Both need evidence that ACCORD ran, like the other protocol metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import contract as C
from . import convergence
from .base import NOT_ACTIVATED, AccordMetric
from .spans import decode, ordered

_DECODER = json.JSONDecoder()


def _count(value: Any) -> int | None:
    """A provider-reported token count as an int; None when absent or not a number."""
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# protocol window
# --------------------------------------------------------------------------


@dataclass
class Window:
    start: float
    end: float | None  # the lock; None when the session never locked
    llm_calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    agents: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def fresh_tokens(self) -> int:
        return self.prompt_tokens - self.cached_tokens


def measure_window(context: dict[str, Any]) -> Window | None:
    """Sum provider usage over the calls inside the protocol window; None if ACCORD never ran."""
    found = convergence.analysis_for(context).contracts
    if not (found.activated or found.canonical is not None):
        return None
    llms = context.get("llm_calls") or context.get("llm_spans") or []
    agents = context.get("agent_calls") or context.get("agent_spans") or []
    owner = {str(s.get("spanId")): str(s.get("agentName")) for s in agents if s.get("spanId") and s.get("agentName")}

    window = Window(start=found.activated_at, end=found.canonical_at if found.canonical is not None else None)
    for span in ordered(llms):
        started = float(span.get("startTime") or 0.0)
        if started < window.start or (window.end is not None and started > window.end):
            continue
        prompt = _count(span.get("promptTokenCount"))
        if prompt is None:
            continue
        cached = _count(span.get("cacheReadTokenCount")) or 0
        completion = _count(span.get("completionTokenCount")) or 0
        window.llm_calls += 1
        window.prompt_tokens += prompt
        window.cached_tokens += cached
        window.completion_tokens += completion
        bucket = window.agents.setdefault(
            owner.get(str(span.get("parentSpanId")), "unknown"),
            {"llm_calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0},
        )
        bucket["llm_calls"] += 1
        bucket["prompt_tokens"] += prompt
        bucket["cached_tokens"] += cached
        bucket["completion_tokens"] += completion
    return window if window.llm_calls else None


class _WindowMetric(AccordMetric):
    def compute(self, resource_id: str, context: dict[str, Any]):
        if not convergence.analysis_for(context).ran:
            return self._skip(resource_id, NOT_ACTIVATED)
        window = measure_window(context)
        if window is None:
            return self._skip(resource_id, "No LLM call inside the ACCORD window reports provider token usage")
        return self.facet(resource_id, window)

    def facet(self, session_id: str, window: Window):  # pragma: no cover
        raise NotImplementedError


class AccordProtocolPromptTokens(_WindowMetric):
    NAME = "AccordProtocolPromptTokens"
    DESCRIPTION = (
        "Prompt tokens of the LLM calls made while ACCORD ran (first initiator activation to the lock). "
        "Reasoning carries the window and the per-agent split as JSON."
    )

    def facet(self, session_id, window):
        detail = {
            "window_start": window.start,
            "window_end": window.end,
            "locked": window.end is not None,
            "llm_calls": window.llm_calls,
            "agents": dict(sorted(window.agents.items())),
        }
        return self._result(session_id, window.prompt_tokens, json.dumps(detail))


class AccordProtocolCachedTokens(_WindowMetric):
    NAME = "AccordProtocolCachedTokens"
    DESCRIPTION = "Prompt tokens of the ACCORD window served from the provider's cache."

    def facet(self, session_id, window):
        return self._result(session_id, window.cached_tokens)


class AccordProtocolFreshTokens(_WindowMetric):
    NAME = "AccordProtocolFreshTokens"
    DESCRIPTION = "Prompt tokens of the ACCORD window billed at the full input rate (prompt minus cache reads)."

    def facet(self, session_id, window):
        return self._result(session_id, window.fresh_tokens)


class AccordProtocolCompletionTokens(_WindowMetric):
    NAME = "AccordProtocolCompletionTokens"
    DESCRIPTION = "Completion tokens generated by the LLM calls of the ACCORD window."

    def facet(self, session_id, window):
        return self._result(session_id, window.completion_tokens)


# --------------------------------------------------------------------------
# contract overhead
# --------------------------------------------------------------------------


@dataclass
class CallOverhead:
    agent: str
    started: float
    copies: int  # contract blocks found in the call's messages
    contract_chars: int
    message_chars: int
    prompt_tokens: int
    estimated_tokens: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "started": self.started,
            "copies": self.copies,
            "prompt_tokens": self.prompt_tokens,
            "estimated_contract_tokens": round(self.estimated_tokens),
            "share_of_prompt": round(self.estimated_tokens / self.prompt_tokens, 4) if self.prompt_tokens else 0.0,
        }


def _message_texts(prompt: Any) -> list[str]:
    """The text of each message of an LLM call's prompt."""
    prompt = decode(prompt)
    if not isinstance(prompt, list):
        return [prompt] if isinstance(prompt, str) else []
    texts = []
    for message in prompt:
        content = message.get("content") if isinstance(message, dict) else message
        texts.append(content if isinstance(content, str) else json.dumps(content, default=str) if content is not None else "")
    return texts


def _contract_blocks(text: str) -> list[int]:
    """Lengths of the contract blocks in a text: JSON objects that follow ``LOCKED_INTENT_CONTRACT:``."""
    sizes, at = [], 0
    while True:
        at = text.find(C.MARKER, at)
        if at < 0:
            return sizes
        at += len(C.MARKER)
        rest = text[at:].lstrip(": \t\r\n")
        if not rest.startswith("{"):
            continue
        try:
            _obj, end = _DECODER.raw_decode(rest)
        except ValueError:
            continue  # truncated or not JSON: not a block we can size
        sizes.append(end)
        at = len(text) - len(rest) + end


def measure_overhead(context: dict[str, Any]) -> list[CallOverhead]:
    """The calls that carry the locked contract, with an estimate of what it adds to each."""
    llms = context.get("llm_calls") or context.get("llm_spans") or []
    agents = context.get("agent_calls") or context.get("agent_spans") or []
    owner = {str(s.get("spanId")): str(s.get("agentName")) for s in agents if s.get("spanId") and s.get("agentName")}

    calls = []
    for span in ordered(llms):
        prompt_tokens = _count(span.get("promptTokenCount"))
        if prompt_tokens is None:
            continue
        texts = _message_texts(span.get("prompt") or span.get("inputContent"))
        message_chars = sum(len(t) for t in texts)
        sizes = [size for text in texts for size in _contract_blocks(text)]
        if not sizes or not message_chars:
            continue
        calls.append(
            CallOverhead(
                agent=owner.get(str(span.get("parentSpanId")), "unknown"),
                started=float(span.get("startTime") or 0.0),
                copies=len(sizes),
                contract_chars=sum(sizes),
                message_chars=message_chars,
                prompt_tokens=prompt_tokens,
                estimated_tokens=prompt_tokens * sum(sizes) / message_chars,
            )
        )
    return calls


class _OverheadMetric(AccordMetric):
    def compute(self, resource_id: str, context: dict[str, Any]):
        if not convergence.analysis_for(context).ran:
            return self._skip(resource_id, NOT_ACTIVATED)
        calls = measure_overhead(context)
        if not calls:
            return self._skip(resource_id, "No LLM call carries the locked contract, so it adds nothing to any prompt")
        return self.facet(resource_id, calls)

    def facet(self, session_id: str, calls: list[CallOverhead]):  # pragma: no cover
        raise NotImplementedError


class AccordContractOverheadTokens(_OverheadMetric):
    NAME = "AccordContractOverheadTokens"
    DESCRIPTION = (
        "Estimated prompt tokens the locked contract adds, summed over the calls that carry it: the price of "
        "carrying the contract. Estimated from the contract's share of each call's text. Reasoning lists each call."
    )

    def facet(self, session_id, calls):
        detail = {"estimate": True, "calls_carrying_contract": len(calls), "calls": [c.as_dict() for c in calls]}
        return self._result(session_id, round(sum(c.estimated_tokens for c in calls)), json.dumps(detail))


class AccordContractOverheadPerCall(_OverheadMetric):
    NAME = "AccordContractOverheadPerCall"
    DESCRIPTION = "Estimated prompt tokens the locked contract adds to each call that carries it, on average."

    def facet(self, session_id, calls):
        return self._result(session_id, round(sum(c.estimated_tokens for c in calls) / len(calls)), f"estimate over {len(calls)} calls")


class AccordContractOverheadShare(_OverheadMetric):
    NAME = "AccordContractOverheadShare"
    DESCRIPTION = "Estimated share of the prompt tokens of the calls carrying the contract that the contract itself accounts for, 0 to 1."

    def facet(self, session_id, calls):
        prompt = sum(c.prompt_tokens for c in calls)
        return self._result(session_id, sum(c.estimated_tokens for c in calls) / prompt if prompt else 0.0, f"estimate over {len(calls)} calls")
