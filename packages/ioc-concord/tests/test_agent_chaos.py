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
RUN2 = "22222222-2222-4222-8222-222222222222"


def uuids(prefix, rng):
    n = itertools.count(1)
    return lambda: f"{prefix}-{rng.getrandbits(16):04x}-4000-8000-{next(n):012x}"


class Chaos:
    """The network, the clock and the models of one run."""

    def __init__(self, agent_mod, seed, n, budget, tau, loss=0.0, dup=0.0, delay=5.0, restart=0.0, crash=None, watcher=False,
                 timeout=TIMEOUT, retries=RETRIES, slow=None, rewrite_in=(), rewrite_out=()):
        self.agent_mod, self.seed, self.rng = agent_mod, seed, random.Random(f"net-{seed}")
        self.slow = slow or {}                       # agent -> seconds it takes to answer a task, on top of the usual
        self.rewrite_in, self.rewrite_out = set(rewrite_in), set(rewrite_out)   # agents that hear, or speak, another version
        self.delivered = {}                          # agent -> the texts that reached it
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
        self.params = {"satisfaction_threshold": tau, "B": budget, "timeout": timeout, "retries": retries}
        self.max_active = 0

    # --- the machinery ---
    def at(self, delay, kind, who, data=None):
        heapq.heappush(self.queue, (self.now + delay, next(self.counter), kind, who, data))

    def alive(self, who):
        return self.crash.get(who, float("inf")) > self.now

    def after(self, who, step):
        """What a step of `who` sends goes on the network; its next tick and its next thought are scheduled."""
        for text in step.send:
            if who in self.rewrite_out:
                text = text.replace('version="0.3.0"', 'version="0.4.0"')
            self.log.append((self.now, who, text))
            for to in self.agents:
                copies = 1 + (self.rng.random() < self.dup)
                for _ in range(copies):
                    if self.rng.random() >= self.loss:
                        heard = text.replace('version="0.3.0"', 'version="0.4.0"') if to in self.rewrite_in else text
                        self.at(self.rng.uniform(0.0, self.delay), "deliver", to, heard)
        self.plan(who)

    def plan(self, who):
        a = self.agents[who]
        if a.due() and who not in self.thinking:
            self.thinking.add(who)
            self.at(self.rng.uniform(0.0, 2.0) + self.slow.get(who, 0.0), "think", who)
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
            rid = t["run_id"]
            if t["kind"] == "init-reply":
                rej, idl = number(seed, who, "rej", lo=0, hi=40), number(seed, who, "idl", lo=60, hi=100)
                self.after(who, a.submit_init_reply(rid, rej, idl, f"plan of {who}", "because", number(seed, who, "seed"), "good"))
            elif t["kind"] == "score":
                self.after(who, a.submit_score(rid, [(c["id"], number(seed, who, c["id"], lo=45 if c["id"][0] == "r" else 0), "my view")
                                                     for c in t["candidates"]]))
            else:
                self.after(who, a.submit_repair(rid, f"fix {t['round']} by {who}", "because",
                                                number(seed, who, "repair", t["round"], lo=60), "better"))

    def run(self, horizon=10 ** 5, initiators=None):
        for who, run_id in initiators or [(self.initiator, RUN)]:
            init = self.agents[who].start(self.names, "pick one", "nothing", "all agree", SPEC, params=self.params, run_id=run_id)
            self.after(who, init)
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
                self.delivered.setdefault(who, []).append(data)
                self.after(who, self.agents[who].receive(data))
            elif kind == "tick":
                self.ticking.discard((who, data))
                self.maybe_restart(who)
                self.after(who, self.agents[who].tick())
            else:
                self.thinking.discard(who)
                self.maybe_restart(who)
                self.answer(who)
            active = sum(1 for r in self.agents[who].state["runs"].values() if r["status"] == "active")
            self.max_active = max(self.max_active, active)
            assert active <= 1, f"{who} is in {active} runs at once"
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
        if s is None:                                   # the init never reached it (and nothing else can start a run)
            assert not any(W.peek(t)["tag"] == "concord-init" for t in c.delivered.get(who, [])
                           if W.peek(t)["attrs"].get("version") == "0.3.0"), who
            continue
        assert s["status"] in ("ended", "aborted"), (who, s["status"])
        assert s["timers"] == {} and a.next_wakeup() is None and a.due() == []
        if s["status"] == "ended":
            assert (rep["outcome"], rep["winner"], rep["rounds"], rep["scores"], rep["shortfall"]) == (
                rep0["outcome"], rep0["winner"], rep0["rounds"], rep0["scores"], rep0["shortfall"]), who
            assert rep["mismatches"]["sent"] == 0 and rep["mismatches"]["received"] == [], who
    assert counts(c, "cancel").count(c.names[0]) <= 1
    for who in c.agents:
        assert counts(c, "cancel").count(who) <= 1 and counts(c, "result").count(who) <= 1
    assert counts(c, "result").count(c.names[0]) <= 1 and set(counts(c, "result")) <= {c.names[0]}
    n, budget = len(c.names), c.params["B"]
    assert len(c.log) <= 40 * n * n * (budget + 4) * (c.params["retries"] + 2)     # no flood
    check_log(c)


