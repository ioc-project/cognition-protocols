#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The shared part of the skill scripts: the lock, the output, the session and the commands both scripts have."""
import io
import threading
import time

import pytest
from concord_cli.commands import RECEIVE, REPORT, TICK, WAIT, Command, cli_main
from concord_cli.lock import locked
from concord_cli.out import CliError, Out, error_text, wake_text
from concord_cli.session import Session
from ioc_concord.agent import Agent

SPEC = {"type": "string", "description": "an option"}
COMMANDS = [RECEIVE, TICK, WAIT, REPORT]


def init_text(timeout=60, retries=3, participants=("a", "b")):
    return Agent("ini").start(list(participants), "pick one", "nothing is decided", "everyone agrees", SPEC,
                              params={"timeout": timeout, "retries": retries}).send[0]


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    """Run a command of the CLI for agent `me`: (exit code, what it printed)."""
    def run(command, *, me="a", stdin=None, commands=COMMANDS, extra=()):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin if stdin is not None else ""))
        code = cli_main("prog.py", commands, "doc", ["--me", me, "--state-dir", str(tmp_path), command, *extra])
        return code, capsys.readouterr().out
    run.dir = tmp_path
    return run


# --- out ------------------------------------------------------------------------------------------------------------------------

def test_the_output_is_a_status_sections_and_a_next_line():
    out = Out("SEND")
    out.add("SEND to: b", "<x/>\n")
    out.add("WAKE", "later")
    out.next = "do it"
    assert out.text() == "STATUS: SEND\n--- SEND to: b\n<x/>\n--- WAKE\nlater\nNEXT: do it\n"


def test_a_fault_always_says_what_to_do():
    assert error_text("bad", "fix it") == "STATUS: ERROR\nbad\nNEXT: fix it\n"
    assert wake_text(1000.0, 940.0).endswith("(in 60 s)")


# --- lock -----------------------------------------------------------------------------------------------------------------------

def test_a_second_call_waits_for_the_lock(tmp_path):
    order = []
    state = tmp_path / "state.json"

    def second():
        with locked(state):
            order.append("second")

    with locked(state):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.3)
        order.append("first")                   # the second call is still waiting here
    t.join(5)
    assert order == ["first", "second"]


# --- the commands ---------------------------------------------------------------------------------------------------------------

def test_a_command_without_a_run_says_how_one_starts(cli):
    for command in ("tick", "report", "wait"):
        code, out = cli(command)
        assert code == 2 and out.startswith("STATUS: ERROR\nthere is no run for a") and "receive" in out.splitlines()[-1]


def test_the_message_that_opens_the_run_gives_a_task_with_the_brief_once(cli):
    code, out = cli("receive", stdin=init_text())
    assert code == 0 and out.startswith("STATUS: ANSWER\n--- BRIEF\n")
    assert out.index("--- BRIEF") < out.index("--- READ") < out.index("--- TASK") < out.index("--- WAKE") < out.index("\nNEXT:")
    assert "prog.py --me a --state-dir" in out and " answer`" in out
    code, again = cli("receive", stdin="not a concord message")
    assert code == 0 and "--- BRIEF" not in again and "--- NOTE\nnot used: not a CONCORD message" in again


def test_a_message_that_is_not_concord_changes_nothing_and_says_so(cli):
    code, out = cli("receive", stdin="hello")
    assert code == 0 and out.startswith("STATUS: WAIT\n--- NOTE\nnot used: not a CONCORD message\n")


def test_standard_input_is_required_where_a_command_reads_it(cli):
    code, out = cli("receive", stdin="  \n")
    assert code == 2 and "empty standard input" in out


def test_an_agent_id_that_is_not_one_is_refused(cli):
    code, out = cli("report", me="not an id")
    assert code == 2 and "is not an agent id" in out


def test_a_bad_command_line_is_an_error_with_a_hint(cli, capsys):
    code = cli_main("prog.py", COMMANDS, "doc", ["--me", "a", "nonsense"])
    out = capsys.readouterr().out
    assert code == 2 and out.startswith("STATUS: ERROR\n") and "--help" in out.splitlines()[-1]


def test_the_message_for_another_agents_run_goes_to_every_other_agent_of_it(cli):
    cli("receive", stdin=init_text(participants=("a", "b", "c")))
    session = Session("prog.py", "a", cli.dir)
    with session as s:
        run = s.run()
        text = f'<concord-error version="0.3.0" from="a" run_id="{run["run_id"]}" uuid="x" to="c" what="score" reason="missing"/>'
        assert s.recipients(text) == ["b", "c", "ini"]                       # the participants, then the initiator, never itself
        stranger = '<concord-error version="0.3.0" from="a" run_id="9d6e0c1a-0000-4000-8000-000000000000" to="zed" what="init" reason="busy"/>'
        assert s.recipients(stranger) == ["zed"]                             # a run it does not know: the addressee


def test_the_report_of_a_run_that_is_not_over_says_so_and_has_a_timer(cli):
    cli("receive", stdin=init_text())
    code, out = cli("report")
    assert code == 0 and out.startswith("STATUS: WAIT\n--- END\nThe run is not over") and "--- WAKE" in out


