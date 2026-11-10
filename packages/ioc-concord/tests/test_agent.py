#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The node of an agent, as imported and as bundled into each script (see conftest.py)."""
import itertools
import json
import re

import pytest

from ioc_concord import wire as W
from ioc_concord.xmlutil import with_check

M = 1_000_000
RUN = "11111111-1111-4111-8111-111111111111"
SPEC = {"type": "string", "description": "a plan"}


def uuids(prefix):
    n = itertools.count(1)
    return lambda: f"{prefix}-0000-4000-8000-{next(n):012x}"


def init_doc(participants=("cost", "security"), run_id=RUN, **kw):
    args = dict(from_="alice", participants=list(participants), context="pick one", x_reject="nothing", x_ideal="all agree",
                spec=SPEC, run_id=run_id, uid=uuids("a0a0a0a0")())
    return W.build_init(**{**args, **kw})


class MemStore:
    """A store that keeps the JSON of the state, as a file would: what it holds is what a restart would find."""

    def __init__(self, agent_mod):
        self.agent_mod, self.text = agent_mod, None

    def load(self):
        if self.text is None:
            raise self.agent_mod.StateError("no state")
        return json.loads(self.text)

    def save(self, state):
        self.text = json.dumps(state)


def forge(doc, old, new):
    """`doc` with `old` replaced by `new` and its check computed again: a message that is intact but not what an honest author sends."""
    assert old in doc
    return with_check(re.sub(r' check="[^"]*"', "", doc.replace(old, new), count=1))


class Net:
    """A channel on which every message reaches every agent, the sender included (a broadcast)."""

    def __init__(self, agent_mod, names, **kw):
        self.agents = {n: agent_mod.Agent(n, uuid_fn=uuids(f"{i:08x}"), **kw) for i, n in enumerate(names, 1)}
        self.events = []
        self.log = []

    def broadcast(self, step, who=None):
        """Deliver what `step` sends to every agent, then what the agents send in answer, in the order it was sent."""
        self.events += [(who, e) for e in step.events]
        queue = list(step.send)
        while queue:
            text = queue.pop(0)
            self.log.append(text)
            for name, a in self.agents.items():
                got = a.receive(text)
                self.events += [(name, e) for e in got.events]
                queue += got.send

    def start(self, doc=None):
        self.broadcast(type("S", (), {"send": [doc or init_doc()], "events": []})())

    def answer_init(self, name, raw_reject=0, raw_ideal=100, raw=80, candidate=None):
        a = self.agents[name]
        task = [t for t in a.due() if t["kind"] == "init-reply"][0]
        self.broadcast(a.submit_init_reply(task["run_id"], raw_reject, raw_ideal, candidate or f"plan of {name}", "because", raw,
                                           "I like it"), name)

    def answer_score(self, name, **raw):
        a = self.agents[name]
        task = [t for t in a.due() if t["kind"] == "score"][0]
        self.broadcast(a.submit_score(task["run_id"], [(c["id"], raw[c["id"].replace("-", "_")], "my view")
                                                       for c in task["candidates"]]), name)


def happy(agent, cost_on_security=70, security_on_cost=75):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=cost_on_security)
    net.answer_score("security", seed_cost=security_on_cost)
    return net


def reasons(net, name):
    return [e.get("reason") for who, e in net.events if who == name and e["event"] == "ignored"]


# --- joining a run -----------------------------------------------------------

def test_a_participant_that_receives_the_init_joins_and_owes_an_init_reply(agent):
    a = agent.Agent("cost", uuid_fn=uuids("cccccccc"))
    assert a.due() == [] and a.status() is None
    step = a.receive(init_doc(), now=5)
    assert [e["event"] for e in step.events] == ["joined"] and step.events[0]["at"] == 5 and step.send == []
    (task,) = a.due()
    assert task == {"kind": "init-reply", "run_id": RUN, "initiator": "alice", "context": "pick one", "x_reject": "nothing",
                    "x_ideal": "all agree", "schema": SPEC}
    assert a.due() == a.due()
    assert (a.status()["status"], a.status()["state"], a.status()["outcome"]) == ("active", "wait", None)


