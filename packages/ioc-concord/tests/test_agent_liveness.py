#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Timers, gaps and silence: what the node does when messages are lost or an agent stops answering."""
from test_agent import RUN, MemStore, Net, init_doc, uuids

from ioc_concord import wire as W

PARAMS = {"timeout": 10, "retries": 2}


def kinds(step):
    return [W.parse_message(t)["kind"] for t in step.send]


def errors(step):
    return [W.parse_message(t) for t in step.send if W.peek(t)["tag"] == "concord-error"]


def joined(agent, now=0, params=PARAMS, names=("cost", "security"), me="cost"):
    a = agent.Agent(me, uuid_fn=uuids("cccccccc"))
    a.receive(init_doc(list(names), params=params), now=now)
    return a


def tick_all(net, now):
    net.now = now
    for name, a in net.agents.items():
        if name not in net.dead:
            net.broadcast(a.tick(now), name)


def settle(net, start, every=10, rounds=40):
    """Let time pass, every agent ticking, until nobody has a timer left."""
    now = start
    for _ in range(rounds):
        if all(a.next_wakeup() is None for n, a in net.agents.items() if n not in net.dead):
            return now
        now += every
        tick_all(net, now)
    raise AssertionError("the timers never end")


def counts(net, kind):
    return [W.peek(t)["attrs"]["from"] for t in net.log if W.peek(t)["tag"] == f"concord-{kind}"]


# --- the timers --------------------------------------------------------------------

def test_a_timer_starts_for_every_message_that_becomes_owed_and_a_message_ends_it(agent):
    a = joined(agent, now=100)
    assert a.status()["timers"] == {"init-reply|security": 110} and a.next_wakeup() == 110
    a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good", now=101)
    assert a.status()["timers"] == {"init-reply|security": 110}
    a.receive(W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good"), now=105)
    assert a.status()["timers"] == {"score|security|seeds": 115} and a.next_wakeup() == 115      # owed once every init reply is in
    a.receive(W.build_score("security", RUN, [("seed-cost", 70, "r")]), now=106)
    assert a.status()["timers"] == {}


def test_a_timer_that_expires_asks_again_and_the_wait_is_retries_plus_one_timeouts(agent):
    a = joined(agent, now=0)
    assert a.tick(9).send == [] and a.next_wakeup() == 10
    step = a.tick(10)
    (e,) = errors(step)
    assert (e["from"], e["to"], e["what"], e["reason"], e["candidate"]) == ("cost", "security", "init-reply", "missing", None)
    assert a.next_wakeup() == 20
    assert errors(a.tick(19)) == [] and len(errors(a.tick(20))) == 1 and a.next_wakeup() == 30
    step = a.tick(30)
    assert [e["event"] for e in step.events] == ["aborted", "sent"]
    (c,) = [W.parse_message(t) for t in step.send]
    assert (c["kind"], c["from"], c["reason"], c["agent"]) == ("cancel", "cost", "missing-agent", "security")
    s, rep = a.status(), a.report()
    assert (s["status"], s["outcome"], s["timers"]) == ("aborted", "ABORTED", {}) and a.next_wakeup() is None
    assert rep["reason"] == {"reason": "missing-agent", "agent": "security", "superseded_by": None}
    assert a.tick(1000).send == [] and a.due() == []


def test_with_no_retries_the_first_expiry_gives_up(agent):
    a = joined(agent, params={"timeout": 10, "retries": 0})
    step = a.tick(10)
    assert kinds(step) == ["cancel"] and a.status()["status"] == "aborted"


def test_a_timeout_of_zero_means_the_timers_are_never_triggered(agent):
    a = joined(agent, params={"timeout": 0, "retries": 1})
    assert a.status()["timers"] == {"init-reply|security": None} and a.next_wakeup() is None
    assert a.tick(10 ** 9).send == [] and a.status()["status"] == "active"


def test_the_timer_of_each_author_is_its_own_and_a_batch_is_done_only_when_nothing_in_it_is_lacking(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"], params=PARAMS))
    for n in ("cost", "security", "legal"):
        net.answer_init(n)
    cost = net.agents["cost"]
    assert sorted(cost.status()["timers"]) == ["score|legal|seeds", "score|security|seeds"]
    cost.receive(W.build_score("security", RUN, [("seed-cost", 70, "r"), ("seed-legal", 70, "r")]))
    assert sorted(cost.status()["timers"]) == ["score|legal|seeds"]
    part = W.build_score("legal", RUN, [("seed-cost", 70, "r")])
    cost.receive(part)
    assert sorted(cost.status()["timers"]) == ["score|legal|seeds"]            # seed-security is still lacking from legal
    (e,) = errors(cost.tick(cost.next_wakeup()))
    assert (e["to"], e["what"], e["candidate"]) == ("legal", "score", "seed-security")


def test_a_repair_that_is_owed_has_a_timer_and_a_repair_that_arrives_ends_it(agent):
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(params=PARAMS))
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=20)
    net.answer_score("security", seed_cost=30)
    cost = net.agents["cost"]
    assert list(cost.status()["timers"]) == ["repair|security|1"]
    (e,) = errors(cost.tick(cost.next_wakeup()))
    assert (e["to"], e["what"], e["candidate"]) == ("security", "repair", "r1-security")
    net.broadcast(net.agents["security"].submit_repair(RUN, "better", "because", 70, "ok"), "security")
    assert cost.status()["timers"] == {}


