#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Where a run's state is kept. The library asks only for `load` and `save`: how and where is the caller's choice (a file, a
database, memory). Locking and path policy belong to the caller too: a store makes one `save` all-or-nothing, and
nothing more."""
import json
import os
from pathlib import Path
from typing import Protocol

from ioc_concord.errors import StateError


class Store(Protocol):
    """What a state store must offer."""

    def load(self) -> dict:
        """The saved state. Raises StateError if there is none or it cannot be read."""

    def save(self, state: dict) -> None:
        """Replace the saved state with `state`."""


class FileStore:
    """A state kept in one JSON file. A save writes a temporary file next to it and renames it over the old one, so a reader
    (or a crash) never sees half a state. The rename gives the file a new identity: serialise writers with a lock on a
    separate file, not on this one."""

    def __init__(self, path, indent=2):
        self.path = Path(path)
        self.indent = indent

    def exists(self):
        return self.path.exists()

    def load(self):
        if not self.path.exists():
            raise StateError(f"no state file at {self.path}")
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise StateError(f"state file {self.path} is not valid JSON ({exc})")

    def save(self, state):
        text = json.dumps(state, indent=self.indent) + "\n"          # first: a state that is not JSON leaves the file alone
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
