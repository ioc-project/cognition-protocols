"""Outcome Satisfaction and Score Fairness, with an LLM as a judge.

Ported from ``compute_satisfaction_fairness.py``: same gate, same extraction, same
prompt, same mechanical checks. The transport differs: the script calls litellm
itself; this goes through ``Metric.call_llm`` so MCE records the judge's tokens
and cost on the result.

Outcome Satisfaction  at the end each agent must be satisfied with the outcome.
Score Fairness        each agent must score the OTHER agents' proposals fairly,
                      without imposing its own view on them.

The LLM never reads the whole trace. It answers one tiny question per item, and
only says which way an agent's reasoning points (FOR, AGAINST or MIXED) WITHOUT
seeing the score. The score is then compared with that direction in code:

  satisfaction, per agent that scored the winner, tau = the run's threshold
    NOT_SATISFIED  normalised score below tau (no LLM call needed)
    COMPROMISE     at or above tau BUT the reasoning argues AGAINST the winner
    SATISFIED      at or above tau and the reasoning does not argue against it
    UNRELIABLE     no reasoning, or the judge gave no valid answer
    the run: the worst agent decides (NOT_SATISFIED, COMPROMISE, UNRELIABLE, SATISFIED)

  fairness, per score an agent gave to ANOTHER agent's candidate
    similar candidates (same action) scored the same: FAIR, not even judged
    reasoning FOR but a LOW score, or AGAINST but a HIGH score: UNFAIR
    anything else, MIXED included: FAIR
    the run: any UNFAIR score makes it UNFAIR

Roles. The script put each agent's system-prompt preview next to its name; the
KG session has no such part, so the judge is given the agent's name.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from mce.core.metadata import MetricNature

from . import session as S
from .base import ConcordMetric

MAX_RETRIES = 2
# Score fairness: the normalised score (0-1) is HIGH at or above HIGH_SCORE and LOW at or below LOW_SCORE.
# The cut-offs are strict on purpose: only an evident contradiction (e.g. a rejection scored 0.85) is flagged.
HIGH_SCORE, LOW_SCORE = 0.80, 0.30
# Two candidates are similar when the action part of their text (before any `| RATIONALE`) matches at least
# SIMILAR, and scored the same when their normalised scores differ by at most SAME_SCORE.
SIMILAR, SAME_SCORE = 0.90, 0.05

JUDGE_SYSTEM = (
    "You are a careful evaluator of multi-agent negotiations. You judge only what the text says, never style. "
    "Answer in exactly the format requested, with no other text."
)

DIRECTION_PROMPT = """An agent in a multi-agent negotiation wrote an assessment of each candidate below. For EACH item, read ONLY the reasoning and say which way it points. You are not shown any score; do not guess one.

FOR: overall the reasoning supports adopting the candidate (any concerns are minor).
AGAINST: the reasoning rejects the candidate or concludes it should not be adopted. Naming a concern, a risk or an open question is NOT enough for AGAINST.
MIXED: the reasoning names real strengths and also a real concern or unresolved risk, without rejecting the candidate or clearly recommending it.

CANDIDATE FORMAT / OPTIONS (from the protocol set-up, to read candidates such as option numbers): {schema}
AGENT: {role}

ITEMS:
{items}

Answer with exactly one line per item, in order, and nothing else:
<item number> | <FOR or AGAINST or MIXED>
Example:
1 | FOR
2 | AGAINST"""


# --------------------------------------------------------------------------- what to judge


def action_key(text: str) -> str:
    head = re.split(r"\|\s*rationale", text or "", flags=re.I)[0]
    return re.sub(r"[^a-z0-9+]+", " ", head.lower()).strip()


def similar(a: str, b: str) -> bool:
    ka, kb = action_key(a), action_key(b)
    return ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= SIMILAR


def fairness_items(s: S.Session) -> dict[str, list[dict[str, Any]]]:
    """Per agent: the scores it gave to other agents' candidates, with the similar one it scored the same, if any."""
    out = {}
    for agent in s.agents:
        items = []
        for (a, cid), score in s.scores.items():
            if a != agent or cid not in s.candidates or s.candidates[cid].proposer == agent or not score.reasoning:
                continue
            items.append(dict(cid=cid, text=s.candidates[cid].text, raw=score.raw, norm=round(s.norm_score(agent, score.raw), 2), reasoning=score.reasoning))
        scored = {cid: s.norm_score(agent, sc.raw) for (a, cid), sc in s.scores.items() if a == agent and cid in s.candidates}
        for it in items:  # a similar proposal (any the agent scored, its own included) that got the same score
            it["same_as"] = next(
                (c for c, n in scored.items() if c != it["cid"] and similar(s.candidates[c].text, it["text"]) and abs(n - it["norm"]) <= SAME_SCORE),
                None,
            )
        out[agent] = items
    return out