def test_a_result_that_does_not_arrive_is_asked_for_and_the_resend_ends_the_run(agent):
    net = Net(agent, ["cost", "security"])
    net.drop = lambda text, name: name == "security" and W.peek(text)["tag"] == "concord-result"
    net.start(init_doc(params=PARAMS))
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=70)
    net.answer_score("security", seed_cost=75)
    sec = net.agents["security"]
    assert (sec.status()["outcome"], sec.status()["status"]) == ("SUCCESS", "active") and list(sec.status()["timers"]) == ["result|cost"]
    settle(net, 0)
    assert (sec.status()["status"], sec.report()["status"]) == ("ended", "confirmed")
    assert counts(net, "error") == ["security"] and counts(net, "resend") == ["cost"]


def test_the_timers_are_kept_with_the_state_and_survive_a_restart(agent):
    store = MemStore(agent)
    a = agent.Agent("cost", store=store)
    a.receive(init_doc(params=PARAMS), now=50)
    b = agent.Agent("cost", store=store)
    assert b.status()["timers"] == {"init-reply|security": 60} and b.next_wakeup() == 60
    assert kinds(b.tick(60)) == ["error"]
    assert agent.Agent("cost", store=store).next_wakeup() == 70


# --- lost messages ---------------------------------------------------------------------

def lossy_three(agent, drop, params=PARAMS):
    net = Net(agent, ["cost", "security", "legal"])
    net.drop = drop
    net.start(init_doc(["cost", "security", "legal"], params=params))
    for n in ("cost", "security", "legal"):
        net.answer_init(n, raw=40)
    return net


def all_score(net, raw=40):
    for n, others in (("cost", ("security", "legal")), ("security", ("cost", "legal")), ("legal", ("cost", "security"))):
        net.answer_score(n, **{f"seed_{o}": raw for o in others})


def test_a_repair_that_proves_a_lost_score_makes_the_agent_ask_at_once_and_it_is_judged_when_the_score_comes(agent):
    lost = lambda text, name: name == "security" and W.peek(text)["tag"] == "concord-score" and W.peek(text)["attrs"]["from"] == "legal"
    net = lossy_three(agent, lost)
    all_score(net)
    cost, sec = net.agents["cost"], net.agents["security"]
    assert sec.status()["state"] == "wait" and cost.status()["bottleneck"] == "cost"
    before = len(net.events)
    net.broadcast(cost.submit_repair(RUN, "better", "because", 70, "ok"), "cost")
    mine = [e for who, e in net.events[before:] if who == "security"]
    assert [e["event"] for e in mine if e["event"] in ("held", "asked")] == ["held", "asked"]
    asked = [e for e in mine if e["event"] == "asked"][0]
    assert (asked["author"], asked["what"], asked["candidate"]) == ("legal", "score", "seed-cost")
    assert counts(net, "resend") == ["legal"]                                   # legal answered with the stored message
    assert "r1-cost" in [c["id"] for c in sec.state["runs"][RUN]["engine"]["pool"]] and sec.due()[0]["kind"] == "score"
    assert sec.status()["lacking"] == [("security", "r1-cost"), ("legal", "r1-cost")]


def test_a_score_for_a_candidate_the_agent_does_not_hold_makes_it_ask_the_author_of_the_candidate(agent):
    lost = lambda text, name: name == "security" and W.peek(text)["tag"] == "concord-repair"
    net = lossy_three(agent, lost)
    all_score(net)
    net.broadcast(net.agents["cost"].submit_repair(RUN, "better", "because", 70, "ok"), "cost")
    sec = net.agents["security"]
    assert sec.status()["lacking"] == []
    net.answer_score("legal", r1_cost=50)                                       # security has no r1-cost: a gap
    assert "gap" in [e["event"] for who, e in net.events if who == "security"]
    assert counts(net, "error") == ["security"] and counts(net, "resend") == ["cost"]
    assert "r1-cost" in [c["id"] for c in sec.state["runs"][RUN]["engine"]["pool"]]


