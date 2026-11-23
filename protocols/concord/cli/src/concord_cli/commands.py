#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The commands both scripts have, and the command line: `receive`, `tick`, `wait` and `report`. A script adds its own."""
import argparse
import sys
import time
from collections import namedtuple

from ioc_concord.errors import ConcordError
from ioc_concord.state import RUN_ACTIVE

from concord_cli.out import EXIT_ERROR, CliError, Out, error_text
from concord_cli.session import DEFAULT_STATE_DIR, Session

# name, help, add_args (a function of the parser, or None), handler (a function of the session factory, the arguments and the text
# on standard input), and whether the command reads standard input.
Command = namedtuple("Command", "name help add_args handler stdin")

POLL = 5.0


def run_receive(factory, args, text):
    """A message arrived: any text, what is not CONCORD is ignored."""
    with factory() as s:
        before = s.before()
        return s.compose(s.agent.receive(text), before)


def run_tick(factory, args, text):
    """Time has passed: ask again for what is late, or give up on a silent agent."""
    with factory() as s:
        s.need_run()
        before = s.before()
        return s.compose(s.agent.tick(), before)


def run_report(factory, args, text):
    with factory() as s:
        return s.report_out()


def run_wait(factory, args, text):
    """The timer, for a host that can run a command in the background: block until the next timer is due, tick, and return what the tick
    produced. The lock is held for each look at the state and never while sleeping, so the other commands work meanwhile."""
    while True:
        with factory() as s:
            status = s.need_run()
            if status["status"] != RUN_ACTIVE:
                # Not the END again: it was printed when the run ended, and `report` prints it on demand. A model that starts `wait` again
                # each time it returns must be told to stop, and not be given a result to report once more.
                out = Out("DONE")
                out.next = (f"The run is over, so there is nothing to wait for: do not start `{s.command('wait')}` again. `{s.command('report')}` "
                            f"prints the result again. If messages of this run still arrive, pass each one to `{s.command('receive')}` and "
                            "send only what it prints.")
                return out
            wake = s.agent.next_wakeup()
            if wake is None:
                out = Out("WAIT")
                out.next = (f"No timer runs: do not start `{s.command('wait')}` again until a call prints a WAKE. Pass every message "
                            f"of this run you receive, exactly as received, one per call, to `{s.command('receive')}`.")
                return out
            delay = wake - s.agent.clock()                              # the clock of the agent, not the time of day
            if delay <= 0:
                before = s.before()
                step = s.agent.tick()
                # It returns for what the tick sent or ended, and not for a task that is due: a tick never makes a task due (it asks again, or
                # aborts, which removes them), so a task that is due now was due before, and the model was shown it when it became due.
                # Returning for it would return at once, again and again, for as long as the answer is owed.
                if step.send or s.agent.status()["status"] != RUN_ACTIVE:
                    return s.compose(step, before)      # composed only now: it marks the run as briefed, which a discarded output must not
                delay = args.poll                       # nothing to send (answered meanwhile): look again after a poll interval, not at once
        time.sleep(max(0.05, min(args.poll, delay)))


def _poll_arg(parser):
    parser.add_argument("--poll", type=float, default=POLL, help="seconds between two looks at the state while waiting (default 5)")


RECEIVE = Command("receive", "pass a message that arrived, as received, on standard input", None, run_receive, True)
TICK = Command("tick", "time has passed: ask again for what is late, or give up on a silent agent", None, run_tick, False)
WAIT = Command("wait", "block until the next timer is due, then tick (run it in the background)", _poll_arg, run_wait, False)
REPORT = Command("report", "how the run stands or ended", None, run_report, False)


def read_stdin(command):
    if sys.stdin is None or sys.stdin.isatty():              # None: standard input is closed
        raise CliError(f"{command} reads standard input and there is none", "Pipe the text in, or use a here-document.")
    text = sys.stdin.read()
    if not text.strip():
        raise CliError(f"{command} got an empty standard input", "Pass the text exactly as you received it.")
    return text


class _Parser(argparse.ArgumentParser):
    """A parser whose faults are CliErrors. The hint names the script, never `self.prog`: for a command that is the script and what
    argparse puts ahead of it, which differs from one version of Python to the next."""

    def __init__(self, *args, script=None, command="", **kwargs):
        super().__init__(*args, **kwargs)
        self.script, self.command = script, command

    def error(self, message):
        raise CliError(message, f"Run `{self.script} {self.command + ' ' if self.command else ''}--help`.")


def cli_main(prog, commands, doc, argv=None, task_text=None, add_globals=None, session_class=Session, session_options=None):
    """Parse the command line, run the command, print what it says. Exit code 0, or 2 for an ERROR. A script that has flags of its own
    before the command adds them with `add_globals` (a function of the parser), and turns what they say into the keyword arguments of its
    session class with `session_options` (a function of the parsed arguments)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):          # messages are UTF-8 whatever the locale says: never read or print them otherwise
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    parser = _Parser(prog=prog, script=prog, description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--me", required=True, help="your agent id, as in the run")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="where the state of the agent is kept (default: the current directory)")
    if add_globals:
        add_globals(parser)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for c in commands:
        p = sub.add_parser(c.name, help=c.help, description=c.help, script=prog, command=c.name)
        if c.add_args:
            c.add_args(p)
    by_name = {c.name: c for c in commands}
    try:
        args = parser.parse_args(argv)
        command = by_name[args.command]
        text = read_stdin(command.name) if command.stdin else None
        options = session_options(args) if session_options else {}
        out = command.handler(lambda guard=True: session_class(prog, args.me, args.state_dir, task_text, guard=guard, **options), args, text)
    except CliError as exc:
        print(error_text(str(exc), exc.hint), end="")
        return EXIT_ERROR
    except ConcordError as exc:                 # not a refused answer (the script handles those): the state or the input is wrong
        print(error_text(str(exc), "Nothing was changed. Fix what the message says (the file or the argument it names) and run the same "
                                   "command again. If it keeps failing, stop and say why."), end="")
        return EXIT_ERROR
    print(out.text(), end="")
    return EXIT_ERROR if out.status == "ERROR" else 0
