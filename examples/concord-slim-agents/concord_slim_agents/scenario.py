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

from concord_slim_agents.guidance import INITIATOR_GUIDANCE, RUN_DESCRIPTION

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
