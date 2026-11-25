#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Plays a scripted ACCORD run through the real initiator script and records every call.

ACCORD's receiver is a prompt, not a script, so there is no second process to drive: the specialists' judgement comes
from a policy (a function of the request message) that returns the JSON a receiver would have returned. A run is
therefore deterministic — ACCORD_RUN_KEY fixes the state id and the state path, ACCORD_EMBEDDING=off keeps grounding on
difflib rather than a model, and every temp path is replaced by a placeholder. The transcript is what the tests compare.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
INITIATOR = SKILLS / "accord-initiator" / "scripts" / "accord_initiator.py"
MAX_STEPS = 40


class Run:
    def __init__(self, run_key="test"):
        self.tmp = Path(tempfile.mkdtemp(prefix="accord-test-"))
        self.env = dict(os.environ, ACCORD_RUN_KEY=run_key, ACCORD_STATE_DIR=str(self.tmp / "state"),
                        ACCORD_EMBEDDING="off", PYTHONHASHSEED="0")
        self.env.pop("ACCORD_STATE", None)
        self.log = []

    def norm(self, text):
        text = text.replace(str(self.tmp.resolve()), "<TMP>").replace(str(self.tmp), "<TMP>")
        # argparse quotes the choices only in some Python versions
        text = re.sub(r"\(choose from ([^)]*)\)", lambda m: "(choose from " + m.group(1).replace("'", "") + ")", text)
        # and breaks the usage line before the subcommand's `...` only in some others
        return re.sub(r"\}\n\s+\.\.\.", "} ...", text)

    def call(self, label, argv, stdin=""):
        # -s, not -I: -I implies -E, which would discard the PYTHONHASHSEED that keeps set iteration stable.
        p = subprocess.run([sys.executable, "-s", str(INITIATOR), *argv], input=stdin, capture_output=True, text=True,
                           env=self.env, cwd=self.tmp)
        self.log.append({"call": label, "argv": [self.norm(a) for a in argv], "stdin": self.norm(stdin),
                         "rc": p.returncode, "stdout": self.norm(p.stdout), "stderr": self.norm(p.stderr)})
        return p

    def initiator(self, argv, stdin="", label=None):
        return self.call(label or "initiator " + (argv[0] if argv else ""), list(argv), stdin)


def data(p):
    """The `data` object of an initiator result, or {} when the command did not produce one."""
    try:
        return json.loads(p.stdout).get("data") or {}
    except (ValueError, AttributeError):
        return {}


def play(init_args, policy, run_key="test"):
    """Drive a whole self-driving run. `policy(agent, phase, message) -> the agent's raw reply text."""
    run = Run(run_key)
    d = data(run.initiator(["init", *init_args]))
    for _ in range(MAX_STEPS):
        messages = d.get("messages") or {}
        if not messages:
            break
        phase = d.get("phase", "")
        for agent, message in messages.items():
            d = data(run.initiator(["handle", "--agent", agent, "--reply", policy(agent, phase, message)],
                                   label=f"handle {phase} {agent}"))
        if d.get("locked_contract_exists"):
            break
    run.final = run.initiator(["status"]).stdout
    return run


WIDTH = 120
WRAP = "\u23ce"        # ends a line that was wrapped for display only: the next line continues it


def _wrap(prefix, line):
    """`line` as lines of at most WIDTH columns, each starting with `prefix`; a wrapped line ends in WRAP."""
    room = WIDTH - len(prefix) - 1
    out = []
    while len(line) > room + 1:
        cut = line.rfind(" ", 0, room) + 1 or room         # after the last space that fits, else mid-word
        out.append(prefix + line[:cut] + WRAP)
        line = line[cut:]
    return out + [prefix + line]


def dump(run):
    """The transcript as text: one block per call, every stream line prefixed and wrapped to WIDTH columns."""
    lines = []
    for e in run.log:
        lines.append(f"=== {e['call']} (exit {e['rc']})")
        lines += _wrap("$ ", " ".join(e["argv"]))
        for tag, key in (("in", "stdin"), ("out", "stdout"), ("err", "stderr")):
            for line in e[key].split("\n") if e[key] else []:
                lines += _wrap(f"{tag:>3}| ", line)
        lines.append("")
    return "\n".join(lines) + "\n"


def contract_of(run):
    """The locked Intent Contract, from the call that produced it. `status` reports only a summary."""
    for e in reversed(run.log):
        if "locked_intent_contract" in e["stdout"]:
            return json.loads(e["stdout"])["data"]["locked_intent_contract"]
    return None


def section(message, label):
    """The JSON value a request carries under `LABEL:`. Handles `[]` as well as a pretty-printed array."""
    start = message.index(f"{label}: ") + len(label) + 2
    return json.JSONDecoder().raw_decode(message, start)[0]


def echo_delivery(message, reply):
    """A reply as a receiver returns it: the delivery line it was sent, then the payload.

    The line carries the check `handle` verifies, so passing it back is what distinguishes a real reply from one the
    orchestrator wrote itself. `EXECUTION_REQUEST` carries no delivery line and is returned unchanged.
    """
    first = message.split("\n", 1)[0]
    return f"{first}\n\n{reply}" if first.startswith("<accord-delivery") else reply


def policy(cards, atoms, units, checks, clarify=None, revised=None):
    """A policy from tables, one entry per agent.

    `cards` {agent: (role, capability, scope)}, `atoms` {agent: [(text, type), ...]}, `units` {agent: {term: unit}},
    `checks` {agent: [(criterion, [clause, ...]), ...]}, and optionally `clarify` {agent: definition} and `revised`
    {agent: [(text, type), ...]} — the atoms the agent returns on the second FRAME round instead of its first set.
    """
    def pol(agent, phase, message):
        return echo_delivery(message, _payload(agent, message))

    def _payload(agent, message):
        if "CONVENE_REQUEST" in message:
            role, capability, scope = cards[agent]
            return json.dumps({"agent": agent, "roles": [role], "capabilities": [capability],
                               "access_scopes": [scope], "holdings_summary": f"{role} knowledge for this incident"})
        if "FRAME_REQUEST" in message:
            mine = revised[agent] if revised and "YOUR_PREVIOUS_ATOMS" in message and agent in revised else atoms[agent]
            return json.dumps({"agent": agent, "atoms": [{"text": t, "type": k} for t, k in mine]})
        if "GROUND_REQUEST" in message:
            mine = units[agent]
            return json.dumps({"agent": agent, "units": {t: mine[t] for t in section(message, "KEY_TERMS")}})
        if "CLARIFY_REQUEST" in message:
            term = section(message, "AMBIGUOUS_PAIR")[0]["term"]
            return json.dumps({"agent": agent, "term": term,
                               "extended_definition": (clarify or {}).get(agent, f"{term} as {agent} measures it"),
                               "distinguishing_context": f"the sense used in {agent}'s own tooling"})
        if "VERIFY_REQUEST" in message:
            holds = section(message, "REQUIRED_KNOWLEDGE")
            return json.dumps({"agent": agent, "holds": holds[:1],
                               "checks": [{"criterion": c, "covers": cov} for c, cov in checks[agent]]})
        return "LOCK_STATUS: RECEIVED\n\nnothing to execute in this test"
    return pol
