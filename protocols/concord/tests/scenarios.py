#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The scripted runs the tests replay. Each returns a Run whose .log is the transcript."""
import json
import re

import driver
from driver import INITIATOR, RECEIVER, REASONS

OPTIONS = ["--option", "X", "--option", "Y", "--option", "Z"]
OPEN = ["--format", "a one-line proposal"]


def start_args(schema=OPTIONS, threshold="0.6", extra=()):
    return ["--context", "Pick one option", "--x-reject", "nothing is decided", "--x-ideal", "a decision everyone accepts",
            *schema, "--satisfaction-threshold", threshold, *extra]


ANCHORS = {"a": (10, 90), "b": (20, 80), "c": (0, 100)}
SEEDS = {"a": "X", "b": "Y", "c": "Z"}


# --- complete runs -----------------------------------------------------------------------------------------------------------------

def success_at_score():
    scores = {"a": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}}
    return driver.play(start_args(), driver.policy(ANCHORS, SEEDS, scores), ["a", "b"])


def three_agents_success():
    scores = {"a": {"X": 90, "Y": 60, "Z": 40}, "b": {"X": 70, "Y": 80, "Z": 50}, "c": {"X": 80, "Y": 60, "Z": 90}}
    return driver.play(start_args(threshold="0.5"), driver.policy(ANCHORS, SEEDS, scores), ["a", "b", "c"])


def repair_then_success():
    scores = {"a": {"X": 90, "Y": 20, "Z": 85}, "b": {"X": 25, "Y": 80, "Z": 75}}
    pol = driver.policy(ANCHORS, SEEDS, scores, repairs={"a": ["Z"], "b": ["Z"]})
    return driver.play(start_args(threshold="0.7"), pol, ["a", "b"])


def best_effort_budget_spent():
    scores = {"a": {"X": 90, "Y": 20, "Z": 30}, "b": {"X": 25, "Y": 80, "Z": 30}}
    pol = driver.policy(ANCHORS, SEEDS, scores, repairs={"a": ["Z", "Z"], "b": ["Z", "Z"]})
    return driver.play(start_args(threshold="0.9", extra=["--budget", "1"]), pol, ["a", "b"])


def open_schema_with_pattern():
    schema = ["--schema", '{"type":"string","description":"a plan name","pattern":"^plan-[a-z]+$","maxLength":20}']
    seeds = {"a": "plan-alpha", "b": "plan-beta"}
    scores = {"a": {"plan-alpha": 90, "plan-beta": 60}, "b": {"plan-alpha": 70, "plan-beta": 80}}
    return driver.play(start_args(schema=schema, threshold="0.5"), driver.policy(ANCHORS, seeds, scores), ["a", "b"])


def initiator_takes_part():
    """The initiator is a participant: `start`, then the receiver script only (its guard refuses the initiator's)."""
    anchors, seeds = {"ini": (10, 90), "b": (20, 80)}, {"ini": "X", "b": "Y"}
    scores = {"ini": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}}
    p = driver.Play(driver.policy(anchors, seeds, scores), ["ini", "b"], takes_part=True)
    p.start(start_args())
    p.drain()
    p.run.call("ini", INITIATOR, ["receive"], stdin="anything")
    return p.report()


def star_with_relay():
    """The initiator is the centre of a star: every message of a spoke goes to the other spoke through `forward`."""
    scores = {"a": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}}
    return driver.play(start_args(), driver.policy(ANCHORS, SEEDS, scores), ["a", "b"], relay=["a", "b"])


def star_centre_takes_part():
    """The centre is also a participant: `forward` in the initiator script, then `receive` in the receiver script."""
    anchors, seeds = {"ini": (10, 90), "a": (20, 80), "b": (0, 100)}, {"ini": "X", "a": "Y", "b": "Z"}
    scores = {"ini": {"X": 90, "Y": 60, "Z": 40}, "a": {"X": 70, "Y": 80, "Z": 50}, "b": {"X": 80, "Y": 60, "Z": 90}}
    p = driver.Play(driver.policy(anchors, seeds, scores), ["ini", "a", "b"], takes_part=True, relay=["a", "b"])
    p.start(start_args(threshold="0.5"))
    p.drain()
    return p.report()


def silent_agent_aborts():
    """b never answers: after `retries` the initiator gives up on it and cancels, and a ends the run too."""
    scores = {"a": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}}
    p = driver.Play(driver.policy(ANCHORS, SEEDS, scores), ["a", "b"], silent=["b"])
    p.start(start_args(extra=["--timeout", "1", "--retries", "0"]))
    p.drain()
    out = p.run.call("ini", INITIATOR, ["wait", "--poll", "0.2"]).stdout          # blocks until the timer is due, then ticks
    p.queue += [(t, "ini", None) for _, t in driver.sections(out, "SEND")]
    p.drain()
    return p.report()


# --- the scripts' own errors -----------------------------------------------------------------------------------------------------

