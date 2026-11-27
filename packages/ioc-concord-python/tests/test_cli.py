#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The `ioc_concord.cli` module, the command of the wheel: that it is wired to the library's `cli_run`. The command itself is tested in the
Rust crate (`tests/cli.rs`, and `tests/transcripts.rs`, which replays the transcripts of the skill scripts), and through this wheel by
`just test-transcripts`."""
import os
import subprocess
import sys

import pytest


def run(*args, cwd, stdin=None):
    proc = subprocess.run(
        [sys.executable, "-m", "ioc_concord.cli", *args], input=None if stdin is None else stdin.encode("utf-8"), capture_output=True,
        cwd=cwd, timeout=60,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def the_message(out):
    """The text of the first SEND section of an output."""
    return out.split("\n--- SEND to: ", 1)[1].split("\n", 1)[1].split("\nNEXT:", 1)[0].split("\n--- ", 1)[0]


def test_a_run_opens_and_its_first_message_comes_out(tmp_path):
    code, out, err = run(
        "initiator", "--me", "ini", "start", "--participants", "cost,security", "--context", "which plan? café ✓ 中文", "--x-reject",
        "no plan", "--x-ideal", "the best plan", "--format", "a plan", cwd=tmp_path,
    )
    assert code == 0 and out.startswith("STATUS: SEND\n--- SEND to: cost, security\n<concord-init "), out + err
    assert "café ✓ 中文" in out and (tmp_path / "concord-state-ini.json").exists()
    code, out, err = run("receiver", "--me", "cost", "receive", cwd=tmp_path, stdin=the_message(out))
    assert code == 0 and out.startswith("STATUS: ANSWER\n--- BRIEF\n") and "ANSWER FORMAT" in out, out + err


def test_an_error_is_a_status_with_what_to_do_and_an_exit_code(tmp_path):
    code, out, err = run("initiator", "--me", "ini", "report", cwd=tmp_path)
    assert code == 2 and out.startswith("STATUS: ERROR\nthere is no run for ini") and "NEXT:" in out, out + err


@pytest.mark.skipif(os.name == "nt", reason="a closed standard input is a feature of POSIX shells")
def test_a_closed_standard_input_is_an_error_and_not_a_traceback(tmp_path):
    proc = subprocess.run(["sh", "-c", f'"{sys.executable}" -m ioc_concord.cli receiver --me a receive <&-'], capture_output=True,
                          text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2 and proc.stdout.startswith("STATUS: ERROR\nreceive reads standard input and there is none\n"), proc


def test_help_exits_with_zero(tmp_path):
    code, out, err = run("receiver", "--help", cwd=tmp_path)
    assert code == 0 and out.startswith("usage: uvx ioc-concord receiver"), out + err
