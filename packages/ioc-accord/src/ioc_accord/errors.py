#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the library raises. A caller catches `AccordError` and decides how to report it: the library never prints or
exits. Every one of them leaves the session usable — nothing is half-recorded."""


class AccordError(Exception):
    """Base of every error the library raises on purpose. `code` is a stable, machine-readable name for the kind of
    error; `str(error)` says what is wrong, in words an orchestrator can act on."""

    code = "accord_error"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


class AccordRefusal(AccordError):
    """An error whose bookkeeping must outlive it, so the state is written before it propagates.

    A refusal that counts an attempt (see `flow.verify_reply_check`) has to leave that count behind: otherwise the
    next attempt starts from zero and the session can never move past it. `store.locked_state` knows to save on this
    one, which is the whole reason it is a separate class.
    """

    code = "refused"


class StateError(AccordError):
    """A saved session state cannot be loaded."""

    code = "state_error"


class PhaseError(AccordError):
    """A session is not in a phase where this step makes sense."""

    code = "invalid_phase"