def initiator_errors():
    run = driver.Run()
    call = lambda *argv, **kw: run.call("ini", INITIATOR, list(argv), **kw)
    base = ["--participants", "a,b", "--context", "Pick one option", "--x-reject", "nothing", "--x-ideal", "all"]
    for command in ("receive", "tick", "wait", "report"):                         # no run yet
        call(command, stdin="x")
    call("forward", stdin="x")                                                    # no --relay
    call("start", *base)                                                           # none of the ways to say what an option is
    call("start", *base, *OPTIONS, "--format", "words")                           # two of them
    call("start", *base, "--schema", "{not json")
    call("start", *base, *OPTIONS, "--satisfaction-threshold", "2")               # the library refuses it
    call("start", "--participants", "a", "--context", "c", "--x-reject", "r", "--x-ideal", "i", *OPTIONS)
    call("start", *base, "--format", "words", "--timeout", "slow")                # not an integer
    call("receive", stdin="   ")                                                  # an empty message
    p = call("start", *base, *OPEN)                                               # a valid start
    call("start", *base, *OPEN)                                                   # a second one, while the first is open
    call("receive", stdin="hello, this is not a CONCORD message")
    call("report")
    return run


def receiver_errors():
    """Every kind of refused answer. A task is refused at most STOP_AFTER_ANY - 1 times in a row here (a model is stopped at the sixth), so
    the kinds are shared between two participants."""
    run = driver.Run()
    ini = lambda *argv, **kw: run.call("ini", INITIATOR, list(argv), **kw)
    rec = lambda *argv, **kw: run.call("a", RECEIVER, list(argv), **kw)
    recb = lambda *argv, **kw: run.call("b", RECEIVER, list(argv), **kw)
    for command in ("tick", "report", "task", "status"):                           # no run yet
        rec(command)
    rec("answer", stdin="{}")                                                     # nothing is due
    init = driver.sections(ini("start", "--participants", "a,b", *start_args(OPTIONS)).stdout, "SEND")[0][1]
    rec("receive", stdin=init)
    rec("receive", stdin=init)                                                    # the same message again: a duplicate
    recb("receive", stdin=init)
    good = {"raw_reject": 10, "raw_ideal": 90, "candidate": "X", "rationale": REASONS, "raw": 85, "reasoning": REASONS}
    rec("answer", stdin="{not json")
    rec("answer", stdin="[1, 2]")                                                 # not an object
    rec("answer", stdin=json.dumps({"raw_reject": 10}))                           # what it lacks, next to what it sent
    rec("answer", stdin=json.dumps({**good, "raw": "high"}))                      # a number that is not one
    rec("answer", stdin=json.dumps({**good, "raw_reject": 0.1, "raw_ideal": 0.9, "raw": 0.8}))      # normalised scores
    rec("answer", stdin=json.dumps(good))                                         # and a good answer is taken
    rec("answer", stdin=json.dumps(good))                                         # nothing is due again: not to be retried
    rec("task")
    rec("status")
    rec("receive", stdin="   ")
    recb("answer", stdin=json.dumps({**good, "raw_reject": 80, "raw_ideal": 20}))  # anchors the wrong way round
    for _ in range(4):                                                            # the same refusal three times: it stops, and goes on stopping
        recb("answer", stdin=json.dumps({**good, "rationale": "short", "reasoning": "short"}))
    recb("answer", stdin=json.dumps(good))                                        # a good answer is taken, whatever came before
    return run


def receiver_schema_enforcement():
    """What a candidate must be, as the schema of the init says: the receiver refuses what the schema does not allow."""
    run = driver.Run()
    ini = lambda *argv, **kw: run.call("ini", INITIATOR, list(argv), **kw)
    rec = lambda *argv, **kw: run.call("a", RECEIVER, list(argv), **kw)
    answer = lambda candidate: rec("answer", stdin=json.dumps({"raw_reject": 10, "raw_ideal": 90, "candidate": candidate,
                                                              "rationale": REASONS, "raw": 85, "reasoning": REASONS}))
    schema = '{"type":"string","description":"a plan name","pattern":"^plan-[a-z]+$","maxLength":12}'
    init = driver.sections(ini("start", "--participants", "a,b", *start_args(["--schema", schema])).stdout, "SEND")[0][1]
    rec("receive", stdin=init)
    answer("a plan with no name")                                                 # does not match the pattern
    answer("plan-averyverylongname")                                              # matches it, but is too long
    answer("plan-alpha")                                                          # fine
    init2 = driver.sections(run.call("ini2", INITIATOR, ["start", "--participants", "c,d", *start_args(OPTIONS)]).stdout, "SEND")[0][1]
    run.call("c", RECEIVER, ["receive"], stdin=init2)
    run.call("c", RECEIVER, ["answer"], stdin=json.dumps({"raw_reject": 10, "raw_ideal": 90, "candidate": "W", "rationale": REASONS,
                                                          "raw": 85, "reasoning": REASONS}))      # not in the list of options
    run.call("c", RECEIVER, ["answer"], stdin=json.dumps({"raw_reject": 10, "raw_ideal": 90, "candidate": "X", "rationale": REASONS,
                                                          "raw": 85, "reasoning": REASONS}))
    return run


SCENARIOS = {f.__name__: f for f in (
    success_at_score, three_agents_success, repair_then_success, best_effort_budget_spent, open_schema_with_pattern, initiator_takes_part,
    star_with_relay, star_centre_takes_part, silent_agent_aborts, initiator_errors, receiver_errors, receiver_schema_enforcement)}