def test_wait_returns_what_the_tick_produced_when_the_timer_is_due(cli):
    cli("receive", stdin=init_text(timeout=1, retries=1))
    started = time.time()
    code, out = cli("wait", extra=("--poll", "0.1"))
    assert 0.5 < time.time() - started < 5
    assert code == 0 and out.startswith("STATUS: SEND\n--- SEND to: b, ini\n<concord-error ") and 'reason="missing"' in out
    code, out = cli("wait", extra=("--poll", "0.1"))                         # after `retries` requests the agent gives up on b
    assert out.startswith("STATUS: SEND") and "<concord-cancel " in out and "--- END\nThe run was aborted" in out
    code, out = cli("wait")                                                  # over: nothing to wait for
    assert out.startswith("STATUS: DONE\n--- END")


def test_wait_returns_at_once_when_no_timer_runs(cli):
    """An initiator that is not a participant keeps no timer once the init replies are in (spec, Messages owed): `wait` must not block, and
    must not be started again and again, so it says so and has no WAKE."""
    from ioc_concord.store import FileStore
    solo = Agent("solo", store=FileStore(cli.dir / "concord-state-solo.json"))
    init = solo.start(["x", "y"], "c", "w", "b", SPEC, params={"timeout": 1000}).send[0]
    for name in ("x", "y"):
        peer = Agent(name)
        peer.receive(init)
        reply = peer.submit_init_reply(solo.state["active"], 10, 90, f"option {name}", "r" * 30, 80, "r" * 30).send[0]
        solo.receive(reply)
    started = time.time()
    code, out = cli("wait", me="solo")
    assert time.time() - started < 2
    assert code == 0 and out.startswith("STATUS: WAIT\nNEXT: No timer runs") and "--- WAKE" not in out


def test_two_commands_can_be_given_to_one_cli_and_a_script_can_add_its_own(cli):
    mine = Command("hello", "says hello", None, lambda factory, args, text: _hello(factory), False)
    code, out = cli("hello", commands=COMMANDS + [mine], me="z")
    assert code == 0 and "hello z" in out


def _hello(factory):
    with factory() as s:
        out = Out("DONE")
        out.add("HI", f"hello {s.me}")
        out.next = "nothing"
        return out


def test_a_fault_of_the_library_is_printed_as_a_refusal_with_a_way_back(cli):
    cli("receive", stdin=init_text())
    def broken(factory, args, text):
        from ioc_concord.errors import StateError
        raise StateError("the state is not what was expected")
    code, out = cli("boom", commands=[Command("boom", "x", None, broken, False)])
    assert code == 2 and out.startswith("STATUS: ERROR\nrefused: the state is not what was expected") and "NEXT:" in out


# --- what a script can add: flags before the command, and a guard on its session --------------------------------------------------

class Guarded(Session):
    def __init__(self, prog, me, state_dir=".", task_text=None, guard=True, tag="x"):
        super().__init__(prog, me, state_dir, task_text, guard)
        self.tag = tag

    def check_role(self):
        raise CliError(f"not for {self.me}", f"use the other script ({self.tag})")


def test_a_script_can_guard_its_session_and_a_call_can_ask_not_to_be_guarded(cli, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = cli_main("p.py", COMMANDS, "doc", ["--me", "a", "--state-dir", str(cli.dir), "report"], session_class=Guarded,
                    session_options=lambda args: {"tag": "T"})
    assert code == 2
    assert capsys.readouterr().out == "STATUS: ERROR\nnot for a\nNEXT: use the other script (T)\n"      # the guard stops a command
    with Guarded("p.py", "a", cli.dir, guard=False) as s:                                             # and a call that opts out gets in
        assert s.me == "a"


def test_a_script_can_add_flags_before_the_command_and_turn_them_into_session_options(cli, monkeypatch, capsys):
    seen = {}

    def handler(factory, args, text):
        with factory() as s:
            seen["tag"], seen["flag"] = s.tag, args.relay
            out = Out("DONE")
            out.next = "ok"
            return out

    class Tagged(Session):
        def __init__(self, prog, me, state_dir=".", task_text=None, guard=True, tag=None):
            super().__init__(prog, me, state_dir, task_text, guard)
            self.tag = tag

    code = cli_main("p.py", [Command("c", "x", None, handler, False)], "doc",
                    ["--me", "a", "--state-dir", str(cli.dir), "--relay", "x,y", "c"],
                    add_globals=lambda p: p.add_argument("--relay"), session_class=Tagged,
                    session_options=lambda args: {"tag": args.relay.split(",")})
    assert code == 0 and seen == {"tag": ["x", "y"], "flag": "x,y"}


def test_the_command_line_of_another_script_can_be_printed(cli):
    with Session("one.py", "a", cli.dir) as s:
        assert s.command("go") == f"one.py --me a --state-dir {cli.dir} go"
        assert s.command("go", prog="two.py").startswith("two.py --me a")


def test_standard_input_is_read_as_utf_8_whatever_the_locale_says(cli, monkeypatch, capsys):
    got = []

    def handler(factory, args, text):
        got.append(text)
        out = Out("DONE")
        out.add("ECHO", text)
        out.next = "ok"
        return out

    latin_1 = io.TextIOWrapper(io.BytesIO("décision → 日本語".encode("utf-8")), encoding="latin-1")      # what a Latin-1 locale would open
    monkeypatch.setattr("sys.stdin", latin_1)
    code = cli_main("p.py", [Command("c", "x", None, handler, True)], "doc", ["--me", "a", "--state-dir", str(cli.dir), "c"])
    assert code == 0 and got == ["décision → 日本語"]