def satisfaction_items(s: S.Session) -> list[dict[str, Any]]:
    """Per agent that scored the winner: its normalised score and reasoning (the last score it gave)."""
    return [
        dict(agent=a, raw=sc.raw, norm=round(s.norm_score(a, sc.raw), 2), reasoning=sc.reasoning)
        for a in s.agents
        if (sc := s.scores.get((a, s.winner)))
    ]


def score_level(norm: float) -> str:
    return "HIGH" if norm >= HIGH_SCORE else ("LOW" if norm <= LOW_SCORE else "MIDDLE")


def contradicts(direction: str | None, level: str) -> bool:
    """The score points the other way from the reasoning: FOR with a LOW score, or AGAINST with a HIGH score."""
    return (direction == "FOR" and level == "LOW") or (direction == "AGAINST" and level == "HIGH")


def parse_direction(text: str, n: int) -> list[str] | None:
    """One ``<i> | FOR|AGAINST|MIXED`` line per item; None unless items 1..n each appear exactly once."""
    got = {}
    for line in text.splitlines():
        m = re.match(r"^\W*(\d+)\W*\|\s*(FOR|AGAINST|MIXED)\b.*$", line.strip(), re.I)
        if m:
            got[int(m.group(1))] = m.group(2).upper()
    return [got[k] for k in range(1, n + 1)] if sorted(got) == list(range(1, n + 1)) else None


# --------------------------------------------------------------------------- the verdicts


class _Judged(ConcordMetric):
    """Both metrics of a family read one set of judge answers, computed once per session."""

    NATURE = MetricNature.STOCHASTIC
    _CACHE: dict[tuple, Any] = {}

    def judge(self, prompt: str, n: int) -> tuple[list[str] | None, str]:
        """Ask about ``n`` items, validate mechanically, retry: (directions, why it failed)."""
        why = ""
        for _ in range(MAX_RETRIES + 1):
            try:
                text = self.call_llm(prompt, system_prompt=JUDGE_SYSTEM, temperature=0.0)
            except Exception as exc:  # a failed call is retried, then reported as UNRELIABLE, never counted as a pass or a fail
                why = f"{type(exc).__name__}: {str(exc)[:200]}"
                continue
            got = parse_direction(text, n)
            if got is not None:
                return got, ""
            why = "answer rejected by the checks: " + text[:300].replace("\n", " / ")
        return None, why

    def verdicts(self, s: S.Session, kind: str) -> Any:
        """``kind`` is "satisfaction" or "fairness": each is judged only when a metric of its family asks."""
        key = (kind, s.signature)
        cache = _Judged._CACHE
        if key not in cache:
            if len(cache) >= 16:
                cache.pop(next(iter(cache)))
            cache[key] = self.judge_satisfaction(s) if kind == "satisfaction" else self.judge_fairness(s)
        return cache[key]

    def judge_satisfaction(self, s: S.Session) -> list[dict[str, Any]]:
        items = satisfaction_items(s)
        todo = [it for it in items if s.tau is not None and it["norm"] >= s.tau and it["reasoning"]]  # below tau: no question needed
        directions: dict[str, str | None] = {}
        why = ""
        if todo:
            win = s.candidates[s.winner].text
            text = "\n".join(f"{n}. candidate: {win}\n   reasoning: {it['reasoning']}" for n, it in enumerate(todo, 1))
            got, why = self.judge(DIRECTION_PROMPT.format(schema=s.schema, role="several agents", items=text), len(todo))
            directions = {it["agent"]: (got[k] if got else None) for k, it in enumerate(todo)}
        out = []
        for it in items:
            d = directions.get(it["agent"])
            if s.tau is None:
                label = "UNRELIABLE"
            elif it["norm"] < s.tau:
                label = "NOT_SATISFIED"
            elif not it["reasoning"] or d is None:
                label = "UNRELIABLE"
            else:
                label = "COMPROMISE" if d == "AGAINST" else "SATISFIED"
            out.append(dict(agent=it["agent"], raw=it["raw"], norm=it["norm"], tau=s.tau, direction=d, label=label, why=why if label == "UNRELIABLE" else ""))
        return out

    def judge_fairness(self, s: S.Session) -> dict[str, Any]:
        out = {}
        for agent, items in fairness_items(s).items():
            todo = [it for it in items if not it["same_as"]]
            directions: dict[str, str | None] = {}
            why = ""
            if todo:
                text = "\n".join(f"{n}. candidate: {it['text']}\n   reasoning: {it['reasoning']}" for n, it in enumerate(todo, 1))
                got, why = self.judge(DIRECTION_PROMPT.format(schema=s.schema, role=agent, items=text), len(todo))
                directions = {it["cid"]: (got[k] if got else None) for k, it in enumerate(todo)}
            res = []
            for it in items:
                level = score_level(it["norm"])
                if it["same_as"]:
                    res.append(dict(cid=it["cid"], norm=it["norm"], direction=None, level=level, label="FAIR", same_as=it["same_as"]))
                    continue
                d = directions.get(it["cid"])
                label = "UNFAIR" if contradicts(d, level) else ("FAIR" if d else "UNRELIABLE")
                res.append(dict(cid=it["cid"], norm=it["norm"], direction=d, level=level, label=label))
            out[agent] = dict(
                checked=len(items),
                unfair=sum(1 for x in res if x["label"] == "UNFAIR"),
                unreliable=sum(1 for x in res if x["label"] == "UNRELIABLE"),
                items=res,
                why=why,
            )
        return out


