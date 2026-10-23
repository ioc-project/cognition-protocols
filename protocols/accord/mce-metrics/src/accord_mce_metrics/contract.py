"""Contract validation, ported from ``accord-convergence/accord_convergence_eval.py``.

The CLI script is left untouched and still works on trace files. This copy is
the same validation (``validate`` and its helpers, unchanged) so the MCE
metrics can run it on a contract found in a session's KG spans without the
script's file handling. Keep the two in step when the contract format changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

LOCKED_STATUS = "INTENT-LOCKED"
MARKER = "LOCKED_INTENT_CONTRACT"
INITIATOR_SKILL = "l9-accord-initiator"
PHASES = ("init", "roster", "frame", "glossary", "verify")
GLOSSARY_STATUSES = frozenset({"agreed", "flagged"})
REQUIRED_TOP_LEVEL = (
    "status",
    "roster",
    "problem_frame",
    "glossary",
    "verifiable_objective",
    "flagged_items",
)
REQUIRED_OBJECTIVE = ("owners", "uncovered", "checks")
# The pre-slim contract shape, recognised only to produce a useful error.
LEGACY_KEYS = frozenset({"Roster", "Frame", "Glossary", "VerifiableObjective", "flagged"})


@dataclass
class Report:
    """Findings for a single contract."""

    source: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def _identity(entry: Any) -> str | None:
    """The agent name in a roster entry, however the entry was written.

    CONVENE returns a bare name from some models and a profile object
    (identity / roles / capabilities / accessScopes) from others. Both name the
    same agent, so both are accepted.
    """
    if _is_str(entry):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("identity", "agent", "name", "id"):
            if _is_str(entry.get(key)):
                return entry[key].strip()
    return None


class AtomIndex:
    """Resolve an atom reference the way a reader would.

    problem_frame names atoms by id, verifiable_objective quotes their text,
    and either copy can arrive clipped by a field-width limit, so a reference
    is matched by id, then by exact text, then by a prefix that is unique in
    one direction or the other.
    """

    def __init__(self, atoms: list[dict[str, Any]]) -> None:
        self.by_id: dict[str, str] = {}
        self.by_text: dict[str, str] = {}
        for atom in atoms:
            if not isinstance(atom, dict) or not _is_str(atom.get("id")):
                continue
            atom_id = atom["id"].strip()
            self.by_id[atom_id] = atom_id
            if _is_str(atom.get("text")):
                self.by_text.setdefault(atom["text"].strip(), atom_id)

    def __bool__(self) -> bool:
        return bool(self.by_id)

    def resolve(self, reference: str) -> str | None:
        reference = reference.strip()
        if reference in self.by_id:
            return self.by_id[reference]
        if reference in self.by_text:
            return self.by_text[reference]
        matches = {
            atom_id
            for text, atom_id in self.by_text.items()
            if text.startswith(reference) or reference.startswith(text)
        }
        return matches.pop() if len(matches) == 1 else None


# --------------------------------------------------------------------------
# required fields and types
# --------------------------------------------------------------------------


def _check_roster(contract: dict[str, Any], report: Report) -> list[str]:
    if "roster" not in contract:
        return []
    roster = contract["roster"]
    if not isinstance(roster, list):
        report.error("roster: expected a list of agent identities")
        return []
    identities = [name for name in map(_identity, roster) if name]
    if len(identities) != len(roster):
        report.error("roster: every entry must name an agent")
    if not identities:
        report.warn("roster: empty, so no atom can be validly owned")
    for dupe in _duplicates(identities):
        report.error(f"roster: duplicate identity {dupe!r}")
    return identities


def _check_frame(contract: dict[str, Any], report: Report) -> AtomIndex:
    if "problem_frame" not in contract:
        return AtomIndex([])
    frame = contract["problem_frame"]
    if not isinstance(frame, list):
        report.error("problem_frame: expected a list of atoms")
        return AtomIndex([])

    atom_ids: list[str] = []
    supports: list[int] = []
    for index, atom in enumerate(frame):
        where = f"problem_frame[{index}]"
        if not isinstance(atom, dict):
            report.error(f"{where}: expected an object")
            continue
        for key in ("id", "text", "support"):
            if key not in atom:
                report.error(f"{where}: missing required field {key!r}")
        if _is_str(atom.get("id")):
            atom_ids.append(atom["id"])
        elif "id" in atom:
            report.error(f"{where}: 'id' must be a non-empty string")
        if "text" in atom and not _is_str(atom.get("text")):
            report.error(f"{where}: 'text' must be a non-empty string")
        support = atom.get("support")
        if "support" in atom:
            if isinstance(support, bool) or not isinstance(support, int):
                report.error(f"{where}: 'support' must be an integer")
            elif support < 1:
                report.error(f"{where}: 'support' must be >= 1, got {support}")
            else:
                supports.append(support)

    for dupe in _duplicates(atom_ids):
        report.error(f"problem_frame: duplicate atom id {dupe!r}")
    # Every atom backed by exactly one agent means nothing was corroborated.
    if len(frame) > 3 and supports and all(s == 1 for s in supports):
        report.warn(
            f"problem_frame: all {len(frame)} atoms are unverified singletons "
            "(assemble_lock records this as fragile_frame)"
        )
    return AtomIndex([atom for atom in frame if isinstance(atom, dict)])


def _check_glossary(
    contract: dict[str, Any], roster: list[str], report: Report
) -> tuple[list[str], list[str]]:
    """Return (all terms, flagged terms)."""
    if "glossary" not in contract:
        return [], []
    glossary = contract["glossary"]
    if not isinstance(glossary, list):
        report.error("glossary: expected a list of entries")
        return [], []

    terms: list[str] = []
    flagged: list[str] = []
    for index, entry in enumerate(glossary):
        where = f"glossary[{index}]"
        if not isinstance(entry, dict):
            report.error(f"{where}: expected an object")
            continue
        term = entry.get("term")
        if not _is_str(term):
            report.error(f"{where}: 'term' must be a non-empty string")
            term = None
        else:
            terms.append(term)
            where = f"glossary[{term}]"

        status = entry.get("status")
        if status not in GLOSSARY_STATUSES:
            report.error(
                f"{where}: 'status' must be one of {sorted(GLOSSARY_STATUSES)}, got {status!r}"
            )
            continue

        if status == "agreed":
            if not _is_str(entry.get("definition")):
                report.error(f"{where}: agreed entries need a non-empty 'definition'")
            if "variants" in entry:
                report.error(f"{where}: agreed entries must not carry 'variants'")
            continue

        # flagged
        if term:
            flagged.append(term)
        if "definition" in entry:
            report.error(f"{where}: flagged entries must not carry 'definition'")
        variants = entry.get("variants")
        if not isinstance(variants, list):
            report.error(f"{where}: flagged entries need a 'variants' list")
            continue
        if not variants:
            report.error(f"{where}: flagged entries need at least one variant")
        elif len(variants) < 2:
            # The point of flagging a term is to carry the disagreement.
            report.warn(
                f"{where}: flagged with a single variant, so no competing "
                "definition was actually recorded"
            )
        for position, variant in enumerate(variants):
            spot = f"{where}.variants[{position}]"
            if not isinstance(variant, dict):
                report.error(f"{spot}: expected an object")
                continue
            if not _is_str(variant.get("agent")):
                report.error(f"{spot}: 'agent' must be a non-empty string")
            elif roster and variant["agent"] not in roster:
                report.error(f"{spot}: agent {variant['agent']!r} is not in the roster")
            if not _is_str(variant.get("definition")):
                report.error(f"{spot}: 'definition' must be a non-empty string")

    for dupe in _duplicates(terms):
        report.error(f"glossary: duplicate term {dupe!r}")
    return terms, flagged


# --------------------------------------------------------------------------
# referential integrity, resolved loosely
# --------------------------------------------------------------------------


def _drift(report: Report, where: str, unresolved: list[str], total: int) -> None:
    """Note references that never landed on a frame atom, once per field.

    owners and uncovered are keyed by knowledge-map items, which the kernel
    keeps alongside the contract rather than inside it, so most of these are
    expected. One line says the copies drifted without burying the real
    defects under a thousand of its own.
    """
    if not unresolved:
        return
    report.warn(
        f"{where}: {len(unresolved)} of {total} references do not resolve to a "
        f"problem_frame atom, e.g. {unresolved[0][:60]!r}"
    )


def _check_objective(
    contract: dict[str, Any], atoms: AtomIndex, roster: list[str], report: Report
) -> list[str]:
    """Return the uncovered references, as written."""
    if "verifiable_objective" not in contract:
        return []
    objective = contract["verifiable_objective"]
    if not isinstance(objective, dict):
        report.error("verifiable_objective: expected an object")
        return []
    for key in REQUIRED_OBJECTIVE:
        if key not in objective:
            report.error(f"verifiable_objective: missing required field {key!r}")

    owners = objective.get("owners")
    if owners is not None:
        if not isinstance(owners, dict):
            report.error("verifiable_objective.owners: expected a reference -> agent map")
        else:
            drifted = []
            for reference, agent in owners.items():
                if not _is_str(agent):
                    report.error(
                        f"verifiable_objective.owners[{reference}]: "
                        "owner must be a non-empty string"
                    )
                elif roster and agent not in roster:
                    report.error(
                        f"verifiable_objective.owners[{reference}]: "
                        f"owner {agent!r} is not in the roster"
                    )
                if atoms and atoms.resolve(str(reference)) is None:
                    drifted.append(str(reference))
            _drift(report, "verifiable_objective.owners", drifted, len(owners))

    uncovered = objective.get("uncovered")
    uncovered_refs: list[str] = []
    if uncovered is not None:
        if not isinstance(uncovered, list):
            report.error("verifiable_objective.uncovered: expected a list of references")
        else:
            drifted = []
            for reference in uncovered:
                if not _is_str(reference):
                    report.error(
                        "verifiable_objective.uncovered: entries must be non-empty strings"
                    )
                    continue
                uncovered_refs.append(reference)
                if atoms and atoms.resolve(reference) is None:
                    drifted.append(reference)
            for dupe in _duplicates(uncovered_refs):
                report.error(f"verifiable_objective.uncovered: duplicate entry {dupe!r}")
            _drift(report, "verifiable_objective.uncovered", drifted, len(uncovered_refs))

    checks = objective.get("checks")
    if checks is not None:
        if not isinstance(checks, list):
            report.error("verifiable_objective.checks: expected a list")
        else:
            drifted = []
            for index, check in enumerate(checks):
                spot = f"verifiable_objective.checks[{index}]"
                if not isinstance(check, dict):
                    report.error(f"{spot}: expected an object")
                    continue
                reference = check.get("atom")
                if not _is_str(reference):
                    report.error(f"{spot}: 'atom' must be a non-empty string")
                elif atoms and atoms.resolve(reference) is None:
                    drifted.append(reference)
                if not _is_str(check.get("criterion")):
                    report.error(f"{spot}: 'criterion' must be a non-empty string")
            _drift(report, "verifiable_objective.checks", drifted, len(checks))
            if not checks and atoms:
                report.warn(
                    "verifiable_objective.checks: empty, so the objective is not verifiable"
                )
    return uncovered_refs


# --------------------------------------------------------------------------
# invariants assemble_lock derives
# --------------------------------------------------------------------------


def _check_flagged_items(
    contract: dict[str, Any],
    flagged_terms: list[str],
    uncovered_ids: list[str],
    report: Report,
) -> None:
    if "flagged_items" not in contract:
        return
    items = contract["flagged_items"]
    if not isinstance(items, list):
        report.error("flagged_items: expected a list of defect strings")
        return
    if not all(isinstance(item, str) for item in items):
        report.error("flagged_items: every entry must be a string")
        return

    def tagged(prefix: str) -> set[str]:
        return {item[len(prefix) :].strip() for item in items if item.startswith(prefix)}

    recorded_terms = tagged("ambiguous_term:")
    for term in flagged_terms:
        if term not in recorded_terms:
            report.error(
                f"flagged_items: missing 'ambiguous_term: {term}' for the flagged glossary entry"
            )
    for term in sorted(recorded_terms - set(flagged_terms)):
        report.error(
            f"flagged_items: 'ambiguous_term: {term}' has no flagged glossary entry"
        )

    recorded_atoms = tagged("uncovered_atom:")
    for atom_id in uncovered_ids:
        if atom_id not in recorded_atoms:
            report.error(
                f"flagged_items: missing 'uncovered_atom: {atom_id}' for the uncovered atom"
            )
    for atom_id in sorted(recorded_atoms - set(uncovered_ids)):
        report.error(
            f"flagged_items: 'uncovered_atom: {atom_id}' is not listed in "
            "verifiable_objective.uncovered"
        )


def validate(contract: Any, source: str = "<contract>") -> Report:
    """Validate one locked contract and return its findings."""
    report = Report(source=source)

    if not isinstance(contract, dict):
        report.error(f"expected a JSON object, got {type(contract).__name__}")
        return report

    if LEGACY_KEYS & set(contract):
        report.error(
            "this is the legacy PascalCase contract (Roster/Frame/Glossary/"
            "VerifiableObjective/flagged), not the assemble_lock format"
        )
        return report

    for key in REQUIRED_TOP_LEVEL:
        if key not in contract:
            report.error(f"missing required field {key!r}")

    status = contract.get("status")
    if "status" in contract and status != LOCKED_STATUS:
        report.error(f"status: expected {LOCKED_STATUS!r}, got {status!r}")

    for key in sorted(set(contract) - set(REQUIRED_TOP_LEVEL)):
        report.warn(f"unrecognised top-level field {key!r}")

    roster = _check_roster(contract, report)
    atoms = _check_frame(contract, report)
    terms, flagged_terms = _check_glossary(contract, roster, report)
    uncovered_refs = _check_objective(contract, atoms, roster, report)
    _check_flagged_items(contract, flagged_terms, uncovered_refs, report)

    # The two things that make a lock worth relaying: shared intent, and
    # agreed words for it. A contract carrying neither locked nothing.
    if not atoms:
        report.error("problem_frame: locked with no atoms, so there is no shared intent")
    if not terms:
        report.error("glossary: locked with no terms, so no vocabulary was agreed")
    return report


# --------------------------------------------------------------------------
# finding the contracts in a trace
# --------------------------------------------------------------------------


def embedded_objects(text: str) -> Iterator[dict[str, Any]]:
    """Yield every balanced top-level {...} in text that parses as JSON.

    String-aware, so a brace inside a quoted definition does not confuse the
    depth count. A contract truncated mid-emission yields nothing, which is how
    a half-relayed contract is detected.
    """
    depth = 0
    start = -1
    in_string = escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    yield parsed
                start = -1


def _locked(text: str, after_marker: bool = False) -> dict[str, Any] | None:
    """First object with status INTENT-LOCKED, optionally searched past MARKER."""
    if after_marker:
        at = text.find(MARKER)
        if at >= 0:
            text = text[at:]
    for obj in embedded_objects(text):
        if obj.get("status") == LOCKED_STATUS:
            return obj
    return None
