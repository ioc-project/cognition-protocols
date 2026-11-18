#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The facilitator of a CONCORD run (and the centres of the star): the initiator, which is not a participant. It posts the opening of step 1
(each participant posts what it knows, in free text), waits for the three posts, opens the run with fixed content (from the scenario),
and then only keeps the node going (receive, tick) until the run is over. It writes nothing else to the discussion.

`ConcordFacilitator` has no model (scenario D, over SLIM). `DelegatingFacilitator` is the centre of a star (scenario C): a model says who
gets each message of a spoke, since there is no Relay. `DelegatingFreeFacilitator` is the centre of a star for free speech (scenario A)."""
import time

from ioc_concord.agent import Agent
from ioc_concord.store import FileStore

from concord_comparison.common import chat, flush, new_usage, note, say, write_record
from concord_comparison.free import FreeFacilitator, sender_of
from concord_comparison.prompts import (DELEGATE, HUB_ALREADY, HUB_BAD_CALL, HUB_MESSAGE, HUB_NOT_PASSED_ON, HUB_PASSED_ON, HUB_POST_PREFIX,
                                        HUB_SYSTEM, opening)
from concord_comparison.transport import SessionClosed

DEFAULT_THRESHOLD = 0.6
SHARE_WAIT = 60                                      # seconds to wait for the three posts of step 1


class ConcordFacilitator:
    linger = 4                                       # seconds to stay after the last message of an ended run: a resend may be asked for

    def __init__(self, me, group, scenario, participants, out_dir, threshold=None, timeout=None, config=None, log=print):
        self.channel = []                                  # the opening and the posts of step 1: {"t", "from", "text"}
        self.me, self.group, self.scenario, self.participants, self.log = me, group, scenario, list(participants), log
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self.timeout = timeout
        self.out_dir = out_dir
        self.config = config or {}
        out_dir.mkdir(parents=True, exist_ok=True)
        store = FileStore(out_dir / f"{me}-state.json")
        store.path.unlink(missing_ok=True)
        self.node = Agent(me, store=store)
        self.events = []
        self.usage = new_usage()      # no model here

    async def _on_message(self, origin, text):
        await flush(self.node.receive(text), self.group, self.events, self.log)

    async def _on_share_post(self, origin, text):
        """Nothing to do for a post of step 1 on the channel: every participant has it already. (A hub passes it on here.)"""

    async def _share(self):
        """Step 1: post the opening, and wait until each participant has posted once, or SHARE_WAIT seconds."""
        text = opening(self.scenario, "concord")
        start = time.time()
        await self.group.send(text)
        self.channel.append({"t": start, "from": self.me, "text": text})
        posted = set()
        while posted != set(self.participants) and time.time() - start < SHARE_WAIT:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                break
            who = got and sender_of(got[0])
            if who in self.participants:
                posted.add(who)
                self.channel.append({"t": time.time(), "from": who, "text": got[1]})
                self.log(f"step 1: post from {who}")
                await self._on_share_post(who, got[1])
        self.log(f"step 1 over: {len(posted)} of {len(self.participants)} posted in {time.time() - start:.0f}s")

    async def run(self):
        await self._share()
        params = {"satisfaction_threshold": self.threshold}
        if self.timeout is not None:
            params["timeout"] = self.timeout
        init = self.scenario.init
        step = self.node.start(self.participants, init["context"], init["x_reject"], init["x_ideal"],
                               {"enum": list(self.scenario.options)}, params=params)
        self.log(f"start the run: participants {self.participants}, threshold {self.threshold}")
        await flush(step, self.group, self.events, self.log)
        linger_until = None
        while True:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                self.log("the session is closed")
                break
            if got:
                await self._on_message(*got)
            await flush(self.node.tick(), self.group, self.events, self.log)
            status = self.node.status()
            if status and status["status"] != "active":
                if got or linger_until is None:
                    linger_until = time.time() + self.linger
                if time.time() > linger_until:
                    break

    def finish(self, **extra):
        report = self.node.report()
        write_record(self.out_dir, self.me, role="facilitator", config=self.config, report=report, status=self.node.status(),
                     usage=self.usage, events=self.events, channel=self.channel, **extra)
        return report


# ---------------------------------------------------------------------------------------------------------------------------
# The centre of a star that routes with its model: no Relay, the model says who gets each message of a spoke.
# ---------------------------------------------------------------------------------------------------------------------------

MAX_ROUTING_ROUNDS = 3


class HubRouting:
    """What the centre of a star does with its model, for CONCORD and for free speech: for each message of a spoke, the model picks names
    with `delegate_to` and the harness sends the message to them. The text of the message is never touched by the model. What the model
    got wrong is recorded as events: `misrouted` (a recipient missing, the sender, an unknown name, a message passed on twice) and
    `dropped` (a message it never passed on)."""

    linger = 30                                      # a result still has to cross the hub, a model turn at a time

    def _init_hub(self, llm, model, temperature):
        self.llm, self.model, self.temperature = llm, model, temperature
        self.system = HUB_SYSTEM.format(participants=", ".join(self.participants))
        self.messages = []
        self.sent_text = {}                          # message id -> (sender, text)

    def _wire(self, sender, text):
        """What goes out to the recipients for a message of `sender`: the text as it is."""
        return text

    async def _route(self, mid):
        origin, text = self.sent_text[mid]
        pending = {mid}
        say(self.messages, [{"type": "text", "text": HUB_MESSAGE.format(mid=mid, origin=origin, text=text)}])
        for _ in range(MAX_ROUTING_ROUNDS):
            resp = await chat(self.llm, self.model, self.system, [DELEGATE], self.messages, self.usage, self.temperature, max_tokens=1024, log=self.log)
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
        """One call of delegate_to: send the message to the recipients that are participants, and record what is wrong."""
        mid = args.get("message_id") if isinstance(args, dict) else None
        names = args.get("recipients") if isinstance(args, dict) else None
        if mid not in self.sent_text or not isinstance(names, list):
            return {"content": HUB_BAD_CALL, "is_error": True}
        sender, text = self.sent_text[mid]
        names = [str(n) for n in names]
        if mid not in pending:                       # a second call for a message already passed on would send it again
            note(self.events, self.log, "misrouted", message=mid, sender=sender, missing=[], extra=names, repeated=True)
            return {"content": HUB_ALREADY.format(mid=mid), "is_error": True}
        valid = [n for n in dict.fromkeys(names) if n in self.participants and n != sender]
        missing = [n for n in self.participants if n != sender and n not in valid]
        extra = [n for n in names if n not in valid]
        await self.group.send_to(valid, self._wire(sender, text))
        pending.discard(mid)
        note(self.events, self.log, "delegated", message=mid, sender=sender, to=valid)
        if missing or extra:
            note(self.events, self.log, "misrouted", message=mid, sender=sender, missing=missing, extra=extra)
        return {"content": HUB_PASSED_ON.format(names=", ".join(valid) or "nobody")}


class DelegatingFacilitator(HubRouting, ConcordFacilitator):
    """Scenario A: the centre of a star for CONCORD. The original message is sent on, so the text is never touched."""

    def __init__(self, me, group, scenario, participants, out_dir, llm, model, temperature=None, **kw):
        super().__init__(me, group, scenario, participants, out_dir, **kw)
        self._init_hub(llm, model, temperature)

    def _wire(self, sender, text):
        """A CONCORD message goes on as it is. A free-text post (step 1) gets its author in front, as in free speech over a star,
        because a spoke only sees the centre."""
        return text if text.lstrip().startswith("<") else HUB_POST_PREFIX.format(who=sender) + text

    async def _on_message(self, origin, text):
        await super()._on_message(origin, text)      # the node checks and records it, as an initiator does
        mid = f"m{len(self.sent_text) + 1}"
        self.sent_text[mid] = (origin, text)
        await self._route(mid)

    async def _on_share_post(self, origin, text):
        mid = f"m{len(self.sent_text) + 1}"
        self.sent_text[mid] = (origin, text)
        await self._route(mid)

    def finish(self):
        return super().finish(transcript=self.messages, routed=self.sent_text)


class DelegatingFreeFacilitator(HubRouting, FreeFacilitator):
    """Scenario E: the centre of a star for free speech. It posts the opening, routes every post with its model, and does what the
    facilitator of free speech does (idle close, verdict). A spoke only sees the centre, so the harness (not the model) puts the
    author in front of each post it passes on: `[participant-b] text`."""

    def __init__(self, me, group, scenario, participants, out_dir, llm, model, temperature=None, **kw):
        self.participants = list(participants)
        super().__init__(me, group, scenario, out_dir, llm, model, temperature, **kw)
        self._init_hub(llm, model, temperature)
        self.events = []

    def _wire(self, sender, text):
        return HUB_POST_PREFIX.format(who=sender) + text

    async def _on_post(self, origin, text):
        mid = f"m{len(self.sent_text) + 1}"
        self.sent_text[mid] = (origin, text)
        await self._route(mid)

    def finish(self):
        return super().finish(events=self.events, transcript=self.messages, routed=self.sent_text)
