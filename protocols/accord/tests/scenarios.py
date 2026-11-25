#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The scripted runs the tests replay. Each returns a Run whose .log is the transcript."""
import json
import re

import driver

TASK = "Checkout error rate rose after the 14:02 deploy; decide what to roll back"
TERMS = "error_rate,rollback_target"

CARDS = {
    "telemetry": ("telemetry analyst", "query metrics and traces", "prometheus"),
    "database": ("database specialist", "inspect query plans and locks", "pg-stat"),
    "deploy": ("deployment engineer", "read deployment history", "argo"),
}

ATOMS = {
    "telemetry": [("checkout error rate rose from 0.4 to 8.7 after 14:02", "objective"),
                  ("the regression is confined to the checkout path", "assumption")],
    "database": [("checkout error rate rose from 0.4 to 8.7 after 14:02", "objective"),
                 ("connection pool saturation began at 14:03", "constraint")],
    "deploy": [("two services shipped at 14:02, checkout and cart", "objective"),
               ("rolling back cart alone leaves checkout on the new build", "constraint")],
}

# telemetry reports a fraction, database a percentage: the split the Glossary phase exists to catch.
SPLIT_UNITS = {
    "telemetry": {"error_rate": "fraction 0-1", "rollback_target": "deployment id"},
    "database": {"error_rate": "percent 0-100", "rollback_target": "deployment id"},
    "deploy": {"error_rate": "percent 0-100", "rollback_target": "deployment id"},
}
AGREED_UNITS = {a: dict(u, error_rate="fraction 0-1") for a, u in SPLIT_UNITS.items()}

# Genuinely different definitions, not reworded ones: CLARIFY is meant to confirm a split, not talk it away.
CLARIFY = {
    "telemetry": "failed HTTP requests divided by total requests, a fraction between 0 and 1",
    "database": "failed statements as a percentage of statements executed, 0 to 100",
    "deploy": "failed rollouts as a percentage of rollouts attempted, 0 to 100",
}

CHECKS = {
    "telemetry": [("checkout error rate below 0.01 for 5 minutes", [])],
    "database": [("connection pool wait time returns to baseline", [])],
    "deploy": [("the rolled-back revision is the 14:02 checkout build", [])],
}


def init_args(specialists="telemetry,database", task=TASK, terms=TERMS, action_tool="remediate"):
    return ["--specialists", specialists, "--key-terms", terms, "--action-tool", action_tool, "--task", task]


def pol(agents, units=None, **kw):
    sub = lambda table: {a: table[a] for a in agents}  # noqa: E731
    kw.setdefault("clarify", sub(CLARIFY))
    return driver.policy(sub(CARDS), sub(ATOMS), sub(units or SPLIT_UNITS), sub(CHECKS), **kw)


def split_term_triggers_clarify():
    """The reference run: two agents disagree on the unit of a key term, so GROUND flags it and CLARIFY follows."""
    return driver.play(init_args(), pol(["telemetry", "database"]))


def agreed_terms_skip_clarify():
    """The same run with no unit split. CLARIFY is conditional, so the run goes straight from GROUND to VERIFY."""
    return driver.play(init_args(), pol(["telemetry", "database"], units=AGREED_UNITS), run_key="test-agreed")


def three_agents_with_singleton():
    """Three specialists. Only `deploy` knows two services shipped together — a support-1 atom the Frame must keep."""
    agents = ["telemetry", "database", "deploy"]
    return driver.play(init_args(",".join(agents)), pol(agents), run_key="test-three")


def withdrawn_atoms():
    """An agent returns `atoms: []` on the revision round, the documented way to withdraw everything it contributed."""
    revised = {"database": []}
    return driver.play(init_args(), pol(["telemetry", "database"], revised=revised), run_key="test-withdrawn")


def step_by_step_phase_commands():
    """The other documented path: explicit per-phase merges instead of `handle`, with knowledge and clauses supplied.

    This is the only way to populate REQUIRED_KNOWLEDGE and OBJECTIVE_CLAUSES, so it is also where coverage gaps and
    clause assignment are exercised.
    """
    run = driver.Run("test-steps")
    agents = ["telemetry", "database"]
    run.initiator(["init", "--specialists", ",".join(agents), "--key-terms", TERMS, "--action-tool", "remediate"])

    cards = [json.dumps({"agent": a, "roles": [CARDS[a][0]], "capabilities": [CARDS[a][1]],
                         "access_scopes": [CARDS[a][2]], "holdings_summary": f"{CARDS[a][0]} knowledge"})
             for a in agents]
    run.initiator(["roster", "--responses", "-"], json.dumps(cards), label="roster")

    frames = [json.dumps({"agent": a, "atoms": [{"text": t, "type": k} for t, k in ATOMS[a]]}) for a in agents]
    run.initiator(["frame", "--responses", "-", "--current-frame", "[]"], json.dumps(frames), label="frame round 1")

    grounds = [json.dumps({"agent": a, "units": SPLIT_UNITS[a]}) for a in agents]
    run.initiator(["glossary", "--responses", "-"], json.dumps(grounds), label="glossary")

    required = ["the 14:02 deployment manifest", "checkout error budget policy"]
    clauses = ["checkout error rate returns below 0.01", "no unrelated service is rolled back"]
    verifies = [json.dumps({"agent": a, "holds": required[:1] if a == "telemetry" else [],
                            "checks": [{"criterion": c, "covers": cov or clauses[:1]} for c, cov in CHECKS[a]]})
                for a in agents]
    run.initiator(["verify", "--responses", "-", "--required-knowledge", json.dumps(required),
                   "--objective-clauses", json.dumps(clauses)], json.dumps(verifies), label="verify")
    run.initiator(["lock"], label="lock")
    run.initiator(["status"], label="status")
    return run


