# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""Reconstruct a CONCORD session from a MCE session's tool spans.

The initiator script is driven through `init`, `handle` (the replies of the agents on stdin) and `mark-missing`. The
session is rebuilt by replaying those calls through the ioc-concord engine, the code the script itself runs, so the
pool, the ratings, the incumbent, the repair rounds and the outcome are the ones the run had.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import unescape

from ioc_concord.engine import add_anchor, add_candidate, add_ratings, advance, parse_reply_doc
from ioc_concord.errors import ConcordError
from ioc_concord.schema import parse_schema
from ioc_concord.state import new_run_state
from ioc_concord.wire import DEFAULT_PARAMS
from ioc_concord.xmlutil import AGENT_DOC_RE, escape_bare_chars

from .spans import ordered, tool_arguments, tool_name, tool_output

INITIATOR_SCRIPTS = ("concord_initiator.py",)
STEPS = ("init", "anchors", "seeds", "scores", "repairs", "completed")
# The cost bucket each phase opens.
PHASE_OF = {"Anchor": "anchors", "Seed": "seeds", "Score": "scores", "Repair": "repair", "Complete": "after_commit"}

_STATE = re.compile(r"concord-state-([0-9a-f]+)\.json")
_COMPLETE = re.compile(r"^RUN COMPLETE: outcome (SUCCESS|BEST_EFFORT)\.", re.M)
_WINNER = re.compile(r"^Winning candidate (\S+) ", re.M)
_FROM = re.compile(r"^--- from: *(\S+) *$", re.M)
FALLBACK_SCHEMA = {"type": "string", "description": "an option"}


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(text or "", {"&quot;": '"', "&apos;": "'"})).strip()


def option(args: list[str], *names: str) -> str | None:
    for name in names:
        if name in args and args.index(name) + 1 < len(args):
            return args[args.index(name) + 1]
    return None


@dataclass
class Call:
    """One call of the initiator script, accepted or not."""

    args: list[str]
    stdin: str
    exit: int | None
    stdout: str
    stderr: str
    start: float
    end: float

    @property
    def command(self) -> str:
        """`init`, `handle`, `mark-missing`, ... : the first argument that is not a global option (`--state <path>`)."""
        a, i = self.args, 0
        while i < len(a) and a[i] in ("--state", "--format"):
            i += 2
        return a[i] if i < len(a) else ""


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
    schema: str  # the candidate schema as the agents read it, for the judge to read option numbers
    phase: str | None = None  # Anchor, Seed, Score, Repair or Complete
    outcome: str | None = None  # SUCCESS (the commit) or BEST_EFFORT
    winner: str | None = None
    candidates: dict[str, Candidate] = field(default_factory=dict)
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)  # agent -> (raw_reject, raw_ideal)
    scores: dict[tuple[str, str], Score] = field(default_factory=dict)  # (agent, candidate id) -> score
    anchored: set[str] = field(default_factory=set)
    seeded: set[str] = field(default_factory=set)
    scored: dict[str, set[str]] = field(default_factory=dict)  # agent -> candidate ids it scored
    missing: set[tuple[str, str]] = field(default_factory=set)  # (agent, candidate id) marked MISSING
    pool: list[str] = field(default_factory=list)  # candidate ids of the run
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
                stderr=str(out.get("stderr") or ""),
                start=start,
                end=float(span.get("endTime") or start),
            )
        )
    return calls


# ---- replay -------------------------------------------------------------


def _text(value: str | None) -> str:
    """An `init` text argument. A `@file` was read by the script; its text is not in the trace, so the file is read when
    it is still there and the name is kept otherwise (only the schema matters to the metrics)."""
    value = value or ""
    if value.startswith("@"):
        try:
            return Path(value[1:]).read_text().rstrip("\n")
        except OSError:
            return value
    return value


