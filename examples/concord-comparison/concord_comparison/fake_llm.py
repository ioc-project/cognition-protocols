#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A stand-in for the model, to check the plumbing (SLIM, the node, the tools) without an API key: `--fake-llm`.

It answers the tools by keywords. It rules an option out when a fact it has read (its own, or one written in a rationale or a
reasoning of another agent) says that the way to it is blocked, so a run reaches the right answer only when facts do travel.
"""
import json
import re
from types import SimpleNamespace

BLOCKS = (("stuck in the tunnel", "East Town"), ("blocked the supply truck", "East Town"), ("mudslide", "North Hill"),
          ("walking trails have been closed", "North Hill"))


class _Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def _options(system):
    part = system.split("## The options\n", 1)[1].split("\n\n", 1)[0]
    return [line[2:].strip() for line in part.splitlines() if line.startswith("- ")]


def _blocked(text):
    return {option for key, option in BLOCKS if key in text}


def _use(name, args, n):
    return SimpleNamespace(content=[_Block(type="tool_use", id=f"fake-{n}", name=name, input=args)],
                           usage=SimpleNamespace(input_tokens=0, output_tokens=0))


class FakeLLM:
    def __init__(self):
        self.messages = self
        self.n = 0
        self.wants_task = True
        self.task = None

    async def create(self, model, max_tokens, system, tools, tool_choice, messages, extra_body=None):
        self.n += 1
        if tools[0]["name"] == "delegate_to":                  # the hub: pass every message on to the other participants
            return self._delegate(system, messages)
        if tools[0]["name"] in ("post", "final_answer", "report_outcome"):
            return self._free(tools[0]["name"], system, messages)
        name, options = tools[0]["name"], _options(system)
        known = system
        task = self.task or {}
        known += json.dumps(task)
        for m in messages:
            for b in m["content"] if isinstance(m["content"], list) else []:
                if b.get("type") == "text":
                    known += b["text"]
        bad = _blocked(known)
        good = [o for o in options if o not in bad] or options
        why = "; ".join(sorted(f"the way to {o} is blocked" for o in bad)) or "nothing I know rules an option out"
        if name == "submit_init_reply":
            return _use(name, {"raw_reject": 5, "raw_ideal": 95, "candidate": good[0], "rationale": f"{good[0]}: {why}.",
                               "own_score": 85, "reasoning": why}, self.n)
        if name == "submit_score":
            scores = [{"id": c["id"], "score": 15 if c["candidate"] in bad else 85, "reasoning": why} for c in task["candidates"]]
            return _use(name, {"scores": scores}, self.n)
        return _use(name, {"candidate": good[0], "rationale": f"{good[0]}: {why}.", "own_score": 85, "reasoning": why}, self.n)

    def _delegate(self, system, messages):
        names = re.search(r"^You are the facilitator, the hub of a star: (.*?) are the participants", system).group(1).split(", ")
        text = "".join(b.get("text", "") for b in messages[-1]["content"] if b.get("type") == "text")
        blocks = [_Block(type="tool_use", id=f"fake-{self.n}-{i}", name="delegate_to",
                         input={"message_id": mid, "recipients": [n for n in names if n != sender]})
                  for i, (mid, sender) in enumerate(re.findall(r"\[(m\d+)\] from (\S+):", text))]
        return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    def _free(self, name, system, messages):
        """Free speech: say what is known once, then agree on the option that nothing known rules out, then keep quiet."""
        text = "".join(b.get("text", "") for m in messages for b in (m["content"] if isinstance(m["content"], list) else [])
                       if b.get("type") == "text")
        options = _options(system) if "## The options" in system else re.findall(r"West City|East Town|North Hill", system)
        good = [o for o in options if o not in _blocked(system + text)] or options
        if name == "report_outcome":
            agreed = re.findall(r"I agree on (West City|East Town|North Hill)", text)
            ok = len(agreed) >= 2 and len(set(agreed)) == 1
            return _use(name, {"agreed": ok, "option": agreed[0] if ok else "no decision"}, self.n)
        if name == "final_answer":
            return _use(name, {"option": good[0], "rationale": "what the others wrote rules out the other options."}, self.n)
        posted = sum(1 for m in messages if m["role"] == "assistant" for b in m["content"]
                     if b.get("type") == "tool_use" and b["name"] == "post")
        if posted == 0:
            facts = [l[2:] for l in system.splitlines() if l.startswith("- ") and any(k in l for k, _ in BLOCKS)]
            return _use("post", {"text": " ".join(facts) or "I have nothing new, I think West City is fine."}, self.n)
        if posted == 1 and len(good) == 1:
            return _use("post", {"text": f"I agree on {good[0]}."}, self.n)
        return _use("stay_silent", {"reason": "nothing to add"}, self.n)