def execution_messages():
    """After lock, one EXECUTION_REQUEST per specialist. The contract travels inside it; nothing is sent at LOCK."""
    run = split_term_triggers_clarify()
    for agent in ("telemetry", "database"):
        run.initiator(["execution-message", "--agent", agent, "--assignment-file", "-"],
                      f"Confirm the rollback target with {agent} evidence.", label=f"execution-message {agent}")
    run.initiator(["execution-message", "--agent", "nobody", "--assignment-file", "-"], "unknown agent",
                  label="execution-message unknown-agent")
    return run


def reply_check_refusals():
    """A reply must carry the check of the message that was sent, or `handle` refuses it and asks for the real one.

    A wrong check is always refused, however often it is retried. A missing one is refused once and then recorded
    with a defect, so a receiver that never echoes the line cannot stall the session.
    """
    run = driver.Run("test-check")
    d = driver.data(run.initiator(["init", *init_args()]))
    delivery = d["messages"]["telemetry"].split("\n", 1)[0]
    tampered = re.sub(r'check="[0-9a-f]+"', 'check="deadbeef"', delivery)

    def card(agent):
        return json.dumps({"agent": agent, "roles": [CARDS[agent][0]], "capabilities": [CARDS[agent][1]],
                           "access_scopes": [CARDS[agent][2]], "holdings_summary": f"{CARDS[agent][0]} knowledge"})

    run.initiator(["handle", "--agent", "telemetry", "--reply", "Telemetry reports elevated errors."],
                  label="handle summary-no-check")
    run.initiator(["handle", "--agent", "telemetry", "--reply", f"{tampered}\n\n{card('telemetry')}"],
                  label="handle wrong-check")
    run.initiator(["handle", "--agent", "telemetry", "--reply", f"{tampered}\n\n{card('telemetry')}"],
                  label="handle wrong-check again")
    run.initiator(["handle", "--agent", "telemetry", "--reply", f"{delivery}\n\n{card('telemetry')}"],
                  label="handle correct-check")

    # database never echoes the line, but its body is readable: refused once, then recorded with a defect.
    run.initiator(["handle", "--agent", "database", "--reply", card("database")], label="handle db-no-line-1")
    run.initiator(["handle", "--agent", "database", "--reply", card("database")], label="handle db-no-line-2")
    run.initiator(["status"], label="status")
    return run


def reply_shape_refusals():
    """A reply must also be something the phase's merge can read; the check only says which message it answers.

    The leniency that lets a non-echoing receiver through does not extend to an unreadable body: a summary carrying
    the right delivery line is still refused, however many times it is sent.
    """
    run = driver.Run("test-shape")
    d = driver.data(run.initiator(["init", *init_args()]))
    delivery = d["messages"]["telemetry"].split("\n", 1)[0]

    for label, body in (
        ("prose", "Telemetry confirms the error rate is elevated."),
        ("truncated-json", '{"agent": "telemetry", '),
        ("json-but-no-agent", '{"roles": ["telemetry analyst"], "capabilities": ["query metrics"]}'),
        ("summary-as-json", '{"summary": "telemetry sees elevated errors", "agent": "telemetry"}'),
    ):
        run.initiator(["handle", "--agent", "telemetry", "--reply", f"{delivery}\n\n{body}"],
                      label=f"handle {label}")
    # the same prose, retried: a readable body is the bar, and retrying does not lower it
    run.initiator(["handle", "--agent", "telemetry", "--reply", f"{delivery}\n\nStill just prose."],
                  label="handle prose again")
    run.initiator(["status"], label="status")
    return run


def error_paths():
    """Bad input to every entry point: each must end in a readable error, never a traceback."""
    run = driver.Run("test-errors")
    run.initiator(["frobnicate"], label="unknown-command")
    run.initiator(["init"], label="init no-specialists")
    run.initiator(["status"], label="status before-init")
    run.initiator(["handle", "--agent", "telemetry", "--reply", "{}"], label="handle before-init")
    run.initiator(["lock"], label="lock before-init")
    run.initiator(["init", *init_args()], label="init")
    run.initiator(["handle", "--agent", "nobody", "--reply", "{}"], label="handle unknown-agent")
    run.initiator(["handle", "--agent", "telemetry", "--reply", "this is prose, not a reply"], label="handle prose")
    run.initiator(["handle", "--agent", "telemetry", "--reply", '{"agent": "telemetry", '], label="handle broken-json")
    run.initiator(["lock"], label="lock before-verify")
    run.initiator(["roster", "--responses", "not json"], label="roster bad-json")
    run.initiator(["frame", "--responses", "[]", "--current-frame", "-"], "also not json", label="frame bad-stdin")
    run.initiator(["glossary", "--responses", "/nope/missing.json"], label="glossary missing-file")
    run.initiator(["execution-message", "--agent", "telemetry", "--assignment-file", "-"], "a",
                  label="execution-message before-lock")
    run.initiator(["status"], label="status")
    return run


SCENARIOS = {f.__name__: f for f in (
    split_term_triggers_clarify, agreed_terms_skip_clarify, three_agents_with_singleton, withdrawn_atoms,
    step_by_step_phase_commands, execution_messages, reply_check_refusals, reply_shape_refusals,
    error_paths)}
