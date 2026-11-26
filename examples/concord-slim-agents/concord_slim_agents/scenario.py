#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The hidden-profile scenario: what each participant knows, and the prompt that carries it.

A fixture holds the scenario text, the facts every participant shares, the facts only one of them holds, and the options. Each
participant gets the shared facts and its own, shuffled together, so the order says nothing about which fact is private.
"""
import random
from pathlib import Path

import yaml

from ioc_concord.guidance import INITIATOR_GUIDANCE, RUN_DESCRIPTION

# The facts of an agent are shown in one fixed, mixed order (the same in every run), so that the runs differ only by the model.
FACT_ORDER_SEED = 0

AGENTS = ("participant-a", "participant-b", "participant-c")
INITIATOR = "participant-a"


class Scenario:
    def __init__(self, path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.id = raw["id"]
        self.text = raw["scenario"].strip()
        self.shared = list(raw["shared_information"])
        self.options = list(raw["possible_answers"])
        self.correct = raw["correct_answer"]
        self.hidden = {a: list(d["hidden_information"]) for a, d in raw["agents"].items()}

    def facts_for(self, agent):
        facts = self.shared + self.hidden[agent]
        random.Random(f"{FACT_ORDER_SEED}/{agent}").shuffle(facts)
        return facts


_PROMPT = """You are {me}, one of three community leaders (participant-a, participant-b, participant-c) taking part in a group decision.

## The scenario
{scenario}

## What you know
Only you know some of these facts, and the others know things you do not. The order is random and means nothing.
{facts}

## The options
{options}

## Your role
You and the other leaders want the same thing: the right place to evacuate to. Each of you knows only part of the picture, so what
you know is part of the evidence, not all of it. Share everything you know, all facts and information, openly with the group:
the tools you are given are how. Do not invent facts: use what you hold, and what another leader wrote.

{run}
{extra}"""

_INITIATOR_EXTRA = """
## Your part
In order to agree on the direction to take for the evacuation, use CONCORD with participant-b and participant-c. You start the run
with concord_start, and then you take part in it like the others.

""" + INITIATOR_GUIDANCE + "\n"


def system_prompt(scenario, agent):
    facts = "\n".join(f"- {f}" for f in scenario.facts_for(agent))
    options = "\n".join(f"- {o}" for o in scenario.options)
    extra = _INITIATOR_EXTRA if agent == INITIATOR else ""
    return _PROMPT.format(me=agent, scenario=scenario.text, facts=facts, options=options, run=RUN_DESCRIPTION, extra=extra)


# --- the same scenario, for an agent that uses the skills --------------------------------------------------------------------------

_SKILLS_ROLE = """
## How you work
Your agent id is {me}. Your working directory is {work}: the shell starts there, and it is where your files go. You work with a shell (bash). The messages of the other leaders reach you as new messages after your turn ends: you cannot fetch them. When you are waiting,
end your turn: a message, or a background command that finishes, brings you back. You reach the other leaders with send_message. You have these skills: a skill is a folder with a SKILL.md that says when to use it and how.
{skills}"""

_SKILL_LAZY = "Read the SKILL.md of a skill, with the shell, when what you are doing is what its description says, and follow it."
_SKILL_PRELOAD = "Their SKILL.md files are given below: follow them."


def skill_info(skill_dir):
    """(name, description, the text of SKILL.md) of a skill folder."""
    text = (Path(skill_dir) / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---", 2)[1])
    return meta["name"], " ".join(meta["description"].split()), text


def skills_of(agent, skills_root):
    """The skill folders an agent has: the one that opens a run and the one that takes part in it for the initiator, the second for
    the others."""
    names = ("concord-initiator", "concord-receiver") if agent == INITIATOR else ("concord-receiver",)
    return [Path(skills_root) / n for n in names]


def skills_system_prompt(scenario, agent, skills_root, work, preload=False):
    """The system prompt of an agent that has the skills: the scenario and its facts, how it works, and the skills (their
    descriptions and where they are; with `preload`, their SKILL.md as well). Nothing about CONCORD beyond them."""
    facts = "\n".join(f"- {f}" for f in scenario.facts_for(agent))
    options = "\n".join(f"- {o}" for o in scenario.options)
    extra = _SKILLS_EXTRA if agent == INITIATOR else ""
    base = _PROMPT.format(me=agent, scenario=scenario.text, facts=facts, options=options, run="", extra=extra)
    base = base.replace(":\nthe tools you are given are how.", ".")            # the lib mode's tools are how it shares; here the skills say
    listing, bodies = [], []
    for folder in skills_of(agent, skills_root):
        name, description, text = skill_info(folder)
        listing.append(f"- {name}: {description}\n  folder: {folder.resolve()}")
        bodies.append(f"### {name} (folder {folder.resolve()})\n{text}")
    role = _SKILLS_ROLE.format(me=agent, work=Path(work).resolve(), skills="\n".join(listing) + "\n\n" + (_SKILL_PRELOAD if preload else _SKILL_LAZY))
    return base + role + ("\n" + "\n\n".join(bodies) + "\n" if preload else "")


_SKILLS_EXTRA = """
## Your part
To agree on where to evacuate, use CONCORD with participant-b and participant-c. You take part in the decision yourself.
"""
