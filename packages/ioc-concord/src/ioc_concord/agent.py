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

from ioc_concord.engine import add_anchor, add_proposal, add_ratings, evaluate, parse_candidate_id, timers_enabled
from ioc_concord.errors import InvalidMessage, MissingState, StateError
from ioc_concord.guidance import render_task
from ioc_concord.liveness import earliest_deadline, expired_keys, owed_batches, sync_timers
from ioc_concord.protocol import ABORTED, PROTOCOL_VERSION
from ioc_concord.report import report_of, status_of
from ioc_concord.state import (INITIATOR, PARTICIPANT, RUN_ABORTED, RUN_ACTIVE, RUN_ENDED, active_run, item_key, keep_sent, keep_uuid,
                               members, new_agent_state, new_run)
from ioc_concord.tasks import tasks_of, to_rate
from ioc_concord.wire import (DATA_TAGS, ERROR, INIT, KIND_OF_TAG, RESEND, build_cancel, build_error, build_init, build_init_reply,
                              build_repair, build_resend, build_result, build_score, check_ok, locate, new_uuid, parse_message, peek)


class Step:
    """What a call produced: `send`, the messages to broadcast (text), `forward`, the messages to pass on when the agent is the centre of
    a star (see relay.py: the text received, unchanged, or nothing), `accepted`, the texts received that the agent took (it joined a run
    or recorded the message), which its model should read before its next task (see guidance.py), and `events`, what happened (dicts
    with an `event` name, the time `at`, and details), for a log."""

    def __init__(self, now):
        self.now = now
        self.send = []
        self.forward = []
        self.accepted = []
        self.events = []

    def note(self, event, **details):
        self.events.append({"event": event, "at": self.now, **details})

    def __repr__(self):
        return f"Step(send={len(self.send)}, events={[e['event'] for e in self.events]})"


