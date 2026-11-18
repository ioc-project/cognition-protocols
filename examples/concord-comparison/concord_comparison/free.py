#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Free speech on the same SLIM channel, with no protocol: the baseline of the comparison.

The facilitator posts the opening and then only watches. The participants read what arrives and, with a model turn, post or stay
silent as they please: there are no rounds and no turns. The facilitator closes the session when the channel has been quiet for a
while, or at a time or message limit, and says whether the participants agreed on one option. After the close each participant also
gives its final choice privately (it is never sent): it goes in its own record only.
"""
import json
import time

from concord_comparison.agent import MAX_TOOL_ROUNDS, client_from_env
from concord_comparison.common import NO_DECISION, chat, new_usage, note, say, write_record
from concord_comparison.prompts import (CLOSED, CLOSED_AND_ASK, EMPTY_POST, FREE_NO_TOOL, FREE_UNKNOWN_TOOL, OK, POST_LINE, VERDICT_NOBODY,
                                        VERDICT_RETRY, VERDICT_SYSTEM, VERDICT_USER, WRONG_OPTION, free_tools, opening, read_hub_post,
                                        verdict_tool)
from concord_comparison.scenario import AGENTS, FACILITATOR
from concord_comparison.transport import SessionClosed

DEBOUNCE = 2.5            # seconds without a new message before a participant reads what arrived (so that simultaneous replies are read together)
MAX_DEBOUNCE = 10.0       # but never wait longer than this for the first one that arrived


def sender_of(raw):
    """The agent name inside the SLIM name of a sender."""
    return next((a for a in AGENTS + (FACILITATOR,) if a in str(raw)), str(raw))


class FreeAgent:
    """A participant of a free discussion. One conversation, kept whole: what the others wrote comes in front of each turn."""

    def __init__(self, me, group, system, options, llm_model, out_dir, temperature=None, fresh=True, config=None, log=print, llm=None):
        self.me, self.group, self.system, self.options, self.model, self.log = me, group, system, list(options), llm_model, log
        self.temperature, self.out_dir = temperature, out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.tools = free_tools(options)
        self.llm = llm or client_from_env(fresh)
        self.config = config or {}
        self.messages, self.inbox, self.events = [], [], []
        self.usage = new_usage()
        self.final = None
        self.closed = False                             # the session went away while the model was thinking

    def _note(self, name, **detail):
        note(self.events, self.log, name, **detail)

    async def _turn(self, delivery, tool_names, handle):
        """Show `delivery`, then let the model call tools until `handle(name, args)` accepts one (it returns an error text or None)."""
        say(self.messages, [{"type": "text", "text": delivery}])
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await chat(self.llm, self.model, self.system, [self.tools[n] for n in tool_names], self.messages, self.usage,
                              self.temperature, log=self.log)
            results, accepted = [], False
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                self.log(f"tool {block.name} {json.dumps(block.input, ensure_ascii=False)[:600]}")
                error = await handle(block.name, block.input) if block.name in tool_names else FREE_UNKNOWN_TOOL.format(name=block.name)
                accepted = accepted or not error
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": error or OK, "is_error": bool(error)})
            if not results:
                results.append({"type": "text", "text": FREE_NO_TOOL})
            say(self.messages, results)
            if accepted:
                return True
        return False

    async def _discuss(self, name, args):
        if name == "post":
            text = str(args.get("text") or "").strip()
            if not text:
                return EMPTY_POST
            try:
                await self.group.send(text)
            except Exception as e:                      # the facilitator closed the session while the model was still writing this
                self._note("post_lost", text=text, error=str(e)[:200])
                self.closed = True
                return None
            self._note("posted", text=text)
        else:
            self._note("silent", reason=str(args.get("reason") or ""))
        return None

    async def _finalize(self, name, args):
        if args.get("option") not in self.options + [NO_DECISION]:
            return WRONG_OPTION.format(options=", ".join(self.options + [NO_DECISION]))
        self.final = {"option": args["option"], "rationale": args.get("rationale", "")}
        self._note("final_answer", **self.final)
        return None

    def _delivery(self):
        text = "\n\n".join(POST_LINE.format(who=who, text=what) for who, what in self.inbox)
        self.inbox = []
        return text

    async def run(self):
        first = last = None
        while True:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                self.log("the session is closed")
                break
            now = time.time()
            if got:
                who, text = sender_of(got[0]), got[1]
                if who == "hub":                        # a star: the centre says who wrote it, in front of the text
                    who, text = read_hub_post(text) or (FACILITATOR, text)
                if who == self.me:
                    continue
                if who == FACILITATOR and text == CLOSED:
                    break
                self.inbox.append((who, text))
                first, last = first or now, now
            if self.inbox and (now - last >= DEBOUNCE or now - first >= MAX_DEBOUNCE):
                first = last = None
                await self._turn(self._delivery(), ["post", "stay_silent"], self._discuss)
                if self.closed:
                    self.log("the session closed during a turn")
                    break
        delivery = self._delivery()
        await self._turn(f"{delivery}\n\n{CLOSED_AND_ASK}" if delivery else CLOSED_AND_ASK, ["final_answer"], self._finalize)

    def finish(self):
        write_record(self.out_dir, self.me, config=self.config, final=self.final, usage=self.usage, events=self.events,
                     transcript=self.messages)
        return self.final


class FreeFacilitator:
    """Posts the opening, watches, closes the session, and reports whether the participants agreed (and on what)."""

    def __init__(self, me, group, scenario, out_dir, llm, model, temperature=None, idle=60, max_time=600, max_posts=100, config=None,
                 log=print):
        self.me, self.group, self.scenario, self.out_dir, self.log = me, group, scenario, out_dir, log
        self.llm, self.model, self.temperature = llm, model, temperature
        self.idle, self.max_time, self.max_posts = idle, max_time, max_posts
        self.config = config or {}
        out_dir.mkdir(parents=True, exist_ok=True)
        self.channel = []                               # every message: {"t", "from", "text"}
        self.close = None
        self.usage = new_usage()
        self.verdict = None

    def _record(self, who, text, t):
        self.channel.append({"t": t, "from": who, "text": text})

    async def run(self):
        text = opening(self.scenario, "free")
        start = last = time.time()
        await self.group.send(text)
        self._record(self.me, text, start)
        posts, reason = 0, None
        while reason is None:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                reason = "session closed"
                break
            now = time.time()
            if got and sender_of(got[0]) != self.me:
                posts += 1
                last = now
                self._record(sender_of(got[0]), got[1], now)
                self.log(f"post {posts} from {sender_of(got[0])}")
                await self._on_post(sender_of(got[0]), got[1])
                last = time.time()                      # what the hook took (a hub's model turn) is not silence
            if posts >= self.max_posts:
                reason = "max posts"
            elif now - start >= self.max_time:
                reason = "max time"
            elif now - last >= self.idle:
                reason = "idle"
        self.close = {"reason": reason, "posts": posts, "seconds": round(time.time() - start, 1)}
        self.log(f"closing: {self.close}")
        await self.group.send(CLOSED)
        await self._decide()

    async def _on_post(self, origin, text):
        """Nothing to do for a post on the channel: every participant has it already. (A hub passes it on here.)"""

    async def _decide(self):
        options = list(self.scenario.options)
        tool = verdict_tool(options)
        transcript = "\n\n".join(POST_LINE.format(who=m["from"], text=m["text"]) for m in self.channel) or VERDICT_NOBODY
        system = VERDICT_SYSTEM.format(participants=", ".join(AGENTS))
        messages = [{"role": "user", "content": [{"type": "text", "text": VERDICT_USER.format(transcript=transcript)}]}]
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await chat(self.llm, self.model, system, [tool], messages, self.usage, self.temperature, max_tokens=512, log=self.log)
            for block in resp.content:
                if block.type == "tool_use" and block.input.get("option") in options + [NO_DECISION]:
                    agreed = bool(block.input.get("agreed")) and block.input["option"] != NO_DECISION
                    self.verdict = {"outcome": "AGREED" if agreed else "NOT_AGREED", "option": block.input["option"] if agreed else None}
                    return
            messages.append({"role": "user", "content": [{"type": "text", "text": VERDICT_RETRY}]})
        self.verdict = {"outcome": "NOT_AGREED", "option": None, "note": "no verdict from the model"}

    def finish(self, **extra):
        report = {**(self.verdict or {"outcome": "NOT_AGREED", "option": None}), "close": self.close}
        write_record(self.out_dir, self.me, role="facilitator", config=self.config, report=report, usage=self.usage, channel=self.channel,
                     **extra)
        return report
