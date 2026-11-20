#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the scripts are given that is not what they expect, and what they must do about it: a clean error that says what to do, never a
traceback, and no run started on a wrong configuration. One file for the review of the scripts; the scenarios are in test_scripts.py."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import driver  # noqa: E402
import scenarios  # noqa: E402
from driver import COMMAND, INITIATOR, RECEIVER, REASONS  # noqa: E402


def start(run, me, *extra, participants="a,b", context="c", before=()):
    """`start` for the initiator `me`, with what the test does not care about filled in."""
    return run.call(me, INITIATOR, ["start", "--participants", participants, "--context", context, "--x-reject", "r", "--x-ideal", "i", *extra],
                    before=before)


def the_init(p):
    return driver.sections(p.stdout, "SEND")[0][1]


# --- the stop of a model that cannot answer ------------------------------------------------------------------------------------------

def test_a_model_that_changes_its_bad_answer_each_time_is_stopped_all_the_same():
    """The refusal of a score says the numbers it was given, so a model that tries other numbers never repeats a refusal: it is stopped
    after STOP_AFTER_ANY refusals of the one task, whatever their reasons (spec, Appendix A.7: limit the retries)."""
    run = driver.Run()
    run.call("a", RECEIVER, ["receive"], stdin=the_init(start(run, "ini", "--format", "x")))
    stopped = []
    for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        answer = {"raw_reject": v, "raw_ideal": v + 0.05, "candidate": "x", "rationale": REASONS, "raw": v, "reasoning": REASONS}
        p = run.call("a", RECEIVER, ["answer"], stdin=json.dumps(answer))
        assert p.returncode == 2
        stopped.append("Stop." in p.stdout)
    assert stopped == [False] * 5 + [True]
    assert "This task has been refused 6 times in a row" in p.stdout


# --- the spokes of a star ----------------------------------------------------------------------------------------------------------

def test_start_refuses_a_participant_that_is_not_a_spoke_and_starts_nothing():
    run = driver.Run()
    p = start(run, "ini", "--format", "x", before=["--relay", "a,c"])                       # the init would go to a and c, never to b
    assert p.returncode == 2 and "b is a participant but not one of the spokes (--relay a,c)" in p.stdout
    assert "Nothing was started" in p.stdout and not (run.tmp / "concord-state-ini.json").exists()
    p = start(run, "ini", "--format", "x", before=["--relay", "a,b,c"])                     # more spokes than participants is fine
    assert p.returncode == 0 and "--- SEND to: a, b, c" in p.stdout


def test_a_centre_that_takes_part_need_not_name_itself_as_a_spoke():
    run = driver.Run()
    p = start(run, "hub", "--format", "x", participants="hub,a", before=["--relay", "a"])
    assert p.returncode == 0 and "--- SEND to: a" in p.stdout


def test_forward_must_be_told_which_spoke_the_message_came_from():
    run = driver.Run()
    relay = ["--relay", "a,b"]
    init = the_init(start(run, "ini", "--format", "x", before=relay))
    p = run.call("ini", INITIATOR, ["forward", "--from", "z"], stdin=init, before=relay)
    assert p.returncode == 2 and "z is not one of the spokes (--relay a,b)" in p.stdout
    p = run.call("ini", INITIATOR, ["forward", "--from", "a"], stdin=init, before=relay)
    assert p.returncode == 0 and "--- FORWARD to: b" in p.stdout


# --- the texts that are files ----------------------------------------------------------------------------------------------------

def test_a_file_is_read_as_utf_8_and_a_file_that_is_not_is_an_error_and_not_a_traceback():
    run = driver.Run()
    (run.tmp / "ctx.txt").write_text("décision → 日本語", encoding="utf-8")
    p = start(run, "ini1", "--format", "x", context="@ctx.txt")
    assert p.returncode == 0 and "<context>décision → 日本語</context>" in p.stdout
    (run.tmp / "bad.txt").write_bytes("décision".encode("latin-1"))
    p = start(run, "ini2", "--format", "x", context="@bad.txt")
    assert p.returncode == 2 and "bad.txt is not UTF-8 text" in p.stdout and "Traceback" not in p.stderr


def test_a_text_that_starts_with_an_at_sign_is_a_text_and_not_a_file():
    run = driver.Run()
    p = start(run, "ini1", "--option", "@alice", "--option", "@bob")                       # an option is never a file
    assert p.returncode == 0 and '{"enum": ["@alice", "@bob"]}' in p.stdout
    p = start(run, "ini2", "--format", "x", context="@@handle")                             # @@ writes a literal @
    assert p.returncode == 0 and "<context>@handle</context>" in p.stdout
    p = start(run, "ini3", "--format", "x", context="@nofile.txt")                          # and a file that is not there says how to
    assert p.returncode == 2 and "cannot read nofile.txt" in p.stdout and "written with @@" in p.stdout


# --- the ways in -------------------------------------------------------------------------------------------------------------------

def test_a_closed_standard_input_is_an_error_and_not_a_traceback(tmp_path):
    p = subprocess.run(["sh", "-c", f"{shlex.join(COMMAND)} {RECEIVER} --me a receive <&-"], capture_output=True, text=True, cwd=tmp_path)
    assert p.returncode == 2 and "Traceback" not in p.stderr
    assert p.stdout.startswith("STATUS: ERROR\nreceive reads standard input and there is none\nNEXT: Pipe the text in")


def test_wait_on_a_run_that_is_over_does_not_print_the_result_again():
    run = scenarios.success_at_score()
    for _ in range(2):                                                                      # a model that starts `wait` again each time
        p = run.call("a", RECEIVER, ["wait"])
        assert p.returncode == 0 and p.stdout.startswith("STATUS: DONE\nNEXT: The run is over") and "--- END" not in p.stdout
    assert "--- END" in run.call("a", RECEIVER, ["report"]).stdout                            # `report` still gives it
