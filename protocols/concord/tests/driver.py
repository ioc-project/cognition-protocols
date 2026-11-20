#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Plays a scripted CONCORD run through the real initiator and receiver scripts and records every call.

The agents' judgement comes from a policy (a function of what the script printed), so a run is deterministic. What is not is
normalised in the transcript: every UUID becomes `<UUID-n>` in the order it first appears, every `check` becomes `<CHECK>`, a time to
come back becomes `<WAKE>` and the temporary directory `<TMP>`. What the scripts pass to each other is never normalised: only what is
recorded.

The driver is the transport and the model, and nothing else: it delivers each message to the agents the SEND (or FORWARD) section names,
reads the TASK the script printed and answers it from the policy, as an agent would.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
INITIATOR = SKILLS / "concord-initiator" / "scripts" / "concord_initiator.py"
RECEIVER = SKILLS / "concord-receiver" / "scripts" / "concord_receiver.py"
MAX_ROUNDS = 100

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHECK = re.compile(r'\bcheck="[0-9a-f]{8}"')
WAKE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d \(in \d+ s\)")


class Run:
    """One directory, in which every agent keeps its state, and the log of every call."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="concord-test-"))
        self.env = dict(os.environ, PYTHONHASHSEED="0")
        self.log, self.uuids = [], {}

    def norm(self, text):
        text = text.replace(str(self.tmp), "<TMP>").replace(str(self.tmp.resolve()), "<TMP>")
        text = UUID.sub(lambda m: f"<UUID-{self.uuids.setdefault(m.group(0), len(self.uuids) + 1)}>", text)
        return WAKE.sub("<WAKE>", CHECK.sub('check="<CHECK>"', text))

    def call(self, me, script, argv, stdin="", before=()):
        """Run `script` for agent `me`, with the flags `before` (the global ones) ahead of the command."""
        args = ["--me", me, *before, *argv]
        p = subprocess.run([sys.executable, "-I", str(script), *args], input=stdin, capture_output=True, text=True, env=self.env,
                           cwd=self.tmp, timeout=120)
        name = "initiator" if script == INITIATOR else "receiver"
        self.log.append({"call": f"{name} {me} {argv[0]}", "argv": [self.norm(a) for a in args], "stdin": self.norm(stdin),
                         "rc": p.returncode, "stdout": self.norm(p.stdout), "stderr": self.norm(p.stderr)})
        return p


def sections(out, title):
    """[(names, text)] of every `--- <title> to: a, b` section of an output."""
    return [([n.strip() for n in m.group(1).split(",")], m.group(2).strip("\n"))
            for m in re.finditer(rf"^--- {title} to: ([^\n]*)\n(.*?)(?=^--- |^NEXT:)", out, re.S | re.M)]


def task_kind(out):
    """What kind of task a receiver printed: it says so by the fields it asks for."""
    if "each entry of scores" in out:
        return "score"
    return "init-reply" if "raw_reject (" in out else "repair"


def candidates(out):
    """{id: text} of the candidates a score task lists."""
    return {cid: text for cid, text in re.findall(r"^candidate (\S+) \(proposed by \S+\): (.*)$", out, re.M)}


class Play:
    """A whole run: the initiator `who` opens it with `start`; the agents answer from `policy`; messages are delivered to the agents each
    SEND names (a star: spokes send to the centre alone, and the centre passes on what `forward` prints)."""

    def __init__(self, policy, agents, who="ini", takes_part=False, relay=None, forward=True, silent=()):
        self.run, self.policy, self.who, self.relay, self.forward, self.silent = Run(), policy, who, relay, forward, set(silent)
        self.agents = list(agents)                                   # the participants
        self.script = {a: RECEIVER for a in self.agents}
        self.takes_part = takes_part
        if not takes_part:
            self.script[who] = INITIATOR
        self.queue = []

    def flags(self, me, script):
        return ["--relay", ",".join(self.relay)] if self.relay and me == self.who and script == INITIATOR else []

    def start(self, args):
        participants = ",".join(self.agents)
        p = self.run.call(self.who, INITIATOR, ["start", "--participants", participants, *args],
                          before=["--relay", ",".join(self.relay)] if self.relay else [])
        assert p.returncode == 0, p.stdout + p.stderr
        if self.takes_part:                                          # as NEXT says: the receiver script from now on, and `task` first
            self.script[self.who] = RECEIVER
            self.queue += [(t, self.who, None) for _, t in sections(p.stdout, "SEND")]
            self.answer(self.who, self.run.call(self.who, RECEIVER, ["task"]).stdout)
        else:
            self.queue += [(t, self.who, None) for _, t in sections(p.stdout, "SEND")]

    def answer(self, me, out):
        """Answer every task that `out` carries, one after the other, as the policy says."""
        while "--- TASK" in out:
            p = self.run.call(me, RECEIVER, ["answer"], stdin=json.dumps(self.policy(me, task_kind(out), out)))
            assert p.returncode == 0, p.stdout + p.stderr
            self.queue += [(t, me, None) for _, t in sections(p.stdout, "SEND")]
            out = p.stdout

    def targets(self, origin, named):
        if named is not None:
            return named
        if not self.relay:
            return [n for n in self.script if n != origin]
        return [n for n in self.script if n != origin] if origin == self.who else [self.who]      # a spoke sends to the centre alone

    def deliver(self, text, origin, named):
        for me in self.targets(origin, named):
            if me in self.silent:
                continue
            script = self.script[me]
            if self.relay and me == self.who and origin != self.who:
                f = self.run.call(me, INITIATOR, ["forward", "--from", origin], stdin=text, before=["--relay", ",".join(self.relay)]) \
                    if self.forward else None
                if f is not None:
                    assert f.returncode == 0, f.stdout + f.stderr
                    self.queue += [(t, me, names) for names, t in sections(f.stdout, "FORWARD")]
            p = self.run.call(me, script, ["receive"], stdin=text, before=self.flags(me, script))
            assert p.returncode == 0, p.stdout + p.stderr
            self.queue += [(t, me, None) for _, t in sections(p.stdout, "SEND")]
            self.answer(me, p.stdout)

    def drain(self):
        for _ in range(MAX_ROUNDS * 10):
            if not self.queue:
                return
            text, origin, named = self.queue.pop(0)
            self.deliver(text, origin, named)
        raise RuntimeError("the run does not end: the policy or the scripts are wrong")

    def report(self):
        for me in [self.who] + [a for a in self.agents if a != self.who]:
            self.run.call(me, self.script[me], ["report"], before=self.flags(me, self.script[me]))
        return self.run


def play(start_args, policy, agents, **kw):
    """Drive a whole run and record a report of every agent at the end."""
    p = Play(policy, agents, **kw)
    p.start(start_args)
    p.drain()
    return p.report()


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
    """A policy from tables: anchors {agent: (reject, ideal)}, seeds {agent: text}, scores {agent: {text: raw}} and repairs {agent: [text,
    ...]} (one for each repair round that agent is asked for). It answers with the fields of the library."""
    repairs = {a: list(v) for a, v in (repairs or {}).items()}

    def pol(agent, kind, out):
        if kind == "init-reply":
            text = seeds[agent]
            return {"raw_reject": anchors[agent][0], "raw_ideal": anchors[agent][1], "candidate": text, "rationale": REASONS,
                    "raw": scores[agent][text], "reasoning": REASONS}
        if kind == "repair":
            text = repairs[agent].pop(0)
            return {"candidate": text, "rationale": REASONS, "raw": scores[agent][text], "reasoning": REASONS}
        return {"scores": [{"id": cid, "raw": scores[agent][text], "reasoning": REASONS} for cid, text in candidates(out).items()]}
    return pol
