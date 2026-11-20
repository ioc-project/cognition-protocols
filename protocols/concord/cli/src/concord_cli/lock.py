#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""One lock per state, so that two calls on it, from either script, run one after the other."""
import contextlib
import fcntl


@contextlib.contextmanager
def locked(state_path):
    """Hold an exclusive lock on `<state_path>.lock` for the block. A call that finds it taken waits for it. The lock file is on its own
    because a save renames the state file over the old one, which gives it a new identity. It is never read or deleted."""
    with open(f"{state_path}.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
