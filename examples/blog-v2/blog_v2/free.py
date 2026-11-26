#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Free speech among four agents, with no protocol, no facilitator and no hub: the baseline of the comparison.

Every agent is told the same opening and posts once (step 1). Then each reads what arrives and, with a model turn, posts or stays
silent as it pleases: there are no rounds and no turns. Every post goes to all the other agents (a broadcast on SLIM, a write on each
link of the mesh). Nobody closes the discussion for the others: each agent stops by itself when it has seen nothing (a post of
anyone, its own included) for `idle` seconds, or after `max_time` seconds, or after `max_posts` posts, and then gives its final
choice privately (it is never sent): it goes in its own record only. The outcome of a run is read from those final choices.
"""
import json
import time

from blog_v2.agent import client_from_env
from blog_v2.common import (ORCH, data_tools_for, max_rounds, new_usage, note, pick, say, sender_of, tool_turn, wait_opening,
                            write_record, wrong_choice)
from blog_v2.prompts import (ROUTE_ASK, CLOSED, CLOSED_AND_ASK, DONE_PREFIX, EMPTY_POST, FREE_NO_TOOL, FREE_UNKNOWN_TOOL, OK, POST_LINE,
                             free_tools, localize, opening, read_hub_post, share_ask)
from blog_v2.scenario import AGENTS
from blog_v2.transport import SessionClosed

NO_RECEIVER = "Name the leaders that receive this message in `to`, among: {names}. Nothing was sent."

DEBOUNCE = 2.5            # seconds without a new message before an agent reads what arrived (so that simultaneous replies are read together)
MAX_DEBOUNCE = 10.0       # but never wait longer than this for the first one that arrived


class FreeAgent:
    """A participant of a free discussion. One conversation, kept whole: what the others wrote comes in front of each turn."""

    def __init__(self, me, group, system, scenario, llm_model, out_dir, temperature=None, fresh=True, idle=60, max_time=600,
                 max_posts=100, config=None, log=print, llm=None, coordinated=False, directed=False):
        self.me, self.group, self.system, self.scenario, self.model, self.log = me, group, system, scenario, llm_model, log
        self.options = scenario.options
        self.temperature, self.out_dir = temperature, out_dir
        self.idle, self.max_time, self.max_posts = idle, max_time, max_posts
        out_dir.mkdir(parents=True, exist_ok=True)
        self.coordinated = coordinated                  # a coordinator opens and closes the session; the agent says `done` to it
        self.tools = localize(scenario, free_tools(self.options, coordinator=coordinated, directed=directed))
        self.pending = None                             # a mesh: a message written and not yet sent, waiting for its recipients
        self.directed = directed                        # a mesh: the agent says, for each post, who receives it
        self.data_tools = data_tools_for(scenario, me)       # the tools of a role of the mas-lab library: the model calls them before it answers
        self.max_rounds = max_rounds(self.data_tools)
        self.done = False
        self.llm = llm or client_from_env(fresh)
        self.config = config or {}
        self.messages, self.inbox, self.events = [], [], []
        self.usage = new_usage()
        self.final = None
        self.close = None
        self.posts = 0                                  # posts seen on the channel, the agent's own included
        self.closed = False                             # the session went away while the model was thinking

    def _note(self, name, **detail):
        note(self.events, self.log, name, **detail)

    async def _turn(self, delivery, tool_names, handle):
        """Show `delivery`, then let the model call tools until `handle(name, args)` accepts one (it returns an error text or None)."""
        say(self.messages, [{"type": "text", "text": delivery}])
        data = list(self.data_tools.values()) if not {"done", "final_answer", "send_to"} & set(tool_names) else []     # not for the last turn

        async def run(name, args):
            error = await handle(name, args) if name in tool_names else FREE_UNKNOWN_TOOL.format(name=name)
            return error or OK, bool(error)

        return await tool_turn(self, [self.tools[n] for n in tool_names] + data, run, FREE_NO_TOOL, data=bool(data))

    async def _discuss(self, name, args):
        if name == "post":
            text = str(args.get("text") or "").strip()
            if not text:
                return EMPTY_POST
            if self.directed:                           # the message waits: the next model call chooses who receives it
                self.pending = text
                return None
            await self._send(text, text, None)
        elif name == "done":
            return await self._finalize(name, args)
        else:
            self._note("silent", reason=str(args.get("reason") or ""))
        return None

    async def _send(self, text, wire, to):
        try:
            await (self.group.send(wire, to=to) if self.directed else self.group.send(wire))
        except Exception as e:                          # the transport went away while the model was still writing this
            self._note("post_lost", text=text, error=str(e)[:200])
            self.closed = True
            return
        self.posts += 1
        self._note("posted", text=text, **({"to": to or [a for a in AGENTS if a != self.me]} if self.directed else {}))

    async def _route(self, name, args):
        """The recipients of the message that was written (a mesh): a second model call."""
        named = args.get("to")
        named = [named] if isinstance(named, str) else named
        to = [a for a in dict.fromkeys(named or []) if a != self.me]
        if not to or any(a not in AGENTS for a in to):
            return NO_RECEIVER.format(names=", ".join(a for a in AGENTS if a != self.me))
        text, self.pending = self.pending, None
        if set(to) != set(AGENTS) - {self.me}:          # not for everyone: the others read who it went to
            await self._send(text, f"(sent to: {', '.join(to)}) {text}", to)
        else:
            await self._send(text, text, None)
        return None

    async def _speak(self, delivery, tool_names):
        """A turn in which the agent may write a message; in a mesh a second turn then asks who receives it."""
        await self._turn(delivery, tool_names, self._discuss)
        if self.pending is not None:
            names = ", ".join(a for a in AGENTS if a != self.me)
            if not await self._turn(ROUTE_ASK.format(text=self.pending, names=names), ["send_to"], self._route) and self.pending is not None:
                self._note("post_lost", text=self.pending, error="no recipients chosen")
                self.pending = None

    async def _finalize(self, name, args):
        option = pick(self.scenario, args.get("option"))
        if option is None:
            return wrong_choice(self.scenario)
        self.final = {"option": option, "rationale": args.get("rationale", "")}
        self._note("final_answer", **self.final)
        if self.coordinated:                            # the final choice goes to the coordinator, and the agent leaves
            try:
                await self.group.send(DONE_PREFIX + json.dumps(self.final, ensure_ascii=False))
            except Exception as e:
                self._note("post_lost", text="done", error=str(e)[:200])
            self.done = True
        return None

    def _delivery(self):
        text = "\n\n".join(POST_LINE.format(who=who, text=what) for who, what in self.inbox)
        self.inbox = []
        return text

    def _read(self, got):
        """(author, text) of a message: over a star the coordinator puts the author in front of what it passes on."""
        who, text = sender_of(got[0]), got[1]
        if who == "hub":
            who, text = read_hub_post(text) or (ORCH, text)
        return who, text

    async def run(self):
        start = time.time()
        reason = None
        if self.coordinated:                            # the coordinator opens the session and closes it; the agent says when it is done
            text = await wait_opening(self.group)
            self.log("the coordinator opened the session")
        else:
            text = opening(self.scenario, "free")
        await self._speak(f"{text}\n\n{share_ask(self.scenario)}", ["post"])      # step 1: everyone posts once
        first = None
        last = time.time()
        while reason is None and not self.closed and not self.done:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                reason = "session closed"
                break
            now = time.time()
            if got:
                who, text = self._read(got)
                if who == ORCH and text == CLOSED:
                    reason = "closed by the coordinator"
                    break
                if text.startswith(DONE_PREFIX):        # another leader's word to the coordinator, seen on a shared channel: not a post
                    continue
                if who != self.me and who != ORCH:
                    self.inbox.append((who, text))
                    self.posts += 1
                    self._note("received", **{"from": who}, text=text)
                    first, last = first or now, now
            if self.inbox and (now - last >= DEBOUNCE or now - first >= MAX_DEBOUNCE):
                first = None
                await self._speak(self._delivery(), ["post", "stay_silent"])
                last = time.time()                      # what the model took is not silence
            if self.coordinated:                        # the coordinator decides when the session is over
                continue
            if self.posts >= self.max_posts:
                reason = "max posts"
            elif now - start >= self.max_time:
                reason = "max time"
            elif not self.inbox and now - last >= self.idle:
                reason = "idle"
        self.close = {"reason": reason or ("done" if self.done else "session closed"), "posts": self.posts, "seconds": round(time.time() - start, 1)}
        self.log(f"closing: {self.close}")
        if self.done:
            return
        delivery = self._delivery()
        await self._turn(f"{delivery}\n\n{CLOSED_AND_ASK}" if delivery else CLOSED_AND_ASK, ["done" if self.coordinated else "final_answer"],
                         self._finalize)

    def finish(self):
        write_record(self.out_dir, self.me, config=self.config, final=self.final, close=self.close, usage=self.usage, events=self.events,
                     transcript=self.messages)
        return self.final
