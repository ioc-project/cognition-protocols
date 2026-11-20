#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The `ioc_concord.cli` module, the command of the wheel: that it is wired to the library's `cli_run`. The command itself, a whole run through
its arguments and output, is tested in the Rust crate (`tests/cli.rs`)."""
import os
import subprocess
import sys

import pytest


def run(*args, env, stdin=""):
    proc = subprocess.run(
        [sys.executable, "-m", "ioc_concord.cli", *args], input=stdin, capture_output=True, text=True, env=env, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def env(tmp_path):
    return {**os.environ, "CONCORD_STATE_DIR": str(tmp_path / "state")}


def test_a_run_opens_and_its_first_message_comes_out(env, tmp_path):
    state = str(tmp_path / "run.json")
    code, out, err = run(
        "initiator", "--state", state, "init", "--agents", "cost,security", "--context", "which plan?", "--x-reject", "no plan",
        "--x-ideal", "the best plan", "--schema", '{"type": "string", "description": "x"}', env=env,
    )
    assert code == 0 and out.startswith("State: "), out + err
    code, out, err = run("initiator", "--state", state, "handle", env=env)
    assert code == 0 and out.startswith("STATUS: SEND") and "<concord-init" in out, out + err
    code, out, err = run("receiver", "--me", "cost", "handle", env=env, stdin=out.split("--- to: cost, security\n", 1)[1])
    assert code == 0 and out.startswith("STATUS: ASK") and "--reject N --ideal N" in out, out + err


def test_an_error_is_a_status_with_what_to_do_and_an_exit_code(env, tmp_path):
    code, out, err = run("initiator", "--state", str(tmp_path / "none.json"), "handle", env=env)
    assert code == 2 and out.startswith("STATUS: ERROR") and "NEXT:" in out, out + err


def test_help_exits_with_zero(env):
    code, out, err = run("receiver", "--help", env=env)
    assert code == 0 and "receiver" in out, out + err