def _number(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


class _Replay:
    """The engine state of a run, advanced call by call the way the initiator script advanced it."""

    def __init__(self, st: dict[str, Any], budget: str | None, tau: float | None) -> None:
        self.st = st
        self.budget = budget
        self.tau = tau
        self.deferred: list[tuple[str, list]] = []  # ratings that arrived before the candidate they rate
        self.repair_causes: list[str] = []
        self.marks: list[tuple[float, str]] = []
        self.phase = self._phase()

    def _phase(self) -> str:
        st = self.st
        if st["outcome"]:
            return "Complete"
        if st["pending"]:
            return "Repair"
        if any(a not in st["anchors"] for a in st["agents"]):
            return "Anchor"
        seeded = {c["proposer"] for c in st["pool"] if c["round"] == 0}
        if any(a not in seeded for a in st["agents"]):
            return "Seed"
        return "Score"

    def _rate(self, agent: str, ratings: list) -> None:
        known = {c["id"] for c in self.st["pool"]}
        now = [r for r in ratings if r[0] in known]
        if now:
            add_ratings(self.st, agent, now)
        later = [r for r in ratings if r[0] not in known]
        if later:
            self.deferred.append((agent, later))

    def _replay_deferred(self) -> None:
        todo, self.deferred = self.deferred, []
        for agent, ratings in todo:
            self._rate(agent, ratings)

    def _apply(self, agent: str, doc: tuple) -> None:
        st = self.st
        kind, payload = doc
        if kind == "anchor":
            if agent not in st["anchors"]:
                add_anchor(st, agent, *payload)
        elif kind == "candidate":
            text, rationale, cid = payload
            add_candidate(st, agent, text, rationale, cid)
        else:
            self._rate(agent, payload)

    def handle(self, call: Call) -> None:
        st = self.st
        blocks = _FROM.split(call.stdin)
        items = [(None, blocks[0])] + [(blocks[i], blocks[i + 1]) for i in range(1, len(blocks) - 1, 2)]
        for sender, body in items:
            for m in AGENT_DOC_RE.finditer(body):
                try:
                    root = ET.fromstring(escape_bare_chars(m.group(0)))
                except ET.ParseError:
                    continue
                agent = root.get("agent") or sender
                if agent not in st["agents"]:
                    continue
                if root.get("state") and root.get("state") != st.get("state_id"):
                    continue  # a reply of another run
                doc = parse_reply_doc(root)
                if doc is None:
                    continue
                try:
                    self._apply(agent, doc)
                except ConcordError:
                    continue
                self._replay_deferred()
        self._advance(call, cause="scores")

    def mark_missing(self, call: Call) -> None:
        st = self.st
        k = call.args.index("mark-missing")
        if len(call.args) <= k + 2:
            return
        agent, cid = call.args[k + 1], call.args[k + 2]
        if agent in st["agents"] and any(c["id"] == cid for c in st["pool"]) and cid not in st["raw"].get(agent, {}):
            st["raw"].setdefault(agent, {})[cid] = "MISSING"
            self._advance(call, cause="mark-missing")

    def _advance(self, call: Call, cause: str) -> None:
        rounds = self.st["t"]
        advance(self.st)
        if self.st["t"] > rounds:
            self.repair_causes.append(cause)
        self.phase = self._phase()
        if not self.marks or self.marks[-1][1] != PHASE_OF[self.phase]:
            self.marks.append((call.end, PHASE_OF[self.phase]))

    def session(self) -> Session:
        st = self.st
        s = Session(
            agents=list(st["agents"]),
            budget=self.budget,
            tau=self.tau,
            schema=clean(st["schema"])[:700],
            phase=self.phase,
            outcome=st["outcome"],
            winner=st["x_star"] if st["outcome"] else None,
            pool=[c["id"] for c in st["pool"]],
            repair_answered=sum(1 for c in st["pool"] if c["round"] > 0),
            repair_causes=list(self.repair_causes),
            marks=list(self.marks),
        )
        for c in st["pool"]:
            s.candidates[c["id"]] = Candidate(c["proposer"], clean(c["candidate"]), clean(c["rationale"]))
        for agent, a in st["anchors"].items():
            s.anchors[agent] = (a["raw_reject"], a["raw_ideal"])
            s.anchored.add(agent)
        for c in st["pool"]:
            if c["round"] == 0:
                s.seeded.add(c["proposer"])
        for agent, rated in st["raw"].items():
            for cid, raw in rated.items():
                if raw == "MISSING":
                    s.missing.add((agent, cid))
                    continue
                s.scored.setdefault(agent, set()).add(cid)
                s.scores[(agent, cid)] = Score(raw, clean(st.get("score_reasoning", {}).get(agent, {}).get(cid)))
        return s


def _start(call: Call) -> _Replay | None:
    a = call.args
    agents = [x.strip() for x in (option(a, "--agents") or "").split(",") if x.strip()]
    tau = _number(option(a, "--satisfaction-threshold"), DEFAULT_PARAMS["satisfaction_threshold"])
    eps = _number(option(a, "--eps"), DEFAULT_PARAMS["eps"])
    budget = int(_number(option(a, "-B", "--B", "--budget"), DEFAULT_PARAMS["B"]))
    state_id = _STATE.search(call.stdout)
    brief = (_text(option(a, "--context")), _text(option(a, "--x-reject")), _text(option(a, "--x-ideal")))
    schema = _text(option(a, "--schema"))
    st = None
    # The schema only helps the judge read the options: one that cannot be read (a `@file` that is gone) is replaced.
    for spec in (lambda: parse_schema(schema, "--schema"), lambda: FALLBACK_SCHEMA):
        try:
            st = new_run_state(agents, *brief, spec(), tau, eps, budget, state_id=state_id.group(1) if state_id else None)
            break
        except ConcordError:
            continue
    if st is None:
        return None
    replay = _Replay(st, str(budget), tau)
    replay.marks.append((call.start, "anchors"))  # the cost of the protocol starts at init
    return replay


def parse(calls: list[Call]) -> Session | None:
    """The last CONCORD session of the calls, or None when ``init`` never succeeded."""
    run: _Replay | None = None
    end: tuple[str | None, str | None] = (None, None)  # what RUN COMPLETE printed: the outcome and the winner
    for c in calls:
        if c.command == "init" and c.exit == 0:
            run, end = _start(c), (None, None)  # a new session: the previous one is forgotten
        elif run is None or c.exit != 0:
            continue
        elif c.command == "handle":
            run.handle(c)
        elif c.command == "mark-missing":
            run.mark_missing(c)
        text = c.stdout + "\n" + c.stderr
        if run is not None and (done := _COMPLETE.search(text)):
            winner = _WINNER.search(text)
            end = (done.group(1), winner.group(1) if winner else None)
    if run is None:
        return None
    s = run.session()
    if not s.reached_end and end[0]:  # the script says it finished though the replay did not: believe the script
        s.phase, s.outcome, s.winner = "Complete", end[0], end[1]
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
