"""Reconstruct a CONCORD session from a MCE session's tool spans.
"""


from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import unescape

from .spans import ordered, tool_arguments, tool_name, tool_output

INITIATOR_SCRIPTS = ("concordv2.py", "concord_initiator.py")
STEPS = ("init", "anchors", "seeds", "scores", "repairs", "completed")
# `Phase: X` line printed by each accepted initiator call, and the cost bucket it opens.
PHASE_OF = {"Anchor": "anchors", "Seed": "seeds", "Score": "scores", "Repair": "repair", "Complete": "after_commit"}

_PHASE = re.compile(r"^Phase: (\w+)(?:\W+(SUCCESS|BEST_EFFORT))?", re.M)
_POOL = re.compile(r"""<candidate\s+id\s*=\s*["']([^"']+)["']""")
_CANDIDATE = re.compile(r'<candidate id="([^"]+)">(.*?)</candidate>', re.S)
_SCORE = re.compile(r"""<score\s+candidate_id\s*=\s*["']([^"']+)["']\s*>(.*?)</score>""", re.S)
_SCORED_ID = re.compile(r"""candidate_id\s*=\s*["']([^"']+)["']""")


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(text or "", {"&quot;": '"', "&apos;": "'"})).strip()


def tag(text: str | None, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", text or "", re.S)
    return clean(m.group(1)) if m else ""


def phase_of(stdout: str) -> tuple[str | None, str | None]:
    m = _PHASE.search(stdout or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def option(args: list[str], *names: str) -> str | None:
    for name in names:
        if name in args and args.index(name) + 1 < len(args):
            return args[args.index(name) + 1]
    return None


@dataclass
class Call:
    """One accepted-or-not call of the initiator script."""

    args: list[str]
    stdin: str
    exit: int | None
    stdout: str
    start: float
    end: float

    def reply_text(self, k: int) -> str:
        """The reply recorded by a ``record-reply`` at index ``k``: the argument after the agent, or the piped stdin."""
        if len(self.args) > k + 2 and self.args[k + 2] != "--overwrite":
            return self.args[k + 2]
        return self.stdin


@dataclass
class Candidate:
    proposer: str
    text: str
    rationale: str


@dataclass
class Score:
    raw: float
    reasoning: str


@dataclass
class Session:
    agents: list[str]
    budget: str | None
    tau: float | None  # --satisfaction-threshold
    schema: str  # --schema, for the judge to read option numbers
    phase: str | None = None
    outcome: str | None = None  # SUCCESS (the commit) or BEST_EFFORT
    winner: str | None = None
    candidates: dict[str, Candidate] = field(default_factory=dict)
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)  # agent -> (raw_reject, raw_ideal)
    scores: dict[tuple[str, str], Score] = field(default_factory=dict)  # (agent, candidate id) -> last score
    anchored: set[str] = field(default_factory=set)
    seeded: set[str] = field(default_factory=set)
    scored: dict[str, set[str]] = field(default_factory=dict)  # agent -> candidate ids it scored
    missing: set[tuple[str, str]] = field(default_factory=set)  # (agent, candidate id) marked MISSING
    pool: list[str] = field(default_factory=list)  # candidate ids of the last SCORE message
    repair_answered: int = 0
    repair_causes: list[str] = field(default_factory=list)  # per repair round: 'scores' or 'mark-missing'
    marks: list[tuple[float, str]] = field(default_factory=list)  # (time, cost bucket) from `init` on

    @property
    def reached_end(self) -> bool:
        return self.phase == "Complete"

    @property
    def repair_requested(self) -> int:
        return len(self.repair_causes)

    @property
    def signature(self) -> tuple:
        """Identifies what a judge would be shown, for caching."""
        return (self.winner, self.tau, tuple(sorted((k, v.raw, v.reasoning) for k, v in self.scores.items())))

    # ---- scores ---------------------------------------------------------

    def norm_score(self, agent: str, raw: float) -> float:
        """The raw score on the agent's own anchor scale, 0 (reject) to 1 (ideal)."""
        reject, ideal = self.anchors.get(agent, (0, 100))
        return max(0.0, min(1.0, (raw - reject) / (ideal - reject))) if ideal != reject else 0.5

    # ---- compliance -----------------------------------------------------

    @property
    def used_cells(self) -> tuple[int, int]:
        """(scored cells, expected cells) of the score matrix: every agent on every candidate of the pool, none MISSING."""
        scored = sum(
            1
            for agent in self.agents
            for cid in self.pool
            if cid in self.scored.get(agent, set()) and (agent, cid) not in self.missing
        )
        return scored, len(self.pool) * len(self.agents)

    @property
    def used_all_candidates(self) -> bool:
        scored, expected = self.used_cells
        return bool(self.pool) and bool(self.agents) and scored == expected

    @property
    def steps(self) -> dict[str, bool]:
        agents = set(self.agents)
        return {
            "init": True,
            "anchors": bool(agents) and agents <= self.anchored,
            "seeds": bool(agents) and agents <= self.seeded,
            "scores": self.used_all_candidates,
            "repairs": self.repair_requested == self.repair_answered and self.phase != "Repair",
            "completed": self.reached_end,
        }


def initiator_calls(tool_spans: list[dict[str, Any]] | None) -> list[Call]:
    """The initiator script's calls, in execution order."""
    calls = []
    for span in ordered(tool_spans):
        if tool_name(span) != "run_skill_script":
            continue
        args = tool_arguments(span)
        if not isinstance(args, dict) or args.get("script") not in INITIATOR_SCRIPTS:
            continue
        out = tool_output(span)
        out = out if isinstance(out, dict) else {}
        argv = args.get("args")
        start = float(span.get("startTime") or 0.0)
        calls.append(
            Call(
                args=[str(a) for a in argv] if isinstance(argv, list) else [],
                stdin=str(args.get("stdin") or ""),
                exit=out.get("exit_code"),
                stdout=str(out.get("stdout") or ""),
                start=start,
                end=float(span.get("endTime") or start),
            )
        )
    return calls


def _new(c: Call) -> Session:
    a = c.args
    agents = option(a, "--agents")
    tau = option(a, "--satisfaction-threshold")
    try:
        tau_value = float(tau) if tau is not None else None
    except ValueError:
        tau_value = None
    return Session(
        agents=[x.strip() for x in (agents or "").split(",") if x.strip()],
        budget=option(a, "-B", "--B", "--budget"),
        tau=tau_value,
        schema=clean(option(a, "--schema"))[:700],
    )


def parse(calls: list[Call]) -> Session | None:
    """The last CONCORD session of the calls, or None when ``init`` never succeeded."""
    s: Session | None = None
    for c in calls:
        a = c.args
        if a[:1] == ["init"] and c.exit == 0:
            s, before = _new(c), None
            s.marks = [(c.start, "anchors")]  # a new session: the previous one is forgotten
            s.pool = _POOL.findall(c.stdout)
        elif s is None or c.exit != 0:
            continue
        else:
            before = s.phase
        after, outcome = phase_of(c.stdout)

        if "record-reply" in a:
            k = a.index("record-reply")
            agent = a[k + 1] if len(a) > k + 1 else ""
            text = c.reply_text(k)
            if before == "Anchor":
                s.anchored.add(agent)
                reject, ideal = re.search(r"<raw_reject>\s*([-\d.]+)", text), re.search(r"<raw_ideal>\s*([-\d.]+)", text)
                if reject and ideal:
                    s.anchors[agent] = (float(reject.group(1)), float(ideal.group(1)))
            elif before == "Seed":
                s.seeded.add(agent)
            elif before == "Repair":
                s.repair_answered += 1
            elif before == "Score":
                s.scored.setdefault(agent, set()).update(_SCORED_ID.findall(text))
                for cid, body in _SCORE.findall(text):
                    raw = re.search(r"<raw>\s*([-\d.]+)", body)
                    if raw:
                        s.scores[(agent, cid)] = Score(float(raw.group(1)), tag(body, "reasoning") or tag(body, "rationale"))
        elif "mark-missing" in a:
            k = a.index("mark-missing")
            if len(a) > k + 2:
                s.missing.add((a[k + 1], a[k + 2]))

        for cid, body in _CANDIDATE.findall(c.stdout):
            s.candidates.setdefault(cid, Candidate(cid.split("-", 1)[-1], tag(body, "text"), tag(body, "rationale")))

        if after:
            if after == "Repair" and before != "Repair" and "status" not in a:
                s.repair_causes.append("mark-missing" if "mark-missing" in a else "scores")
            if after == "Score":
                s.pool = _POOL.findall(c.stdout) or s.pool
            if after == "Complete":
                s.outcome, s.winner = outcome, tag(c.stdout, "winning_candidate_id")
            if after in PHASE_OF:
                s.marks.append((c.end, PHASE_OF[after]))
            s.phase = after
    return s


_CACHE: dict[tuple, Session | None] = {}


def session_for(context: dict[str, Any]) -> Session | None:
    """The CONCORD session of a MCE session context, parsed once however many metrics ask for it."""
    spans = context.get("tool_calls") or context.get("tool_spans") or []
    key = (
        str(context.get("session_id") or ""),
        tuple((str(s.get("id") or ""), s.get("startTime"), len(str(s.get("toolOutput") or ""))) for s in spans),
    )
    if key not in _CACHE:
        if len(_CACHE) >= 16:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = parse(initiator_calls(spans))
    return _CACHE[key]
