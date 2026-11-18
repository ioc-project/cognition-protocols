#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Everything a model reads in this example, in one place: the system prompts, what the facilitator posts, what each turn says, the
tools (names, descriptions, parameters) and the replies to a wrong tool call. The rest of the code only picks pieces from here.

Not here, because they are not ours:
  - the facts of the scenario, the init of a CONCORD run (`context`, `x_reject`, `x_ideal`) and the options: `scenarios/*.yaml`;
  - the CONCORD instructions (`RUN_DESCRIPTION`, the task of each turn, the refusals, how messages and the end of a run are shown):
    the library, `ioc_concord.guidance`. `RUN_DESCRIPTION` is part of the system prompt below.

`python -m concord_comparison.prompts` prints the prompts of a scenario (the system prompt of a participant in each mode, the opening).

The scenarios and their prompts:
  free speech (A over a star, B over SLIM): STEP1 + STEP2_FREE, opening(mode="free"), tools `post` / `stay_silent` / `final_answer`;
  CONCORD (C over a star, D over SLIM): STEP1 + STEP2_CONCORD + RUN_DESCRIPTION, opening(mode="concord"), step 1 (`post`), then the
  tools of CONCORD. In both the participant is asked for its private final choice at the end (`final_answer`).
"""
import re

from ioc_concord.guidance import RUN_DESCRIPTION

from concord_comparison.common import NO_DECISION, tool_def
from concord_comparison.scenario import AGENTS


# ---------------------------------------------------------------------------------------------------------------------------
# The system prompt of a participant: the same in every scenario except the last block, "how you decide together".
# ---------------------------------------------------------------------------------------------------------------------------

PARTICIPANT = """You are {me}, one of three community leaders (participant-a, participant-b, participant-c) taking part in a group decision.

## The scenario
{scenario}

## What you know
Only you know some of these facts, and the others know things you do not. The order is random and means nothing.
{facts}

## The options
{options}

## Your role
You and the other leaders want the same thing: the right place to evacuate to. Each of you knows only part of the picture, so what
you know is part of the evidence, not all of it. Do not invent facts: use what you hold, and what another leader wrote.

{run}
"""

# "How you decide together": step 1 is the same in every scenario, step 2 is what differs.
STEP1 = """## How you decide together
You decide in two steps, in a shared channel with the other two leaders: everything anyone posts is read by all of them, and by a
facilitator who does not take part.

Step 1: share what you know. You will be asked to post once, in plain text. List your facts as they are, one per line. You may say
which option you lean towards, as a provisional view, and say what you would like to learn from the others. Only what you write
reaches them: a fact you leave out is invisible to them, and what another leader writes is evidence you can use."""

# step 2, free speech (A, B)
STEP2_FREE = """Step 2: decide in the discussion. There is no procedure and no turn order: you keep talking with the other two leaders
until you reach a final decision on where to evacuate.

Each time new messages arrive, you decide what to do with them: answer, ask a question, add something you know, change your mind,
say that you agree, or leave them alone if you have nothing to add. You are not obliged to reply. When you are convinced that the
group has settled, say so, and say on which option.

The facilitator closes the session when the discussion has gone quiet. After that you will be asked privately for your final choice."""

# step 2, CONCORD (C, D): this paragraph comes before the library's RUN_DESCRIPTION, which describes the procedure
STEP2_CONCORD = """Step 2: decide with the procedure. The facilitator then starts the CONCORD procedure and you will receive its instructions. Before
you score, read what the others posted in step 1. Your first proposal and your scores should rest on all the facts that are now on
the table, yours and theirs, not only on your own. If a fact from another leader changes how an option looks, say so in your
rationale and score the option accordingly."""

# run_nonce: with `--fresh`, a line that makes every request new, so that a cache on the way cannot replay an earlier run
SESSION_NOTE = "\n(session {nonce})\n"


def system_prompt(scenario, agent, mode):
    """The system prompt of `agent` for `mode` ("free" or "concord")."""
    facts = "\n".join(f"- {f}" for f in scenario.facts_for(agent))
    options = "\n".join(f"- {o}" for o in scenario.options)
    run = f"{STEP1}\n\n{STEP2_FREE}" if mode == "free" else f"{STEP1}\n\n{STEP2_CONCORD}\n\n{RUN_DESCRIPTION}"
    return PARTICIPANT.format(me=agent, scenario=scenario.text, facts=facts, options=options, run=run)


# ---------------------------------------------------------------------------------------------------------------------------
# What the facilitator posts to open the discussion: the same words in both modes, except the last sentence.
# ---------------------------------------------------------------------------------------------------------------------------

OPENING = ("Leaders, we have to decide together where to evacuate. The options are: {options}. Each of you only knows part of the "
           "picture. Please post once: share the facts you know that bear on the decision, say which option you lean towards if you "
           "already lean one way (a lean is provisional until you have heard the others), and ask the others for what you are missing. "
           "{then}")
OPENING_THEN = {"free": "After that, keep talking until you find the best solution together.",
                "concord": "When you have all posted, I will start the formal procedure to settle it."}
CLOSED = "The session is closed."                     # what the facilitator posts to close a free-speech session


def opening(scenario, mode):
    return OPENING.format(options="; ".join(scenario.options), then=OPENING_THEN[mode])


# ---------------------------------------------------------------------------------------------------------------------------
# What a turn says: how a post is shown, step 1 of CONCORD, the end.
# ---------------------------------------------------------------------------------------------------------------------------

POST_LINE = "{who}: {text}"                           # a post of another leader, as the participant reads it
HUB_POST_PREFIX = "[{who}] "                          # in a star, put by the hub (the harness) in front of a post it passes on


def read_hub_post(text):
    """What a spoke does with a message from the hub of a star: (author, text) of a post the hub passed on, or None when it is the
    facilitator's own message (the opening, the close)."""
    wrote = re.match(re.escape(HUB_POST_PREFIX).replace(r"\{who\}", "(%s)" % "|".join(AGENTS)), text)
    return (wrote.group(1), text[wrote.end():]) if wrote else None


