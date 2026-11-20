#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What a call prints, as the DESIGN says: `STATUS:`, sections that each start with a `--- TITLE` line, then `NEXT:`."""
import datetime

EXIT_ERROR = 2


class CliError(Exception):
    """A fault the caller can fix: what is wrong, and what to do about it. It is printed as `STATUS: ERROR` and never changes the state."""

    def __init__(self, message, hint):
        super().__init__(message)
        self.hint = hint


class Out:
    """What one call prints: a status, sections, and what to do next."""

    def __init__(self, status):
        self.status = status
        self.sections = []
        self.next = ""

    def add(self, title, body):
        self.sections.append((title, str(body).strip("\n")))

    def text(self):
        parts = [f"STATUS: {self.status}"]
        for title, body in self.sections:
            parts.append(f"--- {title}" + (f"\n{body}" if body else ""))
        parts.append(f"NEXT: {self.next}")
        return "\n".join(parts) + "\n"


def error_text(message, hint):
    """The text of a fault: never the fault alone."""
    return f"STATUS: ERROR\n{message}\nNEXT: {hint}\n"


def wake_text(when, now):
    """A time to call `tick` (or to expect `wait` to return), as ISO 8601 with the zone, and how long from now."""
    stamp = datetime.datetime.fromtimestamp(when).astimezone().isoformat(timespec="seconds")
    return f"{stamp} (in {max(0, round(when - now))} s)"
