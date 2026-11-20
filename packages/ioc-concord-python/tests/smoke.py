#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Does the installed wheel work? Run it with the Python of a clean environment where only the wheel is installed, from any folder:
`python smoke.py`. Standard library only: no pytest, no files of the repository.

It checks what a user of the wheel relies on: the module and its native library load, a whole run goes through the library, and the
`ioc-concord` executable that the wheel installs runs a command and prints what an agent reads.
"""
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import ioc_concord as ic

SPEC = json.dumps({"type": "string", "description": "what one option looks like"})


last = None  # the last command that ran: what it printed, for a failure


def check(condition, what):
    if not condition:
        detail = "" if last is None else f"\n  code {last.returncode}\n  stdout: {last.stdout!r}\n  stderr: {last.stderr!r}"
        sys.exit(f"smoke test failed: {what}{detail}")
    print(f"ok: {what}")


def a_run_through_the_library():
    agents = [ic.Agent("cost"), ic.Agent("security")]
    queue = list(agents[0].start(["cost", "security"], "which plan?", "no plan", "the best plan", SPEC).send)
    for _ in range(40):
        if not queue and not any(a.due() for a in agents):
            break
        delivered, queue = queue, []
        for text in delivered:
            for agent in agents:
                queue.extend(agent.receive(text).send)
        for agent in agents:
            for task in agent.due():
                number = ic.Number.INT
                if isinstance(task, ic.Task.INIT_REPLY):
                    step = agent.submit_init_reply(task.run_id, number(0), number(100), "plan A", "the facts", number(90), "why")
                elif isinstance(task, ic.Task.SCORE):
                    scores = [ic.ScoreInput(id=c.id, raw=number(80), reasoning="it holds") for c in task.candidates]
                    step = agent.submit_score(task.run_id, scores)
                else:
                    step = agent.submit_repair(task.run_id, "plan B", "the facts", number(90), "why")
                queue.extend(step.send)
    return json.loads(agents[1].report_json())["outcome"]


def the_executable():
    name = "ioc-concord.exe" if os.name == "nt" else "ioc-concord"
    return Path(sys.executable).parent / name


def main():
    check(ic.version() == importlib.metadata.version("ioc-concord"), f"the library loads, version {ic.version()}")
    check(ic.protocol_version() == "0.3.0", "it speaks the wire version 0.3.0")
    check(a_run_through_the_library() == "SUCCESS", "a run between two agents ends with SUCCESS")

    exe = the_executable()
    check(exe.exists(), f"the wheel installs the executable {exe.name}")
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "CONCORD_STATE_DIR": folder}

        def run(*args, stdin=""):
            global last
            last = subprocess.run(
                [str(exe), *args], input=stdin.encode(), capture_output=True, env=env, cwd=folder, timeout=120
            )
            last.stdout, last.stderr = last.stdout.decode("utf-8"), last.stderr.decode("utf-8")
            return last

        help_ = run("receiver", "--help")
        check(help_.returncode == 0 and "receiver" in help_.stdout, "the executable prints its help")
        state = str(Path(folder) / "run.json")
        init = run("initiator", "--state", state, "init", "--agents", "cost,security", "--context", "which plan? café ✓ 中文", "--x-reject",
                   "no plan", "--x-ideal", "the best plan", "--schema", SPEC)
        check(init.returncode == 0 and init.stdout.startswith("State: "), "initiator init opens a run")
        first = run("initiator", "--state", state, "handle")
        check(first.stdout.startswith("STATUS: SEND") and "<concord-init" in first.stdout, "initiator handle prints the first message")
        message = first.stdout.split("\n--- to: cost, security\n", 1)[1]
        task = run("receiver", "--me", "cost", "handle", stdin=message)
        check(task.stdout.startswith("STATUS: ASK") and "--reject N --ideal N" in task.stdout, "a receiver is asked for its judgement")
        error = run("initiator", "--state", str(Path(folder) / "none.json"), "handle")
        check(error.returncode == 2 and error.stdout.startswith("STATUS: ERROR"), "an error is a status with an exit code")
    print("smoke test passed")


if __name__ == "__main__":
    main()
