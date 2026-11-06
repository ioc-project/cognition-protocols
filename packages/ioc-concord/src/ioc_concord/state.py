#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The two states a run is made of: the run state the initiator keeps and the state each agent keeps. Both are plain dicts,
so any `Store` can hold either; these constructors are the one place that says what is in them."""
import json

from ioc_concord.errors import InvalidRun
from ioc_concord.schema import render_schema, validate_schema
from ioc_concord.wire import DEFAULT_PARAMS

STATE_VERSION = 2


def check_run(agents, satisfaction_threshold, eps, B):
    """Raises InvalidRun unless these are the parameters of a run that can be played."""
    if len(agents) < 2:
        raise InvalidRun("a negotiating set needs at least 2 agents")
    if len(set(agents)) != len(agents):
        raise InvalidRun("duplicate agent names")
    if not (0 < satisfaction_threshold <= 1):
        raise InvalidRun("satisfaction_threshold must be in (0, 1]")
    if eps <= 0:
        raise InvalidRun("eps must be > 0")
    if B < 1:
        raise InvalidRun("B must be >= 1")


def new_run_state(agents, context, x_reject, x_ideal, spec, satisfaction_threshold=DEFAULT_PARAMS["satisfaction_threshold"],
                  eps=DEFAULT_PARAMS["eps"], B=DEFAULT_PARAMS["B"], relay=True,
                  state_id=None):
    """The state of a new run, as the initiator keeps it: the engine state plus the brief that the requests are built from.
    `spec` is the candidate schema (see `ioc_concord.schema`); `state_id` names the run in every message."""
    check_run(agents, satisfaction_threshold, eps, B)
    spec = validate_schema(spec)
    st = {
        "version": STATE_VERSION,
        "params": {"satisfaction_threshold": satisfaction_threshold, "eps": eps, "B": B, "W": "leximin"},
        "agents": list(agents),
        "schema": render_schema(spec),
        "schema_json": json.dumps(spec, ensure_ascii=False, sort_keys=True),
        "options": list(spec["enum"]) if "enum" in spec else None,
        "context": context,
        "x_reject": x_reject,
        "x_ideal": x_ideal,
        "anchors": {},
        "pool": [],
        "raw": {a: {} for a in agents},
        "score_reasoning": {},
        "x_star": None,
        "t": 0,
        "pending": None,
        "outcome": None,
        "log": [],
        "relay": relay,
    }
    if state_id is not None:
        st["state_id"] = state_id
    return st


def new_agent_state(me, state_id=None):
    """The state an agent keeps for one run: its picture of the run (`mirror`, filled in from the messages it reads), what it
    was asked, and what it has sent."""
    return {"me": me, "id": state_id, "mirror": None, "asked": {}, "outbox": {}, "resend": None,
            "sent": {"anchor": False, "seed": False, "repair": [], "rated": []}, "lead": None}
