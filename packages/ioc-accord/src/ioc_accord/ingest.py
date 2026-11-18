#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Reading what the specialists replied. A reply arrives as whatever the agent produced -- a JSON object, a fenced code
block, or prose with an object somewhere inside it -- and leaves here as the plain records the merges in
`ioc_accord.engine` work on. Nothing here judges a reply: an unreadable one is skipped, not reported."""
import json
import re

MAX_FRAME_ATOMS = 6


def strip_fences(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", stripped.strip())


def parse_responses(raw: list) -> list[dict]:
    """Coerce str|dict replies to dicts; tolerate prose around a JSON object."""
    parsed: list[dict] = []
    for r in raw:
        if isinstance(r, str):
            text = strip_fences(r)
            try:
                r = json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                try:
                    r = json.loads(m.group(0)) if m else None
                except json.JSONDecodeError:
                    r = None
        if isinstance(r, dict):
            parsed.append(r)
    return parsed


def ingest_roster(raw: list) -> list[dict]:
    cards = []
    for d in parse_responses(raw):
        identity = str(d.get("agent") or d.get("identity") or "").strip()
        if identity:
            cards.append({
                "identity": identity,
                "roles": list(d.get("roles") or []),
                "capabilities": list(d.get("capabilities") or []),
                "accessScopes": list(d.get("access_scopes") or d.get("accessScopes") or []),
                "holdingsSummary": str(d.get("holdings_summary") or d.get("holdingsSummary") or ""),
            })
    return cards


def ingest_frame_round(raw: list) -> tuple[dict[str, list[dict]], list[dict], bool]:
    """Latest atoms per agent, truncated to the first MAX_FRAME_ATOMS.

    A response with an ``atoms`` list replaces that agent's previous atoms,
    including an empty list. Returns ``(atoms_by_agent, truncated, saw_atoms)``.
    ``truncated`` records agents whose list was cut, in submission order.
    """
    by_agent: dict[str, list[dict]] = {}
    submitted: dict[str, int] = {}
    saw_atoms = False
    for d in parse_responses(raw):
        atoms = d.get("atoms")
        if not isinstance(atoms, list):
            continue
        saw_atoms = True
        agent = str(d.get("agent") or "").strip()
        parsed: list[dict] = []
        for atom in atoms:
            text = str(atom.get("text") or "").strip() if isinstance(atom, dict) else ""
            if text:
                parsed.append({
                    "text": text,
                    "agent": agent,
                    "kind": str(atom.get("type") or "term"),
                })
        by_agent.setdefault(agent, []).extend(parsed)
        submitted[agent] = submitted.get(agent, 0) + len(parsed)
    truncated: list[dict] = []
    for agent, atoms in by_agent.items():
        total = submitted.get(agent, 0)
        if total > MAX_FRAME_ATOMS:
            truncated.append({
                "agent": agent,
                "submitted": total,
                "kept": MAX_FRAME_ATOMS,
                "dropped": total - MAX_FRAME_ATOMS,
            })
            by_agent[agent] = atoms[:MAX_FRAME_ATOMS]
    return by_agent, truncated, saw_atoms


def ingest_glossary(raw: list, key_terms: list[str]) -> list[tuple[str, str, str]]:
    """-> (term, unit, agent) tuples.

    Accepts the GROUND shapes (``units`` map, ``groundings`` list) and the
    CLARIFY shape, a flat object carrying one ``term`` plus a richer definition.
    """
    out: list[tuple[str, str, str]] = []
    for d in parse_responses(raw):
        agent = str(d.get("agent") or "").strip()
        units = d.get("units") or {}
        for term in key_terms:
            unit = str(units.get(term) or "").strip()
            if unit:
                out.append((term, unit, agent))
        for g in list(d.get("groundings") or []) + [d]:
            if not isinstance(g, dict):
                continue
            term = str(g.get("term") or "").strip()
            unit = str(g.get("unit") or g.get("extended_definition")
                       or g.get("definition") or "").strip()
            if term in key_terms and unit and not any(t[0] == term and t[2] == agent for t in out):
                out.append((term, unit, agent))
    return out


def ingest_verify(raw: list) -> tuple[dict[str, list[str]], list[dict], list[str]]:
    covers: dict[str, list[str]] = {}
    checks: list[dict] = []
    all_holds: list[str] = []
    for d in parse_responses(raw):
        agent = str(d.get("agent") or "").strip()
        if not agent:
            continue
        items = [str(i).strip() for i in d.get("holds") or [] if str(i).strip()]
        covers[agent] = items
        all_holds.extend(i for i in items if i not in all_holds)
        for c in d.get("checks") or d.get("proposed_checks") or []:
            if isinstance(c, dict):
                checks.append({**c, "agent": agent})
    return covers, checks, all_holds
