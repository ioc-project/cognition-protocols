"""Deterministic term-selection scoring, copied unchanged from the standalone eval.

Source: ``accord-correctness/accord_correctness_eval.py`` (term handling, the
vocabulary parsers, ``unify_terms``, the per-run scoring dataclasses and
``_mentions``). Only the trace reading and the CLI were left behind; the
session's spans are read in ``correctness.py`` instead.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Sequence

LOCKED_STATUS = "INTENT-LOCKED"
# The kernel's default similarity threshold (``sim_merge``) below which two definitions disagree.
SIM_MERGE = 0.72
_TOKEN = re.compile(r"[a-z0-9]+")
_GLOSSARY_RELAY = re.compile(r"[\"']glossary[\"']\s*:")
# Longest remainder accepted as an elliptical definition in the second pass.
_ELLIPTICAL_WORDS = 4

# Declaration lines are prose, so the patterns stay deliberately narrow: a
# loose rule turns every bulleted instruction into a vocabulary entry.
_QUOTED_MEANS = re.compile(r'^[-*]\s*["\u201c]?([\w \-/]{2,40}?)["\u201d]?\s+means\s+(.+)$', re.I)
_COLON_DEF = re.compile(r"^[-*]\s*([A-Za-z][\w \-/]{1,30}?)\s*:\s*(.+)$")
_COPULA_DEF = re.compile(r"^[-*]\s*([A-Za-z][\w \-/]{1,30}?)\s+(?:are|is)\s+(.+)$", re.I)

# Fragments that mark a line as an instruction rather than a definition.
_NOT_A_DEFINITION = re.compile(
    r"\*\*|do not\b|don't\b|always\b|never\b|must\b|should\b|your output\b|"
    r"call\s+delegate|step\s*\d",
    re.I,
)


# --------------------------------------------------------------------------
# term handling
# --------------------------------------------------------------------------



def normalize_term(text: str) -> str:
    """Fold spelling variants so 'Error rates' and 'error_rate' are one term."""
    cleaned = re.sub(r"[^\w\s/-]", " ", str(text or "").lower())
    words = [w for w in re.split(r"[\s_/-]+", cleaned) if w]
    if not words:
        return ""
    # Singularise only the final word: "error rates" -> "error_rate".
    last = words[-1]
    if len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
        words[-1] = last[:-1]
    return "_".join(words)


def min_pairwise_similarity(definitions: Sequence[str], scorer) -> float:
    """Lowest agreement between any two definitions. 1.0 when fewer than two."""
    unique = [d for d in {d.strip() for d in definitions if str(d).strip()}]
    if len(unique) < 2:
        return 1.0
    return min(scorer(a, b) for a, b in itertools.combinations(unique, 2))


def string_similarity(a: str, b: str) -> float:
    """String similarity: ``difflib``'s ratio over the lower-cased alphanumeric tokens.

    Same as the ACCORD kernel's own when it has no embedding model.
    """
    na = " ".join(_TOKEN.findall((a or "").lower()))
    nb = " ".join(_TOKEN.findall((b or "").lower()))
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return float(SequenceMatcher(None, na, nb).ratio())


BUILTIN_BACKEND = "string similarity (built in)"


def load_scorer(threshold: float | None) -> tuple[Any, float, str]:
    """The similarity to score definitions with, its threshold and a label for the backend."""
    return string_similarity, SIM_MERGE if threshold is None else threshold, BUILTIN_BACKEND


def extract_declarations(prompt: str) -> dict[str, str]:
    """Terms an agent declares for itself, parsed from its system prompt."""
    found: dict[str, str] = {}
    for raw in prompt.splitlines():
        line = raw.strip()
        if not line.startswith(("-", "*")) or _NOT_A_DEFINITION.search(line):
            continue
        for pattern in (_QUOTED_MEANS, _COLON_DEF, _COPULA_DEF):
            match = pattern.match(line)
            if not match:
                continue
            term = normalize_term(match.group(1))
            definition = match.group(2).strip().rstrip(".")
            # A definition longer than a clause is prose, not a term meaning.
            if term and definition and len(definition) <= 120:
                found.setdefault(term, definition)
            break
    return found


def extract_with_vocabulary(prompt: str, vocabulary: Sequence[str]) -> dict[str, str]:
    """Second pass for declarations that omit the verb.

    An agent writing "Time windows exclusive." is defining the same term as one
    writing "Time windows are inclusive.", but only the second parses as a
    sentence. Once the first pass has established that some agent calls this a
    term, the bare form can be read against that vocabulary instead of being
    dropped for lacking a copula.

    Only terse remainders are accepted. Ellipsis of the verb is what makes these
    lines short in the first place, whereas a long clause that failed to parse
    as a definition is usually an instruction that merely opens with a known
    word - "Errors: count by status code (HTTP 500, 503, 504)" describes a task,
    not what the agent means by "error".
    """
    found: dict[str, str] = {}
    for raw in prompt.splitlines():
        line = raw.strip()
        if not line.startswith(("-", "*")) or _NOT_A_DEFINITION.search(line):
            continue
        body = line.lstrip("-* ").strip().rstrip(".")
        words = body.split()
        # Longest leading phrase that names a known term wins, so "time window"
        # is preferred over "time".
        for span in range(min(4, len(words)), 0, -1):
            head = normalize_term(" ".join(words[:span]))
            if head and head in vocabulary:
                definition = " ".join(words[span:]).strip(" :-")
                if definition and len(definition.split()) <= _ELLIPTICAL_WORDS:
                    found.setdefault(head, definition)
                break
    return found


_GENERIC_TOKENS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "for", "this", "that", "if",
        "is", "are", "be", "may", "can", "it", "its", "in", "on", "to",
        "whether", "check", "specific", "last", "most", "public", "accepted",
        "label", "labels", "scale", "scales", "type", "types", "value",
        "values", "unit", "units", "level", "levels",
    }
)


def _content_tokens(term: str) -> set[str]:
    return {t for t in term.split("_") if len(t) > 3 and t not in _GENERIC_TOKENS}


def unify_terms(terms: Sequence[str]) -> dict[str, str]:
    """Map near-synonymous declared terms onto one canonical name.

    Agents name the same concept differently - severity_scale, severity_label,
    public_severity, severity_accepted - and comparing those as four separate
    terms would report no disagreement anywhere, because each is declared by
    only one agent.

    Terms are unified only when they carry the *same* content once qualifiers
    like "label", "scale" or "public" are stripped. Merging on any shared token
    instead is too generous: it folds "migration_state" into "state", then
    reports a disagreement between an incident state machine and a schema
    migration field that no one ever confused.
    """
    groups: dict[frozenset[str], list[str]] = {}
    for term in terms:
        key = frozenset(_content_tokens(term)) or frozenset(term.split("_"))
        groups.setdefault(key, []).append(term)
    canonical: dict[str, str] = {}
    for members in groups.values():
        name = min(members, key=lambda t: (len(t), t))
        for member in members:
            canonical[member] = name
    return canonical


# --------------------------------------------------------------------------
# trace reading
# --------------------------------------------------------------------------


@dataclass
class TermFinding:
    term: str
    locked: bool
    definitions: int
    min_similarity: float
    contested: bool
    referenced_after_lock: int = 0
    declaring_agents: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.locked and not self.contested:
            return "over_selected"
        if not self.locked and self.contested:
            return "under_selected"
        return "justified" if self.locked else "correctly_omitted"


@dataclass
class RunUtilization:
    run_id: str
    locked_terms: list[TermFinding] = field(default_factory=list)
    missed_terms: list[TermFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    has_contract: bool = False
    vocabulary_found: int = 0

    @property
    def selected(self) -> bool:
        """Whether the run chose any terms, which is what can be scored.

        A run that locked nothing, or locked an empty glossary, made no
        selection to judge. Scoring it as though it selected badly would
        conflate a protocol that failed to finish with one that chose wrongly.
        """
        return bool(self.locked_terms)

    @property
    def justified(self) -> int:
        return sum(1 for t in self.locked_terms if t.contested)

    @property
    def over_selected(self) -> int:
        return sum(1 for t in self.locked_terms if not t.contested)

    @property
    def under_selected(self) -> int:
        return len(self.missed_terms)

    @property
    def used(self) -> int:
        return sum(1 for t in self.locked_terms if t.referenced_after_lock)

    @property
    def precision(self) -> float:
        return self.justified / len(self.locked_terms) if self.locked_terms else 0.0

    @property
    def recall(self) -> float:
        total = self.justified + self.under_selected
        return self.justified / total if total else 0.0

    @property
    def utilization(self) -> float:
        return self.used / len(self.locked_terms) if self.locked_terms else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "selected": self.selected,
            "locked": len(self.locked_terms),
            "justified": self.justified,
            "over_selected": self.over_selected,
            "under_selected": self.under_selected,
            "used_after_lock": self.used,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "utilization": round(self.utilization, 4),
            "terms": [
                {
                    "term": t.term,
                    "locked": t.locked,
                    "definitions": t.definitions,
                    "min_similarity": round(t.min_similarity, 4),
                    "contested": t.contested,
                    "references_after_lock": t.referenced_after_lock,
                    "declaring_agents": t.declaring_agents,
                    "verdict": t.verdict,
                }
                for t in sorted(
                    self.locked_terms + self.missed_terms, key=lambda x: x.term
                )
            ],
            "notes": self.notes,
        }


def _mentions(
    term: str,
    segments: Sequence[tuple[float, str, str]],
    after: float,
    *,
    skip_relays: bool = True,
) -> int:
    """Agent messages produced after the lock that mention the term.

    Segments carrying the contract are skipped. Relaying the glossary is the
    protocol restating itself, so crediting it would let one paste mark every
    locked term as used.
    """
    words = [w for w in term.split("_") if w]
    if not words:
        return 0
    pattern = re.compile(r"\b" + r"[\s_-]*".join(re.escape(w) for w in words) + r"s?\b", re.I)
    return sum(
        1
        for when, _, text in segments
        if when > after
        and not (skip_relays and _GLOSSARY_RELAY.search(text))
        and pattern.search(text)
    )