def items_of(text):
    """The items a data message is the message for: (author, kind, candidate or None) for each."""
    info = W.peek(text)
    kind, who = W.KIND_OF_TAG[info["tag"]], info["attrs"]["from"]
    if kind == "score":
        return [(who, kind, e["id"]) for e in W.parse_message(text)["scores"]]
    if kind == "repair":
        return [(who, kind, W.parse_message(text)["proposal"]["id"])]
    return [(who, kind, None)]


def check_log(c):
    """What holds for every message that was sent: each transmission has its own uuid, an author writes one message for an item,
    and a resend carries a message that its author sent before, byte for byte."""
    sent_data, by_item, uuids_seen = {}, {}, set()
    for _, who, text in c.log:
        info = W.peek(text)
        uid = info["attrs"]["uuid"]
        assert uid not in uuids_seen, "a uuid was used for two transmissions"
        uuids_seen.add(uid)
        if info["tag"] in W.DATA_TAGS:
            sent_data.setdefault(who, set()).add(text)
            for item in items_of(text):
                assert by_item.setdefault(item, text) == text, f"two messages for {item}"
    for _, who, text in c.log:
        info = W.peek(text)
        if info["tag"] == W.RESEND:
            inner = W.parse_message(text)["inner"]
            assert inner in sent_data.get(who, set()), "a resend carries a message that was never sent"


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
            # an agent whose run ended with an outcome is disputed exactly when a cancel of another agent reached it afterwards
            cancelled = any(W.peek(t)["tag"] == "concord-cancel" and W.peek(t)["attrs"]["from"] != who for t in c.delivered.get(who, []))
            assert (rep["status"] == "disputed") == cancelled, (who, rep["status"])


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


def reference_report(agent, seed):
    n, budget, tau = setup(seed)
    base = Chaos(agent, seed, n, budget, tau, delay=0.0).run()
    return base.agents[base.names[0]].report()


@pytest.mark.parametrize("seed", range(24))
def test_whatever_the_parameters_every_run_ends_and_those_that_end_agree(agent, seed):
    rng = random.Random(seed)
    n, budget = rng.choice([2, 3, 4, 5]), rng.choice([1, 2, 5])
    tau, retries, timeout = rng.choice(["0.5", "0.8"]), rng.choice([0, 1, 3]), rng.choice([3, 30])
    base = Chaos(agent, seed, n, budget, tau, delay=0.0, timeout=timeout, retries=retries).run()
    rep0 = base.agents[base.names[0]].report()
    c = Chaos(agent, seed, n, budget, tau, loss=0.1, dup=0.2, delay=4.0, restart=0.1, timeout=timeout, retries=retries).run()
    check_common(c, rep0)


