"""Read a session's KG spans the way the ACCORD evals read trace events.

MCE hands a session metric ``tool_spans`` / ``llm_spans`` / ``agent_spans``
(normalised KG nodes, see ``oxp.providers.kg``). Tool arguments and outputs
are stored as State text, so they are decoded here when they are JSON and kept
as text otherwise.
"""

from __future__ import annotations

import json
from typing import Any, Iterator


def decode(value: Any) -> Any:
    """JSON-decode text, pass anything else (or undecodable text) through."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def ordered(spans: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Spans in execution order. A span with no start time keeps its place."""
    spans = list(spans or [])
    return [s for _, s in sorted(enumerate(spans), key=lambda p: (p[1].get("startTime") or 0, p[0]))]


def tool_name(span: dict[str, Any]) -> str:
    return str(span.get("toolName") or span.get("name") or "")


def tool_arguments(span: dict[str, Any]) -> Any:
    """Decoded arguments (a dict when the call stored a JSON object)."""
    return decode(span.get("toolArguments") or span.get("inputContent") or span.get("inputParams"))


def tool_output(span: dict[str, Any]) -> Any:
    return decode(span.get("toolOutput") if span.get("toolOutput") is not None else span.get("outputContent"))


def output_payloads(output: Any) -> Iterator[dict[str, Any]]:
    """Yield the JSON objects a tool output can carry.

    The merge tool returns the kernel's JSON directly. The skill-script
    delivery runs the same kernel as a subprocess and wraps its JSON in a
    ``stdout`` string, so that layer is peeled as well.
    """
    output = decode(output)
    if not isinstance(output, dict):
        return
    yield output
    stdout = output.get("stdout")
    if isinstance(stdout, str):
        nested = decode(stdout)
        if isinstance(nested, dict):
            yield nested