def test_a_gap_is_asked_for_once_and_the_timer_repeats_the_request(agent):
    lost = lambda text, name: name == "security" and W.peek(text)["tag"] == "concord-repair"
    net = lossy_three(agent, lost)
    all_score(net)
    net.broadcast(net.agents["cost"].submit_repair(RUN, "better", "because", 70, "ok"), "cost")
    sec = net.agents["security"]
    net.dead.add("cost")                                                         # nobody answers any more
    for _ in range(3):
        net.broadcast(sec.receive(W.build_score("legal", RUN, [("r1-cost", 50, "r")], uid=uuids("d0d0d0d0")())), "security")
    assert sum(1 for who, e in net.events if who == "security" and e["event"] == "asked") == 1


def test_a_result_that_cannot_be_judged_asks_for_what_is_lacking(agent):
    net = lossy_three(agent, lambda text, name: False)
    sec = net.agents["security"]
    step = sec.receive(W.build_result("cost", RUN, "BEST_EFFORT", "r2-legal"))
    assert [e["event"] for e in step.events][:1] == ["held"]
    assert [(e["to"], e["what"], e["candidate"]) for e in errors(step)] == [("legal", "score", "seed-cost"),
                                                                           ("cost", "score", "seed-security")]


# --- silence and the cancel -----------------------------------------------------------

def test_an_agent_that_never_answers_ends_the_run_for_everyone_who_waits_for_it(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.dead.add("legal")
    net.start(init_doc(["cost", "security", "legal"], params=PARAMS))
    net.answer_init("cost")
    net.answer_init("security")
    settle(net, 0)
    for n in ("cost", "security"):
        s, rep = net.agents[n].status(), net.agents[n].report()
        assert (s["status"], s["outcome"], rep["status"]) == ("aborted", "ABORTED", "aborted")
        assert rep["reason"]["reason"] == "missing-agent" and rep["reason"]["agent"] == "legal"
        assert net.agents[n].due() == []
    assert sorted(counts(net, "cancel")) == ["cost", "security"]                  # each agent sends its cancel once
    assert counts(net, "result") == []


def test_one_agent_that_gives_up_is_enough_the_cancel_reaches_the_others_and_each_passes_it_on_once(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"], params=PARAMS))
    for n in ("cost", "security", "legal"):
        net.answer_init(n)
    net.broadcast(net.agents["cost"].receive(W.build_cancel("legal", RUN, "missing-agent", agent="security", uid=uuids("f0f0f0f0")())), "cost")
    assert {n: a.status()["status"] for n, a in net.agents.items()} == {"cost": "aborted", "security": "aborted", "legal": "aborted"}
    assert sorted(counts(net, "cancel")) == ["cost", "legal", "security"]
    assert all(a.report()["reason"]["agent"] == "security" for a in net.agents.values())


def test_the_reporters_result_reaches_one_agent_but_not_the_other_and_then_the_reporter_goes_silent(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.drop = lambda text, name: name == "legal" and W.peek(text)["tag"] == "concord-result"
    net.start(init_doc(["cost", "security", "legal"], params=PARAMS))
    for n in ("cost", "security", "legal"):
        net.answer_init(n, raw=80)
    all_score(net, raw=70)
    assert {n: (a.status()["outcome"], a.status()["status"]) for n, a in net.agents.items()} == {
        "cost": ("SUCCESS", "ended"), "security": ("SUCCESS", "ended"), "legal": ("SUCCESS", "active")}
    net.dead.add("cost")                                                         # the reporter stops answering
    settle(net, 0)
    legal, sec = net.agents["legal"], net.agents["security"]
    assert (legal.status()["status"], legal.status()["outcome"]) == ("aborted", "ABORTED")      # computed an outcome, but never confirmed
    assert legal.report()["reason"] == {"reason": "missing-agent", "agent": "cost", "superseded_by": None}
    assert (sec.status()["status"], sec.status()["outcome"], sec.report()["status"]) == ("ended", "SUCCESS", "disputed")
    assert counts(net, "cancel").count("legal") == 1 and counts(net, "cancel").count("security") == 1


def test_damaged_copies_are_asked_for_only_retries_times_per_author_and_kind(agent):
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(params=PARAMS))
    a = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    sent = [len(a.receive(reply.replace("because", f"becauz{i}")).send) for i in range(4)]
    assert sent == [1, 1, 0, 0]
    assert a.receive(reply).events[0]["event"] == "recorded"


def test_a_message_in_a_run_that_has_ended_starts_no_timer(agent):
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(params=PARAMS))
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=70)
    net.answer_score("security", seed_cost=75)
    for a in net.agents.values():
        assert a.status()["timers"] == {} and a.next_wakeup() is None and a.tick(10 ** 6).send == []