class Agent:
    """The node of the agent `me`. `store` keeps its state between calls (None keeps it in memory only). `clock` gives the time in
    seconds, `uuid_fn` a new UUID as text. With a `relay` (see relay.py) the agent is the centre of a star: besides what it does as an
    agent, it says in `step.forward` which of the messages it receives to pass on to the spokes. Without one `step.forward` stays empty."""

    def __init__(self, me, store=None, clock=time.time, uuid_fn=new_uuid, relay=None):
        self.me = me
        self.store = store
        self.clock = clock
        self.new_uuid = uuid_fn
        self.relay = relay
        self.state = self._load()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    def _load(self):
        if self.store is None:
            return new_agent_state(self.me)
        try:
            state = self.store.load()
        except MissingState:                    # nothing saved yet; a state that cannot be read is an error, never a fresh start
            return new_agent_state(self.me)
        if not isinstance(state, dict):
            raise StateError("the saved state is not the state of an agent")
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
        if self.relay is not None:
            step.forward = self.relay.receive(text)         # first, and whatever the agent does with the message: it is mechanical
        self._handle(text, step)
        if any(e["event"] in ("joined", "recorded") for e in step.events):
            step.accepted.append(text)
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
        if run is None and tag not in (INIT, RESEND):
            return step.note("ignored", reason="unknown run", **where)
        if run is not None and sender not in members(run):
            return step.note("ignored", reason="sender is not in the run", **where)
        # 2. the uuid is not one already accepted
        if run is not None and uid in run["uuids"]:
            return step.note("ignored", reason="duplicate", **where)
        # a version refusal of the init is read whatever its version: the initiator relies on its root attributes and its text
        if (tag == ERROR and run is not None and attrs.get("reason") == "version" and attrs.get("what") == "init"
                and attrs.get("to") == self.me and run["initiator"] == self.me and sender in run["participants"]):
            keep = self._on_version_refusal(run, info, step)
            if keep:
                keep_uuid(run, uid)
            return
        # 3 to 6, for the message itself and, in a resend, for the message inside
        keep = self._unwrap(doc, info, run, step) if tag == RESEND else self._accept(doc, info, run, step)
        run = self.state["runs"].get(run_id)             # the init may have started it
        if keep and run is not None:
            keep_uuid(run, uid)
            self._advance(run, step)

    def _unwrap(self, doc, info, run, step):
        """A <concord-resend>: steps 2 and 3 are for the wrapper (it has no check), and 3 to 5 for the message inside."""
        attrs, inner = info["attrs"], info["inner"]
        where = {"kind": RESEND, "from": attrs.get("from"), "run_id": attrs.get("run_id")}
        if attrs.get("version") != PROTOCOL_VERSION:
            step.note("ignored", reason="version", version=attrs.get("version"), **where)
            if inner is not None and inner["tag"] == INIT:
                self._refuse_version(inner, step)
            return False
        try:
            msg = parse_message(doc)
        except InvalidMessage as exc:
            step.note("ignored", reason="invalid", detail=str(exc), **where)
            return False
        inner = peek(msg["inner"])
        if inner is None or inner["attrs"].get("from") != attrs.get("from") or inner["attrs"].get("run_id") != attrs.get("run_id"):
            step.note("ignored", reason="invalid", detail="the message inside is not the sender's own, of this run", **where)
            return False
        return self._accept(msg["inner"], inner, run, step, resent=True)

    def _accept(self, doc, info, run, step, resent=False):
        """Steps 3 to 6 for `doc`. Returns whether its uuid is to be kept: it was processed."""
        attrs, tag = info["attrs"], info["tag"]
        where = {"kind": tag, "from": attrs.get("from"), "run_id": attrs.get("run_id")}
        if run is None and tag != INIT:          # a message of a run it does not know, such as one carried by a resend
            step.note("ignored", reason="unknown run", **where)
            return False
        if attrs.get("version") != PROTOCOL_VERSION:
            step.note("ignored", reason="version", version=attrs.get("version"), **where)
            if tag == INIT:
                self._refuse_version(info, step)
            return False
        if tag in DATA_TAGS and not check_ok(doc):
            step.note("ignored", reason="check", **where)
            if run is not None and tag != INIT:
                self._ask_damaged(run, info, step)
            return False
        try:
            msg = parse_message(doc, run["schema"] if run is not None else None)
        except InvalidMessage as exc:
            step.note("ignored", reason="invalid", detail=str(exc), **where)
            if tag == INIT and run is None:
                self._error(step, attrs.get("from"), attrs.get("run_id"), "init", "invalid", text=str(exc))
            return False
        if run is None:
            return self._join(msg, doc, step)
        if msg["kind"] == "init":
            return self._known_init(run, msg, resent, step)
        kept = self._record(run, msg, doc, step)
        if kept is None:                                  # it cannot be judged before the run state is complete
            if info["attrs"]["uuid"] not in run["held"]:
                if len(run["held"]) >= len(run["participants"]) * (run["params"]["B"] + 1):
                    step.note("ignored", reason="too many messages held", kind=msg["kind"], **{"from": msg["from"]})
                    return False
                run["held"][info["attrs"]["uuid"]] = doc
            step.note("held", kind=msg["kind"], reason="the run state is incomplete", **{"from": msg["from"]})
            self._ask_keys(run, self._lacking_keys(run), step)
            return False
        return kept

    def _refuse_version(self, info, step):
        """An init of another version: the agent does not join, and tells the author which version it speaks."""
        got = info["attrs"].get("version")
        self._error(step, info["attrs"].get("from"), info["attrs"].get("run_id"), "init", "version",
                    text=f"I speak version {PROTOCOL_VERSION}; this init is version {got}.")

    def _error(self, step, to, run_id, what, reason, candidate=None, ref=None, text=""):
        """Add a <concord-error> to the messages to send, unless it cannot be built (an unreadable author or run id). Returns whether
        it was added."""
        try:
            step.send.append(build_error(self.me, run_id, to, what, reason, candidate=candidate, ref=ref, text=text,
                                         uid=self.new_uuid()))
        except InvalidMessage as exc:
            step.note("cannot answer", detail=str(exc))
            return False
        step.note("sent", kind="error", to=to, what=what, error=reason, run_id=run_id)
        return True

    def _join(self, init, doc, step):
        where = {"kind": INIT, "from": init["from"], "run_id": init["run_id"]}
        if self.me not in init["participants"]:
            step.note("ignored", reason="not a participant", **where)
            return False
        mine = active_run(self.state)
        if mine is not None and self._waits_for_replies(mine) and mine["run_id"] > init["run_id"]:
            # two runs opened at about the same time: the one with the lower run id continues, and this agent joins it
            self._abort(mine, step, "superseded", superseded_by=init["run_id"])
        elif mine is not None:
            step.note("ignored", reason="busy", **where)
            self._error(step, init["from"], init["run_id"], "init", "busy", text="I am in another run.")
            return False
        self._open_run(init, doc)
        step.note("joined", initiator=init["from"], participants=init["participants"], run_id=init["run_id"])
        return True

    def _open_run(self, init, doc):
        """Make the run of the parsed init `init` (the text `doc`) the active one. A run id that the agent knows is never reused: it would
        replace the state of that run."""
        if init["run_id"] in self.state["runs"]:
            raise StateError(f"the agent knows the run {init['run_id']} already")
        run = self.state["runs"][init["run_id"]] = new_run(self.me, init, doc)
        self.state["active"] = init["run_id"]
        return run

    def _waits_for_replies(self, run):
        """Whether this agent opened `run` and still waits for init replies."""
        return run["initiator"] == self.me and len(run["engine"]["anchors"]) < len(run["engine"]["agents"])

    def _on_version_refusal(self, run, info, step):
        """A participant refuses the init because of its protocol version: the initiator ends its run `ABORTED` with the reason
        "version" and sends the cancel, so that the participants that already joined end it too."""
        where = {"kind": "error", "from": info["attrs"]["from"], "error": "version", "run_id": run["run_id"]}
        if run["status"] != RUN_ACTIVE:
            step.note("ignored", reason="run over", **where)
            return False
        step.note("refused", text=info["text"], **where)
        self._abort(run, step, "version")
        run["abort"]["note"] = {"agent": info["attrs"]["from"], "text": info["text"]}        # which agent refused, and what it said
        return True

    def _record(self, run, msg, doc, step):
        """Record the valid message `msg` (the text `doc`) of a known run. Returns whether to keep its uuid, or None when it cannot
        be judged yet (see `_accept`)."""
        kind, author, where = msg["kind"], msg["from"], {"kind": msg["kind"], "from": msg["from"], "run_id": run["run_id"]}
        if kind == "error":
            return self._record_error(run, msg, step)
        if kind == "cancel":
            return self._record_cancel(run, msg, step)
        if run["status"] != RUN_ACTIVE:
            step.note("ignored", reason="run over", **where)
            return False
        if run["role"] == INITIATOR:
            return self._record_as_initiator(run, msg, doc, step)
        if kind == "result":
            return self._record_result(run, msg, doc, step)
        handler = {"repair": self._record_repair, "init-reply": self._record_init_reply, "score": self._record_score}.get(kind)
        if handler is None or author not in run["participants"]:
            step.note("ignored", reason="not handled", **where)
            return False
        return handler(run, msg, doc, step)

    def _record_as_initiator(self, run, msg, doc, step):
        """An initiator that is not a participant records only the init replies, and the repairs, which it does not judge, and takes the
        result of the reporter: that is how it learns how the run ended."""
        kind, author = msg["kind"], msg["from"]
        if kind == "result":
            return self._observe_result(run, msg, doc, step)
        if author in run["participants"] and kind == "init-reply":
            return self._record_init_reply(run, msg, doc, step)
        if author in run["participants"] and kind == "repair":
            return self._observe_repair(run, msg, doc, step)
        step.note("ignored", reason="not for the initiator", kind=kind, **{"from": author}, run_id=run["run_id"])
        return False

    def _known_init(self, run, msg, resent, step):
        """An init for a run the agent knows is ignored, unless it comes inside a resend from the initiator: it is waiting for init
        replies, and a participant that has sent its own sends it again."""
        key = item_key("init-reply", self.me)
        if not resent or key not in run["sent"] or msg["from"] != run["initiator"]:
            step.note("ignored", reason="run already known", kind="init", **{"from": msg["from"]}, run_id=run["run_id"])
            return False
        step.send.append(build_resend(self.me, run["run_id"], run["sent"][key], uid=self.new_uuid()))
        step.note("resent", kind="init-reply", run_id=run["run_id"])
        return True

    def _record_error(self, run, msg, step):
        """An error is answered by its addressee, with the stored original word for word, even after the run is over. Only a
        mismatch is read by every agent."""
        where = {"kind": "error", "from": msg["from"], "error": msg["reason"], "what": msg["what"]}
        if msg["reason"] == "mismatch":
            return self._record_mismatch(run, msg, step)
        if msg["reason"] not in ("missing", "check") or msg["to"] != self.me or msg["what"] == "init":
            step.note("ignored", reason="no answer expected", **where)
            return False
        if msg["reason"] == "check":
            stored = run["sent_by_uuid"].get(msg["ref"])
        else:
            stored = run["sent"].get(item_key(msg["what"], self.me, msg["candidate"]))
        if stored is None:
            step.note("nothing to send", **where)
            return True
        step.send.append(build_resend(self.me, run["run_id"], stored, reply_to=msg["uuid"], uid=self.new_uuid()))
        step.note("resent", kind=msg["what"], to=msg["from"], run_id=run["run_id"])
        return True

    def _record_cancel(self, run, msg, step):
        """A cancel ends the run for this agent, whatever its stage, and is passed on once."""
        where = {"kind": "cancel", "from": msg["from"], "cancel": msg["reason"]}
        if msg["agent"] is not None and msg["agent"] not in run["participants"]:
            step.note("ignored", reason="invalid", detail=f"{msg['agent']} is not a participant of the run", **where)
            return False
        self._abort(run, step, msg["reason"], msg["agent"], msg["superseded_by"])
        return True

    def _abort(self, run, step, reason, agent=None, superseded_by=None):
        """End the run `ABORTED` with `reason`, and send the cancel once. An agent whose run has ended with an outcome keeps it:
        its report is disputed. An aborted run sends no result."""
        why = {"run_id": run["run_id"], "reason": reason, "agent": agent, "superseded_by": superseded_by}
        if run["status"] == RUN_ACTIVE:
            run["status"], run["outcome"], run["abort"] = RUN_ABORTED, ABORTED, {"reason": reason, "agent": agent,
                                                                                 "superseded_by": superseded_by, "note": None}
            run["timers"], run["held"] = {}, {}
            step.note("aborted", **why)
        elif run["status"] == RUN_ENDED:
            run["disputed"] = True
            step.note("disputed", **why)
        if not run["cancel_sent"]:
            run["cancel_sent"] = True
            step.send.append(build_cancel(self.me, run["run_id"], reason, superseded_by=superseded_by, agent=agent,
                                          uid=self.new_uuid()))
            step.note("sent", kind="cancel", **why)

    def _already(self, run, key, step, where):
        """Whether the message of the item `key` has been recorded, or sent, already: the first one stands."""
        if key not in run["received"] and key not in run["sent"]:
            return False
        step.note("ignored", reason="already recorded", **where)
        return True

    def _observe_repair(self, run, msg, doc, step):
        """An initiator that does not negotiate keeps the repairs it hears, without judging them, so that its report can name the
        winner. A repair id carries its author, so a repair that the participants ignore cannot take the place of a valid one."""
        author, p = msg["from"], msg["proposal"]
        where = {"kind": "repair", "from": author, "candidate": p["id"]}
        key = item_key("repair", author, p["id"])
        if self._already(run, key, step, where):
            return True
        add_proposal(run["engine"], author, p["id"], p["candidate"], p["rationale"], p["raw"], p["reasoning"])
        run["received"][key] = doc
        step.note("recorded", **where)
        return True

    def _record_init_reply(self, run, msg, doc, step):
        author, key, p = msg["from"], item_key("init-reply", msg["from"]), msg["proposal"]
        if self._already(run, key, step, {"kind": "init-reply", "from": author}):
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
        mine = [cid for cid in unknown if parse_candidate_id(cid)[1] == self.me]       # this agent never proposed it: not a gap
        if mine:
            step.note("entries ignored", kind="score", candidates=mine, **{"from": author})
        unknown = [cid for cid in unknown if cid not in mine]
        if unknown:
            step.note("gap", kind="score", candidates=unknown, **{"from": author})
            keys = []
            for cid in unknown:
                rnd, proposer = parse_candidate_id(cid)
                if proposer in run["participants"]:
                    if cid not in run["expected"]:
                        run["expected"].append(cid)
                    keys.append(f"init-reply|{proposer}" if rnd == 0 else f"repair|{proposer}|{rnd}")
            self._ask_keys(run, keys, step)
        step.note("recorded", kind="score", candidates=recorded, **{"from": author})
        return True

    def _record_repair(self, run, msg, doc, step):
        """A repair counts only from the bottleneck of the round, and only when one is owed. A copy of the run state that is not
        complete cannot tell: None says the repair is to be held (see `_accept`) and judged again later."""
        author, p = msg["from"], msg["proposal"]
        where = {"kind": "repair", "from": author, "candidate": p["id"]}
        key = item_key("repair", author, p["id"])
        if self._already(run, key, step, where):
            return True
        ev = evaluate(run["engine"])
        if ev["state"] == "wait":
            return None
        if ev["state"] != "repair" or ev["bottleneck"] != author or p["round"] != ev["round"]:
            step.note("ignored", reason="no repair owed from this agent", **where)
            return False
        try:
            add_proposal(run["engine"], author, p["id"], p["candidate"], p["rationale"], p["raw"], p["reasoning"])
        except InvalidMessage as exc:
            step.note("ignored", reason="invalid", detail=str(exc), **where)
            return False
        run["received"][key] = doc
        step.note("recorded", **where)
        return True

    def _result_to_take(self, run, msg, step):
        """Whether the result `msg` is to be taken: only the result of the reporter counts, and the first one stands. Returns None when it
        is to be taken, else what the caller returns (False: ignored, True: already held)."""
        where = {"kind": "result", "from": msg["from"]}
        if msg["from"] != run["participants"][0]:
            step.note("ignored", reason="not the reporter", **where)
            return False
        return True if self._already(run, item_key("result", msg["from"]), step, where) else None

    def _take_result(self, run, msg, doc, step):
        run["reported"] = {"outcome": msg["outcome"], "winning_candidate_id": msg["winning_candidate_id"]}
        run["received"][item_key("result", msg["from"])] = doc
        step.note("recorded", kind="result", outcome=msg["outcome"], winner=msg["winning_candidate_id"], **{"from": msg["from"]})

    def _record_result(self, run, msg, doc, step):
        """The result counts only from the reporter, and its winner must be a candidate of the run. Until this agent has computed
        its own outcome it cannot say whether the winner exists: None says the result is to be held (see `_accept`)."""
        taken = self._result_to_take(run, msg, step)
        if taken is not None:
            return taken
        author, winner = msg["from"], msg["winning_candidate_id"]
        where = {"kind": "result", "from": author}
        if winner not in {c["id"] for c in run["engine"]["pool"]}:
            if run["outcome"] is None:
                return None
            step.note("ignored", reason="invalid", detail=f"{winner} is not a candidate of the run", **where)
            return False
        self._take_result(run, msg, doc, step)
        return True

    def _observe_result(self, run, msg, doc, step):
        """The result as an initiator that is not a participant takes it: from the reporter, as it is, since it holds no copy of the run
        state to judge the winner by. It ends the run for the initiator."""
        taken = self._result_to_take(run, msg, step)
        if taken is not None:
            return taken
        self._take_result(run, msg, doc, step)
        run["status"], run["outcome"], run["timers"] = RUN_ENDED, msg["outcome"], {}
        step.note("ended", outcome=msg["outcome"], run_id=run["run_id"])
        return True

    def _record_mismatch(self, run, msg, step):
        """A mismatch error is read by every agent, also after its run has ended: the run is in doubt."""
        author, where = msg["from"], {"kind": "error", "from": msg["from"], "about": "mismatch"}
        if author not in run["participants"] or msg["to"] != run["participants"][0]:
            step.note("ignored", reason="not about the result of the reporter", **where)
            return False
        run["mismatches"]["received"].append(author)
        step.note("mismatch received", **where)
        return True

    def _advance(self, run, step):
        """Evaluate the engine over the state, note the outcome when this agent has computed it, and take the step that follows:
        the reporter sends its result, any other participant compares the result it holds with its outcome."""
        if run["status"] != RUN_ACTIVE:
            return
        if run["role"] == INITIATOR:
            return self._sync_timers(run, step)
        self._replay_held(run, step)
        ev = evaluate(run["engine"])                    # once: the steps below read it, and none of them changes the engine
        if ev["state"] == "done" and run["outcome"] is None:
            run["outcome"] = ev["outcome"]
            step.note("outcome", outcome=ev["outcome"], winner=ev["x_star"], run_id=run["run_id"])
        elif ev["state"] == "repair":
            step.note("repair owed", bottleneck=ev["bottleneck"], round=ev["round"], run_id=run["run_id"])
        if run["outcome"] is not None and run["status"] == RUN_ACTIVE:
            self._confirm(run, ev, step)
        self._sync_timers(run, step, ev)

    def _replay_held(self, run, step):
        """Judge again the repairs and results that were held, now that the run state may be complete."""
        moved = True
        while moved and run["held"]:
            moved = False
            for uid, doc in list(run["held"].items()):
                kept = self._record(run, parse_message(doc, run["schema"]), doc, step)
                if kept is None:
                    continue
                del run["held"][uid]
                moved = True
                if kept:
                    keep_uuid(run, uid)

    def _confirm(self, run, ev, step):
        """The reporter ends its run by sending its result; another participant ends it when it holds that result, and a result
        that differs from its own outcome makes it send a mismatch error. A result never changes the outcome."""
        reporter, where = run["participants"][0], {"run_id": run["run_id"]}
        if self.me == reporter:
            text = build_result(self.me, run["run_id"], ev["outcome"], ev["x_star"], uid=self.new_uuid())
            keep_sent(run, item_key("result", self.me), text)
            step.send.append(text)
            step.note("sent", kind="result", **where)
        elif run["reported"] is not None:
            got = (run["reported"]["outcome"], run["reported"]["winning_candidate_id"])
            if got != (ev["outcome"], ev["x_star"]):
                text = build_error(self.me, run["run_id"], reporter, "result", "mismatch", uid=self.new_uuid(),
                                   text=f"My outcome is {ev['outcome']} with {ev['x_star']}; the result says {got[0]} with {got[1]}.")
                run["mismatches"]["sent"] += 1
                run["mismatches"]["last"] = text
                step.send.append(text)
                step.note("mismatch", mine=[ev["outcome"], ev["x_star"]], theirs=list(got), **where)
        else:
            return
        run["status"] = RUN_ENDED
        step.note("ended", outcome=run["outcome"], **where)

    # ------------------------------------------------------------------
    # the messages owed, and the timers
    # ------------------------------------------------------------------

    def _evaluation(self, run):
        """The evaluation of the engine over `run`; an initiator that does not negotiate has none, and nothing reads it."""
        return None if run["role"] == INITIATOR else evaluate(run["engine"])

    def _sync_timers(self, run, step, ev=None):
        """Start the timer of every batch that has become owed, and drop the timer of every batch that is complete (see liveness.py).
        The timers are in the state, so a restart keeps them."""
        sync_timers(run, self.me, step.now, ev or self._evaluation(run))

    def _request(self, run, key, step, ref=None, from_timer=False):
        """Ask the author of the batch for the first message it lacks, and restart the timer. It counts against `retries`, once the
        request has been sent. With `ref` the request is `check`: the document of that uuid arrived damaged. An initiator whose timer
        expires for an init reply sends the init again, in a resend, since an agent that never received it cannot answer an error.
        Returns whether a request was sent."""
        t = run["timers"][key]
        if ref is None and from_timer and t["what"] == "init-reply" and run["initiator"] == self.me:
            step.send.append(build_resend(self.me, run["run_id"], run["sent"][item_key("init", self.me)], uid=self.new_uuid()))
        elif ref is not None:
            if not self._error(step, t["author"], run["run_id"], t["what"], "check", ref=ref):
                return False
        elif not self._error(step, t["author"], run["run_id"], t["what"], "missing", candidate=t["candidate"]):
            return False
        t["requests"] += 1
        if timers_enabled(run["params"]):
            t["deadline"] = step.now + run["params"]["timeout"]
        step.note("asked", author=t["author"], what=t["what"], candidate=t["candidate"], request=t["requests"], run_id=run["run_id"])
        return True

    def _ask_damaged(self, run, info, step):
        """A document arrived damaged. If this agent is owed that kind of message from its author, ask for it again, naming the uuid
        of the damaged document; the request counts against `retries` with the others for that batch. A document that is not owed (it
        is early, or the agent holds it already) is not asked for: the timer will ask when it is owed."""
        author, kind = info["attrs"].get("from"), KIND_OF_TAG[info["tag"]]
        self._sync_timers(run, step)
        for key, t in sorted(run["timers"].items(), key=lambda kv: (kv[1]["deadline"] is None, kv[1]["deadline"] or 0, kv[0])):
            if t["author"] == author and t["what"] == kind and t["requests"] < run["params"]["retries"]:
                if self._request(run, key, step, ref=info["attrs"].get("uuid")):
                    return

    def _lacking_keys(self, run):
        return [k for k, o in owed_batches(run, self.me, self._evaluation(run)).items() if o["what"] != "result"]

    def _ask_keys(self, run, keys, step):
        """Ask at once for the batches of `keys`, since a message that has just arrived proves they exist, without waiting for their
        timers. A batch is asked this way once; the timer repeats the request. Each request counts against `retries`."""
        self._sync_timers(run, step)
        for key in keys:
            t = run["timers"].get(key)
            if t is not None and not t["asked"] and t["requests"] < run["params"]["retries"]:
                t["asked"] = True
                self._request(run, key, step)

    def tick(self, now=None):
        """Time has passed: for every timer that has expired, ask again, or, when `retries` requests have not been answered, give up on
        the author: the run ends `ABORTED` with the reason "missing-agent" and the cancel goes out. Returns a Step. A run with a
        `timeout` of 0 has no timers."""
        step = self._step(now)
        for run in list(self.state["runs"].values()):
            if run["status"] != RUN_ACTIVE or not timers_enabled(run["params"]):
                continue
            self._sync_timers(run, step)
            for key in expired_keys(run, step.now):
                t = run["timers"].get(key)
                if t is None:
                    break                                   # the run was aborted
                if t["requests"] >= run["params"]["retries"]:
                    self._abort(run, step, "missing-agent", agent=t["author"])
                    break
                self._request(run, key, step, from_timer=True)
        self._save()
        return step

    def next_wakeup(self):
        """The time of the earliest timer of an active run, or None: when to call `tick` next."""
        return earliest_deadline(self.state["runs"].values())

    # ------------------------------------------------------------------
    # what the model must write
    # ------------------------------------------------------------------

    def instructions(self, task):
        """The text that tells the agent's model what a task of `due()` asks of it (see guidance.py)."""
        return render_task(self._run(task["run_id"], "instructions"), self.me, task)

    def due(self):
        """The tasks the agent's model owes now, as dicts with a `kind`:

        init-reply  run_id, initiator, context, x_reject, x_ideal, schema: anchors, a seed and a score for it
        score       run_id, candidates [{id, from, candidate, rationale, reasoning}]: a rating for each, on the same scale
        repair      run_id, round, incumbent {id, candidate, rationale}, threshold, floor, scores {agent: normalized score of the
                    incumbent}, shortfall {agent: how far below the threshold}, reasoning {agent: what it said about the
                    incumbent}, repairs_left: a better candidate, which only the bottleneck of the round writes

        The numbers in a task are normalized scores between 0 and 1, as text with at most 6 decimal places.
        """
        return tasks_of(active_run(self.state), self.me)

    # ------------------------------------------------------------------
    # what the model wrote
    # ------------------------------------------------------------------

    def start(self, participants, context, x_reject, x_ideal, spec, params=None, run_id=None, now=None):
        """Open a run: build the `<concord-init>`, which the caller broadcasts, and keep it to send again. This agent is the initiator;
        it is a participant too when it is listed in `participants`, and then it owes an init reply like the others. `spec` is the
        candidate schema as a dict, `params` any of satisfaction_threshold, eps, B, timeout, retries (see wire.build_init). A fresh
        run id is made when none is given. Raises StateError when the agent is in a run, and InvalidMessage when the run is not valid."""
        if active_run(self.state) is not None:
            raise StateError("start: the agent is in a run already")
        text = build_init(self.me, participants, context, x_reject, x_ideal, spec, params=params,
                          run_id=run_id or self.new_uuid(), uid=self.new_uuid())
        init = parse_message(text)
        run = self._open_run(init, text)
        step = self._step(now)
        step.send.append(text)
        step.note("started", participants=init["participants"], run_id=init["run_id"], role=run["role"])
        if self.relay is not None:
            self.relay.learn(text)              # the centre sends this init to the spokes: what comes back for the run is passed on
        self._advance(run, step)
        self._save()
        return step

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
        keep_sent(run, key, text)
        return self._sent(run, text, "init-reply", now)

    def submit_score(self, run_id, scores, now=None):
        """Answer the `score` task: `scores` is [(candidate id, raw, reasoning)] for exactly the candidates the task listed. Raises
        StateError when no score is owed, and InvalidMessage when the list is not the one owed or something is invalid."""
        run = self._run(run_id, "submit_score")
        owed = to_rate(run, self.me)
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
            keep_sent(run, item_key("score", self.me, cid), text)
        return self._sent(run, text, "score", now)

    def submit_repair(self, run_id, candidate, rationale, raw, reasoning, now=None):
        """Answer the `repair` task: a better candidate, with its rationale and the agent's own score for it with its reasoning. The
        round is the one the task named. Raises StateError when no repair is owed from this agent, and InvalidMessage when something
        is invalid."""
        run = self._run(run_id, "submit_repair")
        ev = evaluate(run["engine"])
        if ev["state"] != "repair" or ev["bottleneck"] != self.me:
            raise StateError("submit_repair: no repair is owed from this agent now")
        text = build_repair(self.me, run_id, ev["round"], candidate, rationale, raw, reasoning, spec=run["schema"],
                            uid=self.new_uuid())
        p = parse_message(text, run["schema"])["proposal"]
        add_proposal(run["engine"], self.me, p["id"], p["candidate"], p["rationale"], p["raw"], p["reasoning"])
        keep_sent(run, item_key("repair", self.me, p["id"]), text)
        return self._sent(run, text, "repair", now)

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
        return status_of(run) if run is not None else None

    def report(self, run_id=None):
        """The report of a run (the active one when `run_id` is None; None when there is none), as the spec describes it (see
        report.py): `outcome`, `winner`, `scores`, `shortfall`, `rounds`, `reason`, `results_compared`, `mismatches` and `status`."""
        run = self.state["runs"].get(run_id or self.state["active"])
        return report_of(run, self.me) if run is not None else None
