#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""One agent: a CONCORD node, a SLIM group session and an LLM that writes what the node asks for.

The node (ioc_concord.Agent) holds the protocol: it checks every message, keeps the state and builds what is sent. The model only
writes content, through tools that call the node's `start` and `submit_*`. The loop is:

    a message arrives     -> node.receive(text), then send what it hands back
    nothing for a second  -> node.tick(), which asks again for what is late or gives up
    node.due() not empty  -> a turn of the model, which must answer with a tool
    the run is over       -> write the report and stop
"""
import json
import os
import time
import traceback

from anthropic import AsyncAnthropic
from ioc_concord.agent import Agent
from ioc_concord.errors import ConcordError
from ioc_concord.store import FileStore

from concord_slim_agents.guidance import (NO_TOOL, START_PARAMS, UNEXPECTED, UNKNOWN_TOOL, check_answer, lacks, refusal, render_init_reply,
                                          render_repair, render_score, run_view, settled)
from concord_slim_agents.transport import SessionClosed

MAX_TURNS_WITHOUT_PROGRESS = 3
MAX_TOOL_ROUNDS = 4

_NUM = {"type": "number", "minimum": 0, "maximum": 100}
_TEXT = {"type": "string"}


def _tool(name, description, properties, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


def tools_for(options):
    candidate = {"type": "string", "enum": options, "description": "One of the options, exactly as written."}
    scores = {"type": "number", "minimum": 0, "maximum": 100,
              "description": "0 to 100, from your own point of view, with what you know."}
    own = {**scores, "description": "Your own score for this proposal: a number from 0 to 100 on your raw scale. Required: it goes "
                                    "in this field, not only in the reasoning."}
    return {
        "concord_start": _tool(
            "concord_start",
            "Open the CONCORD run among the participants. Every participant then proposes an option and rates all of them; the "
            "run ends when one option is rated well enough by everybody.",
            {"participants": {"type": "array", "items": _TEXT, "description": START_PARAMS["participants"]},
             "context": {**_TEXT, "description": START_PARAMS["context"]},
             "x_reject": {**_TEXT, "description": START_PARAMS["x_reject"]},
             "x_ideal": {**_TEXT, "description": START_PARAMS["x_ideal"]},
             "satisfaction_threshold": {"type": "number", "exclusiveMinimum": 0, "maximum": 1,
                                        "description": START_PARAMS["satisfaction_threshold"]},
             "options": {"type": "array", "items": _TEXT, "description": START_PARAMS["options"]}},
            ["participants", "context", "x_reject", "x_ideal", "satisfaction_threshold", "options"]),
        "submit_init_reply": _tool(
            "submit_init_reply",
            "Answer the init-reply task: your two anchors, the option you propose first, and your score for it.",
            {"raw_reject": {**scores, "description": "How satisfied you would be with the outcome x_reject (usually low)."},
             "raw_ideal": {**scores, "description": "How satisfied you would be with the outcome x_ideal (usually high)."},
             "candidate": candidate,
             "rationale": {**_TEXT, "description": "Why this option, with the facts you know that bear on it. The others read this."},
             "own_score": own, "reasoning": {**_TEXT, "description": "Why that score, with the facts behind it."}},
            ["raw_reject", "raw_ideal", "candidate", "rationale", "own_score", "reasoning"]),
        "submit_score": _tool(
            "submit_score",
            "Answer the score task: one score for each candidate the task lists, and no other.",
            {"scores": {"type": "array", "description": "One entry for each candidate the task lists, and no other.",
                         "items": {"type": "object", "properties": {
                "id": {**_TEXT, "description": "The id of the candidate, as in the task."}, "score": scores,
                "reasoning": {**_TEXT, "description": "Why that score, with the facts behind it."}},
                "required": ["id", "score", "reasoning"]}}},
            ["scores"]),
        "submit_repair": _tool(
            "submit_repair",
            "Answer the repair task: a better option than the incumbent, which you rated low. You may repeat an option, if a fact "
            "the others wrote makes you rate it differently now.",
            {"candidate": candidate, "rationale": {**_TEXT, "description": "Why this option, with the facts that bear on it."},
             "own_score": own, "reasoning": {**_TEXT, "description": "Why that score."}},
            ["candidate", "rationale", "own_score", "reasoning"]),
    }


def client_from_env(fresh=True):
    """The model client: LLM_BASE_URL and LLM_API_KEY when set (a LiteLLM proxy, say), else the SDK's own ANTHROPIC_* variables.
    `fresh` asks every server on the way not to answer from a cache (the header LiteLLM documents for it)."""
    return AsyncAnthropic(base_url=os.environ.get("LLM_BASE_URL") or None, api_key=os.environ.get("LLM_API_KEY") or None,
                          default_headers={"Cache-Control": "no-cache"} if fresh else None)


TOOL_OF_TASK = {"init-reply": "submit_init_reply", "score": "submit_score", "repair": "submit_repair"}


class ConcordAgent:
    def __init__(self, me, group, system, options, llm_model, out_dir, kickoff=None, timeout=None, threshold=None, temperature=None, fresh=True, log=print, llm=None):
        self.me, self.group, self.system, self.options, self.model, self.log = me, group, system, options, llm_model, log
        self.kickoff = kickoff                              # the first message to the model, for the agent that starts the run
        self.timeout = timeout
        self.temperature = temperature                      # None: the model's default
        self.threshold = threshold                          # fixed by the harness, or None: the initiator chooses
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        store = FileStore(out_dir / f"{me}-state.json")
        store.path.unlink(missing_ok=True)
        self.node = Agent(me, store=store)
        self.tools = tools_for(options)
        self.llm = llm or client_from_env(fresh)
        self.messages = []
        self.usage = {"input": 0, "output": 0, "calls": 0}
        self.events = []
        self.last_refusal = None
        self._last_key, self._repeats, self._settled, self._offered = None, 0, False, ()   # what a refusal needs to know

    # --- the node's side --------------------------------------------------------------------------------------------------

    async def _flush(self, step):
        for event in step.events:
            self.events.append(event)
            detail = {k: v for k, v in event.items() if k not in ("event", "at")}
            self.log(f"event {event['event']} {json.dumps(detail, default=str)[:300]}")
        for text in step.send:
            await self.group.send(text)

    def _refuse(self, name, text):
        """The refusal the model reads for the call `name`, and the books on it: the last one, how many times in a row, and whether the
        call is already answered (then the turn is over)."""
        self._repeats = self._repeats + 1 if (name, text) == self._last_key else 1
        self._last_key = (name, text)
        self.last_refusal = f"{name}: {text}"
        self._settled = self._settled or settled(text)
        return refusal(text, self._repeats), True

    async def _run_tool(self, name, args):
        """Call the node for a tool of the model. Returns (what the model reads, is_error)."""
        node, status = self.node, self.node.status()
        run_id = status["run_id"] if status else None
        if name not in self.tools or (self._offered and name not in self._offered):
            return self._refuse(name, UNKNOWN_TOOL.format(name=name, tools=", ".join(self._offered or self.tools)))
        missing = self._missing(name, args)
        if missing:
            return self._refuse(name, missing)
        problems = check_answer(name, args, self._anchors(run_id))
        if problems:
            return self._refuse(name, " ".join(problems))
        try:
            if name == "concord_start":
                spec = {"enum": list(args["options"])}
                params = {"satisfaction_threshold": self.threshold if self.threshold is not None else args["satisfaction_threshold"]}
                if self.threshold is not None and args.get("satisfaction_threshold") != self.threshold:
                    self.log(f"threshold fixed to {self.threshold} (the model asked for {args.get('satisfaction_threshold')})")
                if self.timeout is not None:
                    params["timeout"] = self.timeout
                step = node.start(args["participants"], args["context"], args["x_reject"], args["x_ideal"], spec, params=params)
            elif name == "submit_init_reply":
                step = node.submit_init_reply(run_id, args["raw_reject"], args["raw_ideal"], args["candidate"], args["rationale"],
                                              args["own_score"], args["reasoning"])
            elif name == "submit_score":
                step = node.submit_score(run_id, [(s["id"], s["score"], s["reasoning"]) for s in args["scores"]])
            else:
                step = node.submit_repair(run_id, args["candidate"], args["rationale"], args["own_score"], args["reasoning"])
        except ConcordError as e:
            return self._refuse(name, str(e))
        except Exception as e:                                     # a bug or a call of a shape nobody foresaw: never a bare traceback
            self.log(f"tool {name} failed unexpectedly:\n{traceback.format_exc()}")
            return self._refuse(name, UNEXPECTED.format(kind=type(e).__name__, detail=e))
        self._last_key, self._repeats = None, 0
        await self._flush(step)
        return "sent", False

    def _missing(self, name, args):
        """The arguments the tool needs and the call lacks, as a sentence (empty when none): the model reads it and fixes the call."""
        schema = self.tools[name]["input_schema"]
        top, nested = [], []

        def need(props, required, obj, out, where="", suffix=""):
            for k in required:
                if not isinstance(obj, dict) or obj.get(k) in (None, ""):
                    out.append(f"{where}`{k}` ({props[k].get('description', '')}){suffix}")

        need(schema["properties"], schema["required"], args, top)
        for k, prop in schema["properties"].items():
            items = prop.get("items") if isinstance(prop.get("items"), dict) else None
            if items and items.get("type") == "object" and isinstance(args.get(k), list):
                for n, entry in enumerate(args[k], 1):
                    have = ", ".join(entry) if isinstance(entry, dict) else "nothing"
                    suffix = f" [the entry has: {have}; an entry takes: {', '.join(items['properties'])}]"
                    need(items["properties"], items["required"], entry, nested, f"entry {n} of `{k}`: ", suffix)
        if nested:
            return lacks(nested + top)
        return lacks(top, sent=list(args) if isinstance(args, dict) else [], takes=list(schema["properties"])) if top else ""

    def _anchors(self, run_id):
        """The raw anchors of this agent as numbers, or None before its init reply."""
        run = self.node.state["runs"].get(run_id) if run_id else None
        view = run_view(run, self.me) if run else None
        a = view and view["anchors"]
        return {k: float(v) for k, v in a.items()} if a else None

    def _render(self, task):
        """The text the model reads for `task` (see guidance.py)."""
        run = self.node.state["runs"][task["run_id"]]
        if task["kind"] == "init-reply":
            return render_init_reply(self.me, task, run["participants"])
        if task["kind"] == "score":
            return render_score(self.me, task, run_view(run, self.me))
        return render_repair(self.me, task, run["context"], run["schema"])

    # --- the model's side -------------------------------------------------------------------------------------------------

    def _say(self, blocks):
        """Add to what the model reads. Two messages in a row of the same role are one."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"].extend(blocks)
        else:
            self.messages.append({"role": "user", "content": list(blocks)})

    async def _turn(self, text, tool_names, task=None):
        """One turn: tell the model what is owed, and let it call the tools until one is accepted."""
        self._say([{"type": "text", "text": text}])
        if getattr(self.llm, "wants_task", False):
            self.llm.task = task
        tools = [self.tools[n] for n in tool_names]
        self._offered, self._settled = tuple(tool_names), False
        for _ in range(MAX_TOOL_ROUNDS):
            # this SDK has no `temperature` argument: it goes in the body of the request
            extra = {} if self.temperature is None else {"extra_body": {"temperature": self.temperature}}
            resp = await self.llm.messages.create(model=self.model, max_tokens=2048, system=self.system, tools=tools,
                                                  tool_choice={"type": "any"}, messages=self.messages, **extra)
            self.usage["calls"] += 1
            self.usage["input"] += resp.usage.input_tokens
            self.usage["output"] += resp.usage.output_tokens
            self.messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
            results, accepted = [], False
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                self.log(f"tool {block.name} {json.dumps(block.input, ensure_ascii=False)[:600]}")
                result, is_error = await self._run_tool(block.name, block.input)
                if is_error:
                    self.log(f"tool {block.name} {result}")
                accepted = accepted or not is_error
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": result, "is_error": is_error})
            if not results:
                results.append({"type": "text", "text": NO_TOOL})
            self._say(results)
            if accepted:
                return True
            if self._settled:                                      # already answered: asking again changes nothing
                return False
        return False

    # --- the loop ---------------------------------------------------------------------------------------------------------

    async def run(self):
        if self.kickoff:
            self.log("kickoff")
            if not await self._turn(self.kickoff, ["concord_start"]):
                raise RuntimeError("the model did not start the run")
        stuck, last = 0, None
        linger_until = None
        while True:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                self.log("the session is closed")
                break
            if got:
                await self._flush(self.node.receive(got[1]))
            await self._flush(self.node.tick())
            status = self.node.status()
            if status and status["status"] != "active":
                linger_until = linger_until or time.time() + 4        # stay a little: another agent may still ask for a resend
                if time.time() > linger_until:
                    break
                continue
            tasks = self.node.due()
            if not tasks:
                continue
            sig = json.dumps([(t["kind"], t.get("round"), [c["id"] for c in t.get("candidates", [])]) for t in tasks])
            stuck = stuck + 1 if sig == last else 0
            last = sig
            if stuck >= MAX_TURNS_WITHOUT_PROGRESS:
                raise RuntimeError(f"{self.me} could not answer its {tasks[0]['kind']} task after {stuck} turns; last refusal: "
                                   f"{self.last_refusal}")
            task = tasks[0]
            self.log(f"task {task['kind']}")
            await self._turn(self._render(task), [TOOL_OF_TASK[task["kind"]]], task)

    def finish(self):
        """Write what happened, and return the report of the run."""
        report = self.node.report()
        record = {"agent": self.me, "report": report, "status": self.node.status(), "usage": self.usage,
                  "events": self.events, "transcript": self.messages}
        (self.out_dir / f"{self.me}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return report
