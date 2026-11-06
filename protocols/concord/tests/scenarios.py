#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The scripted runs the tests replay. Each returns a Run whose .log is the transcript."""
import re

import driver

ENUM = '{"enum":["X","Y","Z"]}'
OPEN = '{"type":"string","description":"a one-line proposal"}'


def init_args(agents="a,b", schema=ENUM, thr="0.6", extra=()):
    return ["--agents", agents, "--satisfaction-threshold", thr, "--schema", schema, "--context", "Pick one option",
            "--x-reject", "nothing is decided", "--x-ideal", "a decision everyone accepts", *extra]


ANCHORS = {"a": (10, 90), "b": (20, 80), "c": (0, 100)}
SEEDS = {"a": "X", "b": "Y", "c": "Z"}


def success_at_score():
    pol = driver.policy(ANCHORS, SEEDS, {"a": {"X": 90, "Y": 50}, "b": {"X": 75, "Y": 70}})
    return driver.play(init_args(), pol)


def three_agents_success():
    scores = {"a": {"X": 90, "Y": 60, "Z": 40}, "b": {"X": 70, "Y": 80, "Z": 50}, "c": {"X": 80, "Y": 60, "Z": 90}}
    return driver.play(init_args("a,b,c", thr="0.5"), driver.policy(ANCHORS, SEEDS, scores))


def repair_then_success():
    scores = {"a": {"X": 90, "Y": 20, "Z": 85}, "b": {"X": 25, "Y": 80, "Z": 75}}
    pol = driver.policy(ANCHORS, SEEDS, scores, repairs={"a": ["Z"], "b": ["Z"]})
    return driver.play(init_args(thr="0.7"), pol)


def best_effort_budget_spent():
    scores = {"a": {"X": 90, "Y": 20, "Z": 30}, "b": {"X": 25, "Y": 80, "Z": 30}}
    pol = driver.policy(ANCHORS, SEEDS, scores, repairs={"a": ["Z", "Z"], "b": ["Z", "Z"]})
    return driver.play(init_args(thr="0.9", extra=["-B", "1"]), pol)


def open_schema_with_pattern():
    schema = '{"type":"string","description":"a plan name","pattern":"^plan-[a-z]+$","maxLength":20}'
    seeds = {"a": "plan-alpha", "b": "plan-beta"}
    scores = {"a": {"plan-alpha": 90, "plan-beta": 60}, "b": {"plan-alpha": 70, "plan-beta": 80}}
    return driver.play(init_args(schema=schema, thr="0.5"), driver.policy(ANCHORS, seeds, scores))


def no_relay_first_message():
    run = driver.Run("test-norelay")
    p = run.initiator(["init", "--quiet", *init_args(extra=["--no-relay"])])
    run.state = re.search(r"^State: (.+)$", p.stdout, re.M).group(1)
    run.initiator(["handle"])
    run.initiator(["--format", "json", "status"], label="status json")
    return run


def error_paths():
    """Bad input to every entry point: each must end in a status and a way back, never a traceback."""
    run = driver.Run("test-errors")
    ok = init_args()
    run.initiator(["init", "--quiet", *init_args(schema="not json")], label="init bad-json-schema")
    run.initiator(["init", "--quiet", *init_args(schema='{"enum":["X"]}')], label="init one-option-enum")
    run.initiator(["init", "--quiet", *init_args(schema='{"type":"string","description":"d","pattern":"("}')],
                  label="init invalid-pattern")
    run.initiator(["init", "--quiet", *init_args(schema='{"type":"string","description":"d","color":1}')],
                  label="init unknown-keyword")
    run.initiator(["init", "--quiet", *init_args(schema='{"type":"string","description":"d","minLength":5,"maxLength":2}')],
                  label="init min-gt-max")
    run.initiator(["init", "--quiet"], label="init missing-args")
    run.initiator(["frobnicate"], label="unknown-command")
    run.initiator(["--state", str(run.tmp / "nope.json"), "status"], label="status no-state")
    p = run.initiator(["init", "--quiet", *ok])
    run.state = re.search(r"^State: (.+)$", p.stdout, re.M).group(1)
    run.initiator(["handle"], label="handle first")
    run.initiator(["handle"], "this is not a concord reply", label="handle prose")
    run.initiator(["handle"], "<concord-anchor-reply agent=\"a\"><raw_reject>9</raw_reject>", label="handle broken-xml")
    run.initiator(["handle"], '<concord-anchor-reply agent="zzz"><raw_reject>10</raw_reject><raw_ideal>90</raw_ideal>'
                  "</concord-anchor-reply>", label="handle unknown-agent")
    run.initiator(["handle"], '<concord-anchor-reply agent="a"><raw_reject>90</raw_reject><raw_ideal>10</raw_ideal>'
                  "</concord-anchor-reply>", label="handle inverted-anchor")
    run.initiator(["handle", "--resend"], label="handle resend")
    run.initiator(["mark-missing", "--help"], label="mark-missing help")
    run.initiator(["status"], label="status")
    return run


