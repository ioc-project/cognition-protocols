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


def test_runs_are_deterministic():
    assert driver.dump(scenarios.success_at_score()) == driver.dump(scenarios.success_at_score())
