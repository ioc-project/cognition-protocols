#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the library raises. A caller catches `ConcordError` and decides how to report it: the library never prints or exits."""


class ConcordError(Exception):
    """Base of every error the library raises on purpose. `code` is a stable, machine-readable name for the kind of error;
    `str(error)` says what is wrong, in words an agent can act on."""

    code = "concord_error"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


class SchemaError(ConcordError):
    """A candidate schema (or one of its keywords) is not valid or not supported."""

    code = "invalid_schema"


class ReplyError(ConcordError):
    """A reply or request could not be read: it is not CONCORD XML, not well-formed, or lacks a field it needs."""

    code = "invalid_reply"


class InvalidRun(ConcordError):
    """The parameters of a run are not valid (too few agents, a threshold out of range, ...)."""

    code = "invalid_run"


class StateError(ConcordError):
    """A saved state cannot be loaded."""

    code = "state_error"
