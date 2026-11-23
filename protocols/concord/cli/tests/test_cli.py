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
    assert code == 0 and out.startswith("STATUS: WAIT\n--- NOT OVER\nThe run is not over") and "--- WAKE" in out
    assert "--- END" not in out


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


def test_a_fault_of_the_library_says_what_is_wrong_and_what_to_do_and_does_not_talk_of_answers(cli):
    def broken(factory, args, text):
        from ioc_concord.errors import StateError
        raise StateError("the state is not what was expected")
    code, out = cli("boom", commands=[Command("boom", "x", None, broken, False)])
    assert code == 2 and out.startswith("STATUS: ERROR\nthe state is not what was expected\nNEXT: Nothing was changed. Fix what")
    assert "answer" not in out                                           # the advice of a refused answer does not fit a fault of the state


def test_a_state_file_that_is_not_json_is_a_fault_with_a_way_back(cli):
    (cli.dir / "concord-state-a.json").write_text("{not json")
    code, out = cli("receive", stdin=init_text())
    assert code == 2 and out.startswith("STATUS: ERROR\nstate file ") and "is not valid JSON" in out
    assert out.splitlines()[-1].startswith("NEXT: Nothing was changed.") and "already answered" not in out


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


# --- wait ---------------------------------------------------------------------------------------------------------------------------

def joined_but_not_briefed(cli):
    """The agent `a` has joined a run whose init had a short timeout, and has never been shown the brief of the run."""
    from ioc_concord.store import FileStore
    Agent("a", store=FileStore(cli.dir / "concord-state-a.json")).receive(init_text(timeout=1))


def test_a_tick_that_does_nothing_does_not_use_up_the_brief(cli, monkeypatch):
    """`wait` composes the output of a tick only when it returns it: composing marks the run as briefed."""
    from ioc_concord.agent import Step
    joined_but_not_briefed(cli)
    real, calls = Agent.tick, []

    def tick(self, now=None):
        calls.append(1)
        return Step(self.clock()) if len(calls) <= 2 else real(self, now)        # two ticks that do nothing, then the real one

    monkeypatch.setattr(Agent, "tick", tick)
    code, out = cli("wait", extra=("--poll", "0.05"))
    assert len(calls) >= 3 and code == 0
    assert out.startswith("STATUS: SEND\n--- BRIEF\n")                         # the brief is in the output that is returned


def test_a_tick_that_does_nothing_is_followed_by_a_poll_interval_and_not_by_another_tick_at_once(cli, monkeypatch):
    from ioc_concord.agent import Step
    joined_but_not_briefed(cli)
    real, times = Agent.tick, []

    def tick(self, now=None):
        times.append(time.time())
        return Step(self.clock()) if len(times) <= 3 else real(self, now)

    monkeypatch.setattr(Agent, "tick", tick)
    cli("wait", extra=("--poll", "0.3"))
    gaps = [b - a for a, b in zip(times, times[1:3])]
    assert len(gaps) == 2 and all(g >= 0.25 for g in gaps), gaps               # before: 50 ms apart, whatever --poll said


# --- the sidecar and the report ---------------------------------------------------------------------------------------------------

def test_what_a_call_that_raises_put_in_the_sidecar_is_not_kept(cli):
    """A call that raises printed nothing: what it was going to show (a brief) must still be shown by the next one."""
    with pytest.raises(RuntimeError):
        with Session("p.py", "a", cli.dir) as s:
            s.side["briefed"].append("a-run")
            raise RuntimeError("the call failed")
    assert not (cli.dir / "concord-script-a.json").exists()
    with Session("p.py", "a", cli.dir) as s:
        assert s.side["briefed"] == []
        s.side["briefed"].append("a-run")
    assert Session("p.py", "a", cli.dir).side_store.load()["briefed"] == ["a-run"]    # and one that ends well is kept


def converge_without_the_result(dir):
    """Two agents, `a` (the reporter, in memory) and `b` (in `dir`), run a whole negotiation, except that the result of `a` does not reach
    `b`. Returns the messages that were dropped."""
    from ioc_concord.store import FileStore
    reason = "because it fits my role best, with concrete facts"
    ini = Agent("ini")
    agents = {"a": Agent("a"), "b": Agent("b", store=FileStore(dir / "concord-state-b.json"))}
    queue, dropped = [("ini", ini.start(["a", "b"], "pick one", "nothing", "all agree", SPEC).send[0])], []
    while queue:
        origin, text = queue.pop(0)
        for name, agent in agents.items():
            if name == origin or ("<concord-result" in text and name == "b"):
                dropped += [text] if "<concord-result" in text and name == "b" else []
                continue
            queue += [(name, t) for t in agent.receive(text).send]
            for task in agent.due():
                if task["kind"] == "init-reply":
                    sent = agent.submit_init_reply(task["run_id"], 10, 90, f"option {name}", reason, 80, reason)
                else:
                    sent = agent.submit_score(task["run_id"], [(c["id"], 80, reason) for c in task["candidates"]])
                queue += [(name, t) for t in sent.send]
    return dropped


