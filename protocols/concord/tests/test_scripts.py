#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Script tests: the scripts' complete output on scripted runs must not change.

    python3 -m pytest protocols/concord/tests        compare against the recorded transcripts
    UPDATE_TRANSCRIPTS=1 python3 -m pytest protocols/concord/tests    re-record them (review the diff before committing)

A transcript changes only when the scripts' behavior does, so a refactor that must not change behavior leaves them as they are.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import driver  # noqa: E402
import scenarios  # noqa: E402
from driver import INITIATOR, RECEIVER, REASONS  # noqa: E402

TRANSCRIPTS = Path(__file__).resolve().parent / "transcripts"


@pytest.mark.parametrize("name", sorted(scenarios.SCENARIOS))
def test_transcript(name):
    got = driver.dump(scenarios.SCENARIOS[name]())
    assert "Traceback" not in got, "a script crashed with a traceback"
    path = TRANSCRIPTS / f"{name}.txt"
    if os.environ.get("UPDATE_TRANSCRIPTS"):
        TRANSCRIPTS.mkdir(exist_ok=True)
        path.write_text(got)
        return
    assert path.exists(), f"no transcript for {name}: run with UPDATE_TRANSCRIPTS=1"
    assert got == path.read_text(), f"{name}: output differs from the recorded transcript"


def last(run, call):
    """The output of the last call of the log called `call`."""
    return next(e["stdout"] for e in reversed(run.log) if e["call"] == call)


def test_runs_are_deterministic():
    assert driver.dump(scenarios.success_at_score()) == driver.dump(scenarios.success_at_score())


# --- what the transcripts say, said once more as assertions, so that a wrong transcript cannot be re-recorded unnoticed -----------------

def test_a_run_ends_the_same_way_for_everyone():
    run = scenarios.success_at_score()
    for who in ("initiator ini", "receiver a", "receiver b"):
        out = last(run, f"{who} report")
        assert out.startswith("STATUS: DONE") and "SUCCESS, winning candidate seed-a" in out
    assert "Status of the run for you: unverified" in last(run, "initiator ini report")        # it has no outcome of its own to compare
    assert "Status of the run for you: confirmed" in last(run, "receiver a report")


def test_a_budget_that_is_spent_ends_best_effort_and_names_who_is_short():
    out = last(scenarios.best_effort_budget_spent(), "receiver a report")
    assert "BEST_EFFORT" in out and "Below the bar:" in out


def test_a_star_needs_the_centre_to_pass_the_messages_on():
    """A negative control for `forward`: the same star, with the centre only reading what reaches it, never ends."""
    run = driver.play(scenarios.start_args(), driver.policy(scenarios.ANCHORS, scenarios.SEEDS, {"a": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}}),
                      ["a", "b"], relay=["a", "b"], forward=False)
    for who in ("initiator ini", "receiver a", "receiver b"):
        assert last(run, f"{who} report").startswith("STATUS: WAIT")
    ok = scenarios.star_with_relay()
    assert all(last(ok, f"{who} report").startswith("STATUS: DONE") for who in ("initiator ini", "receiver a", "receiver b"))


def test_a_centre_that_takes_part_ends_the_run_with_the_others():
    run = scenarios.star_centre_takes_part()
    assert all(last(run, f"receiver {a} report").startswith("STATUS: DONE") for a in ("ini", "a", "b"))


def printed(out, script):
    """The commands of `script` that the NEXT line of `out` prints, each between backticks, as the list of its arguments."""
    return [shlex.split(m.replace("<the spoke>", "<the-spoke>")) for m in re.findall(rf"`({re.escape(script.name)} [^`]*)`", out.split("NEXT:", 1)[1])]


def run_printed(run, argv, stdin="", spoke="a"):
    """Run a command exactly as NEXT printed it, with the spoke filled in for `<the spoke>` and nothing else added: the way an agent runs it."""
    argv = [spoke if a == "<the-spoke>" else a for a in argv]
    script = {INITIATOR.name: INITIATOR, RECEIVER.name: RECEIVER}[argv[0]]
    return subprocess.run([sys.executable, "-I", str(script), *argv[1:]], input=stdin, capture_output=True, text=True, env=run.env,
                          cwd=run.tmp, timeout=120)


def test_the_commands_the_centre_of_a_star_is_told_to_run_work_as_printed():
    """NEXT carries --relay (the spokes go on every call of the initiator script), so that `forward` and `receive` do what they should."""
    run = driver.Run()
    start = run.call("ini", INITIATOR, ["start", "--participants", "a,b", *scenarios.start_args()], before=["--relay", "a,b"])
    assert start.returncode == 0, start.stdout + start.stderr
    init = driver.sections(start.stdout, "SEND")[0][1]
    forward = next(c for c in printed(start.stdout, INITIATOR) if "forward" in c)
    assert forward[:5] == [INITIATOR.name, "--me", "ini", "--relay", "a,b"]
    out = run_printed(run, forward, stdin=init)
    assert out.returncode == 0 and "--- FORWARD to: b" in out.stdout, out.stdout + out.stderr
    receive = next(c for c in printed(out.stdout, INITIATOR) if c[-1] == "receive")
    assert "--relay" in receive
    assert run_printed(run, receive, stdin=init).returncode == 0


