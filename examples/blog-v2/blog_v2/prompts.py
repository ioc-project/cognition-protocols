#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Everything a model reads in this example, in one place: the system prompt (the same for the four agents), the opening, what each turn
says, the tools (names, descriptions, parameters) and the replies to a wrong tool call. The rest of the code only picks pieces from here.

Not here, because they are not ours:
  - the facts of the scenario, the init of a CONCORD run (`context`, `x_reject`, `x_ideal`) and the options: `scenarios/`;
  - the CONCORD instructions (`RUN_DESCRIPTION`, the task of each turn, the refusals, how messages and the end of a run are shown):
    the library, `ioc_concord.guidance`. `RUN_DESCRIPTION` is part of the system prompt below.

`python -m blog_v2.prompts` prints the prompts of a scenario (the system prompt of an agent in each mode, the opening).

There is no facilitator: four leaders, all of them in the discussion. The prompt is the same for all of them, with one addition in CONCORD:
every agent can be a receiver (RUN_DESCRIPTION), and only the initiator, chosen by the harness (`--initiator`), also gets INITIATOR_EXTRA
and the tool `concord_start`, with which its model writes the init of the run.
  free speech (M over a mesh, S over SLIM): STEP1 + STEP2_FREE, opening(mode="free"), tools `post` / `stay_silent` / `final_answer`;
  CONCORD (C over SLIM): STEP1 + STEP2_CONCORD + RUN_DESCRIPTION, opening(mode="concord"), step 1 (`post`), then the tools of
  CONCORD. In both the agent is asked for its private final choice at the end (`final_answer`).
"""
import json
import re

from ioc_concord.guidance import INITIATOR_GUIDANCE, RUN_DESCRIPTION, START_PARAMS

from blog_v2 import prompts_mas
from blog_v2.common import NO_DECISION, tool_def
from blog_v2.scenario import AGENTS


# ---------------------------------------------------------------------------------------------------------------------------
# The system prompt of a participant: the same in every scenario except the last block, "how you decide together".
# ---------------------------------------------------------------------------------------------------------------------------

PARTICIPANT = """You are {me}, one of four community leaders (agent-a, agent-b, agent-c, agent-d) taking part in a group decision.

## The scenario
{scenario}

## What you know
Only you know some of these facts, and the others know things you do not. The order is random and means nothing.
{facts}

## The options
{options}

## Your role
You and the three other leaders want the same thing: the right place to evacuate to. Each of you knows only part of the picture, so what
you know is part of the evidence, not all of it. Do not invent facts: use what you hold, and what another leader wrote.

{run}
"""

# "How you decide together": step 1 is the same in every scenario, step 2 is what differs.
STEP1 = """## How you decide together
You decide in two steps, in a shared channel with the other three leaders: everything anyone posts is read by all of them.

Step 1: share what you know. You will be asked to post once, in plain text. List your facts as they are, one per line. You may say
which option you lean towards, as a provisional view, and say what you would like to learn from the others. Only what you write
reaches them: a fact you leave out is invisible to them, and what another leader writes is evidence you can use."""

# step 2, free speech (M, S)
STEP2_FREE = """Step 2: decide in the discussion. There is no procedure and no turn order: you keep talking with the other three leaders
until you reach a final decision on where to evacuate.

Each time new messages arrive, you decide what to do with them: answer, ask a question, add something you know, change your mind,
say that you agree, or leave them alone if you have nothing to add. You are not obliged to reply. When you are convinced that the
group has settled, say so, and say on which option.

