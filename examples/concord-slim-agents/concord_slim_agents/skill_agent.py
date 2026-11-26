#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""One agent that uses the CONCORD skills: a model with a shell and a way to send messages, the way a coding agent has them.

Nothing of the protocol is in this file. The skills (`protocols/concord/skills`) are given to the model as files: it reads `SKILL.md`,
runs the scripts, and does what they print. The harness is only the world around the model:

    a message arrives            -> shown to the model as it came, once its turn is over; the model passes it to `receive`
    the model calls send_message -> the text goes over the transport (SLIM, or the star)
    a background command ends    -> the model is told what it printed (that is how `wait` returns)
    the state of the run         -> read from the file the scripts keep, to know when the run is over

What the model reads is the whole conversation, saved in the record of the agent (see verify.py, which checks it all the same).
The model runs the shell commands it writes in the folder of its agent, with no isolation: run this where that is acceptable.
"""
import asyncio
import fcntl
import json
import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

from ioc_concord.agent import Agent
from ioc_concord.store import FileStore

from concord_slim_agents.agent import client_from_env
from concord_slim_agents.transport import SessionClosed

MAX_TOOL_ROUNDS = 40            # model calls in one turn
COMMAND_TIMEOUT = 120           # seconds a command may take when it is not run in the background
OUTPUT_LIMIT = 20000
QUIET = 0.5                     # events that come within this many seconds of each other are shown together
LINGER = 6                      # seconds the agent stays after the run is over: another agent may still ask for a message

TOOLS = [
    {"name": "shell",
     "description": "Run a command with bash in your working directory and read what it printed, then its exit code. A command that takes "
                    "long (waiting, say) goes in the background: set background to true, it returns at once and you are told what it "
                    "printed when it ends. Messages from other agents reach you only when your turn has ended: calling this tool to look "
                    "for them (sleep, ls, reading files) brings none. When you are waiting, end your turn.",
     "input_schema": {"type": "object", "properties": {
         "command": {"type": "string", "description": "The command line. A text for a command's standard input goes in a here-document."},
         "background": {"type": "boolean", "description": "Run it in the background (default false)."}}, "required": ["command"]}},
    {"name": "send_message",
     "description": "Send a text to other agents, exactly as you give it. It is the only way to reach them.",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "array", "items": {"type": "string"}, "description": "The ids of the agents to send it to."},
         "text": {"type": "string", "description": "The text to send."}}, "required": ["to", "text"]}},
]


def observe(state_file, me):
    """The node's own view of the run, read from the state the scripts keep: (status, report), or (None, None) before there is a run.
    The harness reads it only to know when to stop: it never changes it."""
    try:
        node = Agent(me, store=FileStore(state_file))
        return node.status(), node.report()
    except Exception:                                   # no state yet, or a file that is being written
        return None, None


class SkillAgent:
    def __init__(self, me, group, system, llm_model, out_dir, kickoff=None, temperature=None, fresh=True, config=None, log=print, llm=None,
                 deadline=900):
        self.me, self.group, self.system, self.model, self.log = me, group, system, llm_model, log
        self.kickoff, self.temperature, self.config = kickoff, temperature, config or {}
        self.out_dir = out_dir
        self.work = out_dir / me                        # the folder of the agent: the scripts keep their state here
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_file = self.work / f"concord-state-{me}.json"
        self.llm = llm or client_from_env(fresh)
        self.deadline = time.time() + deadline
        self.messages, self.events = [], []
        self.usage = {"input": 0, "output": 0, "calls": 0}
        self.queue = asyncio.Queue()                    # what happens to the agent: ("message", sender, text), ("done", id, command, text)
        self.background = {}
        self.procs = []
        self.closed = False
        self.commands = 0
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")      # `python3` is the one that runs this
        self.env = env

    # --- the world of the model -------------------------------------------------------------------------------------------

    def _event(self, name, **detail):
        self.events.append({"event": name, "at": time.time(), **detail})
        self.log(f"event {name} {json.dumps(detail, ensure_ascii=False, default=str)[:300]}")

    @staticmethod
    def _what(text):
        """The tag and uuid of a message, for the log of what went over the transport (to find a message that was sent and never arrived)."""
        m = re.search(r"<(concord-[a-z-]+)[^>]*?\buuid=\"([^\"]+)\"", text)
        return {"kind": m.group(1), "uuid": m.group(2)[:8]} if m else {"kind": "not a concord message"}

    async def _pump(self):
        """Put what the transport brings on the queue. An own message that comes back (a broadcast channel does that) is dropped here,
        as most transports would."""
        while True:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                await self.queue.put(("closed",))
                return
            if got is None or (got[0] or "").split("/")[-1] == self.me:
                continue
            self._event("received", **self._what(got[1]), sender=got[0])
            await self.queue.put(("message", got[0], got[1]))

    async def _run_command(self, command, background):
        """Run a command with bash. Its output goes to a file, not a pipe, so that a command that leaves a process behind (`cmd &`)
        returns when the shell does, as it does in a terminal."""
        self.commands += 1
        n = self.commands
        self._event("command", n=n, command=command, background=bool(background))
        out = tempfile.TemporaryFile()
        fcntl.fcntl(out, fcntl.F_SETFL, fcntl.fcntl(out, fcntl.F_GETFL) | os.O_APPEND)      # the command writes at the end, whatever we read
        proc = await asyncio.create_subprocess_shell(command, cwd=self.work, env=self.env, stdin=asyncio.subprocess.DEVNULL, stdout=out,
                                                     stderr=asyncio.subprocess.STDOUT, executable="/bin/bash", start_new_session=True)
        self.procs.append(proc)
        if background:
            self.background[n] = asyncio.create_task(self._finish_background(n, command, proc, out))
            return f"Started in the background (command {n}). You are told what it printed when it ends.", False
        try:
            await asyncio.wait_for(proc.wait(), COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            self._kill(proc)
            return f"The command took more than {COMMAND_TIMEOUT} seconds and was stopped. Run a long one in the background.", True
        printed = self._printed(out, proc.returncode)
        if self._alive(proc):                           # `cmd &`: the shell is done but its command is not: tell the model when it ends
            self.background[n] = asyncio.create_task(self._watch(n, command, proc, out, out.tell()))
        return printed, proc.returncode != 0

    @staticmethod
    def _alive(proc):
        try:
            os.killpg(proc.pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    async def _watch(self, n, command, proc, out, seen):
        """What a command left running printed after the shell returned, once it is over (as a background command's output)."""
        while self._alive(proc):
            await asyncio.sleep(0.5)
        self.background.pop(n, None)
        out.seek(seen)
        text = out.read().decode("utf-8", "replace")[:OUTPUT_LIMIT]
        await self.queue.put(("done", n, command, text or "(it printed nothing)"))

    async def _finish_background(self, n, command, proc, out):
        await proc.wait()
        self.background.pop(n, None)
        await self.queue.put(("done", n, command, self._printed(out, proc.returncode)))

    @staticmethod
    def _kill(proc):
        """Stop a command and everything it started."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _printed(out, code):
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
        if len(text) > OUTPUT_LIMIT:
            text = text[:OUTPUT_LIMIT] + "\n[output cut]"
        return f"{text}\n[exit code {code}]" if code else text

    async def _send(self, to, text):
        """The transport's side of a message: SLIM broadcasts it; a star sends it to the agents named."""
        to = [t for t in (to if isinstance(to, list) else [to]) if isinstance(t, str)]
        if not text or not isinstance(text, str):
            return "Nothing was sent: give the text to send.", True
        if not to:
            return "Nothing was sent: give the ids of the agents to send it to.", True
        if hasattr(self.group, "send_to"):
            sent = await self.group.send_to(to, text)
            if not sent:
                return f"Nothing was sent: none of {', '.join(to)} is connected to you.", True
        else:
            await self.group.send(text)
        self._event("sent", to=to, **self._what(text))
        return "sent", False

    async def _tool(self, name, args):
        args = args if isinstance(args, dict) else {}
        if name == "shell":
            if not isinstance(args.get("command"), str) or not args["command"].strip():
                return "Give the command to run.", True
            return await self._run_command(args["command"], args.get("background"))
        if name == "send_message":
            return await self._send(args.get("to"), args.get("text"))
        return f"{name} is not a tool you have: use shell or send_message.", True

    # --- the model's side -------------------------------------------------------------------------------------------------

    def _say(self, blocks):
        """Add to what the model reads. Two messages in a row of the same role are one."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"].extend(blocks)
        else:
            self.messages.append({"role": "user", "content": list(blocks)})

    async def _turn(self, text):
        """One turn: show the model what happened, and let it call tools until it has nothing more to do."""
        self._say([{"type": "text", "text": text}])
        for _ in range(MAX_TOOL_ROUNDS):
            extra = {} if self.temperature is None else {"extra_body": {"temperature": self.temperature}}
            resp = await self.llm.messages.create(model=self.model, max_tokens=4096, system=self.system, tools=TOOLS,
                                                  tool_choice={"type": "auto"}, messages=self.messages, **extra)
            self.usage["calls"] += 1
            self.usage["input"] += resp.usage.input_tokens
            self.usage["output"] += resp.usage.output_tokens
            self.messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
            results = []
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    self.log(f"says {block.text.strip()[:400]}")
                if block.type != "tool_use":
                    continue
                self.log(f"tool {block.name} {json.dumps(block.input, ensure_ascii=False)[:600]}")
                result, is_error = await self._tool(block.name, block.input)
                if is_error:
                    self.log(f"tool {block.name} error: {result[:300]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": result, "is_error": is_error})
            if not results:
                self._save()
                return
            self._say(results)
        self._event("turn limit", rounds=MAX_TOOL_ROUNDS)
        self._save()

    @staticmethod
    def _shown(event):
        """What the model reads for an event."""
        if event[0] == "message":
            who = f" from {event[1].split('/')[-1]}" if event[1] and event[1] != "hub" else ""
            return f"[message{who}]\n{event[2]}\n[end of message]"
        _, n, command, text = event
        return f"[background command {n} ended]\n$ {command}\n{text}\n[end of command]"

    # --- the loop ---------------------------------------------------------------------------------------------------------

    async def run(self):
        pump = asyncio.create_task(self._pump())
        try:
            if self.kickoff:
                self.log("kickoff")
                await self._turn(self.kickoff)
            over_at = None
            while time.time() < self.deadline:
                try:
                    events = [await asyncio.wait_for(self.queue.get(), 1.0)]
                except asyncio.TimeoutError:
                    events = []
                if events:
                    await asyncio.sleep(QUIET)
                    while not self.queue.empty():
                        events.append(self.queue.get_nowait())
                    if any(e[0] == "closed" for e in events):
                        self.closed = True
                    shown = [self._shown(e) for e in events if e[0] != "closed"]
                    if shown:
                        await self._turn("\n\n".join(shown))
                    if self.closed:
                        self.log("the session is closed")
                        break
                status, _ = observe(self.state_file, self.me)
                if status and status["status"] != "active":
                    over_at = over_at or time.time()
                    if time.time() > over_at + LINGER:
                        break
            else:
                self._event("deadline", note="the run was not over in time")
        finally:
            pump.cancel()
            for task in list(self.background.values()):
                task.cancel()
            for proc in self.procs:
                self._kill(proc)

    def finish(self):
        """Write what happened, and return the report of the run."""
        return self._save()

    def _save(self):
        """Write the record of the agent (and copy the state of the scripts beside it, where verify.py reads it), and return the report.
        It is written after every turn too, so that a run that is stopped leaves what the model read and did."""
        status, report = observe(self.state_file, self.me)
        if self.state_file.exists():
            (self.out_dir / f"{self.me}-state.json").write_bytes(self.state_file.read_bytes())
        record = {"agent": self.me, "config": self.config, "report": report, "status": status, "usage": self.usage,
                  "events": self.events, "transcript": self.messages}
        (self.out_dir / f"{self.me}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return report
