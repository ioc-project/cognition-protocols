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
from ioc_concord.guidance import (UNEXPECTED, check_answer, lacks, refusal, render_delivery, render_end, run_view, settled,
                                  wrong_shape)
from ioc_concord.store import FileStore

from concord_comparison.common import NO_DECISION, chat, flush, new_usage, note, say, write_record
from concord_comparison.prompts import (FINAL_ASK, NO_TOOL, POST_LINE, RECORDED, SENT, SHARE_ASK, SHARED_HEADING, UNKNOWN_TOOL, WRONG_OPTION,
                                        concord_tools, read_hub_post)
from concord_comparison.scenario import AGENTS, FACILITATOR
from concord_comparison.transport import SessionClosed

MAX_TURNS_WITHOUT_PROGRESS = 3
MAX_TOOL_ROUNDS = 4

def client_from_env(fresh=True):
    """The model client: LLM_BASE_URL and LLM_API_KEY when set (a LiteLLM proxy, say), else the SDK's own ANTHROPIC_* variables.
    `fresh` asks every server on the way not to answer from a cache (the header LiteLLM documents for it).
    A call that takes longer than LLM_TIMEOUT seconds (default 120) is cut and tried again, up to LLM_MAX_RETRIES times (default 4,
    with the SDK's backoff), so that one stalled request does not end an agent; a normal call takes a few seconds."""
    return AsyncAnthropic(base_url=os.environ.get("LLM_BASE_URL") or None, api_key=os.environ.get("LLM_API_KEY") or None,
                          timeout=float(os.environ.get("LLM_TIMEOUT", 120)), max_retries=int(os.environ.get("LLM_MAX_RETRIES", 4)),
                          default_headers={"Cache-Control": "no-cache"} if fresh else None)


TOOL_OF_TASK = {"init-reply": "submit_init_reply", "score": "submit_score", "repair": "submit_repair"}
TASK_OF_TOOL = {tool: task for task, tool in TOOL_OF_TASK.items()}