The discussion ends when it has gone quiet, or at a time or message limit. After that you will be asked privately for your final choice."""

# the mesh (M): every leader has a direct link to each of the others, and chooses for each message who receives it
MESH_CHANNEL = ("You decide in two steps, with the other three leaders, over direct links: you have a link to each of them. You write a "
                "message, and then you choose who receives it (one, two or all three of them): send it to everyone, or only to the "
                "leaders it is for. A message you send is read only by the leaders you chose, and they see who else received it. What "
                "you do not receive, you do not know.")
STEP1_MESH = STEP1.replace("You decide in two steps, in a shared channel with the other three leaders: everything anyone posts is read by all of them.",
                           MESH_CHANNEL)
STEP2_FREE_MESH = STEP2_FREE.replace("Each time new messages arrive, you decide what to do with them:",
                                     "Each time new messages arrive, you decide what to do with them, and, if you write, who receives it:")

# step 2, CONCORD (C): this paragraph comes before the library's RUN_DESCRIPTION, which describes the procedure
STEP2_CONCORD = """Step 2: decide with the procedure. Once you have all posted (or after a short wait), the CONCORD procedure starts and you will receive its instructions. Before
you score, read what the others posted in step 1. Your first proposal and your scores should rest on all the facts that are now on
the table, yours and theirs, not only on your own. If a fact from another leader changes how an option looks, say so in your
rationale and score the option accordingly."""

# run_nonce: with `--fresh`, a line that makes every request new, so that a cache on the way cannot replay an earlier run
SESSION_NOTE = "\n(session {nonce})\n"


# only the initiator of a CONCORD run has this: the library's guidance on how to fill the init, and the tool `concord_start`
INITIATOR_EXTRA = """
## Your part as the initiator
You are the one who opens the CONCORD run, once the step-1 posts are in: you will be asked to start it with concord_start. After
that you take part in it like the others: you answer, rate and repair as everyone does.

""" + INITIATOR_GUIDANCE + "\n"


# ---------------------------------------------------------------------------------------------------------------------------
# With a coordinator (the orchestrated scenarios): the same prompt for the four leaders, with another "how you decide together" block, and
# a system prompt of its own for the coordinator, which holds no facts.
# ---------------------------------------------------------------------------------------------------------------------------

COORD_NOTE = {
    "star": ("A coordinator, who holds no facts and takes no side, opened this session and runs it. You decide in two steps with the other "
             "three leaders. Everything you post goes to the coordinator, who passes it on to the leaders it chooses; what the others post "
             "reaches you the same way, with the name of the author in front."),
    "slim": ("A coordinator, who holds no facts and takes no side, opened this session and runs it. You decide in two steps with the other "
             "three leaders, in a shared channel: everything anyone posts is read by all of them and by the coordinator."),
}
STEP1_COORD = """## How you decide together
{note}

Step 1: share what you know. You will be asked to post once, in plain text. List your facts as they are, one per line. You may say
which option you lean towards, as a provisional view, and say what you would like to learn from the others. Only what you write
reaches them: a fact you leave out is invisible to them, and what another leader writes is evidence you can use."""

STEP2_FREE_COORD = """Step 2: decide in the discussion. There is no procedure and no turn order: you keep talking with the other three leaders
until you reach a final decision on where to evacuate.

Each time new messages arrive, you decide what to do with them: answer, ask a question, add something you know, change your mind,
say that you agree, or leave them alone if you have nothing to add. You are not obliged to reply. When you have reached your own final
decision, call done with your option and why: it goes to the coordinator, who summarizes the outcome, and you leave the discussion.

The coordinator closes the session when the discussion has gone quiet, or at a time or message limit. If you are not done by then, you
will be asked for your final choice."""

STEP2_CONCORD_COORD = STEP2_CONCORD.replace("Once you have all posted (or after a short wait), the CONCORD procedure starts",
                                            "Once you have all posted (or after a short wait), the coordinator starts the CONCORD procedure")


# The library's description of CONCORD says that "one participant, the initiator" opens a run. That does not hold with a coordinator, so the
# text it needs is made here (a change in the library is not missed: `_swap` fails when its sentence is not found).
CONCORD_INITIATOR = ("One participant, the initiator, opens a run, and\neveryone answers it.",
                     "The coordinator, who is not a participant, opens a run, and\nevery participant answers it.")


def _swap(text, change):
    old, new = change
    if old not in text:
        raise ValueError(f"the text no longer has this sentence: {old!r}")
    return text.replace(old, new)





def system_prompt(scenario, agent, mode, initiator=False, variant=None, directed=False):
    """The system prompt of `agent` for `mode` ("free" or "concord"); `initiator`: it also opens the CONCORD run. `variant`: with a
    coordinator, free_star, free_slim, or concord (over SLIM); None: no coordinator."""
    if scenario.kind == "mas":
        return prompts_mas.worker_prompt(scenario, agent, mode, variant)
    facts = "\n".join(f"- {f}" for f in scenario.facts_for(agent))
    options = "\n".join(f"- {o}" for o in scenario.options)
    if variant in ("free_star", "free_slim", "concord"):
        step1 = STEP1_COORD.format(note=COORD_NOTE["star" if variant == "free_star" else "slim"])
        run = (f"{step1}\n\n{STEP2_FREE_COORD}" if variant != "concord"
               else f"{step1}\n\n{STEP2_CONCORD_COORD}\n\n{_swap(RUN_DESCRIPTION, CONCORD_INITIATOR)}")
    else:
        run = (f"{STEP1_MESH if directed else STEP1}\n\n{STEP2_FREE_MESH if directed else STEP2_FREE}" if mode == "free"
               else f"{STEP1}\n\n{STEP2_CONCORD}\n\n{RUN_DESCRIPTION}")
        if initiator and mode == "concord":
            run += "\n" + INITIATOR_EXTRA
    return PARTICIPANT.format(me=agent, scenario=scenario.text, facts=facts, options=options, run=run)


ORCHESTRATOR = """You are the coordinator of a group decision. Four community leaders ({participants}) must decide together where to evacuate.
You hold no facts, you take no side and you do not vote: you are not one of the leaders. Each leader holds part of the facts. Use only what
the leaders report, and do not invent facts.

