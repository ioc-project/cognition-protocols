#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A stand-in for the model, to check the plumbing (SLIM, the node, the tools) without an API key: `--fake-llm`.

It answers the tools by keywords. It rules an option out when a fact it has read (its own, or one written in a rationale or a
reasoning of another agent) says that the way to it is blocked, so a run reaches the right answer only when facts do travel.
"""
import json
from types import SimpleNamespace

BLOCKS = (("stuck in the tunnel", "East Town"), ("blocked the supply truck", "East Town"), ("mudslide", "North Hill"),
          ("walking trails have been closed", "North Hill"))


class _Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items()}


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
        if name == "concord_start":
            return _use(name, {"participants": ["participant-a", "participant-b", "participant-c"],
                               "context": "Decide together where the village evacuates.", "options": options, "satisfaction_threshold": 0.6,
                               "x_reject": "Evacuate to a place that cannot be reached.",
                               "x_ideal": "Evacuate to the one place that is safe and reachable."}, self.n)
        if name == "submit_init_reply":
            return _use(name, {"raw_reject": 5, "raw_ideal": 95, "candidate": good[0], "rationale": f"{good[0]}: {why}.",
                               "own_score": 85, "reasoning": why}, self.n)
        if name == "submit_score":
            scores = [{"id": c["id"], "score": 15 if c["candidate"] in bad else 85, "reasoning": why} for c in task["candidates"]]
            return _use(name, {"scores": scores}, self.n)
        return _use(name, {"candidate": good[0], "rationale": f"{good[0]}: {why}.", "own_score": 85, "reasoning": why}, self.n)