class ConcordAgent:
    def __init__(self, me, group, system, options, llm_model, out_dir, temperature=None, fresh=True, config=None, log=print, llm=None):
        self.shared = False                                 # this agent has posted its step 1
        self.posts_in = []                                  # the posts of the others in step 1, not yet shown to the model
        self.me, self.group, self.system, self.options, self.model, self.log = me, group, system, options, llm_model, log
        self.temperature = temperature                      # None: the model's default
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        store = FileStore(out_dir / f"{me}-state.json")
        store.path.unlink(missing_ok=True)
        self.node = Agent(me, store=store)
        self.tools = concord_tools(options)
        self.llm = llm or client_from_env(fresh)
        self.config = config or {}                          # what this agent ran with, saved in its record (see verify.py)
        self.messages = []
        self.inbox = []                                     # the messages of the others, not yet shown to the model
        self.usage = new_usage()
        self.events = []
        self.final = None                                   # the private final choice, asked once the run is over
        self.last_refusal = None
        self._last_key, self._repeats, self._settled, self._offered = None, 0, False, ()   # what a refusal needs to know

    # --- the node's side --------------------------------------------------------------------------------------------------

    def _refuse(self, name, error):
        """The refusal the model reads for the call `name`, and the books on it: the last one, how many times in a row, and whether the
        call is already answered (then the turn is over). `error` is an error of the library, or the text of a refusal."""
        self._repeats = self._repeats + 1 if (name, str(error)) == self._last_key else 1
        self._last_key = (name, str(error))
        self.last_refusal = f"{name}: {error}"
        self._settled = self._settled or settled(error)
        return refusal(error, self._repeats), True

    async def _run_tool(self, name, args):
        """Call the node for a tool of the model. Returns (what the model reads, is_error)."""
        node, status = self.node, self.node.status()
        run_id = status["run_id"] if status else None
        if name not in self.tools or (self._offered and name not in self._offered):
            return self._refuse(name, UNKNOWN_TOOL.format(name=name, tools=", ".join(self._offered or self.tools)))
        shape = self._wrong_shape(name, args)
        if shape:
            return self._refuse(name, shape)
        missing = self._missing(name, args)
        if missing:
            return self._refuse(name, missing)
        if name == "post":                                         # step 1: free text, never a CONCORD message
            text = str(args.get("text") or "").strip()
            await self.group.send(text)
            note(self.events, self.log, "posted", text=text)
            self._last_key, self._repeats = None, 0
            return SENT, False
        if name == "final_answer":
            if args["option"] not in self.options + [NO_DECISION]:
                return self._refuse(name, WRONG_OPTION.format(options=", ".join(self.options + [NO_DECISION])))
            self.final = {"option": args["option"], "rationale": args.get("rationale", "")}
            self.log(f"event final_answer {self.final['option']}")
            self._last_key, self._repeats = None, 0
            return RECORDED, False
        problems = check_answer(TASK_OF_TOOL.get(name), self._answer_of(args), self._anchors(run_id))
        if problems:
            return self._refuse(name, " ".join(problems))
        try:
            if name == "submit_init_reply":
                step = node.submit_init_reply(run_id, args["raw_reject"], args["raw_ideal"], args["candidate"], args["rationale"],
                                              args["own_score"], args["reasoning"])
            elif name == "submit_score":
                step = node.submit_score(run_id, [(s["id"], s["score"], s["reasoning"]) for s in args["scores"]])
            else:
                step = node.submit_repair(run_id, args["candidate"], args["rationale"], args["own_score"], args["reasoning"])
        except ConcordError as e:
            return self._refuse(name, e)
        except Exception as e:                                     # a bug or a call of a shape nobody foresaw: never a bare traceback
            self.log(f"tool {name} failed unexpectedly:\n{traceback.format_exc()}")
            return self._refuse(name, UNEXPECTED.format(kind=type(e).__name__, detail=e))
        self._last_key, self._repeats = None, 0
        await flush(step, self.group, self.events, self.log)
        return SENT, False

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

    def _wrong_shape(self, name, args):
        """The arguments that are not in the shape the tool takes (a list sent as text, an entry that is not an object), as a sentence
        (empty when all are right). What is missing is said by `_missing`; this is for what is there but wrong."""
        out = []
        for k, prop in self.tools[name]["input_schema"]["properties"].items():
            v = args.get(k)
            if v is None or prop.get("type") != "array":
                continue
            items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            fields = ", ".join(items.get("properties", {}))
            if not isinstance(v, list):
                out.append(wrong_shape(k, f"a list of entries ({fields})" if fields else "a list", v))
            elif fields:
                out += [wrong_shape(f"{k}` entry `{n}", f"an object with {fields}", e) for n, e in enumerate(v, 1) if not isinstance(e, dict)]
        return " ".join(out)

    @staticmethod
    def _answer_of(args):
        """What the model sent, under the names the library's checks use: the tools call the score of a proposal `own_score`, and the
        score of a rating `score`; the library calls both `raw`."""
        return {**args, "raw": args.get("own_score"), "scores": [{**s, "raw": s.get("score")} for s in args.get("scores") or []
                                                                   if isinstance(s, dict)]}

    def _anchors(self, run_id):
        """The raw anchors of this agent as numbers, or None before its init reply."""
        run = self.node.state["runs"].get(run_id) if run_id else None
        view = run_view(run, self.me) if run else None
        a = view and view["anchors"]
        return {k: float(v) for k, v in a.items()} if a else None

    # --- the model's side -------------------------------------------------------------------------------------------------

    async def _turn(self, text, tool_names, task=None):
        """One turn: tell the model what is owed, and let it call the tools until one is accepted."""
        delivery = render_delivery(self.inbox)               # what the others sent since the last answer, in front of the task
        self.inbox = []
        posts = self._take_posts() if tool_names != ["post"] else ""
        say(self.messages, [{"type": "text", "text": "\n\n".join(t for t in (posts, delivery, text) if t)}])
        if getattr(self.llm, "wants_task", False):
            self.llm.task = task
        tools = [self.tools[n] for n in tool_names]
        self._offered, self._settled = tuple(tool_names), False
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await chat(self.llm, self.model, self.system, tools, self.messages, self.usage, self.temperature, log=self.log)
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
            say(self.messages, results)
            if accepted:
                return True
            if self._settled:                                      # already answered: asking again changes nothing
                return False
        return False

    def _take_posts(self):
        """The posts of step 1 that the model has not read, with a heading, in front of its next turn (empty when there are none)."""
        if not self.posts_in:
            return ""
        out = SHARED_HEADING + "\n\n" + "\n\n".join(POST_LINE.format(who=who, text=text) for who, text in self.posts_in)
        self.posts_in = []
        return out

    async def _on_free(self, origin, text):
        """Free text, step 1: the facilitator's opening asks for this agent's post, a post of another leader waits for the first CONCORD
        turn. The node never sees it."""
        who = next((a for a in (FACILITATOR,) + AGENTS if a in str(origin)), str(origin))
        if who == "hub":                                       # a star: the centre says who wrote it, in front of the text
            who, text = read_hub_post(text) or (FACILITATOR, text)
        if who == self.me:
            return
        if who == FACILITATOR and not self.shared:
            self.shared = True
            self.log("step 1: share what I know")
            await self._turn(f"{POST_LINE.format(who=who, text=text)}\n\n{SHARE_ASK}", ["post"])
        elif who != FACILITATOR:
            self.posts_in.append((who, text))

    # --- the loop ---------------------------------------------------------------------------------------------------------

    async def run(self):
        stuck, last = 0, None
        linger_until = None
        while True:
            try:
                got = await self.group.recv(timeout=1.0)
            except SessionClosed:
                self.log("the session is closed")
                break
            if got and not got[1].lstrip().startswith("<"):         # not a CONCORD message: step 1
                await self._on_free(*got)
                got = None
            if got:
                step = self.node.receive(got[1])
                self.inbox += step.accepted                          # what the node took: the model reads it before its next task
                await flush(step, self.group, self.events, self.log)
            await flush(self.node.tick(), self.group, self.events, self.log)
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
            await self._turn(self.node.instructions(task), [TOOL_OF_TASK[task["kind"]]], task)
        self._close_conversation()
        await self._turn(FINAL_ASK, ["final_answer"])

    def _close_conversation(self):
        """The end of the run as the spec has it: the reporter's result reaches every other agent, and each knows how it ended. No model
        model turn follows here: it goes into the conversation, so the record shows what the agent knew. (The private final choice is asked
        after it, in `run`.)"""
        report = self.node.report()
        if report is None:
            return
        delivery = render_delivery(self.inbox)
        self.inbox = []
        end = render_end(self.me, report)
        say(self.messages, [{"type": "text", "text": f"{delivery}\n\n{end}" if delivery else end}])

    def finish(self):
        """Write what happened, and return the report of the run."""
        report = self.node.report()
        write_record(self.out_dir, self.me, config=self.config, report=report, final=self.final, status=self.node.status(),
                     usage=self.usage, events=self.events, transcript=self.messages)
        return report