SHARE_ASK = ("Post your step-1 message now: the facts you know, which option you lean towards if you do (provisionally), and what you "
             "would like to learn from the others.")
SHARED_HEADING = "What the other leaders shared in step 1:"      # in front of the first CONCORD turn: the posts of step 1
FINAL_ASK = "Give your final choice now."
CLOSED_AND_ASK = f"{CLOSED} {FINAL_ASK}"              # free speech: the last turn of a participant


# ---------------------------------------------------------------------------------------------------------------------------
# The tools of a participant.
# ---------------------------------------------------------------------------------------------------------------------------

POST_TOOL = tool_def("post", "Write a message to the channel: all the other leaders and the facilitator read it.",
                     {"text": {"type": "string", "description": "The message, as you want the others to read it."}}, ["text"])


def final_answer_tool(options):
    """The private final choice of a participant, asked once the discussion is over, in both modes. It is never sent."""
    return tool_def("final_answer", "Give your own final choice. It is not sent to anyone.",
                    {"option": {"type": "string", "enum": list(options) + [NO_DECISION],
                                "description": f"The option you choose, exactly as written, or {NO_DECISION!r}."},
                     "rationale": {"type": "string", "description": "Why."}}, ["option", "rationale"])


def free_tools(options):
    return {
        "post": POST_TOOL,
        "stay_silent": tool_def("stay_silent", "Do not write anything now. Nothing is sent.",
                                {"reason": {"type": "string", "description": "Why you have nothing to say now. It stays private."}}, ["reason"]),
        "final_answer": final_answer_tool(options),
    }


def concord_tools(options):
    candidate = {"type": "string", "enum": options, "description": "One of the options, exactly as written."}
    scores = {"type": "number", "minimum": 0, "maximum": 100,
              "description": "0 to 100, from your own point of view, with what you know."}
    own = {**scores, "description": "Your own score for this proposal: a number from 0 to 100 on your raw scale. Required: it goes "
                                    "in this field, not only in the reasoning."}
    text = {"type": "string"}
    return {
        "final_answer": final_answer_tool(options),
        "post": POST_TOOL,                                     # step 1 only
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
# The hub of a star (A and C): a model that passes each message of a spoke on.
# ---------------------------------------------------------------------------------------------------------------------------

HUB_SYSTEM = """You are the facilitator, the hub of a star: {participants} are the participants of a group decision and each of them is \
connected to you and to no one else. They cannot talk to each other except through you. You take no part in the decision.

Every message a participant sends reaches you alone. For each one, use delegate_to to say which of the other participants must \
receive it; the message is passed on exactly as it was written, you never write or change one. Every participant must receive \
what it needs to take part, so pass each message to all the participants it concerns, and never back to its sender."""

HUB_MESSAGE = "[{mid}] from {origin}:\n{text}"          # a message of a spoke, as the hub model reads it
DELEGATE = tool_def("delegate_to", "Pass a message of a participant on, as it is, to the participants that must receive it.",
                    {"message_id": {"type": "string", "description": "The id of the message, as shown (m1, m2, ...)."},
                     "recipients": {"type": "array", "items": {"type": "string"}, "description": "The participants that receive it."}},
                    ["message_id", "recipients"])
HUB_NOT_PASSED_ON = "Not passed on yet: {ids}. Use delegate_to."
HUB_BAD_CALL = "Unknown message or recipients: use a message id shown to you and a list of participants."
HUB_ALREADY = "{mid} was passed on already."
HUB_PASSED_ON = "passed on to {names}"


# ---------------------------------------------------------------------------------------------------------------------------
# The verdict of the facilitator of free speech: after the session, a model reads it and says if the participants agreed.
# ---------------------------------------------------------------------------------------------------------------------------

VERDICT_SYSTEM = """You are the facilitator of a group discussion between {participants}. You took no part in it and the session is now \
closed. Read it and report the outcome: did the participants agree on one option? They agreed only if they converged on the same \
option, explicitly. If they did not, or are still split, they did not agree."""

VERDICT_USER = "The discussion:\n\n{transcript}"
VERDICT_NOBODY = "(nobody wrote anything)"
VERDICT_RETRY = "Answer with report_outcome."


def verdict_tool(options):
    return tool_def("report_outcome", "Report whether the participants agreed, and on which option.",
                    {"agreed": {"type": "boolean", "description": "True only if they explicitly converged on one option."},
                     "option": {"type": "string", "enum": list(options) + [NO_DECISION],
                                "description": f"The option they agreed on, exactly as written; {NO_DECISION!r} if they did not agree."}},
                    ["agreed", "option"])


if __name__ == "__main__":
    import sys
    from concord_comparison.scenario import Scenario
    from pathlib import Path

    scenario = Scenario(Path(__file__).resolve().parent.parent / "scenarios" / "evacuation-west-city.yaml")
    for mode in ("free", "concord"):
        print(f"{'=' * 30} system prompt of participant-a, {mode}\n{system_prompt(scenario, 'participant-a', mode)}")
        print(f"{'=' * 30} opening, {mode}\n{opening(scenario, mode)}\n")
    print(f"{'=' * 30} step 1 ask\n{SHARE_ASK}\n\n{'=' * 30} hub system\n{HUB_SYSTEM}\n\n{'=' * 30} verdict system\n{VERDICT_SYSTEM}")
    sys.exit(0)
