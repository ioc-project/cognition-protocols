#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The hidden-profile scenario: what each participant knows (the prompt that carries it is in prompts.py).

A fixture holds the scenario text, the facts every participant shares, the facts only one of them holds, and the options. Each
participant gets the shared facts and its own, shuffled together, so the order says nothing about which fact is private.
"""
import random
from pathlib import Path

import yaml

# The facts of an agent are shown in one fixed, mixed order (the same in every run), so that the runs differ only by the model.
FACT_ORDER_SEED = 0

AGENTS = ("participant-a", "participant-b", "participant-c")
FACILITATOR = "facilitator"                 # starts the run (or the session) and takes no part in the discussion


class Scenario:
    def __init__(self, path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.id = raw["id"]
        self.text = raw["scenario"].strip()
        self.shared = list(raw["shared_information"])
        self.options = list(raw["possible_answers"])
        self.correct = raw["correct_answer"]
        self.init = dict(raw["init"])             # what the facilitator opens a CONCORD run with: context, x_reject, x_ideal
        self.hidden = {a: list(d["hidden_information"]) for a, d in raw["agents"].items()}

    def facts_for(self, agent):
        facts = self.shared + self.hidden[agent]
        random.Random(f"{FACT_ORDER_SEED}/{agent}").shuffle(facts)
        return facts
