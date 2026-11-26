#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The coordinator of the orchestrated scenarios: a fifth model, with no facts and no vote, that opens the session and runs it.

  O1  `StarFreeOrchestrator`: free speech over a star: the coordinator passes every post on with `delegate_to` (the harness sends the text and
      puts the author in front). A leader that is done sends its final choice to the coordinator; when all are done (or after 60 s without a
      post, 10 minutes or 100 posts) it closes, and a model summarizes the outcome.
  O2  `FreeOrchestrator`: the same over a SLIM group session: every post reaches everyone, so there is nothing to pass on.
  O3  `ConcordOrchestrator`: the initiator of a CONCORD run (over SLIM) that is not a participant: its model writes the init once the leaders
      have posted what they know; the result is the one of the run.
"""
import json
import time

from ioc_concord.agent import Agent
from ioc_concord.errors import ConcordError
from ioc_concord.store import FileStore

from blog_v2.agent import DEFAULT_THRESHOLD, SHARE_WAIT
from blog_v2.common import MAX_TOOL_ROUNDS, NO_DECISION, ORCH, chat, flush, new_usage, note, pick, say, sender_of, write_record
from blog_v2.prompts import (CLOSED, DELEGATE, DONE_PREFIX, HUB_ALREADY, HUB_BAD_CALL, HUB_MESSAGE,
                             HUB_NOT_PASSED_ON, HUB_PASSED_ON, HUB_POST_PREFIX, POST_LINE, SHARED_HEADING, SUMMARY_NO_OUTCOME, SUMMARY_NOBODY,
                             SUMMARY_RETRY, SUMMARY_USER, coordinator_start_ask, coordinator_start_tool, lex, localize, opening, orchestrator_prompt,
                             report_outcome_tool)
from blog_v2.scenario import AGENTS
from blog_v2.transport import SessionClosed

DONE_WAIT = 60                  # seconds to wait for the final choice of the leaders that are not done when the coordinator closes
MAX_ROUTING_ROUNDS = 3


class Orchestrator:
    me = ORCH

    def __init__(self, group, scenario, out_dir, llm, model, variant, temperature=None, config=None, log=print):
        self.group, self.scenario, self.out_dir, self.log = group, scenario, out_dir, log
        self.llm, self.model, self.temperature = llm, model, temperature
        self.system = orchestrator_prompt(scenario, variant)
        self.config = config or {}
        out_dir.mkdir(parents=True, exist_ok=True)
        self.channel = []                                  # what the leaders posted: {"t", "from", "text"}
        self.events = []
        self.messages = []                                 # the conversation of the coordinator's model
        self.usage = new_usage()
        self.report = None

    def _record(self, who, text, t=None):
        self.channel.append({"t": t or time.time(), "from": who, "text": text})

    def _leader(self, got):
        """The leader that wrote a message, or None."""
        who = sender_of(got[0])
        return who if who in AGENTS else None

    async def _recv(self):
        try:
            return await self.group.recv(timeout=1.0)
        except SessionClosed:
            self.log("the session is closed")
            raise

    def finish(self, **extra):
        write_record(self.out_dir, self.me, role="orchestrator", config=self.config, report=self.report, usage=self.usage, events=self.events,
                     channel=self.channel, transcript=self.messages, **extra)
        return self.report


# ---------------------------------------------------------------------------------------------------------------------------
# O2 (and O1 with a routing hook): free speech, each leader says when it is done
# ---------------------------------------------------------------------------------------------------------------------------

class FreeOrchestrator(Orchestrator):
    def __init__(self, *a, variant="free_slim", idle=60, max_time=600, max_posts=100, **kw):
        super().__init__(*a, variant=variant, **kw)
        self.idle, self.max_time, self.max_posts = idle, max_time, max_posts
        self.dones = {}                                    # leader -> {"option", "rationale"}
        self.close = None

    async def _on_post(self, origin, text):
        """Nothing to do for a post on a shared channel: every leader has it already. (Over a star it is passed on here.)"""

    def _take(self, who, text):
        """A message of a leader: its final choice (it tells the coordinator it is done) or a post."""
        if text.startswith(DONE_PREFIX):
            try:
                self.dones[who] = json.loads(text[len(DONE_PREFIX):])
            except ValueError:
                self.dones[who] = {"option": NO_DECISION, "rationale": text[len(DONE_PREFIX):]}
            note(self.events, self.log, "done", leader=who, **self.dones[who])
            return False
        return True

    async def run(self):
        text = opening(self.scenario, "free_coord")
        start = last = time.time()
        await self.group.send(text)
        self._record(self.me, text, start)
        posts, reason = 0, None
        while reason is None:
            try:
                got = await self._recv()
            except SessionClosed:
                reason = "session closed"
                break
            now = time.time()
            who = got and self._leader(got)
            if who:
                if self._take(who, got[1]):
                    posts += 1
                    self._record(who, got[1], now)
                    self.log(f"post {posts} from {who}")
                    await self._on_post(who, got[1])
                last = time.time()                      # what the hook took (a model turn) is not silence
            if len(self.dones) == len(AGENTS):
                reason = "all done"
            elif posts >= self.max_posts:
                reason = "max posts"
            elif now - start >= self.max_time:
                reason = "max time"
            elif now - last >= self.idle:
                reason = "idle"
        if reason != "all done":                         # close, and ask whoever is not done for its final choice
            await self.group.send(CLOSED)
            deadline = time.time() + DONE_WAIT
            while len(self.dones) < len(AGENTS) and time.time() < deadline:
                try:
                    got = await self._recv()
                except SessionClosed:
                    break
                who = got and self._leader(got)
                if who:
                    self._take(who, got[1])
        self.close = {"reason": reason, "posts": posts, "seconds": round(time.time() - start, 1)}
        self.log(f"closing: {self.close}")
        await self._summarize()

    async def _summarize(self):
        """A model reads the discussion and the final choices, and reports the outcome."""
        options = self.scenario.options
        transcript = "\n\n".join(POST_LINE.format(who=m["from"], text=m["text"]) for m in self.channel if m["from"] in AGENTS) or SUMMARY_NOBODY
        outcomes = "\n\n".join(f"{w}: {d['option']}. {d.get('rationale', '')}" for w, d in self.dones.items()) or SUMMARY_NO_OUTCOME
        messages = [{"role": "user", "content": [{"type": "text", "text": SUMMARY_USER.format(transcript=transcript, outcomes=outcomes)}]}]
        system = orchestrator_prompt(self.scenario, "summary")
        self.report = {"outcome": "NOT_AGREED", "option": None, "close": self.close, "dones": self.dones}
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await chat(self.llm, self.model, system, [localize(self.scenario, report_outcome_tool(options))], messages, self.usage, self.temperature,
                              max_tokens=1024, log=self.log)
            for block in resp.content:
                if block.type == "tool_use" and pick(self.scenario, block.input.get("option")) is not None:
                    option = pick(self.scenario, block.input["option"])
                    agreed = bool(block.input.get("agreed")) and option != NO_DECISION
                    self.report.update(outcome="AGREED" if agreed else "NOT_AGREED", option=option if agreed else None,
                                       summary=block.input.get("summary", ""))
                    return
            messages.append({"role": "user", "content": [{"type": "text", "text": SUMMARY_RETRY}]})

    def finish(self, **extra):
        return super().finish(close=self.close, dones=self.dones, **extra)


class HubRouting:
    """What the centre of a star does with its model: for each post of a leader, the model picks names with `delegate_to` and the harness sends
    the post to them (the text is never touched by the model). What it got wrong is recorded as events: `misrouted` (a recipient missing, the
    sender, an unknown name, a message passed on twice) and `dropped` (a message it never passed on)."""

    def _init_hub(self):
        self.participants = list(AGENTS)
        self.sent_text = {}                          # message id -> (sender, text)

    async def _route(self, mid):
        origin, text = self.sent_text[mid]
        pending = {mid}
        say(self.messages, [{"type": "text", "text": HUB_MESSAGE.format(mid=mid, origin=origin, text=text)}])
        for _ in range(MAX_ROUTING_ROUNDS):
            resp = await chat(self.llm, self.model, self.system, [localize(self.scenario, DELEGATE)], self.messages, self.usage, self.temperature, max_tokens=1024, log=self.log)
            answer = [{"type": "tool_result", "tool_use_id": b.id, **await self._delegate(b.input, pending)}
                      for b in resp.content if b.type == "tool_use"]
            if pending:
                answer.append({"type": "text", "text": HUB_NOT_PASSED_ON.format(ids=", ".join(sorted(pending)))})
            say(self.messages, answer)
            if not pending:
                return
        for m in sorted(pending):
            note(self.events, self.log, "dropped", message=m, sender=self.sent_text[m][0])

    async def _delegate(self, args, pending):
        mid = args.get("message_id") if isinstance(args, dict) else None
        names = args.get("recipients") if isinstance(args, dict) else None
        if mid not in self.sent_text or not isinstance(names, list):
            return {"content": lex(self.scenario, HUB_BAD_CALL), "is_error": True}
        sender, text = self.sent_text[mid]
        names = [str(n) for n in names]
        if mid not in pending:                       # a second call for a message already passed on would send it again
            note(self.events, self.log, "misrouted", message=mid, sender=sender, missing=[], extra=names, repeated=True)
            return {"content": HUB_ALREADY.format(mid=mid), "is_error": True}
        valid = [n for n in dict.fromkeys(names) if n in self.participants and n != sender]
        missing = [n for n in self.participants if n != sender and n not in valid]
        extra = [n for n in names if n not in valid]
        await self.group.send_to(valid, HUB_POST_PREFIX.format(who=sender) + text)
        pending.discard(mid)
        note(self.events, self.log, "delegated", message=mid, sender=sender, to=valid)
        if missing or extra:
            note(self.events, self.log, "misrouted", message=mid, sender=sender, missing=missing, extra=extra)
        return {"content": HUB_PASSED_ON.format(names=", ".join(valid) or "nobody")}


class StarFreeOrchestrator(HubRouting, FreeOrchestrator):
    """O1: the centre of a star for free speech. A leader only sees the centre, so the harness puts the author in front of each post."""

    def __init__(self, *a, **kw):
        super().__init__(*a, variant="free_star", **kw)
        self._init_hub()

    async def _on_post(self, origin, text):
        mid = f"m{len(self.sent_text) + 1}"
        self.sent_text[mid] = (origin, text)
        await self._route(mid)

    def finish(self):
        return super().finish(routed=self.sent_text)


# ---------------------------------------------------------------------------------------------------------------------------
# O3: CONCORD, the coordinator is the initiator and not a participant
# ---------------------------------------------------------------------------------------------------------------------------

class ConcordOrchestrator(Orchestrator):
    linger = 4                                       # seconds to stay after the last message of an ended run: a resend may be asked for

    def __init__(self, *a, threshold=None, timeout=None, **kw):
        super().__init__(*a, variant="concord", **kw)
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self.timeout = timeout
        store = FileStore(self.out_dir / f"{self.me}-state.json")
        store.path.unlink(missing_ok=True)
        self.node = Agent(self.me, store=store)
        self.started = False
        self.last_refusal = None

    async def _share(self):
        """Step 1: open the session, and wait until each leader has posted once, or SHARE_WAIT seconds."""
        text = opening(self.scenario, "concord_coord")
        start = time.time()
        await self.group.send(text)
        self._record(self.me, text, start)
        posted = {}
        while len(posted) < len(AGENTS) and time.time() - start < SHARE_WAIT:
            try:
                got = await self._recv()
            except SessionClosed:
                break
            who = got and self._leader(got)
            if who and who not in posted and not got[1].lstrip().startswith("<"):
                posted[who] = got[1]
                self._record(who, got[1])
                self.log(f"step 1: post from {who}")
        self.log(f"step 1 over: {len(posted)} of {len(AGENTS)} posted in {time.time() - start:.0f}s")
        return posted

    def _schema(self, args):
        """The candidate schema of the run: the options the initiator copied, or the open format of the scenario."""
        o = self.scenario.options
        return ({"type": "string", "pattern": o.pattern, "description": o.description} if o.open else {"enum": list(args["options"])})

    async def _start(self, args):
        """The tool of the coordinator: open the run with what its model wrote. Returns an error text, or None."""
        open_format = self.scenario.options.open       # the harness fixes the candidate schema of an open format: no list to write
        need = ("participants", "context", "x_reject", "x_ideal", "satisfaction_threshold") + (() if open_format else ("options",))
        if not isinstance(args, dict) or any(args.get(k) in (None, "", []) for k in need):
            return "concord_start needs: " + ", ".join(need) + "."
        if sorted(args["participants"]) != sorted(AGENTS):
            return lex(self.scenario, f"the participants must be exactly the four leaders: {', '.join(AGENTS)}.")
        if not open_format and sorted(args["options"]) != sorted(self.scenario.options):
            return f"the options must be exactly, as written: {'; '.join(self.scenario.options)}."
        threshold = args["satisfaction_threshold"] if self.threshold_fixed is None else self.threshold_fixed
        if self.threshold_fixed is not None and args.get("satisfaction_threshold") != self.threshold_fixed:
            self.log(f"threshold fixed to {self.threshold_fixed} (the model asked for {args.get('satisfaction_threshold')})")
        params = {"satisfaction_threshold": threshold}
        if self.timeout is not None:
            params["timeout"] = self.timeout
        try:
            step = self.node.start(list(args["participants"]), args["context"], args["x_reject"], args["x_ideal"],
                                   self._schema(args), params=params)
        except ConcordError as e:
            return str(e)
        self.started = True
        self.log(f"start the run: participants {args['participants']}, threshold {threshold}")
        await flush(step, self.group, self.events, self.log)
        return None

    async def run(self):
        self.threshold_fixed = self.threshold if self.config.get("threshold") is not None else None
        posted = await self._share()
        body = "\n\n".join(POST_LINE.format(who=w, text=t) for w, t in posted.items())
        heard = lex(self.scenario, "The leaders have posted." if len(posted) == len(AGENTS)
                    else f"{len(posted)} of the {len(AGENTS)} leaders have posted; the wait is over.")
        ask = coordinator_start_ask(self.scenario, heard,
                                    f" Use satisfaction_threshold {self.threshold_fixed}." if self.threshold_fixed is not None else "")
        say(self.messages, [{"type": "text", "text": f"{lex(self.scenario, SHARED_HEADING)}\n\n{body}\n\n{ask}" if body else ask}])
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await chat(self.llm, self.model, self.system, [localize(self.scenario, coordinator_start_tool(self.scenario.options))], self.messages,
                              self.usage, self.temperature, log=self.log)
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    self.log(f"tool {block.name} {json.dumps(block.input, ensure_ascii=False)[:600]}")
                    error = await self._start(block.input) if block.name == "concord_start" else f"{block.name} is not a tool you have now."
                    self.last_refusal = error or self.last_refusal
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": error or "sent", "is_error": bool(error)})
            say(self.messages, results or [{"type": "text", "text": "Answer with concord_start."}])
            if self.started:
                break
        if not self.started:
            raise RuntimeError(f"the coordinator could not start the run; last refusal: {self.last_refusal}")
        linger_until = None
        while True:
            try:
                got = await self._recv()
            except SessionClosed:
                break
            if got and got[1].lstrip().startswith("<"):
                await flush(self.node.receive(got[1]), self.group, self.events, self.log)
            await flush(self.node.tick(), self.group, self.events, self.log)
            status = self.node.status()
            if status and status["status"] != "active":
                if got or linger_until is None:
                    linger_until = time.time() + self.linger
                if time.time() > linger_until:
                    break

    def finish(self, **extra):
        self.report = self.node.report()
        write_record(self.out_dir, self.me, role="orchestrator", config=self.config, report=self.report, status=self.node.status(),
                     usage=self.usage, events=self.events, channel=self.channel, transcript=self.messages, **extra)
        return self.report
