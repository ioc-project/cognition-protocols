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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import driver  # noqa: E402
import scenarios  # noqa: E402

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


def test_replies_that_arrive_at_the_same_time_are_all_recorded():
    """The initiator serialises parallel `handle` calls on the state: no reply may be lost to another."""
    from concurrent.futures import ThreadPoolExecutor
    agents = [f"a{i}" for i in range(6)]
    run = driver.Run("test-parallel")
    p = run.initiator(["init", "--quiet", *scenarios.init_args(",".join(agents))])
    run.state = scenarios.re.search(r"^State: (.+)$", p.stdout, scenarios.re.M).group(1)
    run.initiator(["handle"])
    state_id = Path(run.state).stem.rsplit("-", 1)[-1]
    reply = ('<concord-anchor-reply agent="{a}" state="%s"><raw_reject>10</raw_reject><raw_ideal>90</raw_ideal>'
             "</concord-anchor-reply>") % state_id
    with ThreadPoolExecutor(len(agents)) as pool:
        list(pool.map(lambda a: run.initiator(["handle"], reply.format(a=a)), agents))
    st = json.loads(Path(run.state).read_text())
    assert sorted(st["anchors"]) == sorted(agents)
    assert [e["rc"] for e in run.log if e["call"].startswith("initiator handle")] == [0] * (len(agents) + 1)


def test_runs_are_deterministic():
    assert driver.dump(scenarios.success_at_score()) == driver.dump(scenarios.success_at_score())
