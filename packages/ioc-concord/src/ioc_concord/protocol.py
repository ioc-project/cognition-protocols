#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The constants of the protocol, and the forms the spec gives to ids and numbers: the one place that names its version,
its defaults and its outcomes, and that says what a valid agent id, UUID or number looks like."""
import re

# The version every message carries on its root element. An agent processes only messages of exactly this version.
PROTOCOL_VERSION = "0.3.0"

# One million: every number of the protocol (a raw score, an anchor, `satisfaction_threshold`, `eps`) is held as an integer
# count of millionths, so that no floating-point error can change a decision (see Numbers in the spec). Text appears only on
# the wire and in reports.
MILLION = 1_000_000

# The parameters of a run and their defaults (see Configuration Parameters in the spec): `satisfaction_threshold` and `eps` in
# millionths (0.6 and 0.05), `B` is the `budget` element. A `timeout` of 0 switches the timers off: nobody asks again for a
# message and nobody gives up on a clock. That is an extension of this library, not part of the spec.
DEFAULT_PARAMS = {"satisfaction_threshold": 600_000, "eps": 50_000, "B": 8, "timeout": 300, "retries": 3}

SUCCESS, BEST_EFFORT, ABORTED = "SUCCESS", "BEST_EFFORT", "ABORTED"
OUTCOMES = (SUCCESS, BEST_EFFORT, ABORTED)
# What a <concord-result> can say: an aborted run sends no result.
RESULT_OUTCOMES = (SUCCESS, BEST_EFFORT)

_AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NUMBER_RE = re.compile(r"([0-9]{1,3})(?:\.([0-9]{1,6}))?")


def is_agent_id(text):
    """True if `text` is an agent id: 1 to 64 ASCII letters, digits, `_` or `-`."""
    return isinstance(text, str) and _AGENT_ID_RE.fullmatch(text) is not None


def is_uuid(text):
    """True if `text` is a UUID in lowercase hexadecimal with hyphens (a run id, a `uuid`, a `reply_to`, ...)."""
    return isinstance(text, str) and _UUID_RE.fullmatch(text) is not None


def parse_number(text):
    """The value of `text` in millionths, or None unless it is a number of the spec: one to three ASCII digits, then
    optionally a point and one to six digits (`82`, `82.0`, `0.5`, `007`; not `.5`, `82.`, `+5`, `1e2`, `NaN`). The range
    is for the caller to check."""
    m = _NUMBER_RE.fullmatch(text) if isinstance(text, str) else None
    if m is None:
        return None
    return int(m.group(1)) * MILLION + int((m.group(2) or "").ljust(6, "0"))


def to_millionths(value):
    """`value` in millionths, or None. `value` is the text of a number (read by `parse_number`), an int, or a float, which is
    read from its shortest text (82.5 is 82.5, 0.1 is 0.1) and so needs at most 6 decimal places and no exponent."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return parse_number(str(value)) if value >= 0 else None
    if isinstance(value, float):
        return parse_number(repr(value))
    return parse_number(value)


def format_number(millionths):
    """The text of a number of millionths, as the spec writes it: `82.0`, `0.5`, `0.000001`."""
    whole, frac = divmod(millionths, MILLION)
    return f"{whole}.{str(frac).rjust(6, '0').rstrip('0') or '0'}"
