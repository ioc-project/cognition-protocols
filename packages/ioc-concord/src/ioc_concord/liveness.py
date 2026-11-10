#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The messages an agent is owed, and the timers that wait for them. These are functions of the state of a run: they send nothing.

A batch is a set of messages from one author that arrive together: its init reply, its scores on the seeds, its score on a repair,
the repair of the bottleneck, the result of the reporter. It is owed until nothing in it is lacking, and it has a timer from the
moment it becomes owed (see Messages owed in the spec). The owed batches are worked out from the state each time, never kept; only
the timers are, in `run["timers"]`:

    key            what the batch is: `init-reply|cost`, `score|cost|seeds`, `score|cost|r1-security`, `repair|security|1`, `result|cost`
    timer          {author, what, candidate, requests, asked, deadline}; `candidate` is the first message lacking, which a request names;
                   `requests` counts the requests sent, `asked` says whether a gap has been asked for at once, and `deadline` is when the
                   timer expires (None when the run has no timers)
"""
from ioc_concord.engine import lacking, parse_candidate_id, timers_enabled
from ioc_concord.protocol import ABORTED
from ioc_concord.state import INITIATOR, RUN_ACTIVE


def lacking_by(run):
    """{agent: [candidate ids it has not rated yet], in pool order}. Before every init reply is in nothing is owed, so it is empty."""
    st, out = run["engine"], {}
    if len(st["anchors"]) < len(st["agents"]):
        return out
    for agent, cid in lacking(st):
        out.setdefault(agent, []).append(cid)
    return out


def owed_batches(run, me, ev):
    """{timer key: {author, what, candidate}}: the batches of messages that `me` is owed now, given the evaluation `ev` of the engine
    over the run. An agent is never owed its own messages. An initiator that does not negotiate is owed the init replies, and no timer
    runs for the result."""
    out = {}
    if run["status"] != RUN_ACTIVE:
        return out
    st, reporter = run["engine"], run["participants"][0]

    def owe(key, author, what, candidate=None):
        out[key] = {"author": author, "what": what, "candidate": candidate}

    for a in run["participants"]:
        if a != me and a not in st["anchors"]:
            owe(f"init-reply|{a}", a, "init-reply")
    if run["role"] == INITIATOR:
        return out
    for author, candidates in lacking_by(run).items():
        for cid in candidates:
            if author != me:
                batch = "seeds" if parse_candidate_id(cid)[0] == 0 else cid
                out.setdefault(f"score|{author}|{batch}", {"author": author, "what": "score", "candidate": cid})
    have = {c["id"] for c in st["pool"]}
    for cid in run["expected"]:
        rnd, author = parse_candidate_id(cid)
        if cid not in have and author != me:
            if rnd == 0:
                owe(f"init-reply|{author}", author, "init-reply")
            else:
                owe(f"repair|{author}|{rnd}", author, "repair", cid)
    if ev["state"] == "repair" and ev["bottleneck"] != me:
        owe(f"repair|{ev['bottleneck']}|{ev['round']}", ev["bottleneck"], "repair", f"r{ev['round']}-{ev['bottleneck']}")
    if run["outcome"] not in (None, ABORTED) and me != reporter and run["reported"] is None:
        owe(f"result|{reporter}", reporter, "result")
    return out


def sync_timers(run, me, now, ev):
    """Start the timer of every batch that has become owed, and drop the timer of every batch that is complete. When the message that was
    first lacking in a batch has arrived and the next is still owed, the wait starts again. With a `timeout` of 0 a timer has no
    deadline: nothing is ever triggered."""
    owed, timers = owed_batches(run, me, ev), run["timers"]
    for key in [k for k in timers if k not in owed]:
        del timers[key]
    for key, o in owed.items():
        t = timers.get(key)
        if t is None or t["candidate"] != o["candidate"]:
            deadline = now + run["params"]["timeout"] if timers_enabled(run["params"]) else None
            t = timers[key] = {"requests": 0, "asked": False, "deadline": deadline, "candidate": o["candidate"]}
        t.update(o)


def expired_keys(run, now):
    """The keys of the timers of `run` that have expired at `now`, the earliest first."""
    return [key for _, key in sorted((t["deadline"], key) for key, t in run["timers"].items()
                                     if t["deadline"] is not None and t["deadline"] <= now)]


def earliest_deadline(runs):
    """The earliest deadline of a timer of an active run among `runs`, or None."""
    due = [t["deadline"] for run in runs if run["status"] == RUN_ACTIVE for t in run["timers"].values() if t["deadline"] is not None]
    return min(due) if due else None
