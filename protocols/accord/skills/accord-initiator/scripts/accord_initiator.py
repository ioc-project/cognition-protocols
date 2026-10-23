#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""ACCORD merge CLI.

The orchestrator collects specialist replies (raw LLM text or JSON) and pipes
them through this script, which performs the deterministic merge and persists
session state in a JSON file, so each phase can run as a separate process.

    accord_initiator.py init --specialists a,b,c --key-terms severity,error_rate
    accord_initiator.py roster   --responses r.json
    accord_initiator.py frame    --responses r.json [--current-frame f.json]
    accord_initiator.py glossary --responses r.json
    accord_initiator.py verify   --responses r.json --required-knowledge k.json --objective-clauses c.json
    accord_initiator.py lock
    accord_initiator.py execution-message --agent <id> --assignment-file task.txt
    accord_initiator.py status

``--responses`` / ``--required-knowledge`` / ``--objective-clauses`` accept a
file path or ``-`` for stdin; content is a JSON array (strings or objects).
Every command prints one JSON object ``{"ok": true, "phase": ..., "data": ...}``
to stdout. Optional embedding similarity: ``pip install sentence-transformers``
(else difflib string similarity); force string mode with ``ACCORD_EMBEDDING=off``.

State persists in one JSON file (``--state``, default ``.accord/state.json``
or $ACCORD_STATE), the same path for every command in a session. Set
ACCORD_RUN_KEY to isolate this invocation's state file by a
deterministic key instead (see ``resolve_state_path``) — needed for LLM
response caching across a record/replay pair, and to stop unrelated runs
from sharing one session file. Mirrors concord_initiator.py's CONCORD_RUN_KEY /
``unique_state_path``, applied on every command (not just ``init``) since
accord_initiator.py has no mint-once-reuse convention.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------
# Config + similarity
# --------------------------------------------------------------------------

DEFAULT_STATE = ".accord/state.json"
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_VERIFIER_ID = "verifier"
GROUP_THRESHOLD = 0.72
SIM_MERGE = 0.72
# Equal to SIM_MERGE so the near-match band [SIM_CLARIFY, SIM_MERGE) is empty.
# Glossary pairs either agree or stay flagged. Nothing is sent to CLARIFY.
SIM_CLARIFY = SIM_MERGE
MAX_UNCOVERED_HOLDINGS = 1
MAX_FRAME_ATOMS = 6
MAX_FRAME_ROUNDS = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").lower()))


def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (nu * nv)))


