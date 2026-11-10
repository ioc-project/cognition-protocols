#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The state of an agent: every run it knows, as plain JSON-able dicts that a `Store` can keep.

    agent state   {"me": id, "runs": {run id: run}, "active": run id or None}

    run           role           "participant" or "initiator"
                  initiator      the agent that opened the run
                  participants   in the order of <participants>
                  params         satisfaction_threshold, eps (millionths), B, timeout, retries
                  context, x_reject, x_ideal, schema
                  engine         the state of the decision engine (see engine.py)
                  sent           {item key: the original text this agent sent for that item}
                  received       {item key: the original text it recorded for that item}
                  uuids          the `uuid` of every transmission it has accepted
                  outcome        the outcome this agent computed: SUCCESS, BEST_EFFORT, ABORTED, or None while it is not known
                  reported       the reporter's result as {"outcome", "winning_candidate_id"}, once this agent holds it
                  mismatches     {"sent": [text of each mismatch error this agent sent], "received": [authors of those it got]}
                  abort          why the run aborted, as {"reason", "agent", "superseded_by"}, or None
                  cancel_sent    whether this agent has sent its cancel (it sends it once)
                  disputed       whether a cancel reached this agent after its run had ended with an outcome
                  status         "active" until the run has ended for this agent, then "ended" or "aborted"

An item is what a message is about: an init reply of an agent, a score of an agent on a candidate, a repair, the result. The
first message recorded for an item stands, and the original is kept word for word so that it can be sent again.
"""
from ioc_concord.engine import new_engine_state
from ioc_concord.errors import StateError
from ioc_concord.protocol import is_agent_id

PARTICIPANT, INITIATOR = "participant", "initiator"
RUN_ACTIVE, RUN_ENDED, RUN_ABORTED = "active", "ended", "aborted"


def new_agent_state(me):
    if not is_agent_id(me):
        raise StateError(f"an agent id is 1 to 64 letters, digits, _ or -, not {me!r}")
    return {"me": me, "runs": {}, "active": None}


def item_key(kind, author, item=None):
    """The key of an item: `init-reply|cost`, `score|cost|seed-security`, `result|cost`."""
    return "|".join(p for p in (kind, author, item) if p)


def new_run(me, init, doc):
    """The run that the parsed init `init` (see wire.parse_message) opens for `me`, with the init text `doc` as received."""
    params = dict(init["params"])
    return {"role": PARTICIPANT, "run_id": init["run_id"], "initiator": init["from"], "participants": list(init["participants"]),
            "params": params, "context": init["context"], "x_reject": init["x_reject"], "x_ideal": init["x_ideal"],
            "schema": init["schema"], "engine": new_engine_state(init["participants"], params), "sent": {},
            "received": {item_key("init", init["from"]): doc}, "uuids": [init["uuid"]], "outcome": None, "reported": None,
            "mismatches": {"sent": [], "received": []}, "abort": None, "cancel_sent": False, "disputed": False,
            "status": RUN_ACTIVE}


def members(run):
    """The agents that may send a message of the run: its participants and its initiator."""
    return set(run["participants"]) | {run["initiator"]}


def active_run(state):
    """The run that is active for the agent, or None."""
    run = state["runs"].get(state["active"]) if state["active"] else None
    return run if run is not None and run["status"] == RUN_ACTIVE else None
