#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What a model reads in the scenarios of the mas-lab library (`kind: mas`): the same pieces as prompts.py, for roles instead of leaders.

The prompt of a role is the ORIGINAL prompt of the app, word for word (and the skills the app attaches to that role), followed by one block
that says how the roles decide together. The coordinator is the original moderator (its first paragraphs; the lines about the lab's own
delegation tools are replaced by the ones of this harness). The data a role knows is what its tools return: it calls them.

With a coordinator only (O1 to O3): `variant` is free_star, free_slim or concord, as in prompts.py.
"""
from ioc_concord.guidance import RUN_DESCRIPTION, START_PARAMS

CONCORD_INITIATOR = ("One participant, the initiator, opens a run, and\neveryone answers it.",
                     "The coordinator, who is not a participant, opens a run, and\nevery participant answers it.")

COORD_NOTE = {
    "star": ("A moderator, who holds no mandate and takes no side, opened this session and runs it. You decide in two steps with the other "
             "roles. Everything you post goes to the moderator, who passes it on to the roles it chooses; what the others post reaches "
             "you the same way, with the name of the author in front."),
    "slim": ("A moderator, who holds no mandate and takes no side, opened this session and runs it. You decide in two steps with the other "
             "roles, in a shared channel: everything anyone posts is read by all of them and by the moderator."),
}

STEP1 = """{note}

Step 1: state your position. You will be asked to post once, in plain text: where your role stands on {decision}, which {things} it could
accept and which it could not, with your reasons, and what you would like to learn from the others. Use your tools first.{evidence} What
another role writes is evidence you can use."""
STEP1_EVIDENCE_OPEN = " Include the figures your tools returned, so that the others can use them."
STEP1_EVIDENCE_CONFIDENTIAL = " Follow the handling rules that come with what your tools return."

STEP2_FREE = """Step 2: decide in the discussion. There is no procedure and no turn order: you keep talking with the other roles until you
settle on one {thing} for {decision}.

Each time new messages arrive, you decide what to do with them: answer, ask a question, add something you know, change your mind, say that
you agree, or leave them alone if you have nothing to add. You are not obliged to reply. When you have reached your own final decision, call
done with your option and why: it goes to the moderator, who summarizes the outcome, and you leave the discussion.

The moderator closes the session when the discussion has gone quiet, or at a time or message limit. If you are not done by then, you will be
asked for your final choice."""

STEP2_CONCORD = """Step 2: decide with the procedure. Once you have all posted (or after a short wait), the moderator starts the CONCORD procedure
and you will receive its instructions. Before you score, read what the others posted in step 1. Your first proposal and your scores should
rest on all that is now on the table, yours and theirs, not only on your own. If something another role wrote changes how an option looks,
say so in your rationale and score the option accordingly."""

SHARE_ASK = ("Post your step-1 message now: where your role stands on {decision}, which {things} it could accept and which it could not, with "
             "your reasons, and what you would like to learn from the others.")

OPENING = {
    "free": ("{request}\n\nPlease post once: where your role stands on {decision}, which {things} it could accept and which it could not, with "
             "your reasons, and ask the others for what you are missing. After that, keep talking until you settle on one {thing} together."),
    "concord": ("{request}\n\nPlease post once: where your role stands on {decision}, which {things} it could accept and which it could not, "
                "with your reasons, and ask the others for what you are missing. When you have all posted, I will start the formal procedure "
                "to settle it."),
}

ORCH = """{moderator}

## The request
{request}

## The roles
{roles}

## The candidates
{candidates}

## Your part
{part}
"""

ORCH_PART = {
    "free_star": ("The roles discuss freely and are each connected to you and to no one else: they cannot talk to each other except through "
                  "you. For every message a role sends you, use delegate_to to say which of the other roles must receive it. The message is "
                  "passed on exactly as it was written; you never write or change one. Pass each message to all the roles it concerns, and "
                  "never back to its sender. A role that is done tells you its final choice; you do not pass that on."),
    "free_slim": ("The roles discuss freely in a shared channel: everything anyone posts is read by all of them. You open the session and "
                  "wait. A role that is done tells you its final choice. When the session is over, you summarize the outcome."),
    "summary": ("The session is over. Read the discussion and the final choice of each role, and report the outcome: did the roles agree on one "
                "option? They agreed only if all of them chose the same option. Summarize the outcome in a few sentences."),
    "concord": ("You open a CONCORD run among the roles, once they have shared where they stand. You are not a participant of the run: the "
                "roles answer it, and its outcome is the outcome of the session. You start it with concord_start."),
}


def _guidance(scenario):
    """The library's guidance on how to open a run, for a coordinator that is not a participant (and, for an open format, without a list of options)."""
    params = {**START_PARAMS, "participants": "The ids of the roles, exactly as written in the task. You are not a participant: do not list yourself."}
    if scenario.options.open:
        params.pop("options")
    return "## Starting the run\nTo open the run you give:\n" + "\n".join(f"- {k}: {v}" for k, v in params.items())


def roles_text(scenario):
    return ", ".join(scenario.agents)


def words(scenario):
    """What is chosen, as the prompts name it: options (a closed list), or outcomes (an open format such as a discount)."""
    return dict(things="outcomes" if scenario.options.open else "options", thing="outcome" if scenario.options.open else "option")


def candidates_text(scenario):
    o = scenario.options
    if o.open:
        return o.description
    return "\n".join(f"- {c}" for c in o)


def worker_prompt(scenario, agent, mode, variant):
    """The prompt of a role: the original prompt, its skills, and how the roles decide together."""
    role = scenario.role_of(agent)
    parts = [role["prompt"].strip()]
    for name in role.get("skills") or []:
        parts.append(f"## Skill: {name}\n{scenario.skills[name].strip()}")
    fmt = dict(decision=scenario.decision, roles=roles_text(scenario), **words(scenario))
    evidence = STEP1_EVIDENCE_CONFIDENTIAL if scenario.confidential else STEP1_EVIDENCE_OPEN
    step1 = "## How you decide together\n" + STEP1.format(note=COORD_NOTE["star" if variant == "free_star" else "slim"], evidence=evidence, **fmt)
    if variant == "concord":
        parts += [f"{step1}\n\n{STEP2_CONCORD}", RUN_DESCRIPTION.replace(*CONCORD_INITIATOR)]
    else:
        parts.append(f"{step1}\n\n{STEP2_FREE.format(**fmt)}")
    return "\n\n".join(parts) + "\n"


def orchestrator_prompt(scenario, variant):
    """The prompt of the coordinator (the original moderator)."""
    text = scenario.moderator["prompt"]
    moderator = text.split("Available Skills")[0].strip()       # the lines after it are about the lab's own tools and skills
    part = ORCH_PART[variant].format(decision=scenario.decision)
    if variant == "concord":
        part += "\n\n" + _guidance(scenario)
    return ORCH.format(moderator=moderator, request=scenario.request, roles=roles_text(scenario), candidates=candidates_text(scenario), part=part)


def opening(scenario, mode):
    mode = {"free_coord": "free", "concord_coord": "concord"}.get(mode, mode)
    return OPENING[mode].format(request=scenario.request, decision=scenario.decision, **words(scenario))


def share_ask(scenario):
    return SHARE_ASK.format(decision=scenario.decision, **words(scenario))

