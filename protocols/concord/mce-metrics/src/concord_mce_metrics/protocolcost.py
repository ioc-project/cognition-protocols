# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""What the CONCORD protocol costs in tokens, and where it goes.

Ported from ``compute_protocol_cost.py``. There is no run without CONCORD to
compare with, so the cost is shown in absolute terms, split by the phase active
when each LLM call ended:

  before_concord   before the first successful ``init`` (the SRE's own first analysis)
  anchors          from ``init`` (anchors, then the seeds, then the scoring of the seeds)
  seeds
  scores           the first scoring round
  repair           every repair round: the repair (new candidates) and the re-scoring after it
  after_commit     once the tool printed ``Phase: Complete`` (action, final answer)

The phase comes from the ``Phase: X`` line each accepted initiator call prints.
The protocol share is the share of the run's tokens spent from ``init`` to the end
of the protocol (anchors + seeds + scores + repair). It is a baseline-free
reference, NOT an overhead: without CONCORD the agents might have behaved
differently.

Tokens are the provider's usage on the call: prompt (cache reads included) plus
completion. Dollars are not computed. The script priced calls from a model price
table and the cache-write count; the KG keeps no cache-write count, and MCE
carries no price table for the agents' model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import session as S
from .base import ConcordMetric
from .spans import ordered

PHASES = ("before_concord", "anchors", "seeds", "scores", "repair_rounds", "after_commit")
PROTOCOL = ("anchors", "seeds", "scores", "repair_rounds")


def _count(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Bucket:
    calls: int = 0
    prompt: int = 0
    cached: int = 0
    completion: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt + self.completion

    def add(self, prompt: int, cached: int, completion: int) -> None:
        self.calls += 1
        self.prompt += prompt
        self.cached += cached
        self.completion += completion

    def as_dict(self) -> dict[str, int]:
        return {"llm_calls": self.calls, "prompt_tokens": self.prompt, "cached_tokens": self.cached, "completion_tokens": self.completion, "total_tokens": self.tokens}


@dataclass
class Split:
    total: Bucket = field(default_factory=Bucket)
    phases: dict[str, Bucket] = field(default_factory=lambda: {p: Bucket() for p in PHASES})
    rounds: list[Bucket] = field(default_factory=list)  # one per repair round (repair + re-scoring until the next round or the end)

    @property
    def protocol_tokens(self) -> int:
        return sum(self.phases[p].tokens for p in PROTOCOL)


def split_calls(s: S.Session, llm_spans: list[dict[str, Any]]) -> Split:
    """Put every LLM call with token usage in the phase active when it ended."""
    out = Split()
    for span in ordered(llm_spans):
        prompt = _count(span.get("promptTokenCount"))
        if prompt is None:
            continue
        cached = _count(span.get("cacheReadTokenCount")) or 0
        completion = _count(span.get("completionTokenCount")) or 0
        ended = float(span.get("endTime") or span.get("startTime") or 0.0)

        phase, n_rounds, prev = "before_concord", 0, None
        for ts, ph in sorted(s.marks, key=lambda m: m[0]):
            if ts > ended:
                break
            if ph == "repair" and prev != "repair":
                n_rounds += 1
            phase, prev = ph, ph

        out.total.add(prompt, cached, completion)
        if phase in ("before_concord", "anchors", "seeds", "after_commit") or (phase == "scores" and n_rounds == 0):
            out.phases[phase].add(prompt, cached, completion)
        else:  # repair, or re-scoring after a repair
            out.phases["repair_rounds"].add(prompt, cached, completion)
            while len(out.rounds) < n_rounds:
                out.rounds.append(Bucket())
            out.rounds[n_rounds - 1].add(prompt, cached, completion)
    return out


_CACHE: dict[tuple, Split] = {}


def split_for(s: S.Session, context: dict[str, Any]) -> Split:
    spans = context.get("llm_calls") or context.get("llm_spans") or []
    key = (str(context.get("session_id") or ""), s.signature, tuple((str(x.get("id") or ""), x.get("promptTokenCount")) for x in spans))
    if key not in _CACHE:
        if len(_CACHE) >= 16:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = split_calls(s, spans)
    return _CACHE[key]


class _CostMetric(ConcordMetric):
    def facet(self, session_id, s, context):
        split = split_for(s, context)
        if not split.total.calls:
            return self._skip(session_id, "no LLM call with token usage in the session")
        return self.of(session_id, split)

    def of(self, session_id: str, split: Split):  # pragma: no cover
        raise NotImplementedError


class ConcordRunTokens(_CostMetric):
    NAME = "ConcordRunTokens"
    DESCRIPTION = "Tokens (prompt + completion) of every LLM call of the run, all agents. Reasoning splits it by phase."

    def of(self, session_id, split):
        detail = {"total": split.total.as_dict(), "phases": {p: b.as_dict() for p, b in split.phases.items()}, "repair_rounds": [b.as_dict() for b in split.rounds]}
        return self._result(session_id, split.total.tokens, json.dumps(detail))


class _PhaseTokens(_CostMetric):
    PHASE = ""

    def of(self, session_id, split):
        bucket = split.phases[self.PHASE]
        detail = bucket.as_dict()
        if self.PHASE == "repair_rounds":
            detail["per_round"] = [b.as_dict() for b in split.rounds]
        return self._result(session_id, bucket.tokens, json.dumps(detail))


class ConcordTokensBeforeConcord(_PhaseTokens):
    NAME = "ConcordTokensBeforeConcord"
    DESCRIPTION = "Tokens spent before the first successful init (the SRE's own first analysis)."
    PHASE = "before_concord"


class ConcordTokensAnchors(_PhaseTokens):
    NAME = "ConcordTokensAnchors"
    DESCRIPTION = "Tokens spent in the anchors phase (from init)."
    PHASE = "anchors"


class ConcordTokensSeeds(_PhaseTokens):
    NAME = "ConcordTokensSeeds"
    DESCRIPTION = "Tokens spent in the seeds phase (each agent proposes a candidate)."
    PHASE = "seeds"


class ConcordTokensScores(_PhaseTokens):
    NAME = "ConcordTokensScores"
    DESCRIPTION = "Tokens spent in the first scoring round."
    PHASE = "scores"


class ConcordTokensRepair(_PhaseTokens):
    NAME = "ConcordTokensRepair"
    DESCRIPTION = "Tokens spent in repair rounds: the repair (new candidates) and the re-scoring after it. Reasoning splits it by round."
    PHASE = "repair_rounds"


class ConcordTokensAfterCommit(_PhaseTokens):
    NAME = "ConcordTokensAfterCommit"
    DESCRIPTION = "Tokens spent once the tool printed `Phase: Complete` (action, final answer)."
    PHASE = "after_commit"


class ConcordProtocolTokens(_CostMetric):
    NAME = "ConcordProtocolTokens"
    DESCRIPTION = "Tokens spent from init to the end of the protocol (anchors + seeds + scores + repair rounds)."

    def of(self, session_id, split):
        return self._result(session_id, split.protocol_tokens, json.dumps({p: split.phases[p].tokens for p in PROTOCOL}))


class ConcordProtocolShare(_CostMetric):
    NAME = "ConcordProtocolShare"
    DESCRIPTION = (
        "Share (0-1) of the run's tokens spent from init to the end of the protocol. A reference, not an overhead: "
        "without CONCORD the agents might have behaved differently."
    )

    def of(self, session_id, split):
        if not split.total.tokens:
            return self._skip(session_id, "the run's LLM calls report no tokens")
        return self._result(session_id, split.protocol_tokens / split.total.tokens, f"{split.protocol_tokens} of {split.total.tokens} tokens")