def test_the_init_may_come_wrapped_in_prose_or_a_fence(agent):
    a = agent.Agent("cost")
    a.receive("Here you go:\n```xml\n" + init_doc() + "\n```\n")
    assert a.status()["run_id"] == RUN


def test_an_agent_that_is_not_listed_does_not_join(agent):
    a = agent.Agent("other")
    step = a.receive(init_doc())
    assert a.status() is None and step.events[0]["reason"] == "not a participant"


def test_an_agent_in_an_active_run_does_not_join_a_second_one(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    other = "22222222-2222-4222-8222-222222222222"
    step = a.receive(init_doc(run_id=other))
    assert step.events[0]["reason"] == "busy" and a.status()["run_id"] == RUN and a.status(other) is None


def test_an_init_for_a_run_the_agent_knows_is_ignored(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    assert a.receive(init_doc()).events[0]["reason"] == "duplicate"                 # the same transmission
    assert a.receive(init_doc(uid="33333333-3333-4333-8333-333333333333")).events[0]["reason"] == "run already known"
    assert a.status()["run_id"] == RUN


# --- the order of checks ------------------------------------------------------

def test_what_is_not_a_concord_message_is_ignored(agent):
    a = agent.Agent("cost")
    for text in ("hello", "<other>x</other>", "<concord-init", "", "<concord-score from='a'>never closed"):
        step = a.receive(text)
        assert [e["reason"] for e in step.events] == ["not a CONCORD message"] and step.send == []


def test_a_message_of_an_unknown_run_is_ignored(agent):
    a = agent.Agent("cost")
    doc = W.build_score("security", RUN, [("seed-cost", 5, "r")])
    assert a.receive(doc).events[0]["reason"] == "unknown run"


def test_own_messages_and_senders_outside_the_run_are_ignored(agent):
    net = happy(agent)
    own = W.build_score("cost", RUN, [("seed-security", 5, "r")])
    assert net.agents["cost"].receive(own).events[0]["reason"] == "own message"
    stranger = W.build_score("mallory", RUN, [("seed-security", 5, "r")])
    assert net.agents["cost"].receive(stranger).events[0]["reason"] == "sender is not in the run"


def test_the_initiator_is_a_member_of_the_run_so_its_messages_pass_the_first_step(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    err = W.build_error("alice", RUN, "cost", "init", "busy")
    assert net.agents["cost"].receive(err).events[0]["reason"] == "no answer expected"


def test_a_transmission_is_acted_on_once(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    a = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    assert a.receive(reply).events[0]["event"] == "recorded"
    again = a.receive(reply)
    assert [e["reason"] for e in again.events] == ["duplicate"]


def test_a_message_of_another_version_is_ignored_and_not_kept(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    a = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    other = reply.replace('version="0.3.0"', 'version="0.2.0"')
    assert a.receive(other).events[0]["reason"] == "version"
    assert a.receive(reply).events[0]["event"] == "recorded"            # same uuid, intact and of the right version


def test_a_damaged_message_is_ignored_and_its_intact_copy_is_still_processed(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    a = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    assert a.receive(reply.replace("because", "becauze")).events[0]["reason"] == "check"
    assert a.receive(reply).events[0]["event"] == "recorded"


def test_an_invalid_message_is_ignored_and_nothing_is_recorded(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    a = net.agents["cost"]
    good = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    bad = forge(good, "<raw>80.0</raw>", "<raw>eighty</raw>")
    step = a.receive(bad)
    assert step.events[0]["reason"] == "invalid" and "number" in step.events[0]["detail"]
    assert a.status()["anchors_in"] == []
    assert a.receive(forge(good, 'id="seed-security"', 'id="seed-cost"')).events[0]["reason"] == "invalid"
    assert a.receive(good).events[0]["event"] == "recorded"                      # the valid one with the same uuid is processed


def test_an_init_that_is_invalid_or_of_another_version_is_ignored(agent):
    a = agent.Agent("cost")
    doc = init_doc()
    assert a.receive(doc.replace('version="0.3.0"', 'version="0.1.0"')).events[0]["reason"] == "version"
    assert a.receive(doc.replace("timeout>300", "timeout>-1")).events[0]["reason"] == "check"
    assert a.receive(forge(doc, "timeout>300", "timeout>-1")).events[0]["reason"] == "invalid"
    assert a.status() is None


# --- the happy path -----------------------------------------------------------

def test_the_init_reply_is_built_recorded_and_broadcast(agent):
    a = agent.Agent("cost", uuid_fn=uuids("cccccccc"))
    a.receive(init_doc())
    step = a.submit_init_reply(RUN, 0, 100, "local cache", "because", 82.5, "good", now=9)
    assert len(step.send) == 1 and [e["event"] for e in step.events] == ["sent"] and step.events[0]["at"] == 9
    m = W.parse_message(step.send[0], SPEC)
    assert (m["kind"], m["from"], m["uuid"], m["proposal"]["id"], m["proposal"]["raw"]) == (
        "init-reply", "cost", "cccccccc-0000-4000-8000-000000000001", "seed-cost", 82_500_000)
    assert W.check_ok(step.send[0]) and a.due() == []
    assert a.status()["anchors_in"] == ["cost"] and a.status()["lacking"] == [("security", "seed-cost")]


def test_after_every_init_reply_the_agent_owes_a_score_for_each_seed_of_the_others(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    net.answer_init("cost")
    net.answer_init("security")
    assert [t["kind"] for t in net.agents["cost"].due()] == []             # legal's reply is not in yet
    net.answer_init("legal")
    (task,) = net.agents["cost"].due()
    assert task["kind"] == "score" and [(c["id"], c["from"]) for c in task["candidates"]] == [
        ("seed-security", "security"), ("seed-legal", "legal")]
    assert task["candidates"][0]["candidate"] == "plan of security" and task["candidates"][0]["rationale"] == "because"
    assert task["candidates"][0]["reasoning"] == "I like it"


def test_two_agents_reach_success_and_each_computes_the_same_outcome(agent):
    net = happy(agent)
    for name in ("cost", "security"):
        a = net.agents[name]
        s = a.status()
        assert (s["state"], s["outcome"], s["x_star"], s["floor"], s["status"]) == ("done", "SUCCESS", "seed-cost", 750_000, "ended")
        assert s["lacking"] == [] and a.due() == []
    assert [e["outcome"] for _, e in net.events if e["event"] == "outcome"] == ["SUCCESS", "SUCCESS"]


def test_a_run_whose_floor_is_below_tau_is_not_done_and_names_the_bottleneck(agent):
    net = happy(agent, cost_on_security=20, security_on_cost=30)
    for name in ("cost", "security"):
        s = net.agents[name].status()
        assert (s["state"], s["outcome"], s["bottleneck"], s["round"]) == ("repair", None, "security", 1)
    assert [e["bottleneck"] for _, e in net.events if e["event"] == "repair owed"] == ["security"] * 2


def test_three_agents_reach_success_and_the_last_to_score_waits_for_the_others(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    for n in ("cost", "security", "legal"):
        net.answer_init(n)
    net.answer_score("cost", seed_security=70, seed_legal=70)
    net.answer_score("security", seed_cost=70, seed_legal=70)
    assert net.agents["legal"].status()["state"] == "wait"
    net.answer_score("legal", seed_cost=70, seed_security=70)
    assert {n: a.status()["outcome"] for n, a in net.agents.items()} == {"cost": "SUCCESS", "security": "SUCCESS", "legal": "SUCCESS"}


def test_score_entries_for_the_senders_own_candidate_are_ignored_and_the_others_kept(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    a = net.agents["cost"]
    own = forge(W.build_score("legal", RUN, [("seed-cost", 75, "r"), ("seed-security", 5, "own")]), 'from="legal"', 'from="security"')
    step = a.receive(own)
    assert [e["event"] for e in step.events] == ["entries ignored", "recorded"]
    assert step.events[0]["candidates"] == ["seed-security"] and step.events[1]["candidates"] == ["seed-cost"]


def test_a_rating_of_a_candidate_the_agent_does_not_hold_is_a_gap_not_an_invalid_message(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    a = net.agents["cost"]
    step = a.receive(W.build_score("security", RUN, [("r1-cost", 5, "r")]))
    assert step.events[0]["event"] == "gap" and step.events[0]["candidates"] == ["r1-cost"]


# --- what the model submits ---------------------------------------------------

def test_a_submission_that_is_invalid_changes_nothing_and_may_be_tried_again(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    for bad in (dict(raw_reject=50, raw_ideal=50), dict(raw=101), dict(raw=0.1234567), dict(candidate=" "), dict(reasoning="")):
        args = dict(raw_reject=0, raw_ideal=100, candidate="x", rationale="r", raw=50, reasoning="w")
        with pytest.raises(agent.InvalidMessage):
            a.submit_init_reply(RUN, **{**args, **bad})
    assert a.status()["anchors_in"] == [] and len(a.due()) == 1
    assert a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w").send


def test_the_candidate_must_satisfy_the_schema_of_the_run(agent):
    a = agent.Agent("cost")
    a.receive(init_doc(spec={"enum": ["A", "B"]}))
    with pytest.raises(agent.InvalidMessage, match="exactly one of"):
        a.submit_init_reply(RUN, 0, 100, "C", "r", 50, "w")
    assert a.submit_init_reply(RUN, 0, 100, "A", "r", 50, "w").send


def test_a_submission_in_the_wrong_state_is_refused(agent):
    a = agent.Agent("cost")
    with pytest.raises(agent.StateError, match="knows no run"):
        a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w")
    a.receive(init_doc())
    with pytest.raises(agent.StateError, match="owes no score"):
        a.submit_score(RUN, [("seed-security", 5, "w")])
    a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w")
    with pytest.raises(agent.StateError, match="already sent"):
        a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w")


def test_a_score_covers_exactly_the_candidates_owed(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    for n in ("cost", "security", "legal"):
        net.answer_init(n)
    a = net.agents["cost"]
    for given in ([("seed-security", 5, "w")], [], [("seed-security", 5, "w"), ("seed-legal", 5, "w"), ("seed-cost", 5, "w")],
                  [("seed-security", 5, "w"), ("seed-security", 5, "w")]):
        with pytest.raises(agent.InvalidMessage, match="exactly the candidates not rated yet"):
            a.submit_score(RUN, given)
    with pytest.raises(agent.InvalidMessage, match="0 to 100"):
        a.submit_score(RUN, [("seed-security", 500, "w"), ("seed-legal", 5, "w")])
    assert len(a.due()[0]["candidates"]) == 2
    assert a.submit_score(RUN, [("seed-legal", 5, "w"), ("seed-security", 5, "w")]).send


# --- the state is kept ---------------------------------------------------------

def test_the_state_is_saved_and_a_restarted_agent_owes_the_same(agent):
    store = MemStore(agent)
    net = Net(agent, ["cost", "security"])
    a = agent.Agent("cost", store=store, uuid_fn=uuids("cccccccc"))
    net.agents["cost"] = a
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    before = a.due()
    assert before and before[0]["kind"] == "score"
    again = agent.Agent("cost", store=store)
    assert again.due() == before and again.status() == a.status()
    net.agents["cost"] = again
    net.answer_score("cost", seed_security=70)
    net.answer_score("security", seed_cost=75)
    assert again.status()["outcome"] == "SUCCESS"
    assert agent.Agent("cost", store=store).status()["outcome"] == "SUCCESS"


def test_a_saved_state_of_another_agent_is_refused(agent):
    store = MemStore(agent)
    agent.Agent("cost", store=store).receive(init_doc())
    with pytest.raises(agent.StateError, match="belongs to 'cost'"):
        agent.Agent("security", store=store)


def test_the_clock_is_injected_and_a_given_time_wins(agent):
    a = agent.Agent("cost", clock=lambda: 1234)
    assert a.receive(init_doc()).events[0]["at"] == 1234
    assert a.receive("hello", now=7).events[0]["at"] == 7


# --- the repair ---------------------------------------------------------------

def below_tau(agent):
    """cost and security rate each other's seed low: the floor is .3 and security is the bottleneck of round 1."""
    return happy(agent, cost_on_security=20, security_on_cost=30)


def test_the_bottleneck_owes_a_repair_and_the_task_says_what_to_fix(agent):
    net = below_tau(agent)
    assert net.agents["cost"].due() == []
    (task,) = net.agents["security"].due()
    assert task == {"kind": "repair", "run_id": RUN, "round": 1,
                    "incumbent": {"id": "seed-cost", "candidate": "plan of cost", "rationale": "because"},
                    "threshold": "0.6", "floor": "0.3", "scores": {"cost": "0.8", "security": "0.3"},
                    "shortfall": {"security": "0.3"}, "reasoning": {"cost": "I like it", "security": "my view"}, "repairs_left": 8}
    assert net.agents["security"].due() == net.agents["security"].due()


def test_a_repair_that_clears_tau_for_everyone_ends_the_run_in_success(agent):
    net = below_tau(agent)
    sec = net.agents["security"]
    net.broadcast(sec.submit_repair(RUN, "a better plan", "because", 70, "mine is fine"), "security")
    (task,) = net.agents["cost"].due()
    assert task["kind"] == "score" and [c["id"] for c in task["candidates"]] == ["r1-security"]
    assert sec.due() == []
    net.answer_score("cost", r1_security=65)
    for name in ("cost", "security"):
        s = net.agents[name].status()
        assert (s["state"], s["outcome"], s["x_star"], s["floor"], s["status"]) == ("done", "SUCCESS", "r1-security", 650_000, "ended")
    assert [W.parse_message(t)["kind"] for t in net.log].count("result") == 1


def test_only_the_bottleneck_can_submit_a_repair_and_only_when_one_is_owed(agent):
    net = below_tau(agent)
    with pytest.raises(agent.StateError, match="no repair is owed"):
        net.agents["cost"].submit_repair(RUN, "x", "r", 50, "w")
    ok = happy(agent)
    with pytest.raises(agent.StateError, match="no repair is owed|has ended"):
        ok.agents["security"].submit_repair(RUN, "x", "r", 50, "w")
    with pytest.raises(agent.InvalidMessage, match="0 to 100"):
        net.agents["security"].submit_repair(RUN, "x", "r", 500, "w")
    assert len(net.agents["security"].due()) == 1


def test_a_repair_from_another_agent_or_when_none_is_owed_is_ignored(agent):
    net = below_tau(agent)
    cost = net.agents["cost"]
    from_cost = W.build_repair("cost", RUN, 1, "x", "r", 50, "w")
    assert net.agents["security"].receive(from_cost).events[0]["reason"] == "no repair owed from this agent"
    assert net.agents["security"].status()["lacking"] == []
    ok = happy(agent)
    late = W.build_repair("security", RUN, 1, "x", "r", 50, "w")
    assert ok.agents["cost"].receive(late).events[0]["reason"] == "run over"
    wrong_round = W.build_repair("security", RUN, 2, "x", "r", 50, "w")
    step = cost.receive(wrong_round)
    assert step.events[0]["reason"] == "no repair owed from this agent"
    assert cost.status()["lacking"] == []


def test_a_repair_that_arrives_before_the_state_is_complete_is_held_and_still_accepted_later(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=20)                          # security's score on seed-cost is not in yet
    repair_doc = W.build_repair("security", RUN, 1, "better", "because", 70, "ok", uid="77777777-7777-4777-8777-777777777777")
    cost = net.agents["cost"]
    step = cost.receive(repair_doc)
    assert step.events[0]["event"] == "held" and cost.status()["state"] == "wait"
    net.answer_score("security", seed_cost=30)
    again = cost.receive(repair_doc)
    assert again.events[0]["event"] == "recorded" and cost.due()[0]["kind"] == "score"


# --- the result and the confirmation --------------------------------------------

def test_the_reporter_sends_its_result_by_itself_and_its_run_ends(agent):
    net = happy(agent)
    results = [t for t in net.log if W.parse_message(t)["kind"] == "result"]
    assert len(results) == 1
    m = W.parse_message(results[0])
    assert (m["from"], m["outcome"], m["winning_candidate_id"]) == ("cost", "SUCCESS", "seed-cost") and W.check_ok(results[0])
    sent = [e for who, e in net.events if e["event"] == "sent" and e.get("kind") == "result"]
    assert len(sent) == 1
    assert net.agents["cost"].status()["status"] == "ended" and net.agents["cost"].due() == []


def test_a_participant_that_is_not_the_reporter_ends_when_it_holds_the_result_and_it_matches(agent):
    net = happy(agent)
    rep = net.agents["security"].report()
    assert rep["status"] == "confirmed" and rep["outcome"] == "SUCCESS"
    assert rep["winner"] == {"id": "seed-cost", "candidate": "plan of cost"}
    assert rep["scores"] == {"cost": 800_000, "security": 750_000} and rep["shortfall"] == {} and rep["rounds"] == 0
    assert rep["results_compared"] == {"reporter": "cost", "result": {"outcome": "SUCCESS", "winning_candidate_id": "seed-cost"},
                                       "matches": True}
    assert net.agents["cost"].report()["results_compared"]["matches"] is True and net.agents["cost"].report()["status"] == "confirmed"


def test_a_non_reporter_with_its_outcome_computed_is_not_ended_until_it_holds_the_result(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    net.agents["security"].receive(W.build_score("cost", RUN, [("seed-security", 70, "r")]))
    sec = net.agents["security"]
    sec.submit_score(RUN, [("seed-cost", 75, "r")])
    s = sec.status()
    assert (s["outcome"], s["status"]) == ("SUCCESS", "active") and sec.report()["status"] == "pending"
    assert sec.due() == []
    result = W.build_result("cost", RUN, "SUCCESS", "seed-cost")
    sec.receive(result)
    assert sec.status()["status"] == "ended" and sec.report()["status"] == "confirmed"


def test_a_result_that_arrives_before_the_agent_has_its_outcome_is_recorded_and_compared_later(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    sec = net.agents["security"]
    step = sec.receive(W.build_result("cost", RUN, "SUCCESS", "seed-cost"))
    assert [e["event"] for e in step.events] == ["recorded"] and sec.status()["status"] == "active"
    sec.receive(W.build_score("cost", RUN, [("seed-security", 70, "r")]))
    step = sec.submit_score(RUN, [("seed-cost", 75, "r")])
    assert [e["event"] for e in step.events] == ["sent", "outcome", "ended"] and sec.report()["status"] == "confirmed"


def test_a_result_is_held_when_its_winner_is_unknown_and_the_agent_has_no_outcome_yet(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    sec = net.agents["security"]
    doc = W.build_result("cost", RUN, "BEST_EFFORT", "r3-cost", uid="88888888-8888-4888-8888-888888888888")
    assert sec.receive(doc).events[0]["event"] == "held"
    net.answer_init("security")
    assert sec.receive(doc).events[0]["event"] == "held"                # same uuid, still no outcome: not kept
    sec.receive(W.build_score("cost", RUN, [("seed-security", 70, "r")]))
    sec.submit_score(RUN, [("seed-cost", 75, "r")])
    step = sec.receive(W.build_result("cost", RUN, "BEST_EFFORT", "r3-cost"))
    assert step.events[0]["reason"] == "invalid" and "not a candidate of the run" in step.events[0]["detail"]
    assert sec.status()["status"] == "active"


def test_a_result_from_an_agent_that_is_not_the_reporter_is_ignored(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    fake = W.build_result("security", RUN, "SUCCESS", "seed-security")
    assert net.agents["legal"].receive(fake).events[0]["reason"] == "not the reporter"
    assert net.agents["legal"].report()["results_compared"]["result"] is None


def test_a_result_that_differs_from_the_outcome_sends_a_mismatch_error_and_the_run_is_disputed(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    sec = net.agents["security"]
    sec.receive(W.build_score("cost", RUN, [("seed-security", 70, "r")]))
    sec.submit_score(RUN, [("seed-cost", 75, "r")])
    step = sec.receive(W.build_result("cost", RUN, "BEST_EFFORT", "seed-security"))
    assert [e["event"] for e in step.events] == ["recorded", "mismatch", "ended"]
    (err,) = step.send
    m = W.parse_message(err)
    assert (m["kind"], m["from"], m["to"], m["what"], m["reason"]) == ("error", "security", "cost", "result", "mismatch")
    s = sec.status()
    assert (s["outcome"], s["status"]) == ("SUCCESS", "ended")                  # a result is not a vote
    rep = sec.report()
    assert rep["status"] == "disputed" and rep["results_compared"]["matches"] is False and rep["mismatches"]["sent"] == 1


def test_a_mismatch_error_is_read_by_every_agent_also_after_its_run_has_ended(agent):
    net = happy(agent)
    err = W.build_error("security", RUN, "cost", "result", "mismatch", text="differs")
    for name in ("cost", "security"):
        assert net.agents[name].report()["status"] == "confirmed"
    got = net.agents["cost"].receive(err)
    assert [e["event"] for e in got.events] == ["mismatch received"]
    assert net.agents["cost"].status()["status"] == "ended" and net.agents["cost"].report()["status"] == "disputed"
    assert net.agents["cost"].report()["mismatches"] == {"sent": 0, "received": ["security"]}
    other = W.build_error("security", RUN, "security", "result", "mismatch")
    assert net.agents["cost"].receive(other).events[0]["reason"] == "not about the result of the reporter"


def test_the_agent_is_free_for_a_new_run_once_its_run_has_ended(agent):
    net = happy(agent)
    cost = net.agents["cost"]
    again = "22222222-2222-4222-8222-222222222222"
    assert cost.receive(init_doc(run_id=again)).events[0]["event"] == "joined"
    assert cost.status()["run_id"] == again and cost.status(RUN)["status"] == "ended"
    assert cost.report(RUN)["status"] == "confirmed"


def test_data_messages_of_an_ended_run_are_ignored(agent):
    net = happy(agent)
    late = W.build_score("security", RUN, [("seed-cost", 5, "r")], uid="99999999-9999-4999-8999-999999999999")
    assert net.agents["cost"].receive(late).events[0]["reason"] == "run over"


def test_a_repair_run_that_spends_its_budget_ends_in_best_effort(agent):
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(params={"B": 1}))
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=20)
    net.answer_score("security", seed_cost=30)
    sec = net.agents["security"]
    assert sec.due()[0]["repairs_left"] == 1
    net.broadcast(sec.submit_repair(RUN, "weak", "because", 30, "meh"), "security")
    net.answer_score("cost", r1_security=30)
    for name in ("cost", "security"):
        s, rep = net.agents[name].status(), net.agents[name].report()
        assert (s["outcome"], s["x_star"], s["status"], rep["rounds"]) == ("BEST_EFFORT", "seed-cost", "ended", 1)
        assert rep["shortfall"] == {"security": 300_000} and rep["status"] == "confirmed"