def satisfaction_verdict(sat: list[dict[str, Any]]) -> str:
    labels = {x["label"] for x in sat}
    if not sat:
        return "NO_DATA"
    for verdict in ("NOT_SATISFIED", "COMPROMISE", "UNRELIABLE"):
        if verdict in labels:
            return verdict
    return "SATISFIED"


def fairness_verdict(fair: dict[str, Any]) -> str:
    if not any(a["checked"] for a in fair.values()):
        return "NO_DATA"
    if any(a["unfair"] for a in fair.values()):
        return "UNFAIR"
    return "UNRELIABLE" if any(a["unreliable"] for a in fair.values()) else "FAIR"


class ConcordOutcomeSatisfied(_Judged):
    NAME = "ConcordOutcomeSatisfied"
    DESCRIPTION = (
        "1 if every agent is satisfied with the outcome, 0 if one is not (score below tau) or is in COMPROMISE "
        "(score at or above tau but its reasoning argues against the winner). Skipped when unreliable. LLM-judged."
    )

    def facet(self, session_id, s, context):
        sat = self.verdicts(s, "satisfaction")
        verdict = satisfaction_verdict(sat)
        if verdict in ("NO_DATA", "UNRELIABLE"):
            why = next((x["why"] for x in sat if x["why"]), "no agent scored the winning candidate")
            return self._skip(session_id, f"{verdict}: {why}")
        return self._result(session_id, verdict == "SATISFIED", f"{verdict}; " + json.dumps(sat))


class ConcordSatisfiedAgents(_Judged):
    NAME = "ConcordSatisfiedAgents"
    DESCRIPTION = "Share of the agents that are SATISFIED with the outcome, among those with a reliable label. LLM-judged."

    def facet(self, session_id, s, context):
        sat = [x for x in self.verdicts(s, "satisfaction") if x["label"] != "UNRELIABLE"]
        if not sat:
            return self._skip(session_id, "no agent has a reliable satisfaction label")
        return self._result(session_id, sum(x["label"] == "SATISFIED" for x in sat) / len(sat), json.dumps(sat))


class ConcordScoreFair(_Judged):
    NAME = "ConcordScoreFair"
    DESCRIPTION = (
        "1 if no agent scored another agent's proposal against its own reasoning (FOR with a low score, AGAINST with "
        "a high one), 0 if one did. Skipped when unreliable. LLM-judged."
    )

    def facet(self, session_id, s, context):
        fair = self.verdicts(s, "fairness")
        verdict = fairness_verdict(fair)
        if verdict in ("NO_DATA", "UNRELIABLE"):
            why = next((a["why"] for a in fair.values() if a["why"]), "no agent scored another agent's candidate with reasoning")
            return self._skip(session_id, f"{verdict}: {why}")
        return self._result(session_id, verdict == "FAIR", f"{verdict}; " + json.dumps(fair))


class ConcordUnfairScoreShare(_Judged):
    NAME = "ConcordUnfairScoreShare"
    DESCRIPTION = "Share of the scores given to other agents' proposals that contradict the scorer's own reasoning, among the judged ones. LLM-judged."

    def facet(self, session_id, s, context):
        fair = self.verdicts(s, "fairness")
        checked = sum(a["checked"] - a["unreliable"] for a in fair.values())
        if not checked:
            return self._skip(session_id, "no score of another agent's proposal could be judged")
        unfair = sum(a["unfair"] for a in fair.values())
        return self._result(session_id, unfair / checked, f"{unfair} unfair of {checked} judged; " + json.dumps({k: v["unfair"] for k, v in fair.items()}))
