#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The scenarios: what each participant knows (the prompt that carries it is in prompts.py, or prompts_mas.py for `kind: mas`).

Two kinds of file in `scenarios/`, one folder for each use case (`hidden-bench/`, `e-commerce/`, `renewal/`):
  - the hidden-profile task (evacuation): the scenario text, the facts every participant shares, the facts only one of them holds, and the
    options. Each participant gets the shared facts and its own, shuffled together, so the order says nothing about which fact is private.
  - `kind: mas`, made from an app of the mas-lab library: a roster of roles with the prompt of each role and the data
    tools it may call, a moderator, the request that starts the work, and the candidates (a closed list, or an open format such as a
    discount percentage). What a role knows is what its tools return.
"""
import random
import re
from pathlib import Path

import yaml

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"       # one sub-folder for each use case
DEFAULT_SCENARIO = SCENARIOS / "hidden-bench" / "evacuation-west-city.yaml"


def scenario_files(use_case=None):
    """The task files, sorted: of one use case (a sub-folder of `scenarios/`), or of all of them."""
    return sorted((SCENARIOS / use_case if use_case else SCENARIOS).rglob("*.yaml"))


# The facts of an agent are shown in one fixed, mixed order (the same in every run), so that the runs differ only by the model.
FACT_ORDER_SEED = 0

DEFAULT_AGENTS = ("agent-a", "agent-b", "agent-c", "agent-d")     # the leaders of the evacuation task, all in the discussion


class Roster(list):
    """The names of the agents of this run. One object, shared by every module that imports it: `set_roster` changes it in place (a process
    runs one scenario). Code that has the scenario in hand uses `scenario.agents` instead."""

    def __add__(self, other):
        return tuple(self) + tuple(other)

    def __radd__(self, other):
        return tuple(other) + tuple(self)


AGENTS = Roster(DEFAULT_AGENTS)


def set_roster(names):
    AGENTS[:] = list(names)


class Choices(list):
    """What an agent may choose: a closed list (the list itself), or an open format (an empty list, `open` true, with a `pattern`)."""

    def __init__(self, values=(), pattern=None, description="", example=None):
        super().__init__(values)
        self.open = pattern is not None
        self.pattern, self.description, self.example = pattern, description, example


class Scenario:
    def __init__(self, path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.id = raw["id"]
        self.kind = raw.get("kind", "evacuation")
        self.path = str(path)
        if self.kind == "mas":
            self._load_mas(raw)
        else:
            self.text = raw["scenario"].strip()
            self.shared = list(raw["shared_information"])
            self.options = Choices(raw["possible_answers"])
            self.correct = raw["correct_answer"]
            self.decoys = []
            self.init = dict(raw["init"])             # what the initiator opens a CONCORD run with: context, x_reject, x_ideal
            self.hidden = {a: list(d["hidden_information"]) for a, d in raw["agents"].items()}
            self.agents = DEFAULT_AGENTS
            self.confidential = False
            self.roster, self.data, self.skills, self.moderator, self.request, self.decision = [], {}, {}, {}, "", ""

    def _load_mas(self, raw):
        self.title = raw.get("title", self.id)
        self.request = raw["request"].strip()
        self.text = self.request
        self.decision = raw["decision"]
        self.confidential = bool(raw.get("confidential"))
        self.roster = list(raw["roster"])
        self.agents = tuple(r["id"] for r in self.roster)
        self.moderator = dict(raw.get("moderator") or {})
        self.skills = dict(raw.get("skills") or {})
        self.data = raw.get("data") or {}
        self.init = dict(raw.get("init") or {})
        self.shared, self.hidden = [], {}
        c = raw["candidates"]
        self.options = (Choices(pattern=c["pattern"], description=c.get("description", ""), example=c.get("example"))
                        if c["kind"] == "open" else Choices(c["values"], description=c.get("description", "")))
        truth = raw["ground_truth"]
        self.ground_truth = truth
        self.correct = truth["correct"]
        self.decoys = list(truth.get("decoy") or [])
        self.forbidden = list(truth.get("forbidden") or [])
        assert self.valid_choice(self.correct), f"{self.id}: the correct answer {self.correct!r} is not a candidate"
        assert all(self.valid_choice(d) for d in self.decoys), f"{self.id}: a decoy is not a candidate"

    def role_of(self, agent):
        return next((r for r in self.roster if r["id"] == agent), None)

    def normalize(self, text):
        """The canonical form of a choice, or None when it is not one. A closed list: the option (any case). An open format: the text, and a
        percentage written another way (`15 %`, `discount-15`, `15 percent`) as `15%` when the example is a percentage."""
        if text is None:
            return None
        text = str(text).strip()
        if not self.options.open:
            return next((o for o in self.options if o.lower() == text.lower()), None)
        found = (self.options.example or "").endswith("%") and re.fullmatch(
            r"(?:discount[-_ ]?)?(\d{1,3})(?:\s*%|\s*percent)?", text, re.I)
        text = f"{found.group(1)}%" if found else text
        return text if re.fullmatch(self.options.pattern, text) else None

    def valid_choice(self, text):
        return self.normalize(text) is not None

    def facts_for(self, agent):
        facts = self.shared + self.hidden[agent]
        random.Random(f"{FACT_ORDER_SEED}/{agent}").shuffle(facts)
        return facts
