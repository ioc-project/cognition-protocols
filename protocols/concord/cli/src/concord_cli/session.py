#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""One call of a script: the lock, the agent, the sidecar, and the output of what a step produced."""
import contextlib
import shlex
import sys
from pathlib import Path

from ioc_concord.agent import Agent
from ioc_concord.errors import MissingState
from ioc_concord.guidance import RUN_DESCRIPTION, render_delivery, render_end
from ioc_concord.protocol import is_agent_id
from ioc_concord.state import PARTICIPANT, RUN_ABORTED, RUN_ACTIVE, RUN_ENDED
from ioc_concord.store import FileStore
from ioc_concord.wire import peek

from concord_cli.lock import locked
from concord_cli.out import CliError, Out, wake_text

DEFAULT_STATE_DIR = "."


class Session:
    """The state of one agent, for the length of one call: `with Session(...) as s:`. The lock is held for the block, the agent is
    loaded when it opens (`s.agent`) and saves itself after every call that changes it. `s.side` is the small state that belongs to
    the scripts and not to the library (which runs have been briefed, the refusal counter): it is saved when the block ends well.
    `task_text` is a function of a task and of the command that answers it, giving what the script adds to the task's instructions (its
    answer format), or None. `guard` says whether the script's guard applies to this call: a script that has one (see `check_role`) is
    checked when the session opens."""

    def __init__(self, prog, me, state_dir=DEFAULT_STATE_DIR, task_text=None, guard=True):
        if not is_agent_id(me):
            raise CliError(f"--me {me!r} is not an agent id: 1 to 64 ASCII letters, digits, _ or -",
                           "Run again with the id you were given for this run.")
        self.prog, self.me, self.state_dir, self.task_text, self.guard = prog, me, str(state_dir), task_text, guard
        self.dir = Path(state_dir)
        self.state_path = self.dir / f"concord-state-{me}.json"
        self.side_store = FileStore(self.dir / f"concord-script-{me}.json")

    def __enter__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stack = contextlib.ExitStack()
        try:
            self._stack.enter_context(locked(self.state_path))
            self.agent = Agent(self.me, store=FileStore(self.state_path))
            self.side = self._read_side()
            if self.guard:
                self.check_role()
        except BaseException:
            self._stack.close()
            raise
        self._side_before = repr(self.side)
        return self

    def __exit__(self, kind, exc, trace):
        try:
            if kind is None and repr(self.side) != self._side_before:
                self.side_store.save(self.side)
        finally:
            self._stack.close()
        return False

    def _read_side(self):
        try:
            side = self.side_store.load()
        except MissingState:
            side = {}
        side.setdefault("briefed", [])
        side.setdefault("refusal", None)
        return side

    # --- what the agent says ---------------------------------------------------------------------------------------------

    def check_role(self):
        """Raise a CliError when this script is not the one for the run the agent is in. The base script is for every role."""

    def command(self, name, prog=None):
        """The command line that runs `name` for this agent, as the NEXT lines print it (with another script, when `prog` is given)."""
        where = "" if self.state_dir == DEFAULT_STATE_DIR else f" --state-dir {shlex.quote(self.state_dir)}"
        return f"{prog or self.prog} --me {self.me}{where} {name}"

    def run(self):
        """The run the agent is in, or the last one it was in; None before the first."""
        state = self.agent.state
        return state["runs"].get(state["active"]) if state["active"] else None

    def before(self):
        """The status of the run now, to be given to `compose` after the call that may change it."""
        status = self.agent.status()
        return status["status"] if status else None

    def need_run(self):
        """The status of the run, or a fault when the agent has none."""
        status = self.agent.status()
        if status is None:
            raise CliError(f"there is no run for {self.me} in {self.state_path}",
                           f"A run starts with the message that opens it: pass that message to `{self.command('receive')}`.")
        return status

    def recipients(self, text):
        """The agents a message of this agent is for: every other agent of its run, the initiator included. For a message about a run the
        agent does not know (a refusal of an init) the one it is addressed to."""
        attrs = (peek(text) or {}).get("attrs", {})
        run = self.agent.state["runs"].get(attrs.get("run_id"))
        if run is None:
            return [attrs["to"]] if attrs.get("to") else []
        names = list(run["participants"]) + [run["initiator"]]
        return [n for i, n in enumerate(names) if n != self.me and n not in names[:i]]

    def add_sends(self, out, step):
        for text in step.send:
            out.add(f"SEND to: {', '.join(self.recipients(text)) or 'everyone'}", text.strip())

    def brief(self, out, run, extra=""):
        """The first time a run is met, its description for the model: once per run."""
        if run["run_id"] not in self.side["briefed"]:
            self.side["briefed"].append(run["run_id"])
            out.add("BRIEF", RUN_DESCRIPTION + extra)

    def compose(self, step, before):
        """The output of a call that produced `step`, given the status of the run before it (see `before`): what to send, what was read,
        what is due, how the run ended, when to come back, and what to do next."""
        agent, run, status = self.agent, self.run(), self.agent.status()
        over = status is not None and status["status"] != RUN_ACTIVE
        ended_now = over and before not in (RUN_ENDED, RUN_ABORTED)
        tasks = agent.due()
        out = Out("SEND" if step.send else "ANSWER" if tasks else "DONE" if over else "WAIT")
        if run is not None and run["role"] == PARTICIPANT:
            self.brief(out, run)
        self.add_sends(out, step)
        if step.accepted:
            out.add("READ", render_delivery(step.accepted))
        if tasks:
            extra = self.task_text(tasks[0], self.command("answer")) if self.task_text else ""
            out.add("TASK", agent.instructions(tasks[0]) + ("\n\n" + extra if extra else ""))
        if ended_now:
            out.add("END", render_end(self.me, agent.report()))
        wake = None if over else agent.next_wakeup()
        if wake is not None:
            out.add("WAKE", wake_text(wake, agent.clock()))
        if not step.accepted and not step.send and step.events:
            out.add("NOTE", "not used: " + "; ".join(sorted({e.get("reason") or e["event"] for e in step.events})))
        out.next = self.next_line(bool(step.send), bool(tasks), over, ended_now, wake is not None)
        return out

    def report_out(self):
        """The report of the run: its end, or that it is not over."""
        status = self.need_run()
        over = status["status"] != RUN_ACTIVE
        out = Out("DONE" if over else "WAIT")
        out.add("END", render_end(self.me, self.agent.report()))
        wake = None if over else self.agent.next_wakeup()
        if wake is not None:
            out.add("WAKE", wake_text(wake, self.agent.clock()))
        out.next = self.next_line(False, False, over, over, wake is not None)
        return out

    def next_line(self, sent, task, over, ended_now, timer):
        """What to do now: the one place the scripts say it, in words that do not depend on the command."""
        parts = []
        if sent:
            parts.append("Broadcast each SEND block to the agents on its `to:` line, exactly as printed and without that line. "
                         "Never write or change a message.")
        if task:
            parts.append(("Then answer" if sent else "Answer") + f" the TASK: write the answer it asks for and pass it to "
                         f"`{self.command('answer')}`. Never write a CONCORD message yourself.")
        if ended_now:
            parts.append("The run is over: report what END says, and nothing more.")
        if over:
            parts.append(f"If messages of this run still arrive, pass each one to `{self.command('receive')}` and send only what it "
                         "prints.")
        else:
            parts.append(f"Pass every message of this run you receive, exactly as received, one per call, to `{self.command('receive')}`.")
        if timer:
            parts.append(f"A timer runs: start `{self.command('wait')}` in the background (it returns when something is to be sent) and "
                         f"start it again each time it returns, or call `{self.command('tick')}` at the time under WAKE.")
        return " ".join(parts)