class Similarity:
    """MiniLM cosine when available, difflib ratio otherwise."""

    def __init__(self) -> None:
        self._embedder: Any = None
        self._tried = False
        self._cache: dict[str, tuple[float, ...]] = {}

    def _get_embedder(self) -> Any:
        if os.environ.get("ACCORD_EMBEDDING", "on").lower() in {"0", "off", "false", "none"}:
            return None
        if not self._tried:
            self._tried = True
            try:
                from sentence_transformers import SentenceTransformer

                model = os.environ.get("ACCORD_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
                self._embedder = SentenceTransformer(model, local_files_only=True)
            except Exception:
                self._embedder = None
        return self._embedder

    def _vectors(self, texts: list[str]) -> list[tuple[float, ...]] | None:
        embedder = self._get_embedder()
        if embedder is None:
            return None
        missing = [t for t in texts if t not in self._cache]
        if missing:
            for text, vec in zip(missing, embedder.encode(missing)):
                self._cache[text] = tuple(float(x) for x in vec)
        return [self._cache[t] for t in texts]

    def __call__(self, a: str, b: str) -> float:
        na, nb = normalize_text(a), normalize_text(b)
        if not na and not nb:
            return 1.0
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        vecs = self._vectors([(a or "").strip(), (b or "").strip()])
        if vecs is None:
            return float(SequenceMatcher(None, na, nb).ratio())
        return _cosine(vecs[0], vecs[1])


sim = Similarity()

# --------------------------------------------------------------------------
# Response ingestion
# --------------------------------------------------------------------------


def strip_fences(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", stripped.strip())


def parse_responses(raw: list[Any]) -> list[dict]:
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


def ingest_roster(raw: list[Any]) -> list[dict]:
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


def ingest_frame_round(raw: list[Any]) -> tuple[dict[str, list[dict]], list[dict], bool]:
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


def ingest_glossary(raw: list[Any], key_terms: list[str]) -> list[tuple[str, str, str]]:
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


def ingest_verify(raw: list[Any]) -> tuple[dict[str, list[str]], list[dict], list[str]]:
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

# --------------------------------------------------------------------------
# Phase algorithms
# --------------------------------------------------------------------------


def union_roster(cards: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for c in cards:
        out.setdefault(c["identity"], c)
    return list(out.values())


def group_atoms(observations: list[dict]) -> list[dict]:
    """Cluster similar atoms; support = distinct contributing agents."""
    clusters: list[list[dict]] = []
    for obs in observations:
        for cl in clusters:
            if sim(obs["text"], cl[0]["text"]) >= GROUP_THRESHOLD:
                cl.append(obs)
                break
        else:
            clusters.append([obs])
    species = []
    for i, cl in enumerate(clusters, start=1):
        agents = list(dict.fromkeys(o["agent"] for o in cl))
        species.append({
            "id": f"s{i}",
            "text": cl[0]["text"],
            "type": cl[0]["kind"],
            "support": len(agents),
            "agents": agents,
        })
    return species


def good_turing_missing_mass(support: list[int]) -> float:
    n = sum(support)
    return 1.0 if n <= 0 else sum(1 for s in support if s == 1) / n


def chao1(support: list[int]) -> float:
    s_obs = len(support)
    f1 = sum(1 for s in support if s == 1)
    f2 = sum(1 for s in support if s == 2)
    if s_obs == 0:
        return 0.0
    return s_obs + (f1 * f1 / (2.0 * f2) if f2 else f1 * max(f1 - 1, 0) / 2.0)


def build_glossary(terms: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """Per term, cluster agents' units by similarity.

    One cluster -> ``agreed``. Several -> ``flagged`` with per-agent variants.
    Cross-cluster pairs scoring in [SIM_CLARIFY, SIM_MERGE) are ``ambiguous``
    and should go through CLARIFY.
    """
    by_term: dict[str, list[tuple[str, str]]] = {}
    for term, unit, agent in terms:
        by_term.setdefault(term, []).append((unit, agent))

    entries: list[dict] = []
    ambiguous: list[dict] = []
    for term, items in by_term.items():
        clusters: list[list[int]] = []
        for i, (unit, _) in enumerate(items):
            for cl in clusters:
                if sim(unit, items[cl[0]][0]) >= SIM_MERGE:
                    cl.append(i)
                    break
            else:
                clusters.append([i])
        if len(clusters) == 1:
            agents = list(dict.fromkeys(a for _, a in items))
            entries.append({"term": term, "status": "agreed",
                            "definition": items[0][0], "agents": agents})
            continue
        entries.append({
            "term": term, "status": "flagged",
            "variants": [{"agent": items[i][1], "definition": items[i][0]}
                         for cl in clusters for i in cl],
        })
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                for i in clusters[x]:
                    for j in clusters[y]:
                        s = sim(items[i][0], items[j][0])
                        if SIM_CLARIFY <= s < SIM_MERGE:
                            ambiguous.append({
                                "term": term,
                                "agents": [items[i][1], items[j][1]],
                                "units": [items[i][0], items[j][0]],
                                "sim": round(s, 3),
                            })
    return entries, ambiguous


def greedy_set_cover(universe: list[str], covers: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    remaining = set(universe)
    owned: dict[str, list[str]] = {a: [] for a in covers}
    while remaining:
        best, gain = "", []
        for agent, items in covers.items():
            g = [i for i in items if i in remaining]
            if len(g) > len(gain):
                best, gain = agent, g
        if not best:
            break
        owned[best].extend(gain)
        remaining.difference_update(gain)
    return owned, [i for i in universe if i in remaining]


def assign_holdings(universe: list[str], covers: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """Set-cover knowledge ownership; the verifier never owns knowledge."""
    filtered = {a: list(v) for a, v in covers.items() if a.strip().lower() != DEFAULT_VERIFIER_ID}
    owned, uncovered = greedy_set_cover(universe, filtered)
    for a in covers:
        owned.setdefault(a, [])
    # Tolerate up to MAX_UNCOVERED_HOLDINGS gaps; assign extras to the first agent.
    while len(uncovered) > MAX_UNCOVERED_HOLDINGS and filtered:
        owned[next(iter(filtered))].append(uncovered.pop(0))
    return owned, uncovered


def _covered_clauses(check: dict, clauses: list[str]) -> set[int]:
    covers = check.get("covers", [])
    return {
        i for i, cl in enumerate(clauses)
        if cl in covers or any(cl.strip().lower() in cv.strip().lower() for cv in covers)
    }


def assign_checks(checks_raw: list[dict], clauses: list[str]) -> list[dict]:
    """Greedy set-cover selection of acceptance checks over objective clauses."""
    uncovered = set(range(len(clauses)))
    remaining = list(checks_raw)
    selected = []
    while uncovered and remaining:
        best = max(remaining, key=lambda c: len(uncovered & _covered_clauses(c, clauses)))
        gain = uncovered & _covered_clauses(best, clauses)
        if not gain:
            break
        uncovered -= gain
        remaining.remove(best)
        selected.append({
            # The atom is the objective clause this check discharges, not a
            # prefix of the criterion -- slicing the criterion produced
            # mid-word fragments that made the contract unreadable.
            "atom": clauses[min(gain)],
            "criterion": best.get("criterion", ""),
            "assigned_to": best.get("agent", ""),
            "covers": best.get("covers", []),
        })
    return selected


def assemble_lock(st: dict) -> dict:
    frame, glossary = st["frame"], st["glossary"]
    defects = list(st["defects"])
    if len(frame) > 3 and all(sp["support"] == 1 for sp in frame):
        defects.append(f"fragile_frame: all {len(frame)} atoms are unverified singletons")
    defects += [f"ambiguous_term: {g['term']}" for g in glossary if g["status"] == "flagged"]
    defects += [f"uncovered_atom: {u}" for u in st["uncovered"]]
    defects += list(st.get("coverage_warnings") or [])
    owners = {item: agent for agent, items in st["owned"].items() for item in items}
    return {
        "status": "INTENT-LOCKED",
        "roster": st["roster"],
        "problem_frame": [{"id": s["id"], "text": s["text"], "support": s["support"],
                           "flagged_singleton": s["support"] == 1} for s in frame],
        "glossary": glossary,
        "verifiable_objective": {
            "owners": owners,
            "uncovered": st["uncovered"],
            "checks": [{"atom": c["atom"], "criterion": c["criterion"],
                        "assigned_to": c["assigned_to"]} for c in st["checks"]
                       if c.get("atom") and c.get("criterion")],
        },
        "flagged_items": defects,
    }

# --------------------------------------------------------------------------
# State + I/O
# --------------------------------------------------------------------------


def resolve_state_path(path: str) -> str:
    """Resolve the effective --state path for this invocation.

    `path` is whatever the caller passed via --state, or the $ACCORD_STATE /
    DEFAULT_STATE default — the same literal value on every command in a
    session (see the module docstring). Unmodified, that default is the same
    physical file for every concurrent or sequential run that uses this
    skill, so sessions silently overwrite each other.

    When ACCORD_RUN_KEY is set, this returns a deterministic, isolated
    path instead: a hash of the run key and the original filename, placed in
    ACCORD_STATE_DIR (or next to the original path if that is unset).
    Every command under the same run key maps to the same isolated path, so
    a caller doing record/replay sets ACCORD_RUN_KEY to something stable
    across both runs (for example a hash of the LLM cache path) to get
    byte-identical, collision-free state paths and real cache hits. When
    ACCORD_RUN_KEY is unset, `path` is returned unchanged — today's
    default behavior, preserved for existing callers.
    """
    run_key = os.environ.get("ACCORD_RUN_KEY")
    if not run_key:
        return path
    p = Path(path)
    state_dir = Path(os.environ.get("ACCORD_STATE_DIR") or p.parent)
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_key}:{p.name}").hex[:8]
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / f"{p.stem}-{suffix}{p.suffix}")


def blank_state() -> dict:
    return {
        "specialists": [], "key_terms": [], "action_tool": "", "coverage_warnings": [],
        "roster": [], "obs": [], "frame": [], "glossary": [],
        "owned": {}, "uncovered": [], "checks": [], "defects": [],
        "frame_rounds": 0, "locked_contract": None,
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return blank_state()
    return {**blank_state(), **json.loads(path.read_text())}


def save_state(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=2))


def read_json(arg: str | None, default: Any = None) -> Any:
    if arg is None:
        return default
    stripped = arg.strip()
    if stripped.startswith(("[", "{")):
        # Inline JSON. A caller driving this script through a tool interface has
        # one stdin per invocation, so phases taking several arguments can only
        # supply the rest inline.
        source, text = "inline JSON", stripped
    elif arg == "-":
        source, text = arg, sys.stdin.read()
    else:
        source, text = arg, Path(arg).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {source}: {exc}")


def csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def read_text(arg: str) -> str:
    """Read an assignment from a file path or from stdin when arg is '-'."""
    text = sys.stdin.read() if arg == "-" else Path(arg).read_text()
    return text.strip()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def render_execution_message(agent: str, contract: dict, assignment: str) -> str:
    return (
        "EXECUTION_REQUEST\n\n"
        "Activate your `accord-receiver` skill and follow its EXECUTION_REQUEST\n"
        "handler before starting the assignment. Report values in the units fixed by the\n"
        "contract Glossary, complete the acceptance checks assigned to you in the\n"
        "Verifiable Objective, and begin your reply with `LOCK_STATUS: RECEIVED`.\n\n"
        f"LOCKED_INTENT_CONTRACT: {canonical_json(contract)}\n\n"
        f"ASSIGNMENT: {assignment}"
    )

# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def protocol_control(next_command: str) -> dict:
    return {
        "next_command": next_command,
        "protocol_status": "IN_PROGRESS",
        "locked_contract_exists": False,
        "normal_execution_allowed": False,
        "app_execution_allowed": False,
    }


def cmd_init(a: argparse.Namespace, st: dict) -> dict:
    st = blank_state()
    st["specialists"] = csv(a.specialists)
    st["key_terms"] = csv(a.key_terms)
    st["action_tool"] = a.action_tool or ""
    a._state_out = st
    return {
        "specialists": st["specialists"],
        "key_terms": st["key_terms"],
        "action_tool": st["action_tool"],
        **protocol_control("roster"),
    }


def cmd_roster(a: argparse.Namespace, st: dict) -> dict:
    st["roster"] = union_roster(ingest_roster(read_json(a.responses, [])))
    owners: dict[str, list[str]] = {}
    for c in st["roster"]:
        for role in c["roles"]:
            owners.setdefault(role.strip().lower(), []).append(c["identity"])
    st["defects"] = [f"duplicate exclusive role '{r}' claimed by {o}"
                     for r, o in owners.items() if len(o) > 1]
    return {
        "roster": st["roster"],
        "flagged_items": st["defects"],
        **protocol_control("frame"),
    }


def cmd_frame(a: argparse.Namespace, st: dict) -> dict:
    # Accepted so an explicit current frame still has to be valid JSON. The
    # live atoms come from session state, replaced per agent on each round.
    read_json(a.current_frame, [])
    raw = read_json(a.responses, [])
    by_agent, truncated, saw_atoms = ingest_frame_round(raw)
    if raw and not saw_atoms:
        raise SystemExit(
            "frame: none of the responses carried an 'atoms' list of "
            "{\"text\": ...} objects. Refusing to record an unparseable round."
        )
    submitted = set(by_agent)
    st["obs"] = [obs for obs in st["obs"] if obs["agent"] not in submitted]
    for atoms in by_agent.values():
        st["obs"].extend(atoms)
    st["frame_rounds"] += 1
    st["frame"] = group_atoms(st["obs"]) if st["obs"] else []
    atoms_by_agent = {agent: [] for agent in submitted}
    for obs in st["obs"]:
        atoms_by_agent.setdefault(obs["agent"], []).append(obs["text"])
    support = [s["support"] for s in st["frame"]]
    mm = good_turing_missing_mass(support)
    frame_rounds_done = st["frame_rounds"] >= MAX_FRAME_ROUNDS
    next_command = "glossary" if frame_rounds_done else "frame"
    return {
        "atoms": [{**s, "flagged_singleton": s["support"] == 1} for s in st["frame"]],
        "new_this_round": sum(len(atoms) for atoms in by_agent.values()),
        "round": st["frame_rounds"],
        **protocol_control(next_command),
        "atoms_by_agent": atoms_by_agent,
        "truncated": truncated,
        "coverage": {"good_turing_missing_mass": round(mm, 4),
                     "chao1": round(chao1(support), 4),
                     "coverage": round(max(0.0, 1.0 - mm), 4)},
    }


def cmd_glossary(a: argparse.Namespace, st: dict) -> dict:
    key_terms = csv(a.key_terms) or st["key_terms"]
    raw = read_json(a.responses, [])
    tuples = ingest_glossary(raw, key_terms)
    if raw and not tuples:
        raise SystemExit(
            "glossary: none of the responses carried a definition for a key term "
            f"({', '.join(key_terms) or 'no key terms registered'}). Expected a "
            "'units' map, a 'groundings' list, or 'term' with 'extended_definition'. "
            "Refusing to overwrite the existing glossary."
        )
    entries, ambiguous = build_glossary(tuples)
    # CLARIFY re-grounds only the terms it was asked about, so keep every term
    # this call did not speak to instead of replacing the whole glossary.
    revised = {e["term"] for e in entries}
    st["glossary"] = [e for e in st["glossary"] if e["term"] not in revised] + entries
    next_command = "glossary" if ambiguous else "verify"
    return {
        "glossary": st["glossary"],
        "ambiguous": ambiguous,
        **protocol_control(next_command),
    }


def cmd_verify(a: argparse.Namespace, st: dict) -> dict:
    required = read_json(a.required_knowledge, [])
    clauses = read_json(a.objective_clauses, [])
    covers, checks_raw, all_holds = ingest_verify(read_json(a.responses, []))
    # Without the required-knowledge universe the knowledge map collapses to
    # what the specialists happened to report, so every item is held by
    # definition and no knowledge gap can ever appear. Falling back is still useful,
    # but the contract has to say the coverage was never actually tested.
    # Replaced, not appended, so re-running verify with the arguments supplied
    # clears the warning rather than inheriting it from an earlier attempt.
    warnings = []
    if not required:
        warnings.append(
            "coverage_untested: verify ran without --required-knowledge, so "
            "knowledge gaps could not be detected"
        )
    if not clauses:
        warnings.append(
            "coverage_untested: verify ran without --objective-clauses, so "
            "unverifiable objectives could not be detected"
        )
    st["coverage_warnings"] = warnings
    universe = required or all_holds
    st["owned"], st["uncovered"] = assign_holdings(universe, covers)
    held_by: dict[str, list[str]] = {}
    for agent, items in covers.items():
        for item in items:
            held_by.setdefault(item, []).append(agent)
    st["checks"] = assign_checks(checks_raw, clauses)
    covered: set[int] = set()
    for c in st["checks"]:
        covered |= _covered_clauses(c, clauses)
    return {
        "knowledge_map": [{"item": i, "held_by": held_by.get(i, []), "gap": i in st["uncovered"]}
                          for i in universe],
        "checks": st["checks"],
        "gaps": list(st["uncovered"]),
        "unverifiable_clauses": [cl for i, cl in enumerate(clauses) if i not in covered],
        "locked_intent_contract": _store_contract(st),
        "next": _next_step(st),
    }


def _next_step(st: dict) -> dict:
    """Spell out Phase 5 in the verify payload.

    A contract with flagged items reads like a conclusion, and orchestrators
    have ended the run there with a written recommendation. Fresh tool output
    outranks skill text recalled from many turns ago, so the follow-up command
    ships with the contract itself.
    """
    action_tool = st["action_tool"] or "the action tool"
    return {
        "command": "execution-message",
        "agents": list(st["specialists"]),
        "note": (
            "flagged_items do not end the protocol: render one execution message "
            "per agent, delegate data.message unchanged, then call "
            f"{action_tool} once the replies arrive"
        ),
    }


def _store_contract(st: dict) -> dict:
    contract = assemble_lock(st)
    st["locked_contract"] = contract
    return contract


def cmd_lock(a: argparse.Namespace, st: dict) -> dict:
    return _store_contract(st)


def cmd_execution_message(a: argparse.Namespace, st: dict) -> dict:
    contract = st.get("locked_contract")
    if not contract:
        raise SystemExit("execution-message: no locked contract exists")
    if contract.get("status") != "INTENT-LOCKED":
        raise SystemExit("execution-message: contract is not INTENT-LOCKED")
    assignment = read_text(a.assignment_file)
    return {
        "agent": a.agent,
        "message": render_execution_message(a.agent, contract, assignment),
    }


def cmd_status(a: argparse.Namespace, st: dict) -> dict:
    return {
        "specialists": st["specialists"], "key_terms": st["key_terms"],
        "action_tool": st["action_tool"], "roster": len(st["roster"]),
        "frame_atoms": len(st["frame"]), "frame_rounds": st["frame_rounds"],
        "glossary_terms": len(st["glossary"]), "checks": len(st["checks"]),
        "locked": bool(st["owned"] or st["checks"]),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", default=os.environ.get("ACCORD_STATE", DEFAULT_STATE),
                   help=f"session state file (default: $ACCORD_STATE or {DEFAULT_STATE})")
    sub = p.add_subparsers(dest="phase", required=True)
    # Every JSON argument below accepts a file path, "-" for stdin, or the JSON
    # value inline. Only one argument per invocation can use "-".
    json_arg = "path, '-' for stdin, or inline JSON"

    s = sub.add_parser("init", help="start a new session (resets state)")
    s.add_argument("--specialists", required=True, help="comma-separated specialist ids")
    s.add_argument("--key-terms", default="", help="comma-separated vocabulary terms for GROUND")
    s.add_argument("--action-tool", default="", help="name of the action tool/step for EXECUTION")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("roster", help="Phase 0 CONVENE merge")
    s.add_argument("--responses", required=True, help=f"specialist replies ({json_arg})")
    s.set_defaults(fn=cmd_roster)

    s = sub.add_parser("frame", help="Phase 1 FRAME merge (call once per round)")
    s.add_argument("--responses", required=True, help=f"specialist replies ({json_arg})")
    s.add_argument("--current-frame",
                   help=f"atom texts already merged, [] on round 1 ({json_arg})")
    s.set_defaults(fn=cmd_frame)

    s = sub.add_parser("glossary", help="Phase 2 GROUND merge (also used after CLARIFY)")
    s.add_argument("--responses", required=True, help=f"specialist replies ({json_arg})")
    s.add_argument("--key-terms", help="override key terms from init")
    s.set_defaults(fn=cmd_glossary)

    s = sub.add_parser("verify", help="Phase 3 VERIFY merge; also returns the locked contract")
    s.add_argument("--responses", required=True, help=f"specialist replies ({json_arg})")
    s.add_argument("--required-knowledge",
                   help=f"required knowledge items ({json_arg})")
    s.add_argument("--objective-clauses",
                   help=f"objective clause texts ({json_arg})")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("lock", help="Phase 4 LOCK: assemble contract from saved state")
    s.set_defaults(fn=cmd_lock)

    s = sub.add_parser(
        "execution-message",
        help="render an EXECUTION_REQUEST containing the stored locked contract",
    )
    s.add_argument("--agent", required=True, help="specialist id this message is for")
    s.add_argument(
        "--assignment-file",
        required=True,
        help="assignment text file, or '-' for stdin",
    )
    s.set_defaults(fn=cmd_execution_message)

    s = sub.add_parser("status", help="show session progress")
    s.set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(resolve_state_path(args.state))
    st = load_state(path)
    try:
        data = args.fn(args, st)
    except SystemExit:
        raise
    except Exception as exc:  # surface as JSON so harnesses can read it
        print(json.dumps({"ok": False, "phase": args.phase, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    save_state(path, getattr(args, "_state_out", st))
    print(json.dumps({"ok": True, "phase": args.phase, "data": data}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
