#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A stand-in for the model of the skills mode, to check the plumbing without an API key: `--fake-llm`.

It does what the skills tell a model to do, reading what the scripts print: it starts the run when it is asked to, sends each `SEND to:`
block, passes each message to `receive`, answers each `TASK`, and starts `wait` in the background when there is a `WAKE`. It never reads
the SKILL.md files, so what it cannot show is whether a model finds its way from the text of the skills. As `fake_llm` does, it rules
an option out when a fact it has read says the way to it is blocked.
"""
import json
import re
import shlex
from types import SimpleNamespace

from concord_slim_agents.fake_llm import _Block, _blocked, _options

AGENTS = ["participant-a", "participant-b", "participant-c"]
SECTION = re.compile(r"^--- (\S+)(?: (?:to: )?(.*))?\n", re.M)


def sections(output):
    """[(name, argument, text)] of the sections of the output of a script."""
    marks = list(SECTION.finditer(output))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(output)
        text = output[m.end():end].split("\nNEXT:")[0]
        out.append((m.group(1), (m.group(2) or "").strip(), text.rstrip("\n")))
    return out


def _use(calls, n):
    blocks = [_Block(type="tool_use", id=f"fake-{n}-{i}", name=name, input=args) for i, (name, args) in enumerate(calls)]
    if not blocks:
        blocks = [_Block(type="text", text="Nothing to do now.")]
    return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=0, output_tokens=0))


class FakeSkillLLM:
    def __init__(self, star=False):
        self.messages = self
        self.n = 0
        self.waiting = False
        self.star = star                    # participant-a is the centre of a star: it relays, with `--relay` and `forward`

    def _paths(self, system):
        found = {}
        for path, name in re.findall(r"folder: (\S+/(concord-(?:initiator|receiver)))\n", system):
            found[name] = f"{path}/scripts/{name.replace('-', '_')}.py"
        return found

    async def create(self, model, max_tokens, system, tools, tool_choice, messages, extra_body=None):
        self.n += 1
        me = re.search(r"Your agent id is (\S+)\.", system).group(1)
        scripts = self._paths(system)
        known = system + "".join(b.get("text", "") + str(b.get("content", "")) for m in messages if isinstance(m["content"], list)
                                 for b in m["content"])
        bad = _blocked(known)
        options = _options(system)
        good = [o for o in options if o not in bad] or options
        why = "; ".join(sorted(f"the way to {o} is blocked" for o in bad)) or "nothing I know rules an option out"
        calls = []
        for block in messages[-1]["content"]:
            if block.get("type") == "tool_result":
                calls += self._after(me, scripts, str(block["content"]), bad, good, why)
            elif block.get("type") == "text":
                calls += self._events(me, scripts, block["text"], options, bad, good, why)
        answers = [i for i, (name, args) in enumerate(calls) if name == "shell" and " answer <<" in args["command"]]
        calls = [c for i, c in enumerate(calls) if i not in answers[:-1]]       # one answer for what it owes, however many outputs say so
        return _use(calls, self.n)

    # what the model does when told something ----------------------------------------------------------------------------------

    def _events(self, me, scripts, text, options, bad, good, why):
        calls = []
        if text.startswith("Begin:"):
            threshold = (re.search(r"threshold (\d*\.?\d+)", text) or [None, "0.6"])[1]
            relay = f"--relay {','.join(AGENTS[1:])} " if self.star else ""
            command = (f"python3 {shlex.quote(scripts['concord-initiator'])} --me {me} {relay}start --participants {','.join(AGENTS)} "
                       f"--context 'Decide together where the village evacuates.' "
                       f"--x-reject 'Evacuate to a place that cannot be reached.' "
                       f"--x-ideal 'Evacuate to the one place that is safe and reachable.' "
                       + " ".join(f"--option {shlex.quote(o)}" for o in options) + f" --satisfaction-threshold {threshold}")
            calls.append(("shell", {"command": command}))
        for m in re.finditer(r"\[message(?: from (\S+))?\]\n(.*?)\n\[end of message\]", text, re.S):
            if self.star and me == AGENTS[0] and m.group(1):
                calls.append(("shell", {"command": f"python3 {shlex.quote(scripts['concord-initiator'])} --me {me} --relay {','.join(AGENTS[1:])} "
                                                   f"forward --from {m.group(1)} <<'EOF'\n{m.group(2)}\nEOF"}))
            calls.append(("shell", {"command": f"python3 {shlex.quote(scripts['concord-receiver'])} --me {me} receive <<'EOF'\n{m.group(2)}\nEOF"}))
        for m in re.finditer(r"\[background command \d+ ended\]\n\$ .*?\n(.*?)\n\[end of command\]", text, re.S):
            self.waiting = False
            calls += self._after(me, scripts, m.group(1), bad, good, why)
        return calls

    def _after(self, me, scripts, output, bad, good, why):
        calls = []
        for name, arg, body in sections(output):
            if name in ("SEND", "FORWARD"):
                calls.append(("send_message", {"to": [a.strip() for a in arg.split(",")], "text": body}))
            elif name == "TASK":
                calls.append(("shell", {"command": self._answer(me, scripts, output, body, bad, good, why)}))
            elif name == "WAKE" and not self.waiting:
                self.waiting = True
                calls.append(("shell", {"command": f"python3 {shlex.quote(scripts['concord-receiver'])} --me {me} wait", "background": True}))
        return calls

    def _answer(self, me, scripts, output, task, bad, good, why):
        if "raw_reject (" in task:
            answer = {"raw_reject": 5, "raw_ideal": 95, "candidate": good[0], "rationale": f"{good[0]}: {why}.", "raw": 85, "reasoning": why}
        elif "scores (a list)" in task:
            found = re.findall(r"^candidate (\S+) \(proposed by \S+\): (.*)$", task, re.M)
            answer = {"scores": [{"id": i, "raw": 15 if option in bad else 85, "reasoning": why} for i, option in found]}
        else:
            answer = {"candidate": good[0], "rationale": f"{good[0]}: {why}.", "raw": 85, "reasoning": why}
        return f"python3 {shlex.quote(scripts['concord-receiver'])} --me {me} answer <<'EOF'\n{json.dumps(answer)}\nEOF"