## The situation
Each leader read this description:
{scenario}

## The options
{options}

## Your part
{part}
"""

ORCH_PART = {
    "free_star": ("The leaders discuss freely and are each connected to you and to no one else: they cannot talk to each other except "
                  "through you. For every message a leader sends you, use delegate_to to say which of the other leaders must receive it. The "
                  "message is passed on exactly as it was written; you never write or change one. Pass each message to all the leaders it "
                  "concerns, and never back to its sender. A leader that is done tells you its final choice; you do not pass that on."),
    "free_slim": ("The leaders discuss freely in a shared channel: everything anyone posts is read by all of them. You open the session and "
                  "wait. A leader that is done tells you its final choice. When the session is over, you summarize the outcome."),
    "summary": ("The session is over. Read the discussion and the final choice of each leader, and report the outcome: did the leaders "
                "agree on one option? They agreed only if all of them chose the same option. Summarize the outcome in a few sentences."),
    "concord": ("You open a CONCORD run among the four leaders, once they have shared what they know. You are not a participant of the run: "
                "the leaders answer it, and its outcome is the outcome of the session. You start it with concord_start."),
}
ORCH_INITIATOR_GUIDANCE = "## Starting the run\nTo open the run you give:\n" + "\n".join(
    f"- {k}: {v}" for k, v in {**START_PARAMS, "participants": "The ids of the four leaders, exactly as written in the task. You are not "
                              "a participant: do not list yourself."}.items())


def orchestrator_prompt(scenario, variant):
    """The system prompt of the coordinator for `variant` (free_star, free_slim, concord), or "summary" for its last call."""
    if scenario.kind == "mas":
        return prompts_mas.orchestrator_prompt(scenario, variant)
    part = ORCH_PART[variant] + ("\n\n" + ORCH_INITIATOR_GUIDANCE if variant == "concord" else "")
    return ORCHESTRATOR.format(participants=", ".join(AGENTS), scenario=scenario.text, part=part,
                               options="\n".join(f"- {o}" for o in scenario.options))


# ---------------------------------------------------------------------------------------------------------------------------
# What every agent is told when the discussion starts (the harness shows the same text to each, in place of a facilitator): the same
# words in both modes, except the last sentence.
# ---------------------------------------------------------------------------------------------------------------------------

OPENING = ("Leaders, we have to decide together where to evacuate. The options are: {options}. Each of you only knows part of the "
           "picture. Please post once: share the facts you know that bear on the decision, say which option you lean towards if you "
           "already lean one way (a lean is provisional until you have heard the others), and ask the others for what you are missing. "
           "{then}")
OPENING_THEN = {"free": "After that, keep talking until you find the best solution together.",
                "concord": "When you have all posted, the formal procedure will start to settle it.",
                "concord_coord": "When you have all posted, I will start the formal procedure to settle it."}
CLOSED = "The discussion is over."                      # what a free-speech agent is told when it stops waiting for more posts


def opening(scenario, mode):
    """`mode`: free, concord (distributed, no coordinator); free_coord, concord_coord (a coordinator opens: it speaks as "I")."""
    if scenario.kind == "mas":
        return prompts_mas.opening(scenario, mode)
    mode = {"free_coord": "free"}.get(mode, mode)
    return OPENING.format(options="; ".join(scenario.options), then=OPENING_THEN[mode])


# ---------------------------------------------------------------------------------------------------------------------------
# What a turn says: how a post is shown, step 1 of CONCORD, the end.
# ---------------------------------------------------------------------------------------------------------------------------

POST_LINE = "{who}: {text}"                           # a post of another leader, as the participant reads it
HUB_POST_PREFIX = "[{who}] "                          # over a star, put by the coordinator (the harness) in front of a post it passes on
DONE_PREFIX = "[done] "                               # a leader that is done tells the coordinator: this, then {"option", "rationale"} as JSON


def read_hub_post(text):
    """What a spoke does with a message from the centre of a star: (author, text) of a post the coordinator passed on, or None when it is
    the coordinator's own message (the opening, the close)."""
    wrote = re.match(re.escape(HUB_POST_PREFIX).replace(r"\{who\}", "(%s)" % "|".join(AGENTS)), text)
    return (wrote.group(1), text[wrote.end():]) if wrote else None