def test_a_report_in_the_middle_of_a_run_does_not_say_that_it_is_over(cli):
    """`b` has computed its outcome and waits for the result of the reporter: the run is not over for it, and the end text of the library
    (which says "the run is over" once an outcome is computed) must not be shown as if it were the result."""
    (result,) = converge_without_the_result(cli.dir)
    code, out = cli("report", me="b")
    assert code == 0 and out.startswith("STATUS: WAIT\n--- NOT OVER\n")
    assert "You have computed an outcome (SUCCESS)" in out and "do not report it as the result" in out
    assert "--- END" not in out and "The run is over" not in out
    code, out = cli("receive", me="b", stdin=result)                                  # the result arrives: now it is over
    code, out = cli("report", me="b")
    assert out.startswith("STATUS: DONE\n--- END\nThe run is over.")


# --- what `wait` relies on, and a message with no addressee -------------------------------------------------------------------------------

def test_a_tick_never_makes_a_task_due():
    """`wait` returns for what a tick sent or ended, not for a task that is due. That is right only if a tick never makes a task due: it asks
    again, or aborts (which removes the tasks), so a task that is due after a tick was due before it, and the model was shown it."""
    import copy
    import json
    reason = "because it fits my role best, with concrete facts"
    init = Agent("ini").start(["a", "b"], "pick one", "nothing", "all agree", SPEC, params={"timeout": 10, "retries": 1}).send[0]
    agents = {"a": Agent("a"), "b": Agent("b")}

    def deliver(origin, texts):
        for text in texts:
            for name, agent in agents.items():
                if name != origin:
                    deliver(name, agent.receive(text).send)

    def due(agent):
        return json.dumps(agent.due(), sort_keys=True)

    def ticks_change_nothing_that_is_due(state):
        owed = 0
        for name, agent in agents.items():
            before = due(agent)
            owed += before != "[]"
            alone = copy.deepcopy(agent)
            for n in range(1, 5):                                            # past every deadline, until it gives up
                alone.tick(now=alone.clock() + 1000 * n)
                after = due(alone)
                assert after == before or (after == "[]" and alone.status()["status"] == "aborted"), (state, name, n)
        assert owed, state                                                   # the state did have a task due, or the check shows nothing

    for name, agent in agents.items():
        agent.receive(init)
    ticks_change_nothing_that_is_due("first answer due")
    run_id = agents["a"].state["active"]
    deliver("a", agents["a"].submit_init_reply(run_id, 10, 90, "option a", reason, 90, reason).send)
    deliver("b", agents["b"].submit_init_reply(run_id, 10, 90, "option b", reason, 50, reason).send)
    ticks_change_nothing_that_is_due("ratings due")
    for name, raw in (("a", 20), ("b", 30)):                                 # both rate the other's seed low: the run needs a repair
        task = agents[name].due()[0]
        deliver(name, agents[name].submit_score(run_id, [(c["id"], raw, reason) for c in task["candidates"]]).send)
    assert any(t["kind"] == "repair" for agent in agents.values() for t in agent.due())
    ticks_change_nothing_that_is_due("a repair due")


def test_a_message_with_no_run_and_no_addressee_falls_back_to_the_run_the_agent_is_in(cli):
    cli("receive", stdin=init_text(participants=("a", "b", "c")))
    with Session("prog.py", "a", cli.dir) as s:
        assert s.recipients("<concord-note version='0.3.0' from='a'/>") == ["b", "c", "ini"]        # the audience is the run it is in
        assert s.recipients("not a message at all") == ["b", "c", "ini"]


def test_a_message_with_no_recipient_that_can_be_worked_out_is_never_printed_for_everyone(cli):
    with Session("prog.py", "a", cli.dir) as s:                                                      # the agent is in no run
        with pytest.raises(CliError) as stop:
            s.recipients("<concord-note version='0.3.0' from='a'/>")
        assert "no recipient" in str(stop.value) and "Do not send it" in stop.value.hint
        out = Out("SEND")
        with pytest.raises(CliError):
            s.add_sends(out, type("Step", (), {"send": ["<concord-note version='0.3.0' from='a'/>"]})())
        assert out.sections == []                                                                    # and nothing was added

