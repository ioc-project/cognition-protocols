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

from ioc_concord.engine import add_anchor, add_proposal, add_ratings, evaluate, lacking, parse_candidate_id, timers_enabled
from ioc_concord.errors import InvalidMessage, StateError
from ioc_concord.protocol import ABORTED, PROTOCOL_VERSION, format_number
from ioc_concord.state import PARTICIPANT, RUN_ABORTED, RUN_ACTIVE, RUN_ENDED, active_run, item_key, members, new_agent_state, new_run
from ioc_concord.wire import (DATA_TAGS, INIT, KIND_OF_TAG, RESEND, build_cancel, build_error, build_init_reply, build_repair,
                              build_resend, build_result, build_score, check_ok, locate, new_uuid, parse_message, peek)


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
        if run is None and tag not in (INIT, RESEND):
            return step.note("ignored", reason="unknown run", **where)
        if run is not None and sender not in members(run):
            return step.note("ignored", reason="sender is not in the run", **where)
        # 2. the uuid is not one already accepted
        if run is not None and uid in run["uuids"]:
            return step.note("ignored", reason="duplicate", **where)
        # 3 to 6, for the message itself and, in a resend, for the message inside
        keep = self._unwrap(doc, info, run, step) if tag == RESEND else self._accept(doc, info, run, step)
        run = self.state["runs"].get(run_id)             # the init may have started it
        if keep and run is not None and uid not in run["uuids"]:
            run["uuids"].append(uid)
        if keep and run is not None:
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
            return step.note("ignored", reason="invalid", detail=str(exc), **where) and False
        inner = peek(msg["inner"])
        if inner is None or inner["attrs"].get("from") != attrs.get("from") or inner["attrs"].get("run_id") != attrs.get("run_id"):
            step.note("ignored", reason="invalid", detail="the message inside is not the sender's own, of this run", **where)
            return False
        return self._accept(msg["inner"], inner, run, step, resent=True)

    def _accept(self, doc, info, run, step, resent=False):
        """Steps 3 to 6 for `doc`. Returns whether its uuid is to be kept: it was processed."""
        attrs, tag = info["attrs"], info["tag"]
        where = {"kind": tag, "from": attrs.get("from"), "run_id": attrs.get("run_id")}
        if attrs.get("version") != PROTOCOL_VERSION:
            step.note("ignored", reason="version", version=attrs.get("version"), **where)
            if tag == INIT:
                self._refuse_version(info, step)
            return False
        if tag in DATA_TAGS and not check_ok(doc):
            step.note("ignored", reason="check", **where)
            if run is not None and tag != INIT:
                who = f"{attrs.get('from')}|{KIND_OF_TAG[tag]}"
                if run["damaged"].get(who, 0) < run["params"]["retries"]:
                    run["damaged"][who] = run["damaged"].get(who, 0) + 1
                    self._error(step, attrs.get("from"), attrs.get("run_id"), KIND_OF_TAG[tag], "check", ref=attrs.get("uuid"))
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
        kept = self._record(run, msg, doc, step, resent)
        if kept is None:                                  # it cannot be judged before the run state is complete
            if doc not in run["held"]:
                run["held"].append(doc)
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
        """Add a <concord-error> to the messages to send, unless it cannot be built (an unreadable author or run id)."""
        try:
            step.send.append(build_error(self.me, run_id, to, what, reason, candidate=candidate, ref=ref, text=text,
                                         uid=self.new_uuid()))
        except InvalidMessage as exc:
            return step.note("cannot answer", detail=str(exc))
        step.note("sent", kind="error", to=to, what=what, error=reason, run_id=run_id)

    def _join(self, init, doc, step):
        where = {"kind": INIT, "from": init["from"], "run_id": init["run_id"]}
        if self.me not in init["participants"]:
            step.note("ignored", reason="not a participant", **where)
            return False
        if active_run(self.state) is not None:
            step.note("ignored", reason="busy", **where)
            self._error(step, init["from"], init["run_id"], "init", "busy", text="I am in another run.")
            return False
        self.state["runs"][init["run_id"]] = new_run(self.me, init, doc)
        self.state["active"] = init["run_id"]
        step.note("joined", initiator=init["from"], participants=init["participants"], run_id=init["run_id"])
        return True

    def _record(self, run, msg, doc, step, resent=False):
        """Record the valid message `msg` (the text `doc`) of a known run. Returns whether to keep its uuid."""
        kind, author, where = msg["kind"], msg["from"], {"kind": msg["kind"], "from": msg["from"], "run_id": run["run_id"]}
        if kind == "init":
            return self._known_init(run, resent, step, where)
        if kind == "error":
            return self._record_error(run, msg, step)
        if kind == "cancel":
            return self._record_cancel(run, msg, step)
        if run["status"] != RUN_ACTIVE:
            step.note("ignored", reason="run over", **where)
            return False
        if kind == "repair" and author in run["participants"]:
            return self._record_repair(run, msg, doc, step)
        if kind == "result":
            return self._record_result(run, msg, doc, step)
        if kind == "init-reply" and author in run["participants"]:
            return self._record_init_reply(run, msg, doc, step)
        if kind == "score" and author in run["participants"]:
            return self._record_score(run, msg, doc, step)
        step.note("ignored", reason="not handled", **where)
        return False

    def _known_init(self, run, resent, step, where):
        """An init for a run the agent knows is ignored, unless it comes inside a resend: the initiator is waiting for init replies,
        and a participant that has sent its own sends it again."""
        key = item_key("init-reply", self.me)
        if not resent or key not in run["sent"]:
            step.note("ignored", reason="run already known", **where)
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
            stored = next((t for t in run["sent"].values() if peek(t)["attrs"]["uuid"] == msg["ref"]), None)
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
                                                                                 "superseded_by": superseded_by}
            run["timers"], run["held"] = {}, []
            step.note("aborted", **why)
        elif run["status"] == RUN_ENDED:
            run["disputed"] = True
            step.note("disputed", **why)
        if not run["cancel_sent"]:
            run["cancel_sent"] = True
            step.send.append(build_cancel(self.me, run["run_id"], reason, superseded_by=superseded_by, agent=agent,
                                          uid=self.new_uuid()))
            step.note("sent", kind="cancel", **why)

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
        if key in run["received"] or key in run["sent"]:
            step.note("ignored", reason="already recorded", **where)
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

    def _record_result(self, run, msg, doc, step):
        """The result counts only from the reporter, and its winner must be a candidate of the run. Until this agent has computed
        its own outcome it cannot say whether the winner exists: None says the result is to be held (see `_accept`)."""
        author, winner = msg["from"], msg["winning_candidate_id"]
        where = {"kind": "result", "from": author}
        if author != run["participants"][0]:
            step.note("ignored", reason="not the reporter", **where)
            return False
        if run["reported"] is not None:
            step.note("ignored", reason="already recorded", **where)
            return True
        if winner not in {c["id"] for c in run["engine"]["pool"]}:
            if run["outcome"] is None:
                return None
            step.note("ignored", reason="invalid", detail=f"{winner} is not a candidate of the run", **where)
            return False
        run["reported"] = {"outcome": msg["outcome"], "winning_candidate_id": winner}
        run["received"][item_key("result", author)] = doc
        step.note("recorded", outcome=msg["outcome"], winner=winner, **where)
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
        self._replay_held(run, step)
        ev = evaluate(run["engine"])
        if ev["state"] == "done" and run["outcome"] is None:
            run["outcome"] = ev["outcome"]
            step.note("outcome", outcome=ev["outcome"], winner=ev["x_star"], run_id=run["run_id"])
        elif ev["state"] == "repair":
            step.note("repair owed", bottleneck=ev["bottleneck"], round=ev["round"], run_id=run["run_id"])
        if run["outcome"] is not None and run["status"] == RUN_ACTIVE:
            self._confirm(run, ev, step)
        self._sync_timers(run, step)

    def _replay_held(self, run, step):
        """Judge again the repairs and results that were held, now that the run state may be complete."""
        moved = True
        while moved and run["held"]:
            moved = False
            for doc in list(run["held"]):
                kept = self._record(run, parse_message(doc, run["schema"]), doc, step)
                if kept is None:
                    continue
                run["held"].remove(doc)
                moved = True
                uid = peek(doc)["attrs"]["uuid"]
                if kept and uid not in run["uuids"]:
                    run["uuids"].append(uid)

    def _confirm(self, run, ev, step):
        """The reporter ends its run by sending its result; another participant ends it when it holds that result, and a result
        that differs from its own outcome makes it send a mismatch error. A result never changes the outcome."""
        reporter, where = run["participants"][0], {"run_id": run["run_id"]}
        if self.me == reporter:
            text = build_result(self.me, run["run_id"], ev["outcome"], ev["x_star"], uid=self.new_uuid())
            run["sent"][item_key("result", self.me)] = text
            step.send.append(text)
            step.note("sent", kind="result", **where)
        elif run["reported"] is not None:
            got = (run["reported"]["outcome"], run["reported"]["winning_candidate_id"])
            if got != (ev["outcome"], ev["x_star"]):
                text = build_error(self.me, run["run_id"], reporter, "result", "mismatch", uid=self.new_uuid(),
                                   text=f"My outcome is {ev['outcome']} with {ev['x_star']}; the result says {got[0]} with {got[1]}.")
                run["mismatches"]["sent"].append(text)
                step.send.append(text)
                step.note("mismatch", mine=[ev["outcome"], ev["x_star"]], theirs=list(got), **where)
        else:
            return
        run["status"] = RUN_ENDED
        step.note("ended", outcome=run["outcome"], **where)

    # ------------------------------------------------------------------
    # the messages owed, and the timers
    # ------------------------------------------------------------------

    def _owed(self, run):
        """{timer key: {author, what, candidate}}: the batches of messages this agent is owed now, worked out from the state (see
        Messages owed in the spec). A batch is owed until nothing in it is lacking; the `candidate` is the first one lacking, which
        is what a request names."""
        out = {}
        if run["role"] != PARTICIPANT or run["status"] != RUN_ACTIVE:
            return out
        st, me, reporter = run["engine"], self.me, run["participants"][0]

        def owe(key, author, what, candidate=None):
            out[key] = {"author": author, "what": what, "candidate": candidate}

        for a in run["participants"]:
            if a != me and a not in st["anchors"]:
                owe(f"init-reply|{a}", a, "init-reply")
        if len(st["anchors"]) == len(st["agents"]):
            for a, cid in lacking(st):
                if a != me:
                    batch = "seeds" if parse_candidate_id(cid)[0] == 0 else cid
                    out.setdefault(f"score|{a}|{batch}", {"author": a, "what": "score", "candidate": cid})
        have = {c["id"] for c in st["pool"]}
        for cid in run["expected"]:
            rnd, author = parse_candidate_id(cid)
            if cid not in have:
                if rnd == 0:
                    owe(f"init-reply|{author}", author, "init-reply")
                else:
                    owe(f"repair|{author}|{rnd}", author, "repair", cid)
        ev = evaluate(st)
        if ev["state"] == "repair" and ev["bottleneck"] != me:
            owe(f"repair|{ev['bottleneck']}|{ev['round']}", ev["bottleneck"], "repair", f"r{ev['round']}-{ev['bottleneck']}")
        if run["outcome"] not in (None, ABORTED) and me != reporter and run["reported"] is None:
            owe(f"result|{reporter}", reporter, "result")
        return out

    def _sync_timers(self, run, step):
        """Start the timer of every batch that has become owed, and drop the timer of every batch that is complete. The timers are
        in the state, so a restart keeps them. With a `timeout` of 0 they have no deadline: nothing is ever triggered."""
        owed, timers = self._owed(run), run["timers"]
        for key in [k for k in timers if k not in owed]:
            del timers[key]
        for key, o in owed.items():
            if key not in timers:
                deadline = step.now + run["params"]["timeout"] if timers_enabled(run["params"]) else None
                timers[key] = {"requests": 0, "asked": False, "deadline": deadline}
            timers[key].update(o)

    def _request(self, run, key, step):
        """Ask the author of the batch for the first message it lacks, and restart the timer. It counts against `retries`."""
        t = run["timers"][key]
        t["requests"] += 1
        if timers_enabled(run["params"]):
            t["deadline"] = step.now + run["params"]["timeout"]
        step.note("asked", author=t["author"], what=t["what"], candidate=t["candidate"], request=t["requests"], run_id=run["run_id"])
        self._error(step, t["author"], run["run_id"], t["what"], "missing", candidate=t["candidate"])

    def _lacking_keys(self, run):
        return [k for k, o in self._owed(run).items() if o["what"] != "result"]

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
            expired = sorted((t["deadline"], key) for key, t in run["timers"].items() if t["deadline"] <= step.now)
            for _, key in expired:
                t = run["timers"].get(key)
                if t is None:
                    break                                   # the run was aborted
                if t["requests"] >= run["params"]["retries"]:
                    self._abort(run, step, "missing-agent", agent=t["author"])
                    break
                self._request(run, key, step)
        self._save()
        return step

    def next_wakeup(self):
        """The time of the earliest timer of an active run, or None: when to call `tick` next."""
        due = [t["deadline"] for run in self.state["runs"].values() if run["status"] == RUN_ACTIVE
               for t in run["timers"].values() if t["deadline"] is not None]
        return min(due) if due else None

    # ------------------------------------------------------------------
    # what the model must write
    # ------------------------------------------------------------------

    def due(self):
        """The tasks the agent's model owes now, as dicts with a `kind`:

        init-reply  run_id, initiator, context, x_reject, x_ideal, schema: anchors, a seed and a score for it
        score       run_id, candidates [{id, from, candidate, rationale, reasoning}]: a rating for each, on the same scale
        repair      run_id, round, incumbent {id, candidate, rationale}, threshold, floor, scores {agent: normalized score of the
                    incumbent}, shortfall {agent: how far below the threshold}, reasoning {agent: what it said about the
                    incumbent}, repairs_left: a better candidate, which only the bottleneck of the round writes

        The numbers in a task are normalized scores between 0 and 1, as text with at most 6 decimal places.
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
            return self._repair_task(run)
        by_id = {c["id"]: c for c in st["pool"]}
        return [{"kind": "score", "run_id": run["run_id"], "candidates": [
            {"id": cid, "from": by_id[cid]["proposer"], "candidate": by_id[cid]["candidate"],
             "rationale": by_id[cid]["rationale"], "reasoning": st["reasoning"][by_id[cid]["proposer"]].get(cid, "")}
            for cid in owed]}]

    def _repair_task(self, run):
        ev = evaluate(run["engine"])
        if ev["state"] != "repair" or ev["bottleneck"] != self.me or item_key("repair", self.me, f"r{ev['round']}-{self.me}") in run["sent"]:
            return []
        st, x = run["engine"], ev["x_star"]
        inc = next(c for c in st["pool"] if c["id"] == x)
        text = lambda d: {a: format_number(v) for a, v in d.items()}
        return [{"kind": "repair", "run_id": run["run_id"], "round": ev["round"],
                 "incumbent": {"id": x, "candidate": inc["candidate"], "rationale": inc["rationale"]},
                 "threshold": format_number(st["params"]["satisfaction_threshold"]), "floor": format_number(ev["floor"]),
                 "scores": text(ev["scores"]), "shortfall": text(ev["shortfall"]),
                 "reasoning": {a: st["reasoning"][a].get(x, "") for a in st["agents"]},
                 "repairs_left": st["params"]["B"] - ev["t"]}]

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
        run["sent"][item_key("repair", self.me, p["id"])] = text
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
        if run is None:
            return None
        ev = evaluate(run["engine"])
        return {"run_id": run["run_id"], "role": run["role"], "status": run["status"], "outcome": run["outcome"],
                "state": ev["state"], "x_star": ev["x_star"], "floor": ev["floor"], "scores": ev["scores"],
                "bottleneck": ev["bottleneck"], "round": ev["round"], "reporter": ev["reporter"],
                "lacking": lacking(run["engine"]), "anchors_in": sorted(run["engine"]["anchors"]),
                "timers": {k: t["deadline"] for k, t in run["timers"].items()}}

    def report(self, run_id=None):
        """The report of a run (the active one when `run_id` is None; None when there is none), as the spec describes it:
        `outcome`, `winner` ({id, candidate}), `scores` and `shortfall` (millionths), `rounds`, `reason`, `results_compared` (the
        reporter's result and whether it matches this agent's outcome; for the reporter, its own) and `status`: "pending" until
        the run has ended for this agent, then "confirmed", or "disputed" when a mismatch was found, sent or received or a cancel
        reached it after its run had ended. An aborted run has outcome ABORTED, no winner, its `reason` ({reason, agent,
        superseded_by}) and the status "aborted": the spec gives it none."""
        run = self.state["runs"].get(run_id or self.state["active"])
        if run is None:
            return None
        ev = evaluate(run["engine"])
        done = run["outcome"] not in (None, ABORTED)
        winner = next((c for c in run["engine"]["pool"] if c["id"] == ev["x_star"]), None) if done else None
        reporter = run["participants"][0]
        mine = (run["outcome"], ev["x_star"]) if done else None
        if self.me == reporter:
            compared = {"reporter": reporter, "result": {"outcome": run["outcome"], "winning_candidate_id": ev["x_star"]} if done else None,
                        "matches": True if done else None}
        else:
            rep = run["reported"]
            compared = {"reporter": reporter, "result": rep,
                        "matches": None if rep is None or not done else mine == (rep["outcome"], rep["winning_candidate_id"])}
        mismatches = run["mismatches"]
        if run["status"] == RUN_ABORTED:
            status = "aborted"
        elif mismatches["sent"] or mismatches["received"] or run["disputed"]:
            status = "disputed"
        else:
            status = "confirmed" if run["status"] == RUN_ENDED else "pending"
        return {"run_id": run["run_id"], "outcome": run["outcome"],
                "winner": {"id": winner["id"], "candidate": winner["candidate"]} if winner else None,
                "scores": ev["scores"] if done else {}, "shortfall": ev["shortfall"] if done else {}, "rounds": ev["t"],
                "reason": run["abort"], "results_compared": compared,
                "mismatches": {"sent": len(mismatches["sent"]), "received": list(mismatches["received"])}, "status": status}