SHARE_ASK = ("Post your step-1 message now: the facts you know, which option you lean towards if you do (provisionally), and what you "
             "would like to learn from the others.")
SHARED_HEADING = "What the other leaders shared in step 1:"      # in front of the first CONCORD turn: the posts of step 1
START_ASK = ("{heard} Start the CONCORD run now with concord_start: the participants are {participants} (list yourself first), "
             "and the options are the only candidates.{threshold}")
FINAL_ASK = "Give your final choice now."
CLOSED_AND_ASK = f"{CLOSED} {FINAL_ASK}"              # free speech: the last turn of a participant


# ---------------------------------------------------------------------------------------------------------------------------
# The tools of a participant.
# ---------------------------------------------------------------------------------------------------------------------------

POST_TOOL = tool_def("post", "Write a message to the channel: all the other leaders read it.",
                     {"text": {"type": "string", "description": "The message, as you want the others to read it."}}, ["text"])


def _choice(options, described, extra=()):
    """The property of a choice: a closed list is an enum; an open format (a scenario of the mas-lab library) is a string, said in words."""
    if getattr(options, "open", False):
        said = options.description or "A choice, in the format the task describes."
        return {"type": "string", "description": f"{said}" + (f" Or {extra[0]!r}." if extra else "")}
    return {"type": "string", "enum": list(options) + list(extra), "description": described}


def final_answer_tool(options):
    """The private final choice of a participant, asked once the discussion is over, in both modes. It is never sent."""
    return tool_def("final_answer", "Give your own final choice. It is not sent to anyone.",
                    {"option": _choice(options, f"The option you choose, exactly as written, or {NO_DECISION!r}.", [NO_DECISION]),
                     "rationale": {"type": "string", "description": "Why."}}, ["option", "rationale"])


def done_tool(options):
    """A leader that is done: its final choice goes to the coordinator, and it leaves the discussion."""
    return tool_def("done", "Say that you are done: your final choice and why. It goes to the coordinator, who summarizes the outcome, and you "
                            "leave the discussion.",
                    {"option": _choice(options, f"The option you choose, exactly as written, or {NO_DECISION!r}.", [NO_DECISION]),
                     "rationale": {"type": "string", "description": "Why."}}, ["option", "rationale"])


POST_MESH_TOOL = tool_def("post", "Write a message for the other leaders. Right after, you choose who receives it.",
                          {"text": {"type": "string", "description": "The message, as you want the others to read it."}}, ["text"])
SEND_TO_TOOL = tool_def(
    "send_to", "Choose who receives the message you have just written. Nothing is sent before.",
    {"to": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": list(AGENTS)},
            "description": "The leaders that receive it: one, two or all three of the others (not yourself). Always name them."}}, ["to"])
ROUTE_ASK = ("Your message is written and has not been sent yet:\n\n{text}\n\nWho should receive it? Choose among {names}: one, two or all of "
             "them.")


def free_tools(options, coordinator=False, directed=False):
    """With a coordinator, a leader says it is done (`done`) instead of giving a private final choice. `directed` (the mesh): a post
    goes to the leaders the agent names."""
    post = POST_MESH_TOOL if directed else POST_TOOL
    if coordinator:
        return {"post": post, "stay_silent": free_tools(options)["stay_silent"], "done": done_tool(options)}
    return {
        **({"send_to": SEND_TO_TOOL} if directed else {}),
        "post": post,
        "stay_silent": tool_def("stay_silent", "Do not write anything now. Nothing is sent.",
                                {"reason": {"type": "string", "description": "Why you have nothing to say now. It stays private."}}, ["reason"]),
        "final_answer": final_answer_tool(options),
    }


