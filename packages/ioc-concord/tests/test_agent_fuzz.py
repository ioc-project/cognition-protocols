#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Garbage in: messages of real runs, damaged in every way we can think of, handed to agents in realistic states. Nothing may
raise, a message that is ignored must change nothing, and the state must stay JSON."""
import json
import random
import re

import pytest
from test_agent_chaos import Chaos, setup

from ioc_concord import wire as W

ATTR_VALUES = ["", "x", "0.3.0", "0.4.0", "a b", "9" * 70, "<", "&", "seed-x", "r0-cost", "r99999999999999999999-cost", "cost", "alice",
               "00000000-0000-4000-8000-000000000000", "UPPER-CASE"]


def corpus(agent):
    """Every message of a few runs on a bad network, once each."""
    texts = []
    for seed in range(6):
        n, budget, tau = setup(seed)
        c = Chaos(agent, seed, n, budget, tau, dup=0.2, delay=4.0, loss=0.1).run()
        texts += [t for _, _, t in c.log]
    return list(dict.fromkeys(texts))


def mutate(text, rng):
    kind = rng.randrange(11)
    if kind == 0:
        return text[:rng.randrange(len(text))]
    if kind == 1:
        i = rng.randrange(len(text))
        return text[:i] + chr(rng.randrange(32, 0x2fff)) + text[i + 1:]
    if kind == 2:
        attrs = re.findall(r'(\w+)="([^"]*)"', text[:400])
        if not attrs:
            return text
        name, value = rng.choice(attrs)
        return text.replace(f'{name}="{value}"', f'{name}="{rng.choice(ATTR_VALUES)}"', 1)
    if kind == 3:
        return text.replace("<raw>", "<raw>-", 1) if "<raw>" in text else text * 2
    if kind == 4:
        info = W.peek(text)
        try:
            return W.build_resend(info["attrs"]["from"], info["attrs"]["run_id"], text)
        except Exception:
            return text
    if kind == 5:
        return "<concord-resend " + text[text.find(" "):]
    if kind == 6:
        return text.replace("</", "<", 1)
    if kind == 7:
        return text + text
    if kind == 8:
        return text.replace("seed-", "r999999999999999999999-", 1)
    if kind == 9:
        return text.replace(">", ">" + "x" * 5000, 1)
    return re.sub(r"<(\w[\w-]*)", lambda m: "<" + m.group(1) + " extra=\"1\"", text, count=1)


def shape(state):
    """The state as JSON, without the list of accepted uuids."""
    st = json.loads(json.dumps(state))
    for run in st["runs"].values():
        run.pop("uuids")
    return json.dumps(st, sort_keys=True)


@pytest.mark.parametrize("trial", range(40))
def test_no_message_raises_and_a_message_that_is_ignored_changes_nothing(agent, trial):
    rng = random.Random(trial)
    texts = corpus(agent) if trial == 0 or not hasattr(test_no_message_raises_and_a_message_that_is_ignored_changes_nothing, "_c") else None
    if texts is not None:
        test_no_message_raises_and_a_message_that_is_ignored_changes_nothing._c = texts
    texts = test_no_message_raises_and_a_message_that_is_ignored_changes_nothing._c
    seed = rng.randrange(6)
    n, budget, tau = setup(seed)
    c = Chaos(agent, seed, n, budget, tau, delay=0.0)
    a = c.agents[c.names[rng.randrange(n)]]
    for t in texts[:rng.randrange(len(texts))]:                      # a realistic state: some of what a real run says
        a.receive(t, now=0.0)
    for _ in range(40):
        t = mutate(rng.choice(texts), rng)
        before, uuids_before = shape(a.state), json.dumps([r["uuids"] for r in a.state["runs"].values()])
        step = a.receive(t, now=1.0)
        after = shape(a.state)                                       # also proves the state is JSON
        if not step.send and step.events and all(e["event"] in ("ignored", "cannot answer") for e in step.events):
            assert after == before, (t[:200], step.events)
            if not any(e.get("reason") == "already recorded" for e in step.events):     # only then is a uuid kept
                assert json.dumps([r["uuids"] for r in a.state["runs"].values()]) == uuids_before, (t[:200], step.events)
        a.tick(2.0)
        json.dumps(a.state)
