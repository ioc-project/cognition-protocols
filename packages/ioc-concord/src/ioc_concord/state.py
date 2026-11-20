#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The state of an agent: every run it knows, as plain JSON-able dicts that a `Store` can keep.

    agent state   {"me": id, "runs": {run id: run}, "active": run id or None}

    run           role           "participant", or "initiator" for the agent that opened the run without being one of its participants
                  initiator      the agent that opened the run
                  participants   in the order of <participants>
                  params         satisfaction_threshold, eps (millionths), B, timeout, retries
                  context, x_reject, x_ideal, schema
                  engine         the state of the decision engine (see engine.py)
                  sent           {item key: the original text this agent sent for that item}
                  sent_by_uuid   {uuid: the same texts}, to find the one a damaged-document request names
                  received       {item key: the original text it recorded for that item}
                  uuids          {uuid: True} for the transmissions it has accepted, the latest MAX_UUIDS of them
                  outcome        the outcome this agent computed: SUCCESS, BEST_EFFORT, ABORTED, or None while it is not known
                  reported       the reporter's result as {"outcome", "winning_candidate_id"}, once this agent holds it
                  mismatches     {"sent": how many mismatch errors this agent sent, "last": the text of the last, "received": [authors
                                 of those it got]}
                  timers         {timer key: {author, what, candidate, requests, deadline, asked}} for the messages the agent is owed
                  held           {item key: {uuid, doc}} of the repairs and results that cannot be judged yet, one per item,
                                 judged again as the state fills
                  expected       the ids of candidates that other messages prove to exist, whose proposal this agent lacks
                  unrated        [[author, candidate id]] of the ratings that arrived before the proposal they rate, asked for at
                                 once when the proposal comes, until the rating is in
                  abort          why the run aborted, as {"reason", "agent", "superseded_by", "note"}, or None; the note is for the initiator
                                 that gave up on a version refusal: {"agent", "text"}
                  cancel_sent    whether this agent has sent its cancel (it sends it once)
                  disputed       whether a cancel reached this agent after its run had ended with an outcome
                  status         "active" until the run has ended for this agent, then "ended" or "aborted"