# --- another version ---------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(10))
def test_an_agent_that_speaks_another_version_makes_the_initiator_end_the_run_and_nobody_hangs(agent, seed):
    n, budget, tau = 3 + seed % 2, 2, "0.6"
    legal = NAMES[n - 1]
    c = Chaos(agent, seed, n, budget, tau, dup=0.2, delay=4.0, rewrite_in=[legal]).run()
    assert c.agents[legal].status() is None                                     # it refused the init and never joined
    for who in c.names[:-1]:
        s, rep = c.agents[who].status(), c.agents[who].report()
        assert (s["status"], rep["reason"]["reason"], s["timers"]) == ("aborted", "version", {}), who
    refusals = [t for _, w, t in c.log if w == legal and W.peek(t)["tag"] == "concord-error"]
    assert refusals and all(W.parse_message(t)["reason"] == "version" for t in refusals)


@pytest.mark.parametrize("seed", range(10))
def test_an_agent_whose_messages_are_of_another_version_goes_unheard_and_the_timers_end_the_run(agent, seed):
    n, budget, tau = 3, 2, "0.6"
    c = Chaos(agent, seed, n, budget, tau, dup=0.2, delay=4.0, rewrite_out=["legal"]).run()
    for who in ("cost", "security"):
        s, rep = c.agents[who].status(), c.agents[who].report()
        assert (s["status"], rep["reason"]["reason"], rep["reason"]["agent"], s["timers"]) == ("aborted", "missing-agent", "legal", {}), who
    assert c.agents["legal"].status()["status"] == "aborted" and c.agents["legal"].status()["timers"] == {}


# --- an agent that is alive but slow --------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_an_agent_that_answers_before_the_timeout_costs_the_run_nothing(agent, seed):
    n, budget, tau = setup(seed)
    rep0 = reference_report(agent, seed)
    c = Chaos(agent, seed, n, budget, tau, delay=2.0, slow={c: 20.0 for c in NAMES[:n]}).run()
    check_common(c, rep0)
    assert {c.agents[w].report()["status"] for w in c.names} == {"confirmed"}


@pytest.mark.parametrize("seed", range(8))
def test_an_agent_that_is_slower_than_all_the_requests_of_the_others_ends_the_run_for_everybody(agent, seed):
    n, budget, tau = setup(seed)
    rep0 = reference_report(agent, seed)
    slow = NAMES[n - 1]
    c = Chaos(agent, seed, n, budget, tau, delay=2.0, slow={slow: (RETRIES + 2) * TIMEOUT}).run()
    check_common(c, rep0)
    for who in c.names:
        rep = c.agents[who].report()
        assert rep["status"] == "aborted" and rep["reason"]["agent"] == slow, who


# --- two runs at once ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(30))
def test_two_runs_that_start_together_leave_every_agent_in_one_run_at_a_time_and_end(agent, seed):
    n, budget, tau = 2 + seed % 3, 1 + seed % 3, "0.6"
    loss = [0.0, 0.1, 0.3][seed % 3]
    c = Chaos(agent, seed, n, budget, tau, loss=loss, dup=0.2, delay=5.0, restart=0.1)
    c.run(initiators=[(c.names[0], "11111111-1111-4111-8111-111111111111"), (c.names[-1], RUN2)])
    for who in c.names:
        a = c.agents[who]
        assert a.state["active"] is None or a.state["runs"][a.state["active"]]["status"] != "active", who
        for rid, run in a.state["runs"].items():
            assert run["status"] != "active" and run["timers"] == {}, (who, rid)
    for rid in ("11111111-1111-4111-8111-111111111111", RUN2):
        ended = {(r["outcome"], r["winner"]["id"]) for w in c.names if (r := c.agents[w].report(rid)) and r["status"] in ("confirmed", "disputed")}
        assert len(ended) <= 1, (rid, ended)                                      # the agents that end a run agree on it
    if loss == 0.0:
        done = [rid for rid in ("11111111-1111-4111-8111-111111111111", RUN2)
                if all((c.agents[w].report(rid) or {}).get("status") == "confirmed" for w in c.names)]
        assert done == ["11111111-1111-4111-8111-111111111111"] or done == []   # only the run with the lower id can finish