def _concord_tools(options):
    candidate = (_choice(options, "") if getattr(options, "open", False)
                 else {"type": "string", "enum": options, "description": "One of the options, exactly as written."})
    scores = {"type": "number", "minimum": 0, "maximum": 100,
              "description": "0 to 100, from your own point of view, with what you know."}
    own = {**scores, "description": "Your own score for this proposal: a number from 0 to 100 on your raw scale. Required: it goes "
                                    "in this field, not only in the reasoning."}
    text = {"type": "string"}
    return {
        "final_answer": final_answer_tool(options),
        "post": POST_TOOL,                                     # step 1 only
        "concord_start": tool_def(                             # the initiator only
            "concord_start",
            "Open the CONCORD run among the participants. Every participant then proposes an option and rates all of them; the run "
            "ends when one option is rated well enough by everybody.",
            {"participants": {"type": "array", "items": {"type": "string"}, "description": START_PARAMS["participants"]},
             "context": {"type": "string", "description": START_PARAMS["context"]},
             "x_reject": {"type": "string", "description": START_PARAMS["x_reject"]},
             "x_ideal": {"type": "string", "description": START_PARAMS["x_ideal"]},
             "satisfaction_threshold": {"type": "number", "exclusiveMinimum": 0, "maximum": 1,
                                        "description": START_PARAMS["satisfaction_threshold"]},
             "options": {"type": "array", "items": {"type": "string"}, "description": START_PARAMS["options"]}},
            ["participants", "context", "x_reject", "x_ideal", "satisfaction_threshold", "options"]),
        "submit_init_reply": tool_def(
            "submit_init_reply",
            "Answer the init-reply task: your two anchors, the option you propose first, and your score for it.",
            {"raw_reject": {**scores, "description": "How satisfied you would be with the outcome x_reject (usually low)."},
             "raw_ideal": {**scores, "description": "How satisfied you would be with the outcome x_ideal (usually high)."},
             "candidate": candidate,
             "rationale": {**text, "description": "Why this option, with the facts you know that bear on it. The others read this."},
             "own_score": own, "reasoning": {**text, "description": "Why that score, with the facts behind it."}},
            ["raw_reject", "raw_ideal", "candidate", "rationale", "own_score", "reasoning"]),
        "submit_score": tool_def(
            "submit_score",
            "Answer the score task: one score for each candidate the task lists, and no other.",
            {"scores": {"type": "array", "description": "One entry for each candidate the task lists, and no other.",
                        "items": {"type": "object", "properties": {
                            "id": {**text, "description": "The id of the candidate, as in the task."}, "score": scores,
                            "reasoning": {**text, "description": "Why that score, with the facts behind it."}},
                            "required": ["id", "score", "reasoning"]}}},
            ["scores"]),
        "submit_repair": tool_def(
            "submit_repair",
            "Answer the repair task: a better option than the incumbent, which you rated low. You may repeat an option, if a fact "
            "the others wrote makes you rate it differently now.",
            {"candidate": candidate, "rationale": {**text, "description": "Why this option, with the facts that bear on it."},
             "own_score": own, "reasoning": {**text, "description": "Why that score."}},
            ["candidate", "rationale", "own_score", "reasoning"]),
    }


def concord_tools(options):
    """The CONCORD tools. With an open format the harness fixes the candidate schema, so the initiator does not list the options."""
    tools = _concord_tools(options)
    if getattr(options, "open", False):
        schema = tools["concord_start"]["input_schema"]
        schema["properties"].pop("options", None)
        schema["required"] = [r for r in schema["required"] if r != "options"]
    return tools


# ---------------------------------------------------------------------------------------------------------------------------
# What a participant reads back from a tool call, when it is not what the harness expects. (The refusals of CONCORD calls are the
# library's: `ioc_concord.guidance`.)
# ---------------------------------------------------------------------------------------------------------------------------

UNKNOWN_TOOL = "{name} is not a tool you have for this task: use {tools}."           # CONCORD
NO_TOOL = "Answer with the tool: it is the only way to answer, and nothing was sent."      # CONCORD
FREE_UNKNOWN_TOOL = "{name} is not a tool you have now."                                   # free speech
FREE_NO_TOOL = "Answer with a tool."
EMPTY_POST = "The message is empty."
WRONG_OPTION = "The option must be one of: {options}."
RECORDED, SENT, OK = "recorded", "sent", "ok"                                              # the result of an accepted call


