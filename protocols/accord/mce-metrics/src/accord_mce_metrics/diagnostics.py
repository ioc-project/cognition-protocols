"""Log what the convergence metrics actually received for a session.

When a metric disagrees with the original eval script the usual cause is the
data, not the logic: the KG span may hold a different tool name, a different
argument shape, or a truncated tool output. These lines show previews and sizes
(never whole payloads, which are large) so that can be told from the log.

Logger ``accord_mce_metrics.diagnostics``: INFO for the session summary and the
ACCORD-relevant spans, DEBUG for every tool span.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from . import contract as C
from .spans import decode, ordered, tool_arguments, tool_name, tool_output

logger = logging.getLogger(__name__)

_HEAD, _TAIL = 160, 80
_ACCORD_TOOLS = frozenset({"activate_skill", "run_skill_script", "accord_merge"})
_NEEDLES = (C.INITIATOR_SKILL, C.MARKER, C.LOCKED_STATUS, "locked_intent_contract")


def preview(value: Any) -> str:
    """Type, size and the first/last characters, so truncation shows up."""
    if value is None:
        return "None"
    text = value if isinstance(value, str) else repr(value)
    kind = type(value).__name__
    if len(text) <= _HEAD + _TAIL:
        return f"{kind}[{len(text)}] {text!r}"
    return f"{kind}[{len(text)}] {text[:_HEAD]!r} ... {text[-_TAIL:]!r}"


def _mentions_accord(span: dict[str, Any]) -> list[str]:
    haystack = f"{span.get('toolArguments')} {span.get('toolOutput')} {span.get('inputContent')} {span.get('outputContent')}"
    return [needle for needle in _NEEDLES if needle in haystack]


def describe_span(index: int, span: dict[str, Any]) -> str:
    args, out = tool_arguments(span), tool_output(span)
    return (
        f"#{index} id={span.get('id')} name={tool_name(span)!r} success={span.get('success')} "
        f"start={span.get('startTime')} "
        f"args(raw)={preview(span.get('toolArguments'))} decoded_as={type(args).__name__} "
        f"output(raw)={preview(span.get('toolOutput'))} decoded_as={type(decode(out)).__name__} "
        f"mentions={_mentions_accord(span)}"
    )


def log_context(session_id: str, context: dict[str, Any]) -> None:
    """What the KG provider handed over: which span lists, how many, and their fields."""
    sizes = {k: len(v) for k, v in context.items() if isinstance(v, list)}
    logger.info("[%s] context keys=%s list sizes=%s", session_id, sorted(context), sizes)
    spans = context.get("tool_calls") or context.get("tool_spans") or []
    if spans:
        logger.info("[%s] tool span fields: %s", session_id, sorted({k for s in spans for k in s}))


def log_analysis(session_id: str, spans: list[dict[str, Any]], found: Any, analysis: Any) -> None:
    """Summary plus the spans that matter. ``found`` is convergence.Contracts."""
    spans = ordered(spans)
    names = Counter(tool_name(s) or "<no name>" for s in spans)
    logger.info("[%s] %d tool spans; names=%s", session_id, len(spans), dict(names.most_common()))

    relevant = [
        (i, s)
        for i, s in enumerate(spans)
        if tool_name(s) in _ACCORD_TOOLS or _mentions_accord(s)
    ]
    for i, span in relevant:
        logger.info("[%s] accord span %s", session_id, describe_span(i, span))
    if not relevant:
        logger.info("[%s] no span names or mentions anything ACCORD-related", session_id)
    for i, span in enumerate(spans):
        logger.debug("[%s] span %s", session_id, describe_span(i, span))

    logger.info(
        "[%s] activated=%s phases=%s (%d/%d) canonical_found=%s relay_attempted=%s relayed_found=%s "
        "-> converged=%s fabricated=%s",
        session_id,
        found.activated,
        sorted(found.phases),
        len(found.phases),
        len(C.PHASES),
        found.canonical is not None,
        found.relay_attempted,
        found.relayed is not None,
        analysis.converged,
        analysis.fabricated,
    )
    missing = [p for p in C.PHASES if p not in found.phases]
    if missing:
        logger.info("[%s] phases never seen: %s", session_id, missing)
    if found.canonical is None:
        # The usual reasons: the verify phase's output is absent, truncated, or not JSON.
        verify = [s for s in spans if tool_name(s) == "run_skill_script" and "verify" in str(s.get("toolArguments"))]
        merges = [s for s in spans if tool_name(s) == "accord_merge"]
        logger.info(
            "[%s] canonical contract not found: %d verify-phase span(s), %d accord_merge span(s) seen",
            session_id,
            len(verify),
            len(merges),
        )
