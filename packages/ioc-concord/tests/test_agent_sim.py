#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Whole runs of 2 to 5 agents on a channel that delivers every message to every agent in one order, with no loss.

The model of each agent is a function of the seed, the agent and the candidate, so a run is the same whatever the order in
which the agents answer their tasks. Loss, reordering and silence come with the liveness tests."""
import hashlib
import itertools
import random

import pytest

from ioc_concord import wire as W

RUN = "11111111-1111-4111-8111-111111111111"
NAMES = ["cost", "security", "legal", "ops", "ux"]
SPEC = {"type": "string", "description": "a plan"}


def number(seed, *parts, lo=0, hi=100):
    d = hashlib.sha256(repr((seed, *parts)).encode()).digest()
    return round(lo + (hi - lo) * int.from_bytes(d[:4], "big") / 2 ** 32, 1)


def uuids(prefix):
    n = itertools.count(1)
    return lambda: f"{prefix}-0000-4000-8000-{next(n):012x}"


def play(agent_mod, seed, order_seed):
    """Run the negotiation of `seed` to its end. Returns (agents, the messages in the order they were sent)."""
    n, budget = 2 + seed % 4, 1 + seed % 4
    tau = ["0.5", "0.6", "0.8", "0.95"][seed % 4]
    names = NAMES[:n]
    agents = {a: agent_mod.Agent(a, uuid_fn=uuids(f"{i:08x}")) for i, a in enumerate(names, 1)}
    log, order = [], random.Random(order_seed)

    def broadcast(step):
        queue = list(step.send)
        while queue:
            text = queue.pop(0)
            log.append(text)
            for a in agents.values():
                queue += a.receive(text).send

    init = W.build_init("alice", names, "pick one", "nothing", "all agree", SPEC, run_id=RUN,
                        params={"satisfaction_threshold": tau, "B": budget}, uid=uuids("a0a0a0a0")())
    broadcast(type("S", (), {"send": [init]})())
    for _ in range(1000):
        todo = [(a, t) for a, ag in agents.items() for t in ag.due()]
        if not todo:
            break
        a, t = order.choice(todo)
        ag = agents[a]
        if t["kind"] == "init-reply":
            rej, idl = number(seed, a, "rej", lo=0, hi=40), number(seed, a, "idl", lo=60, hi=100)
            step = ag.submit_init_reply(RUN, rej, idl, f"plan of {a}", "because", number(seed, a, "seed"), "good")
        elif t["kind"] == "score":
            step = ag.submit_score(RUN, [(c["id"], number(seed, a, c["id"], lo=45 if c["id"][0] == "r" else 0), "my view")
                                     for c in t["candidates"]])
        else:
            step = ag.submit_repair(RUN, f"fix {t['round']} by {a}", "because", number(seed, a, "repair", t["round"], lo=60), "better")
        broadcast(step)
    else:
        raise AssertionError("the run does not end")
    return agents, log, (n, budget, tau)


@pytest.mark.parametrize("seed", range(40))
def test_every_agent_ends_with_the_same_confirmed_outcome(agent, seed):
    agents, log, (n, budget, tau) = play(agent, seed, order_seed=seed)
    reports = {a: ag.report() for a, ag in agents.items()}
    first = reports["cost"]
    for a, rep in reports.items():
        assert rep["status"] == "confirmed", a
        assert (rep["outcome"], rep["winner"], rep["rounds"], rep["scores"]) == (first["outcome"], first["winner"], first["rounds"],
                                                                                 first["scores"]), a
        assert rep["results_compared"]["matches"] is True and rep["mismatches"] == {"sent": 0, "received": []}
        assert agents[a].status()["status"] == "ended" and agents[a].due() == []
    assert first["outcome"] in ("SUCCESS", "BEST_EFFORT") and first["rounds"] <= budget
    tau_m = int(round(float(tau) * 1_000_000))
    assert (min(first["scores"].values()) >= tau_m) == (first["outcome"] == "SUCCESS")
    assert (first["outcome"] == "SUCCESS") == (first["shortfall"] == {})
    if first["outcome"] == "BEST_EFFORT":
        assert first["rounds"] == budget
    kinds = [W.parse_message(t)["kind"] for t in log]
    assert kinds.count("result") == 1 and kinds.count("init") == 1 and kinds.count("init-reply") == n
    assert kinds.count("repair") == first["rounds"]
    results = [W.parse_message(t) for t in log if W.peek(t)["tag"] == "concord-result"]
    assert results[0]["from"] == "cost" and results[0]["winning_candidate_id"] == first["winner"]["id"]


@pytest.mark.parametrize("seed", range(0, 40, 3))
def test_the_order_in_which_the_agents_answer_changes_nothing(agent, seed):
    outcomes = []
    for order_seed in (1, 2, 3):
        agents, log, _ = play(agent, seed, order_seed)
        rep = agents["cost"].report()
        outcomes.append((rep["outcome"], rep["winner"]["id"], rep["rounds"], tuple(sorted(rep["scores"].items()))))
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_the_seeds_cover_success_best_effort_and_several_rounds(agent):
    seen = set()
    for seed in range(40):
        agents, _, _ = play(agent, seed, seed)
        rep = agents["cost"].report()
        seen.add((rep["outcome"], min(rep["rounds"], 2)))
    assert {o for o, _ in seen} == {"SUCCESS", "BEST_EFFORT"} and any(r >= 1 for _, r in seen)
