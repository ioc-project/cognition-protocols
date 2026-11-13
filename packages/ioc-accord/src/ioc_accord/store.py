#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Loading and saving a session. A state that was written by an older version is filled in from `blank_state`, so a field
added later is never missing. `locked_state` is the one entry point that may be used concurrently: parallel replies each
do a read-modify-write, and without the lock the last writer wins and earlier replies are silently lost."""
import contextlib
import fcntl
import json
from pathlib import Path

from ioc_accord.errors import AccordRefusal, StateError
from ioc_accord.state import blank_state


def load_state(path: Path) -> dict:
    if not path.exists():
        return blank_state()
    return {**blank_state(), **json.loads(path.read_text())}


def save_state(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=2))


@contextlib.contextmanager
def locked_state(path: Path):
    """Exclusive flock across load → mutate → save.

    Parallel handle calls (multiple agents replying simultaneously) each do a
    read-modify-write on the state file. Without a lock the last writer wins and
    earlier mutations are silently lost. flock(LOCK_EX) serialises them so every
    reply is recorded regardless of arrival order.

    An `AccordRefusal` is saved on its way out: it carries bookkeeping (the count of attempts that could not be
    verified) that has to survive the refusal, or the next attempt starts from zero and the session never moves past
    it. Every other exception leaves the file untouched, so a failed call records nothing.
    """
    if not path.exists():
        raise StateError(f"no state file at {path} — run init first")

    def write(fh, st):
        fh.seek(0)
        fh.write(json.dumps(st, indent=2))
        fh.truncate()

    with open(path, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            st = {**blank_state(), **json.loads(fh.read())}
            try:
                yield st
            except AccordRefusal:
                write(fh, st)       # the refusal counted this attempt; that count has to survive it
                raise
            write(fh, st)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
