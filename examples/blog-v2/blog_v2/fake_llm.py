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


COMM = {"post", "send_to", "stay_silent", "done", "final_answer", "reply", "submit_init_reply", "submit_score", "submit_repair", "concord_start",
        "report_outcome", "delegate_to"}        # the tools that say something; any other is a data tool of a role (mas-lab apps)


def _options(system, tools=()):
    """The options: from the system prompt (the evacuation task, or the coordinator of a mas-lab app), else from the tools (a role)."""
    for head in ("## The options\n", "## The candidates\n"):
        if head in system:
            part = system.split(head, 1)[1].split("\n\n", 1)[0]
            found = [line[2:].strip() for line in part.splitlines() if line.startswith("- ")]
            if found:
                return found
            example = re.search(r"(?:for example|such as) (\d{1,2}%)", part)
            if example:
                return [example.group(1)]
    for t in tools:
        for prop in t["input_schema"]["properties"].values():
            if prop.get("enum"):
                return [o for o in prop["enum"] if o != "no decision"]
            example = re.search(r"such as (\d{1,2}%)", prop.get("description", ""))
            if example:
                return [example.group(1)]
    return []


def _roster(system):
    if "## The roles\n" in system:
        return system.split("## The roles\n", 1)[1].split("\n", 1)[0].split(", ")
    return list(dict.fromkeys(re.findall(r"agent-[a-d]", system.split("\n", 1)[0])))


def _args_for(tool):
    schema = tool["input_schema"]
    return {k: ("Acme Corp" if "account" in k else "x" if schema["properties"][k].get("type") == "string" else 1)
            for k in schema.get("required", [])}


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
        data = [t for t in tools if t["name"] not in COMM]
        if data:                                                # a role of a mas-lab app: call each of its tools once before answering
            called = {b["name"] for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use"}
            todo = next((t for t in data if t["name"] not in called), None)
            if todo:
                return _use(todo["name"], _args_for(todo), self.n)
        if tools[0]["name"] == "concord_start":
            args = {"participants": _roster(system), "context": "The group must decide together.",
                    "x_reject": "No outcome is agreed and the decision is lost.", "x_ideal": "An outcome every role can accept.",
                    "satisfaction_threshold": 0.6, "options": _options(system, tools)}
            if "options" not in tools[0]["input_schema"]["properties"]:
                args.pop("options")
            return _use("concord_start", args, self.n)
        first = tools[0]["name"]
        if first == "send_to":                                  # a mesh: the recipients of the message just written
            return _use("send_to", {"to": list(tools[0]["input_schema"]["properties"]["to"]["items"]["enum"])}, self.n)
        if first == "delegate_to":                              # the coordinator of a star: pass every message on to the other leaders
            return self._delegate(system, messages)
        if first == "report_outcome":
            return self._summarize(system, messages)
        if first in ("post", "final_answer", "reply", "done"):
            return self._free(tools[0]["name"], system, messages, tools)
        name, options = tools[0]["name"], _options(system, tools)
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
        names = _roster(system)
        text = "".join(b.get("text", "") for b in messages[-1]["content"] if b.get("type") == "text")
        blocks = [_Block(type="tool_use", id=f"fake-{self.n}-{i}", name="delegate_to",
                         input={"message_id": mid, "recipients": [n for n in names if n != sender]})
                  for i, (mid, sender) in enumerate(re.findall(r"\[(m\d+)\] from (\S+):", text))]
        return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    def _summarize(self, system, messages):
        """The coordinator reports the outcome from what the leaders wrote: agreement if the final choices match."""
        text = "".join(b.get("text", "") for m in messages for b in (m["content"] if isinstance(m["content"], list) else []) if b.get("type") == "text")
        outcomes = text.split("The final choice of each", 1)[-1]
        finals = [m[1] for m in re.findall(r"^([A-Za-z_-]+): ([^\n]*?)\. ", outcomes, re.M)]
        ok = len(finals) == len(_roster(system)) and len(set(finals)) == 1
        return _use("report_outcome", {"agreed": ok, "option": finals[0] if ok else "no decision", "summary": "the leaders chose."}, self.n)

    def _free(self, name, system, messages, tools=()):
        """Free speech: say what is known once, then agree on the option that nothing known rules out, then keep quiet."""
        text = "".join(b.get("text", "") for m in messages for b in (m["content"] if isinstance(m["content"], list) else [])
                       if b.get("type") == "text")
        options = _options(system, tools) if tools else (_options(system) if "## The options" in system else [])
        good = [o for o in options if o not in _blocked(system + text)] or options
        if name in ("final_answer", "done"):
            return _use(name, {"option": good[0], "rationale": "what the others wrote rules out the other options."}, self.n)
        posted = sum(1 for m in messages if m["role"] == "assistant" for b in m["content"]
                     if b.get("type") == "tool_use" and b["name"] in ("post", "reply"))
        if posted == 0:
            facts = [l[2:] for l in system.splitlines() if l.startswith("- ") and any(k in l for k, _ in BLOCKS)]
            return _use(name if name == "reply" else "post", {"text": " ".join(facts) or "I have nothing new, I think West City is fine."}, self.n)
        if posted == 1 and len(good) == 1:
            return _use("post", {"text": f"I agree on {good[0]}."}, self.n)
        return _use("stay_silent", {"reason": "nothing to add"}, self.n)