def receiver_errors():
    """The receiver on bad input: unusable answers and documents."""
    run = driver.Run("test-errors")
    p = run.initiator(["init", "--quiet", *init_args()])
    run.state = re.search(r"^State: (.+)$", p.stdout, re.M).group(1)
    first = driver.blocks(run.initiator(["handle"]).stdout)[0][1]
    run.receiver("a", ["due"])
    run.receiver("a", ["handle"], "plain words, no concord document")
    run.receiver("a", ["handle"], first)
    run.receiver("a", ["answer", "--reject", "90", "--ideal", "10"])
    run.receiver("a", ["answer", "--reject", "x", "--ideal", "10"])
    run.receiver("a", ["answer"])
    run.receiver("a", ["answer", "--reject", "10", "--ideal", "90"])
    run.receiver("a", ["due"])
    run.receiver("a", ["handle"], first)
    return run


def receiver_schema_enforcement():
    """The receiver checks a seed against the schema a message carries. Messages are hand-written so the schema
    can be one the initiator would never emit (an invalid pattern): that is the case the two scripts disagree on today."""
    run = driver.Run("test-schema")

    def anchor_msg(schema_json):
        return ('<concord-anchor><satisfaction_threshold>0.6</satisfaction_threshold><x_reject>bad</x_reject>'
                '<x_ideal>good</x_ideal><state_id>sch1</state_id><participants><agent>a</agent><agent>b</agent>'
                '</participants><params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps>'
                '<budget>10</budget></params><addressed_to><agent>a</agent><agent>b</agent></addressed_to>'
                '<instructions>i</instructions></concord-anchor>'), (
                '<concord-propose-seed><candidate_schema>s</candidate_schema><schema_json>' + schema_json +
                '</schema_json><satisfaction_threshold>0.6</satisfaction_threshold><context>c</context>'
                '<state_id>sch1</state_id><participants><agent>a</agent><agent>b</agent></participants>'
                '<params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps><budget>10</budget></params>'
                '<addressed_to><agent>a</agent><agent>b</agent></addressed_to><instructions>i</instructions>'
                '</concord-propose-seed>')

    cases = {
        "enum": '{&quot;enum&quot;: [&quot;X&quot;, &quot;Y&quot;]}',
        "pattern": '{&quot;type&quot;: &quot;string&quot;, &quot;description&quot;: &quot;d&quot;, &quot;pattern&quot;: &quot;^ok-[0-9]+$&quot;}',
        "length": '{&quot;type&quot;: &quot;string&quot;, &quot;description&quot;: &quot;d&quot;, &quot;minLength&quot;: 3, &quot;maxLength&quot;: 6}',
        "invalid-pattern": '{&quot;type&quot;: &quot;string&quot;, &quot;description&quot;: &quot;d&quot;, &quot;pattern&quot;: &quot;(&quot;}',
    }
    answers = {"enum": ["Q", "x"], "pattern": ["nope", "ok-12"], "length": ["ab", "abcdefg", "abcd"],
               "invalid-pattern": ["anything"]}
    for name, schema_json in cases.items():
        run.env["CONCORD_STATE_DIR"] = str(run.tmp / name)
        anchor, seed = anchor_msg(schema_json)
        agent = "a"
        run.receiver(agent, ["handle"], anchor)
        run.receiver(agent, ["answer", "--reject", "10", "--ideal", "90"])
        run.receiver(agent, ["handle"], seed)
        for cand in answers[name]:
            run.receiver(agent, ["answer", "--candidate", cand, "--rationale", driver.REASONS])
    return run


SCENARIOS = {f.__name__: f for f in (
    success_at_score, three_agents_success, repair_then_success, best_effort_budget_spent, open_schema_with_pattern,
    no_relay_first_message, error_paths, receiver_errors, receiver_schema_enforcement)}
