#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""One agent of a run: the node that every participant runs.

The node holds no I/O, no clock of its own and no model. The caller feeds it what happens and sends what it hands back:

    step = agent.receive(text)          a message arrived (any text: what is not CONCORD is ignored)
    agent.due()                         what the agent's model must write now: a list of tasks
    step = agent.submit_init_reply(..)  the model's answer to a task
    agent.status()                      where the run stands

Every call that changes something returns a `Step`: `step.send` are the messages to broadcast, as text, and `step.events` say
what happened, for a log. Nothing is ever addressed to one agent: every output is a broadcast. What is mechanical is done by the
node (the order of checks, the bookkeeping, the decisions of the engine, building each message with its `check`); the model
supplies only content. A task is derived from the state, not queued: asking `due()` twice gives the same answer, and after a
restart from the store it still gives the right one.

The state is a plain dict (see state.py), saved through the `Store` after every call that changes it. The clock and the source
of UUIDs are injected, with the standard library as default, so that a run can be replayed.
"""
import time

from ioc_concord.engine import add_anchor, add_proposal, add_ratings, evaluate, lacking
from ioc_concord.errors import InvalidMessage, StateError
from ioc_concord.protocol import PROTOCOL_VERSION
from ioc_concord.state import PARTICIPANT, RUN_ACTIVE, active_run, item_key, members, new_agent_state, new_run
from ioc_concord.wire import (DATA_TAGS, INIT, build_init_reply, build_score, check_ok, locate, new_uuid, parse_message, peek)


class Step:
    """What a call produced: `send`, the messages to broadcast (text), and `events`, what happened (dicts with an `event` name,
    the time `at`, and details), for a log."""

    def __init__(self, now):
        self.now = now
        self.send = []
        self.events = []

    def note(self, event, **details):
        self.events.append({"event": event, "at": self.now, **details})

    def __repr__(self):
        return f"Step(send={len(self.send)}, events={[e['event'] for e in self.events]})"


class Agent:
    """The node of the agent `me`. `store` keeps its state between calls (None keeps it in memory only). `clock` gives the time in
    seconds, `uuid_fn` a new UUID as text."""

    def __init__(self, me, store=None, clock=time.time, uuid_fn=new_uuid):
        self.me = me
        self.store = store
        self.clock = clock
        self.new_uuid = uuid_fn
        self.state = self._load()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    def _load(self):
        if self.store is None:
            return new_agent_state(self.me)
        try:
            state = self.store.load()
        except StateError:
            return new_agent_state(self.me)
        if state.get("me") != self.me:
            raise StateError(f"the saved state belongs to {state.get('me')!r}, not to {self.me!r}")
        return state

    def _save(self):
        if self.store is not None:
            self.store.save(self.state)

    def _step(self, now):
        return Step(self.clock() if now is None else now)

    def _run(self, run_id, what):
        run = self.state["runs"].get(run_id)
        if run is None:
            raise StateError(f"{what}: the agent knows no run {run_id!r}")
        if run["status"] != RUN_ACTIVE:
            raise StateError(f"{what}: the run {run_id} has {run['status']}")
        if run["role"] != PARTICIPANT:
            raise StateError(f"{what}: the agent only opened the run {run_id}, it is not a participant")
        return run

    # ------------------------------------------------------------------
    # what arrives
    # ------------------------------------------------------------------

    def receive(self, text, now=None):
        """Handle `text`, whatever it is, in the order of checks of the spec. Returns a Step."""
        step = self._step(now)
        self._handle(text, step)
        self._save()
        return step

    def _handle(self, text, step):
        try:
            doc = locate(text)
        except InvalidMessage:
            return step.note("ignored", reason="not a CONCORD message")
        info = peek(doc)
        if info is None:
            return step.note("ignored", reason="not a CONCORD message")
        attrs, tag = info["attrs"], info["tag"]
        sender, run_id, uid = attrs.get("from"), attrs.get("run_id"), attrs.get("uuid")
        run = self.state["runs"].get(run_id)
        where = {"kind": tag, "from": sender, "run_id": run_id}
        # 1. the run is known, the sender belongs to it and is not this agent
        if sender == self.me:
            return step.note("ignored", reason="own message", **where)
        if run is None and tag != INIT:
            return step.note("ignored", reason="unknown run", **where)
        if run is not None and sender not in members(run):
            return step.note("ignored", reason="sender is not in the run", **where)
        # 2. the uuid is not one already accepted
        if run is not None and uid in run["uuids"]:
            return step.note("ignored", reason="duplicate", **where)
        # 3. the version is this agent's own
        if attrs.get("version") != PROTOCOL_VERSION:
            return step.note("ignored", reason="version", version=attrs.get("version"), **where)
        # 4. the check is correct, for the messages that carry one
        if tag in DATA_TAGS and not check_ok(doc):
            return step.note("ignored", reason="check", **where)
        # 5. the message is valid
        try:
            msg = parse_message(doc, run["schema"] if run is not None else None)
        except InvalidMessage as exc:
            return step.note("ignored", reason="invalid", detail=str(exc), **where)
        # 6. record it, keep its uuid and act on it
        if run is None:
            return self._join(msg, doc, step)
        if self._record(run, msg, doc, step):
            run["uuids"].append(uid)
            self._advance(run, step)

    def _join(self, init, doc, step):
        where = {"kind": INIT, "from": init["from"], "run_id": init["run_id"]}
        if self.me not in init["participants"]:
            return step.note("ignored", reason="not a participant", **where)
        if active_run(self.state) is not None:
            return step.note("ignored", reason="busy", **where)
        self.state["runs"][init["run_id"]] = new_run(self.me, init, doc)
        self.state["active"] = init["run_id"]
        step.note("joined", initiator=init["from"], participants=init["participants"], run_id=init["run_id"])

    def _record(self, run, msg, doc, step):
        """Record the valid message `msg` (the text `doc`) of a known run. Returns whether to keep its uuid."""
        kind, author, where = msg["kind"], msg["from"], {"kind": msg["kind"], "from": msg["from"], "run_id": run["run_id"]}
        if kind == "init":
            step.note("ignored", reason="run already known", **where)
            return False
        if kind == "init-reply" and author in run["participants"]:
            return self._record_init_reply(run, msg, doc, step)
        if kind == "score" and author in run["participants"]:
            return self._record_score(run, msg, doc, step)
        step.note("ignored", reason="not handled", **where)
        return False

    def _record_init_reply(self, run, msg, doc, step):
        author, key, p = msg["from"], item_key("init-reply", msg["from"]), msg["proposal"]
        if key in run["received"] or key in run["sent"]:
            step.note("ignored", reason="already recorded", kind="init-reply", **{"from": author})
            return True
        try:
            add_anchor(run["engine"], author, msg["raw_reject"], msg["raw_ideal"])
            add_proposal(run["engine"], author, p["id"], p["candidate"], p["rationale"], p["raw"], p["reasoning"])
        except InvalidMessage as exc:
            step.note("ignored", reason="invalid", detail=str(exc), kind="init-reply", **{"from": author})
            return False
        run["received"][key] = doc
        step.note("recorded", kind="init-reply", **{"from": author})
        return True

    def _record_score(self, run, msg, doc, step):
        author = msg["from"]
        try:
            recorded, unknown, ignored = add_ratings(run["engine"], author, [(e["id"], e["raw"], e["reasoning"])
                                                                                for e in msg["scores"]])
        except InvalidMessage as exc:
            step.note("ignored", reason="invalid", detail=str(exc), kind="score", **{"from": author})
            return False
        for cid in recorded:
            run["received"][item_key("score", author, cid)] = doc
        ignored = msg["ignored"] + ignored
        if ignored:
            step.note("entries ignored", kind="score", candidates=ignored, **{"from": author})
        if unknown:
            step.note("gap", kind="score", candidates=unknown, **{"from": author})
        step.note("recorded", kind="score", candidates=recorded, **{"from": author})
        return True

    def _advance(self, run, step):
        """Evaluate the engine over the state, and note the outcome when this agent has computed it."""
        ev = evaluate(run["engine"])
        if ev["state"] == "done" and run["outcome"] is None:
            run["outcome"] = ev["outcome"]
            step.note("outcome", outcome=ev["outcome"], winner=ev["x_star"], run_id=run["run_id"])
        elif ev["state"] == "repair":
            step.note("repair owed", bottleneck=ev["bottleneck"], round=ev["round"], run_id=run["run_id"])

    # ------------------------------------------------------------------
    # what the model must write
    # ------------------------------------------------------------------

    def due(self):
        """The tasks the agent's model owes now, as dicts with a `kind`:

        init-reply  run_id, initiator, context, x_reject, x_ideal, schema: anchors, a seed and a score for it
        score       run_id, candidates [{id, from, candidate, rationale, reasoning}]: a rating for each, on the same scale
        """
        run = active_run(self.state)
        if run is None or run["role"] != PARTICIPANT:
            return []
        me, st = self.me, run["engine"]
        if item_key("init-reply", me) not in run["sent"]:
            return [{"kind": "init-reply", "run_id": run["run_id"], "initiator": run["initiator"], "context": run["context"],
                     "x_reject": run["x_reject"], "x_ideal": run["x_ideal"], "schema": run["schema"]}]
        owed = self._to_rate(run)
        if not owed:
            return []
        by_id = {c["id"]: c for c in st["pool"]}
        return [{"kind": "score", "run_id": run["run_id"], "candidates": [
            {"id": cid, "from": by_id[cid]["proposer"], "candidate": by_id[cid]["candidate"],
             "rationale": by_id[cid]["rationale"], "reasoning": st["reasoning"][by_id[cid]["proposer"]].get(cid, "")}
            for cid in owed]}]

    def _to_rate(self, run):
        """The candidates of the others that this agent has not rated, once every init reply is in."""
        st = run["engine"]
        if len(st["anchors"]) < len(st["agents"]):
            return []
        return [cid for a, cid in lacking(st) if a == self.me]

    # ------------------------------------------------------------------
    # what the model wrote
    # ------------------------------------------------------------------

    def submit_init_reply(self, run_id, raw_reject, raw_ideal, candidate, rationale, raw, reasoning, now=None):
        """Answer the `init-reply` task: the anchors, the seed candidate with its rationale, and the agent's own score for it with its
        reasoning. Scores are numbers from 0 to 100 with at most 6 decimal places (as text, int or float). Raises InvalidMessage
        when something is invalid, and nothing is sent or recorded: the caller fixes it and submits again."""
        run = self._run(run_id, "submit_init_reply")
        key = item_key("init-reply", self.me)
        if key in run["sent"]:
            raise StateError("submit_init_reply: this agent has already sent its init reply")
        text = build_init_reply(self.me, run_id, raw_reject, raw_ideal, candidate, rationale, raw, reasoning,
                                spec=run["schema"], uid=self.new_uuid())
        msg = parse_message(text, run["schema"])
        p = msg["proposal"]
        add_anchor(run["engine"], self.me, msg["raw_reject"], msg["raw_ideal"])
        add_proposal(run["engine"], self.me, p["id"], p["candidate"], p["rationale"], p["raw"], p["reasoning"])
        run["sent"][key] = text
        return self._sent(run, text, "init-reply", now)

    def submit_score(self, run_id, scores, now=None):
        """Answer the `score` task: `scores` is [(candidate id, raw, reasoning)] for exactly the candidates the task listed. Raises
        StateError when no score is owed, and InvalidMessage when the list is not the one owed or something is invalid."""
        run = self._run(run_id, "submit_score")
        owed = self._to_rate(run)
        if not owed:
            raise StateError("submit_score: this agent owes no score now")
        given = [s[0] for s in scores]
        if sorted(given) != sorted(owed):
            raise InvalidMessage("a score message covers exactly the candidates not rated yet: owed " + ", ".join(owed)
                                 + "; given " + (", ".join(given) or "none"))
        text = build_score(self.me, run_id, scores, uid=self.new_uuid())
        msg = parse_message(text, run["schema"])
        add_ratings(run["engine"], self.me, [(e["id"], e["raw"], e["reasoning"]) for e in msg["scores"]])
        for cid in owed:
            run["sent"][item_key("score", self.me, cid)] = text
        return self._sent(run, text, "score", now)

    def _sent(self, run, text, kind, now):
        step = self._step(now)
        step.send.append(text)
        step.note("sent", kind=kind, run_id=run["run_id"])
        self._advance(run, step)
        self._save()
        return step

    # ------------------------------------------------------------------
    # where the run stands
    # ------------------------------------------------------------------

    def status(self, run_id=None):
        """Where a run stands for this agent (the active run when `run_id` is None; None when there is none): its `status`, the
        `outcome` this agent has computed (None before), and the engine's view: `state` ("wait", "repair" or "done"), `x_star`,
        `floor`, `scores` (millionths), `bottleneck`, `round`, and `lacking`, the ratings still missing as [(agent, candidate)]."""
        run = self.state["runs"].get(run_id or self.state["active"])
        if run is None:
            return None
        ev = evaluate(run["engine"])
        return {"run_id": run["run_id"], "role": run["role"], "status": run["status"], "outcome": run["outcome"],
                "state": ev["state"], "x_star": ev["x_star"], "floor": ev["floor"], "scores": ev["scores"],
                "bottleneck": ev["bottleneck"], "round": ev["round"], "reporter": ev["reporter"],
                "lacking": lacking(run["engine"]), "anchors_in": sorted(run["engine"]["anchors"])}
