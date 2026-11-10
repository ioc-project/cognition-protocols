#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the library raises. A caller catches `AccordError` and decides how to report it: the library never prints or exits."""


class AccordError(Exception):
    """Base of every error the library raises on purpose. `code` is a stable, machine-readable name for the kind of error;
    `str(error)` says what is wrong, in words an orchestrator can act on."""

    code = "accord_error"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


class StateError(AccordError):
    """A saved session state cannot be loaded."""

    code = "state_error"


class PhaseError(AccordError):
    """A session is not in a phase where this step makes sense."""

    code = "invalid_phase"