An item is what a message is about: an init reply of an agent, a score of an agent on a candidate, a repair, the result. The
first message recorded for an item stands, and the original is kept word for word so that it can be sent again.
"""
from ioc_concord.engine import new_engine_state
from ioc_concord.errors import StateError
from ioc_concord.protocol import DEFAULT_PARAMS, is_agent_id, is_uuid
from ioc_concord.wire import peek

PARTICIPANT, INITIATOR = "participant", "initiator"
RUN_ACTIVE, RUN_ENDED, RUN_ABORTED = "active", "ended", "aborted"


def new_agent_state(me):
    if not is_agent_id(me):
        raise StateError(f"an agent id is 1 to 64 letters, digits, _ or -, not {me!r}")
    return {"me": me, "runs": {}, "active": None}


# What a saved run holds (see the top of this module): the keys, and which of them are lists and which are dicts.
RUN_KEYS = ("role", "run_id", "initiator", "participants", "params", "context", "x_reject", "x_ideal", "schema", "engine", "sent",
            "sent_by_uuid", "received", "uuids", "outcome", "reported", "mismatches", "timers", "held", "expected", "unrated", "abort",
            "cancel_sent", "disputed", "status")
_RUN_LISTS = ("participants", "expected", "unrated")
_RUN_DICTS = ("params", "schema", "engine", "sent", "sent_by_uuid", "received", "uuids", "mismatches", "timers", "held")
_ENGINE_KEYS = tuple(new_engine_state(["a", "b"], DEFAULT_PARAMS))


def check_agent_state(state, me):
    """`state` itself, if it is the saved state of the agent `me`: the shape that the agent relies on, not every value in it. Raises
    StateError, with the reason, otherwise, so that a state that is not one is never taken for an empty one or fails later with a
    KeyError. What `new_agent_state` and `new_run` make passes."""
    def bad(why):
        raise StateError(f"the saved state is not the state of an agent: {why}")

    if not isinstance(state, dict):
        bad("it is not a JSON object")
    if state.get("me") != me:
        raise StateError(f"the saved state belongs to {state.get('me')!r}, not to {me!r}")
    runs = state.get("runs")
    if not isinstance(runs, dict):
        bad('"runs" is not an object')
    if state.get("active") is not None and state["active"] not in runs:
        bad('"active" names a run that is not in "runs"')
    for run_id, run in runs.items():
        if not is_uuid(run_id):
            bad(f"{run_id!r} is not a run id")
        if not isinstance(run, dict):
            bad(f"the run {run_id} is not an object")
        missing = [k for k in RUN_KEYS if k not in run]
        if missing:
            bad(f"the run {run_id} lacks {', '.join(missing)}")
        if run["run_id"] != run_id:
            bad(f"the run {run_id} says it is {run['run_id']!r}")
        if run["role"] not in (PARTICIPANT, INITIATOR) or run["status"] not in (RUN_ACTIVE, RUN_ENDED, RUN_ABORTED):
            bad(f"the run {run_id} has a role or a status that is not one")
        for k in _RUN_LISTS:
            if not isinstance(run[k], list):
                bad(f'"{k}" of the run {run_id} is not a list')
        for k in _RUN_DICTS:
            if not isinstance(run[k], dict):
                bad(f'"{k}" of the run {run_id} is not an object')
        if not all(is_agent_id(a) for a in run["participants"]) or not is_agent_id(run["initiator"]):
            bad(f"the run {run_id} names an agent that has no valid id")
        if set(run["params"]) != set(DEFAULT_PARAMS) or any(k not in run["engine"] for k in _ENGINE_KEYS):
            bad(f"the run {run_id} holds no parameters or no engine state")
    return state


def item_key(kind, author, item=None):
    """The key of an item: `init-reply|cost`, `score|cost|seed-security`, `result|cost`."""
    return "|".join(p for p in (kind, author, item) if p)


# How many accepted uuids a run keeps. The spec keeps them all, but a participant can send as many messages as it likes, each with a new
# uuid, so this library forgets the oldest past this number (far above what an honest run sends). A copy of a message that is that old
# and arrives again is read once more, which the rules of the protocol make harmless.
MAX_UUIDS = 4096


def keep_uuid(run, uid):
    """Remember that the transmission `uid` has been accepted, and forget the oldest ones past MAX_UUIDS."""
    uuids = run["uuids"]
    uuids[uid] = True
    while len(uuids) > MAX_UUIDS:
        del uuids[next(iter(uuids))]


def keep_sent(run, key, text):
    """Keep `text`, the original that this agent sent for the item `key`, to send it again word for word."""
    run["sent"][key] = text
    run["sent_by_uuid"][peek(text)["attrs"]["uuid"]] = text


def new_run(me, init, doc):
    """The run that the parsed init `init` (see wire.parse_message) opens for `me`, with the init text `doc`: as received, or as
    sent when `me` is the initiator. An initiator that is not a participant has the role INITIATOR: it negotiates nothing, and
    only watches the init replies and the result."""
    params = dict(init["params"])
    mine = init["from"] == me
    run = {"role": PARTICIPANT if me in init["participants"] else INITIATOR, "run_id": init["run_id"],
            "initiator": init["from"], "participants": list(init["participants"]), "params": params, "context": init["context"],
            "x_reject": init["x_reject"], "x_ideal": init["x_ideal"], "schema": init["schema"],
            "engine": new_engine_state(init["participants"], params),
            "sent": {}, "sent_by_uuid": {}, "received": {} if mine else {item_key("init", init["from"]): doc},
            "uuids": {init["uuid"]: True}, "outcome": None, "reported": None,
            "mismatches": {"sent": 0, "last": None, "received": []},
            "timers": {}, "held": {}, "expected": [], "unrated": [], "abort": None, "cancel_sent": False, "disputed": False,
            "status": RUN_ACTIVE}
    if mine:
        keep_sent(run, item_key("init", me), doc)
    return run


def members(run):
    """The agents that may send a message of the run: its participants and its initiator."""
    return set(run["participants"]) | {run["initiator"]}


def active_run(state):
    """The run that is active for the agent, or None."""
    run = state["runs"].get(state["active"]) if state["active"] else None
    return run if run is not None and run["status"] == RUN_ACTIVE else None
