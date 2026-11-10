#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Whole runs on a bad network, in virtual time: messages are delayed (so they arrive in another order), duplicated and lost,
agents are restarted from their saved state, and some stop for good.

What must hold: agents that end with an outcome agree with the run on a perfect network; when messages are only delayed or
duplicated every run still ends well; and whatever happens, every run ends, with no timer left and no flood of messages."""
import heapq
import itertools
import json
import random

import pytest
from test_agent_sim import NAMES, SPEC, number

from ioc_concord import wire as W

RUN = "11111111-1111-4111-8111-111111111111"
TIMEOUT, RETRIES = 30, 3


def uuids(prefix, rng):
    n = itertools.count(1)
    return lambda: f"{prefix}-{rng.getrandbits(16):04x}-4000-8000-{next(n):012x}"


class Chaos:
    """The network, the clock and the models of one run."""

    def __init__(self, agent_mod, seed, n, budget, tau, loss=0.0, dup=0.0, delay=5.0, restart=0.0, crash=None, watcher=False):
        self.agent_mod, self.seed, self.rng = agent_mod, seed, random.Random(f"net-{seed}")
        self.loss, self.dup, self.delay, self.restart_p = loss, dup, delay, restart
        self.now, self.queue, self.counter = 0.0, [], itertools.count()
        self.names = NAMES[:n]
        self.crash = crash or {}                     # agent -> the time it stops for good
        self.stores, self.agents, self.log = {}, {}, []
        self.thinking, self.ticking = set(), set()
        for i, a in enumerate(self.names + (["alice"] if watcher else []), 1):
            self.stores[a] = MemStore(agent_mod)
            self.agents[a] = agent_mod.Agent(a, store=self.stores[a], clock=lambda: self.now, uuid_fn=uuids(f"{i:08x}", self.rng))
        self.initiator = "alice" if watcher else self.names[0]
        self.params = {"satisfaction_threshold": tau, "B": budget, "timeout": TIMEOUT, "retries": RETRIES}

    # --- the machinery ---
    def at(self, delay, kind, who, data=None):
        heapq.heappush(self.queue, (self.now + delay, next(self.counter), kind, who, data))

    def alive(self, who):
        return self.crash.get(who, float("inf")) > self.now

    def after(self, who, step):
        """What a step of `who` sends goes on the network; its next tick and its next thought are scheduled."""
        for text in step.send:
            self.log.append((self.now, who, text))
            for to in self.agents:
                copies = 1 + (self.rng.random() < self.dup)
                for _ in range(copies):
                    if self.rng.random() >= self.loss:
                        self.at(self.rng.uniform(0.0, self.delay), "deliver", to, text)
        self.plan(who)

    def plan(self, who):
        a = self.agents[who]
        if a.due() and who not in self.thinking:
            self.thinking.add(who)
            self.at(self.rng.uniform(0.0, 2.0), "think", who)
        wake = a.next_wakeup()
        if wake is not None and (who, wake) not in self.ticking:
            self.ticking.add((who, wake))
            heapq.heappush(self.queue, (max(wake, self.now), next(self.counter), "tick", who, wake))

    def maybe_restart(self, who):
        if self.rng.random() < self.restart_p:
            self.agents[who] = self.agent_mod.Agent(who, store=self.stores[who], clock=lambda: self.now,
                                                    uuid_fn=uuids(f"{self.rng.getrandbits(32):08x}", self.rng))

    def answer(self, who):
        a, seed = self.agents[who], self.seed
        for t in a.due():
            if t["kind"] == "init-reply":
                rej, idl = number(seed, who, "rej", lo=0, hi=40), number(seed, who, "idl", lo=60, hi=100)
                self.after(who, a.submit_init_reply(RUN, rej, idl, f"plan of {who}", "because", number(seed, who, "seed"), "good"))
            elif t["kind"] == "score":
                self.after(who, a.submit_score(RUN, [(c["id"], number(seed, who, c["id"], lo=45 if c["id"][0] == "r" else 0), "my view")
                                                     for c in t["candidates"]]))
            else:
                self.after(who, a.submit_repair(RUN, f"fix {t['round']} by {who}", "because",
                                                number(seed, who, "repair", t["round"], lo=60), "better"))

    def run(self, horizon=10 ** 5):
        init = self.agents[self.initiator].start(self.names, "pick one", "nothing", "all agree", SPEC, params=self.params, run_id=RUN)
        self.after(self.initiator, init)
        steps = 0
        while self.queue:
            when, _, kind, who, data = heapq.heappop(self.queue)
            self.now = when
            assert self.now < horizon, "the run does not end"
            steps += 1
            assert steps < 200000
            if not self.alive(who):
                continue
            if kind == "deliver":
                self.maybe_restart(who)
                self.after(who, self.agents[who].receive(data))
            elif kind == "tick":
                self.ticking.discard((who, data))
                self.maybe_restart(who)
                self.after(who, self.agents[who].tick())
            else:
                self.thinking.discard(who)
                self.maybe_restart(who)
                self.answer(who)
            if steps % 50 == 0:
                json.dumps(self.agents[who].state)
        return self


class MemStore:
    """What a file would keep: the JSON of the state. It raises the StateError of the implementation under test."""

    def __init__(self, agent_mod):
        self.agent_mod, self.text = agent_mod, None

    def load(self):
        if self.text is None:
            raise self.agent_mod.StateError("none")
        return json.loads(self.text)

    def save(self, state):
        self.text = json.dumps(state)


def setup(seed):
    return 2 + seed % 3, 1 + seed % 4, ["0.5", "0.6", "0.8"][seed % 3]


def counts(c, tag):
    return [who for _, who, t in c.log if W.peek(t)["tag"] == f"concord-{tag}"]


def check_common(c, rep0):
    """What holds on any network."""
    for who in c.names:
        a = c.agents[who]
        if not c.alive(who):
            continue
        s, rep = a.status(), a.report()
        assert s["status"] in ("ended", "aborted"), (who, s["status"])
        assert s["timers"] == {} and a.next_wakeup() is None and a.due() == []
        if s["status"] == "ended":
            assert (rep["outcome"], rep["winner"]) == (rep0["outcome"], rep0["winner"]), who
            assert rep["mismatches"]["sent"] == 0 and rep["mismatches"]["received"] == [], who
    assert counts(c, "cancel").count(c.names[0]) <= 1
    for who in c.agents:
        assert counts(c, "cancel").count(who) <= 1 and counts(c, "result").count(who) <= 1
    assert counts(c, "result").count(c.names[0]) <= 1 and set(counts(c, "result")) <= {c.names[0]}
    n, budget = len(c.names), c.params["B"]
    assert len(c.log) <= 40 * n * n * (budget + 4) * (RETRIES + 2)                 # no flood


@pytest.mark.parametrize("seed", range(30))
def test_messages_that_arrive_late_in_another_order_or_twice_change_nothing(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0).run()
    rep0 = base.agents[base.names[0]].report()
    assert rep0["status"] == "confirmed"
    c = Chaos(agent, seed, n, budget, tau, dup=0.3, delay=6.0).run()
    check_common(c, rep0)
    for who in c.names:
        rep = c.agents[who].report()
        assert (rep["status"], rep["outcome"], rep["winner"], rep["rounds"]) == ("confirmed", rep0["outcome"], rep0["winner"], rep0["rounds"]), who
    assert counts(c, "cancel") == []


@pytest.mark.parametrize("seed", range(30))
def test_agents_that_restart_from_their_saved_state_at_any_moment_lose_nothing(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0).run()
    rep0 = base.agents[base.names[0]].report()
    c = Chaos(agent, seed, n, budget, tau, dup=0.2, delay=6.0, restart=0.3).run()
    check_common(c, rep0)
    assert {c.agents[w].report()["status"] for w in c.names} == {"confirmed"}


@pytest.mark.parametrize("seed", range(30))
def test_when_messages_are_lost_every_run_still_ends_and_those_that_end_agree(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0).run()
    rep0 = base.agents[base.names[0]].report()
    c = Chaos(agent, seed, n, budget, tau, loss=[0.1, 0.25, 0.4][seed % 3], dup=0.1, delay=6.0, restart=0.1).run()
    check_common(c, rep0)
    for who in c.names:
        rep = c.agents[who].report()
        if rep["status"] == "aborted":
            assert rep["outcome"] == "ABORTED" and rep["reason"]["reason"] == "missing-agent" and rep["reason"]["agent"] in c.names
        else:
            assert rep["status"] in ("confirmed", "disputed")


@pytest.mark.parametrize("seed", range(20))
def test_an_agent_that_stops_for_good_ends_the_run_for_the_others_unless_it_was_over(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0).run()
    rep0 = base.agents[base.names[0]].report()
    victim = base.names[seed % n]
    when = random.Random(seed).uniform(0.5, 20.0)
    c = Chaos(agent, seed, n, budget, tau, delay=3.0, crash={victim: when}).run()
    check_common(c, rep0)
    others = [w for w in c.names if w != victim]
    states = {w: c.agents[w].status()["status"] for w in others}
    victim_done = c.agents[victim].status()["status"] == "ended" if c.agents[victim].status() else False
    if victim_done:
        return                                    # it had finished before it stopped: the others may finish too
    for w in others:
        rep = c.agents[w].report()
        if states[w] == "aborted":
            assert rep["reason"]["agent"] in c.names
    assert any(s == "aborted" for s in states.values()) or all(s == "ended" for s in states.values())


@pytest.mark.parametrize("seed", range(12))
def test_an_initiator_that_only_watches_ends_with_the_run_on_a_bad_network(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0, watcher=True).run()
    rep0 = base.agents[base.names[0]].report()
    assert base.agents["alice"].report()["status"] == "unverified"
    c = Chaos(agent, seed, n, budget, tau, dup=0.3, delay=6.0, restart=0.2, watcher=True).run()
    rep = c.agents["alice"].report()
    assert (rep["status"], rep["outcome"], rep["winner"]["id"]) == ("unverified", rep0["outcome"], rep0["winner"]["id"])
    assert c.agents["alice"].status()["timers"] == {}