# ---------------------------------------------------------------------------------------------------------------------------
# What the coordinator reads and the tools it has.
# ---------------------------------------------------------------------------------------------------------------------------

SUMMARY_USER = "The discussion:\n\n{transcript}\n\nThe final choice of each leader:\n\n{outcomes}"
SUMMARY_NOBODY = "(nobody wrote anything)"
SUMMARY_NO_OUTCOME = "(no final choice)"
SUMMARY_RETRY = "Answer with report_outcome."
COORD_START_ASK = ("{heard} Start the CONCORD run now with concord_start: the participants are {participants} (you are not one of them), "
                   "and the options are the only candidates.{threshold}")


def lex(scenario, text):
    """The harness's own words for the group: leaders in the evacuation task, roles in an app of the mas-lab library."""
    return text if scenario.kind != "mas" else text.replace("leaders", "roles").replace("leader", "role")


def localize(scenario, tools):
    """The tool definitions (one, or a dict of them) in the words of the scenario: the apps of the mas-lab library have roles and a moderator,
    not leaders and a coordinator. Evacuation: unchanged."""
    if scenario.kind != "mas":
        return tools
    text = (json.dumps(tools).replace("leaders", "roles").replace("leader", "role").replace("coordinator", "moderator")
            .replace("Decide where the group evacuates", f"Decide {scenario.decision}"))
    return json.loads(text)


def share_ask(scenario):
    return prompts_mas.share_ask(scenario) if scenario.kind == "mas" else SHARE_ASK


def coordinator_start_ask(scenario, heard, threshold):
    """What the coordinator is asked when step 1 is over."""
    names = ", ".join(scenario.agents)
    if scenario.options.open:
        return (f"{heard} Start the CONCORD run now with concord_start: the participants are {names} (you are not one of them), and a "
                f"candidate is {scenario.options.description}{threshold}")
    return COORD_START_ASK.format(heard=heard, participants=names, threshold=threshold)


def report_outcome_tool(options):
    return tool_def("report_outcome", "Report the outcome of the session.",
                    {"agreed": {"type": "boolean", "description": "True only if all the leaders chose the same option."},
                     "option": _choice(options, f"The option they agreed on, exactly as written; {NO_DECISION!r} if they did not agree.", [NO_DECISION]),
                     "summary": {"type": "string", "description": "The outcome in a few sentences."}}, ["agreed", "option", "summary"])


HUB_MESSAGE = "[{mid}] from {origin}:\n{text}"          # a message of a leader, as the coordinator reads it
DELEGATE = tool_def("delegate_to", "Pass a message of a leader on, as it is, to the leaders that must receive it.",
                    {"message_id": {"type": "string", "description": "The id of the message, as shown (m1, m2, ...)."},
                     "recipients": {"type": "array", "items": {"type": "string"}, "description": "The leaders that receive it."}},
                    ["message_id", "recipients"])
HUB_NOT_PASSED_ON = "Not passed on yet: {ids}. Use delegate_to."
HUB_BAD_CALL = "Unknown message or recipients: use a message id shown to you and a list of leaders."
HUB_ALREADY = "{mid} was passed on already."
HUB_PASSED_ON = "passed on to {names}"


def coordinator_start_tool(options):
    """`concord_start` as the coordinator has it: it is not a participant, so it does not list itself."""
    tool = dict(concord_tools(options)["concord_start"])
    props = {**tool["input_schema"]["properties"]}
    props["participants"] = {**props["participants"], "description": ORCH_INITIATOR_GUIDANCE.split("- participants: ")[1].split("\n")[0]}
    return {**tool, "input_schema": {**tool["input_schema"], "properties": props}}


if __name__ == "__main__":
    import sys

    from blog_v2.scenario import DEFAULT_SCENARIO, Scenario

    scenario = Scenario(DEFAULT_SCENARIO)
    for mode in ("free", "concord"):
        print(f"{'=' * 30} system prompt of agent-a, {mode}\n{system_prompt(scenario, 'agent-a', mode)}")
        print(f"{'=' * 30} opening, {mode}\n{opening(scenario, mode)}\n")
    print(f"{'=' * 30} step 1 ask\n{SHARE_ASK}")
    sys.exit(0)
