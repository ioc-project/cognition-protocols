"""Deterministic satisfaction metrics: what the protocol itself recorded about each agent's score.

Ported from ``compute_worst_off.py`` and the self-preference gap of
``compute_satisfaction_fairness.py``. No LLM is used.

An agent's satisfaction is its normalised score (0-1) on the winning candidate,
on the anchor scale it set itself. The run's value is the LOWEST of them, the
satisfaction of the worst-off agent (the quantity leximin maximises).

A run is an OUTLIER when its worst-off agent has a degenerate anchor range
(|raw_ideal - raw_reject| under MIN_SPAN raw points): normalising by such a
tiny range turns an ordinary score into an extreme value such as 0.00, which
says nothing about satisfaction. The script lists it and leaves it out of its
statistics; here it is skipped, which leaves it out of every aggregate the
same way (the reason names the agent and its range).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .base import ConcordMetric

MIN_SPAN = 10


@dataclass
class WorstOff:
    agents: dict[str, float]  # normalised score on the winner, per agent that scored it
    worst_agent: str
    span: float | None  # the worst agent's anchor range, in raw points
    missing: list[str]  # agents with no score on the winner

    @property
    def worst(self) -> float:
        return self.agents[self.worst_agent]

    @property
    def mean(self) -> float:
        return round(sum(self.agents.values()) / len(self.agents), 2)

    @property
    def outlier(self) -> bool:
        return self.span is not None and self.span < MIN_SPAN


def worst_off(s) -> WorstOff | None:
    sat = {
        agent: round(s.norm_score(agent, s.scores[(agent, s.winner)].raw), 2)
        for agent in s.agents
        if (agent, s.winner) in s.scores
    }
    if not sat:
        return None
    worst = min(sat, key=sat.get)
    span = abs(s.anchors[worst][1] - s.anchors[worst][0]) if worst in s.anchors else None
    return WorstOff(sat, worst, span, [a for a in s.agents if a not in sat])


class _WorstOffMetric(ConcordMetric):
    def facet(self, session_id, s, context):
        w = worst_off(s)
        if w is None:
            return self._skip(session_id, "no agent score on the winning candidate was found")
        if w.outlier:
            return self._skip(
                session_id,
                f"OUTLIER: the anchor range of {w.worst_agent} is only {w.span:g} raw points, so its value ({w.worst}) is not meaningful",
            )
        return self.value_of(session_id, s, w)

    def value_of(self, session_id, s, w: WorstOff):  # pragma: no cover
        raise NotImplementedError


class ConcordWorstOffSatisfaction(_WorstOffMetric):
    NAME = "ConcordWorstOffSatisfaction"
    DESCRIPTION = (
        "Satisfaction of the worst-off agent: the lowest normalised score (0-1) any agent gave the winning candidate. "
        "Skipped for an outlier run (degenerate anchor range). Reasoning carries every agent's value."
    )

    def value_of(self, session_id, s, w):
        detail = {"outcome": s.outcome, "tau": s.tau, "worst_agent": w.worst_agent, "agents": w.agents, "no_score_on_winner": w.missing}
        return self._result(session_id, w.worst, json.dumps(detail))


class ConcordMeanSatisfaction(_WorstOffMetric):
    NAME = "ConcordMeanSatisfaction"
    DESCRIPTION = "Mean over agents of the normalised score (0-1) on the winning candidate. Skipped for an outlier run."

    def value_of(self, session_id, s, w):
        return self._result(session_id, w.mean, json.dumps(w.agents))


class ConcordWorstOffAboveTau(_WorstOffMetric):
    NAME = "ConcordWorstOffAboveTau"
    DESCRIPTION = "1 if the worst-off agent is at or above the run's own satisfaction threshold tau, else 0. Skipped when tau is unknown."

    def value_of(self, session_id, s, w):
        if s.tau is None:
            return self._skip(session_id, "the run recorded no satisfaction threshold")
        return self._result(session_id, w.worst >= s.tau, f"worst-off {w.worst_agent} {w.worst} vs tau {s.tau} (margin {w.worst - s.tau:+.2f})")


class ConcordSelfPreferenceGap(ConcordMetric):
    NAME = "ConcordSelfPreferenceGap"
    DESCRIPTION = (
        "Mean over agents of (mean normalised score for the agent's own candidates minus mean for the others'). "
        "A large positive gap suggests bias. No LLM."
    )

    def facet(self, session_id, s, context):
        gaps = {}
        for agent in s.agents:
            own, other = [], []
            for (a, cid), score in s.scores.items():
                if a != agent or cid not in s.candidates:
                    continue
                (own if s.candidates[cid].proposer == agent else other).append(s.norm_score(agent, score.raw))
            if own and other:
                gaps[agent] = round(sum(own) / len(own) - sum(other) / len(other), 2)
        if not gaps:
            return self._skip(session_id, "no agent scored both its own candidate and another's")
        return self._result(session_id, sum(gaps.values()) / len(gaps), json.dumps(gaps))
