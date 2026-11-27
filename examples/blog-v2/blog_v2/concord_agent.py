#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CONCORD agent: the free-speech agent (baseline_agent.py) with a CONCORD node added.

Step 1 is the same as in free speech: the agent writes what it knows in a post. Then the node takes over. It holds the protocol: it
checks every message, keeps the state and builds what is sent. The model only writes content, through tools that call the node's
`start` and `submit_*`. What this class changes in the free agent is the loop:

    a message arrives     -> a CONCORD message: node.receive(text), then send what it hands back; free text: kept, no model call
    nothing for a second  -> node.tick(), which asks again for what is late or gives up
    node.due() not empty  -> a model call, which must answer with a tool
    the run is over       -> the final choice, and the report is written
"""
import json
import time
import traceback

from ioc_concord.agent import Agent
from ioc_concord.errors import ConcordError
from ioc_concord.guidance import (UNEXPECTED, check_answer, lacks, refusal, render_delivery, render_end, run_view, settled,
                                  wrong_shape)
from ioc_concord.store import FileStore

from blog_v2.common import flush, note, pick, say, sender_of, write_record, wrong_choice
from blog_v2.baseline_agent import BaselineAgent
from blog_v2.prompts import (FINAL_ASK, NO_TOOL, POST_LINE, RECORDED, SENT, SHARED_HEADING, START_ASK, UNKNOWN_TOOL, concord_tools, lex,
                             localize)
from blog_v2.scenario import AGENTS

SHARE_WAIT = 60                  # seconds the initiator waits for the step-1 posts of the others before it starts the run
DEFAULT_THRESHOLD = 0.6
MAX_TURNS_WITHOUT_PROGRESS = 3

TOOL_OF_TASK = {"init-reply": "submit_init_reply", "score": "submit_score", "repair": "submit_repair"}
TASK_OF_TOOL = {tool: task for task, tool in TOOL_OF_TASK.items()}


class ConcordAgent(BaselineAgent):
    mode = "concord"
    no_tool = NO_TOOL

    def __init__(self, me, group, system, scenario, llm_model, out_dir, initiator=False, threshold=None, timeout=None,
                 temperature=None, fresh=True, config=None, log=print, llm=None, coordinated=False):
        super().__init__(me, group, system, scenario, llm_model, out_dir, temperature=temperature, fresh=fresh, config=config, log=log,
                         llm=llm, coordinated=coordinated)   # a coordinator opens the session and starts the run; this agent is only a participant
        self.initiator = initiator                           # the initiator's model starts the run once step 1 is over; it is a participant like the rest
        self.initiator_name = me if initiator else None
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self.timeout = timeout
        self.started = False
        self.heard = set()                                  # who of the others has posted in step 1
        self.posts_in = []                                  # the posts of the others in step 1, not yet shown to the model
        store = FileStore(out_dir / f"{me}-state.json")
        store.path.unlink(missing_ok=True)
        self.node = Agent(me, store=store)
        self.tools = localize(scenario, concord_tools(scenario.options))
        self.accepted = []                                  # what the node took from the others, not yet shown to the model
        self.step1_done = self.linger_until = self.last_task = None
        self.stuck = 0
        self.last_refusal = None
        self._last_key, self._repeats, self._settled = None, 0, False   # what a refusal needs to know

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
        if name == "concord_start":                                # the initiator only, once step 1 is over
            return await self._start(args)
        if name == "post":                                         # step 1: free text, never a CONCORD message
            text = str(args.get("text") or "").strip()
            await self.group.send(text)
            note(self.events, self.log, "posted", text=text)
            self._last_key, self._repeats = None, 0
            return SENT, False
        if name == "final_answer":
            option = pick(self.scenario, args["option"])
            if option is None:
                return self._refuse(name, wrong_choice(self.scenario))
            self.final = {"option": option, "rationale": args.get("rationale", "")}
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

    async def _act(self, text, tool_names, task=None):
        """A model call: tell the model what is owed, and let it call the tools until one is accepted."""
        delivery = render_delivery(self.accepted)            # what the others sent since the last answer, in front of the task
        self.accepted = []
        posts = self._take_posts() if tool_names != ["post"] else ""
        if getattr(self.llm, "wants_task", False):
            self.llm.task = task
        self._settled = False
        return await self._ask("\n\n".join(t for t in (posts, delivery, text) if t), tool_names)

    def _stop_asking(self):
        return self._settled

    def _take_posts(self):
        """The posts of step 1 that the model has not read, with a heading, in front of its next call (empty when there are none)."""
        if not self.posts_in:
            return ""
        out = lex(self.scenario, SHARED_HEADING) + "\n\n" + "\n\n".join(POST_LINE.format(who=who, text=text) for who, text in self.posts_in)
        self.posts_in = []
        return out

    def _on_free(self, origin, text):
        """Free text, step 1: a post of another leader waits for the first CONCORD call. The node never sees it."""
        who = sender_of(origin)
        if who == self.me:
            return
        self.heard.add(who)
        self.posts_in.append((who, text))
        note(self.events, self.log, "received", **{"from": who}, text=text)

    async def _start(self, args):
        """The initiator's tool: open the run with what its model wrote. Returns (what the model reads, is_error)."""
        if self.me != self.initiator_name:
            return self._refuse("concord_start", UNKNOWN_TOOL.format(name="concord_start", tools=", ".join(self._offered or self.tools)))
        if sorted(args["participants"]) != sorted(AGENTS):
            return self._refuse("concord_start", f"the participants must be exactly: {', '.join(AGENTS)}.")
        if sorted(args["options"]) != sorted(self.scenario.options):
            return self._refuse("concord_start", f"the options must be exactly, as written: {'; '.join(self.scenario.options)}.")
        threshold = args["satisfaction_threshold"] if self.threshold is None else self.threshold
        if self.threshold is not None and args.get("satisfaction_threshold") != self.threshold:
            self.log(f"threshold fixed to {self.threshold} (the model asked for {args.get('satisfaction_threshold')})")
        params = {"satisfaction_threshold": threshold}
        if self.timeout is not None:
            params["timeout"] = self.timeout
        try:
            step = self.node.start(list(args["participants"]), args["context"], args["x_reject"], args["x_ideal"],
                                   {"enum": list(args["options"])}, params=params)
        except ConcordError as e:
            return self._refuse("concord_start", e)
        self.started = True
        self.log(f"start the run: participants {args['participants']}, threshold {threshold}")
        await flush(step, self.group, self.events, self.log)
        self._last_key, self._repeats = None, 0
        return SENT, False

    async def _start_run(self):
        """The initiator's model opens the run once the others have posted (or SHARE_WAIT is over)."""
        heard = ("The other leaders have posted." if len(self.heard) == len(AGENTS) - 1
                 else f"{len(self.heard)} of the {len(AGENTS) - 1} other leaders have posted; the wait is over.")
        ask = START_ASK.format(heard=heard, participants=", ".join(AGENTS),
                               threshold=f" Use satisfaction_threshold {self.threshold}." if self.threshold is not None else "")
        if not await self._act(ask, ["concord_start"]):
            raise RuntimeError(f"{self.me} could not start the run; last refusal: {self.last_refusal}")

    # --- the loop ---------------------------------------------------------------------------------------------------------

    async def _begin(self):
        self.log("step 1: share what I know")
        await super()._begin()
        self.step1_done = time.time()

    async def _step(self):
        """One pass of the loop. Returns why the agent stops, or None."""
        if self.initiator and not self.started and (len(self.heard) == len(AGENTS) - 1 or time.time() - self.step1_done >= SHARE_WAIT):
            self.log(f"step 1 over: {len(self.heard)} of {len(AGENTS) - 1} others posted in {time.time() - self.step1_done:.0f}s")
            await self._start_run()
        got = await self.group.recv(timeout=1.0)
        if got and not got[1].lstrip().startswith("<"):         # not a CONCORD message: step 1
            self._on_free(*got)
            got = None
        if got:
            step = self.node.receive(got[1])
            self.accepted += step.accepted                       # what the node took: the model reads it before its next task
            await flush(step, self.group, self.events, self.log)
        await flush(self.node.tick(), self.group, self.events, self.log)
        status = self.node.status()
        if status and status["status"] != "active":
            self.linger_until = self.linger_until or time.time() + 4        # stay a little: another agent may still ask for a resend
            return "run over" if time.time() > self.linger_until else None
        tasks = self.node.due()
        if not tasks:
            return None
        sig = json.dumps([(t["kind"], t.get("round"), [c["id"] for c in t.get("candidates", [])]) for t in tasks])
        self.stuck = self.stuck + 1 if sig == self.last_task else 0
        self.last_task = sig
        if self.stuck >= MAX_TURNS_WITHOUT_PROGRESS:
            raise RuntimeError(f"{self.me} could not answer its {tasks[0]['kind']} task after {self.stuck} turns; last refusal: "
                               f"{self.last_refusal}")
        task = tasks[0]
        self.log(f"task {task['kind']}")
        await self._act(self.node.instructions(task), [TOOL_OF_TASK[task["kind"]]], task)
        return None

    async def _last_call(self):
        self._close_conversation()
        await self._act(FINAL_ASK, ["final_answer"])

    def _close_conversation(self):
        """The end of the run as the spec has it: the reporter's result reaches every other agent, and each knows how it ended. No model
        call follows here: it goes into the conversation, so the record shows what the agent knew. (The private final choice is asked
        after it, in `_last_call`.)"""
        report = self.node.report()
        if report is None:
            return
        delivery = render_delivery(self.accepted)
        self.accepted = []
        end = render_end(self.me, report)
        say(self.messages, [{"type": "text", "text": f"{delivery}\n\n{end}" if delivery else end}])

    def finish(self):
        """Write what happened, and return the report of the run."""
        report = self.node.report()
        write_record(self.out_dir, self.me, config=self.config, report=report, final=self.final, status=self.node.status(),
                     usage=self.usage, events=self.events, transcript=self.messages)
        return report
