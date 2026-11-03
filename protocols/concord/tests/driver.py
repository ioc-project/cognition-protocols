#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Plays a scripted CONCORD run through the real initiator and receiver scripts and records every call.

The agents' judgement comes from a policy (a function of the ASK prompt), so a run is deterministic: the state id is
fixed by CONCORD_RUN_KEY and every path is replaced by a placeholder. The transcript is what the tests compare.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
INITIATOR = SKILLS / "concord-initiator" / "scripts" / "concord_initiator.py"
RECEIVER = SKILLS / "concord-receiver" / "scripts" / "concord_receiver.py"
MAX_STEPS = 200


class Run:
    def __init__(self, run_key="test"):
        self.tmp = Path(tempfile.mkdtemp(prefix="concord-test-"))
        self.env = dict(os.environ, CONCORD_RUN_KEY=run_key, CONCORD_STATE_DIR=str(self.tmp / "agents"),
                        CONCORD_MIN_NUDGE="0", PYTHONHASHSEED="0")
        self.env.pop("CONCORD_STATE_ID", None)
        self.log = []
        self.state = None

    def norm(self, text):
        text = text.replace(str(self.tmp), "<TMP>").replace(str(self.tmp.resolve()), "<TMP>")
        # argparse quotes the choices only in some Python versions
        return re.sub(r"\(choose from ([^)]*)\)", lambda m: "(choose from " + m.group(1).replace("'", "") + ")", text)

    def call(self, label, script, argv, stdin=""):
        p = subprocess.run([sys.executable, "-I", str(script), *argv], input=stdin, capture_output=True, text=True,
                           env=self.env, cwd=self.tmp)
        self.log.append({"call": label, "argv": [self.norm(a) for a in argv], "stdin": self.norm(stdin),
                         "rc": p.returncode, "stdout": self.norm(p.stdout), "stderr": self.norm(p.stderr)})
        return p

    def initiator(self, argv, stdin="", label="initiator"):
        argv = list(argv)
        if self.state and argv and argv[0] != "init":
            argv = ["--state", self.state, *argv]
        p = self.call(label + " " + (argv[2] if argv[:1] == ["--state"] else argv[0] if argv else ""), INITIATOR, argv, stdin)
        return p

    def receiver(self, me, argv, stdin=""):
        return self.call(f"receiver {me} {argv[0]}", RECEIVER, ["--me", me, *argv], stdin)


def blocks(out):
    """The `--- to: a, b` blocks of an initiator SEND -> [(recipients, text)]."""
    parts = re.split(r"^--- to: *(.+?) *$", out, flags=re.M)
    return [([r.strip() for r in parts[i].split(",")], parts[i + 1].strip("\n")) for i in range(1, len(parts) - 1, 2)]


def play(init_args, policy, run_key="test"):
    """Drive a whole run. `policy(agent, task, prompt) -> argv for `answer`` (or None to stay silent)."""
    run = Run(run_key)
    p = run.initiator(["init", "--quiet", *init_args])
    run.state = re.search(r"^State: (.+)$", p.stdout, re.M).group(1)
    pending = [""]                      # what to pass to the initiator next
    for _ in range(MAX_STEPS):
        out = run.initiator(["handle"], pending.pop(0) if pending else "").stdout
        if re.search(r"^RUN COMPLETE", out, re.M) or out.startswith("STATUS: NONE") and not pending:
            break
        replies = []
        for recipients, text in blocks(out):
            for agent in recipients:
                r = run.receiver(agent, ["handle"], text).stdout
                tries = 0
                while r.startswith("STATUS: ASK"):
                    tries += 1
                    if tries > 3:
                        raise RuntimeError(f"{agent} kept being asked after 3 answers: the policy is wrong\n{r[:600]}")
                    task = re.search(r"^TASK: (\w+)", r, re.M).group(1)
                    argv = policy(agent, task, r)
                    if argv is None:
                        r = ""
                        break
                    r = run.receiver(agent, ["answer", *argv]).stdout
                if r.startswith("STATUS: SEND"):
                    replies.append(r.split("\n", 1)[1])
        pending = ["\n".join(replies)] if replies else [""]
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


REASONS = "It meets my role criteria on cost, risk and delivery time, as stated in my briefing."


def policy(anchors, seeds, scores, repairs=None):
    """A policy from tables: anchors {agent: (reject, ideal)}, seeds {agent: text}, scores {agent: {text: raw}} and
    repairs {agent: [text, ...]} (one per repair round that agent is asked for)."""
    repairs = {a: list(v) for a, v in (repairs or {}).items()}

    def pol(agent, task, prompt):
        if task == "anchor":
            return ["--reject", str(anchors[agent][0]), "--ideal", str(anchors[agent][1])]
        if task == "seed":
            return ["--candidate", seeds[agent], "--rationale", REASONS]
        texts = dict(re.findall(r"^candidate (\S+): (.*)$", prompt, re.M))
        if task == "repair":
            text = repairs[agent].pop(0)
            rid = re.search(r"^TASK: repair (\S+)", prompt, re.M).group(1)
            return ["--candidate", text, "--rationale", REASONS, "--score", rid, str(scores[agent][text]), REASONS]
        argv = []
        for cid in re.findall(r"[\w-]+", prompt.split("TASK: score", 1)[1].split("\n", 1)[0]):
            argv += ["--score", cid, str(scores[agent][texts[cid]]), REASONS]
        return argv
    return pol