def test_a_centre_that_takes_part_gives_its_spokes_to_the_initiator_script_and_not_to_the_receiver_script():
    run = driver.Run()
    start = run.call("ini", INITIATOR, ["start", "--participants", "ini,a,b", *scenarios.start_args()], before=["--relay", "a,b"])
    assert start.returncode == 0, start.stdout + start.stderr
    assert all("--relay" not in c for c in printed(start.stdout, RECEIVER))
    assert all("--relay" in c for c in printed(start.stdout, INITIATOR))
    task = next(c for c in printed(start.stdout, RECEIVER) if c[-1] == "task")
    assert run_printed(run, task).returncode == 0


def test_a_participant_is_sent_to_the_receiver_script_by_the_guard():
    run = scenarios.initiator_takes_part()
    guard = next(e for e in run.log if e["call"] == "initiator ini receive")
    assert guard["rc"] == 2 and "you take part in this run" in guard["stdout"] and "concord_receiver.py --me ini" in guard["stdout"]


def test_a_silent_agent_ends_the_run_for_everyone_with_a_cancel():
    run = scenarios.silent_agent_aborts()
    assert "<concord-cancel" in last(run, "initiator ini wait") and 'agent="b"' in last(run, "initiator ini wait")
    for who in ("initiator ini", "receiver a"):
        assert "The run was aborted (missing-agent, agent b)" in last(run, f"{who} report")
    assert not any(e["rc"] for e in run.log if e["call"] != "receiver b report")
    assert "there is no run for b" in last(run, "receiver b report")                              # it never heard of the run


def test_every_refusal_of_an_answer_says_what_to_do_and_stops_after_three():
    run = scenarios.receiver_errors()
    answers = [e for e in run.log if e["call"] in ("receiver a answer", "receiver b answer")]
    refused = [e for e in answers if e["rc"] == 2]
    assert refused and all("NEXT:" in e["stdout"] for e in refused)
    stops = [e for e in refused if "Stop. The same refusal came" in e["stdout"]]
    assert [re.search(r"came (\d) times", e["stdout"]).group(1) for e in stops] == ["3", "4"]
    assert not any("This task has been refused" in e["stdout"] for e in refused)                  # the scenario stays under the cap of six
    taken = [e for e in answers if e["rc"] == 0]
    assert [e["call"] for e in taken] == ["receiver a answer", "receiver b answer"] and all("STATUS: SEND" in e["stdout"] for e in taken)


def test_what_the_schema_does_not_allow_is_refused_and_what_it_allows_is_taken():
    run = scenarios.receiver_schema_enforcement()
    codes = [(e["call"], e["rc"]) for e in run.log if e["call"].endswith("answer")]
    assert [rc for _, rc in codes] == [2, 2, 0, 2, 0]


# --- the lock, and the way in -----------------------------------------------------------------------------------------------------

def test_messages_that_arrive_at_the_same_time_are_all_recorded():
    """Two calls on one state never run together: no message may be lost to another."""
    agents = [f"a{i}" for i in range(6)]
    run = driver.Run()
    start = run.call("ini", INITIATOR, ["start", "--participants", ",".join(agents), *scenarios.start_args(scenarios.OPEN)])
    init = driver.sections(start.stdout, "SEND")[0][1]
    replies = {}
    for n, a in enumerate(agents):
        run.call(a, RECEIVER, ["receive"], stdin=init)
        answer = {"raw_reject": 10, "raw_ideal": 90, "candidate": f"option {n}", "rationale": REASONS, "raw": 80, "reasoning": REASONS}
        replies[a] = driver.sections(run.call(a, RECEIVER, ["answer"], stdin=json.dumps(answer)).stdout, "SEND")[0][1]
    with ThreadPoolExecutor(len(agents)) as pool:
        list(pool.map(lambda a: run.call("ini", INITIATOR, ["receive"], stdin=replies[a]), agents))
    state = json.loads((run.tmp / "concord-state-ini.json").read_text())
    (record,) = state["runs"].values()
    assert sorted(record["engine"]["anchors"]) == sorted(agents)
    assert [e["rc"] for e in run.log if e["call"] == "initiator ini receive"] == [0] * len(agents)


def test_the_scripts_say_how_to_use_them():
    for script, words in ((INITIATOR, "open the run"), (RECEIVER, "pass your answer")):
        p = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, text=True)
        assert p.returncode == 0 and words in p.stdout
    p = subprocess.run([sys.executable, "-I", str(INITIATOR), "--me", "x", "start", "--help"], capture_output=True, text=True)
    assert p.returncode == 0 and "The ids of the agents whose satisfaction counts" in p.stdout        # the wording of the library
    assert "the slowest turn of any agent" in p.stdout


def test_a_command_line_that_is_wrong_is_an_error_that_says_what_to_do():
    p = subprocess.run([sys.executable, "-I", str(RECEIVER), "nonsense"], capture_output=True, text=True)
    assert p.returncode == 2 and p.stdout.startswith("STATUS: ERROR\n") and "NEXT:" in p.stdout
