#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The initiator: opening a run, watching it when it is not a participant, sending the init again, and the races between runs."""
import pytest
from test_agent import MemStore, Net, init_doc, uuids
from test_agent_liveness import PARAMS, counts, errors, kinds, settle, tick_all

from ioc_concord import wire as W

R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"
SPEC = {"type": "string", "description": "a plan"}


def open_run(net, who="alice", names=("cost", "security"), run_id=R1, params=PARAMS):
    step = net.agents[who].start(list(names), "pick one", "nothing", "all agree", SPEC, params=params, run_id=run_id)
    net.broadcast(step, who)
    return step


def watched(agent, names=("cost", "security"), **kw):
    return Net(agent, ["alice", *names], **kw)


# --- opening a run ---------------------------------------------------------------------

def test_start_builds_the_init_and_the_initiator_that_is_not_a_participant_only_watches(agent):
    a = agent.Agent("alice", uuid_fn=uuids("aaaaaaaa"))
    step = a.start(["cost", "security"], "pick one", "nothing", "all agree", SPEC, params=PARAMS, run_id=R1, now=7)
    (text,) = step.send
    m = W.parse_message(text)
    assert (m["kind"], m["from"], m["run_id"], m["participants"], m["params"]["timeout"]) == ("init", "alice", R1, ["cost", "security"], 10)
    assert W.check_ok(text) and [e["event"] for e in step.events] == ["started"] and step.events[0]["role"] == "initiator"
    s = a.status()
    assert (s["role"], s["status"], s["outcome"], s["state"]) == ("initiator", "active", None, "wait")
    assert a.due() == [] and s["timers"] == {"init-reply|cost": 17, "init-reply|security": 17} and a.next_wakeup() == 17
    assert a.report()["status"] == "pending"


def test_start_makes_a_run_id_when_none_is_given_and_refuses_what_is_invalid(agent):
    a = agent.Agent("alice", uuid_fn=uuids("aaaaaaaa"))
    text = a.start(["cost", "security"], "pick one", "nothing", "all agree", SPEC).send[0]
    assert W.parse_message(text)["run_id"] == "aaaaaaaa-0000-4000-8000-000000000001"
    b = agent.Agent("bob")
    for bad in (dict(participants=["cost"]), dict(spec={}), dict(context=" "), dict(params={"eps": 0})):
        args = dict(participants=["cost", "security"], context="c", x_reject="a", x_ideal="b", spec=SPEC)
        with pytest.raises(agent.InvalidMessage):
            b.start(**{**args, **bad})
    assert b.status() is None


def test_an_agent_in_a_run_cannot_open_another(agent):
    a = agent.Agent("alice")
    a.start(["cost", "security"], "c", "a", "b", SPEC, run_id=R1)
    with pytest.raises(agent.StateError, match="in a run already"):
        a.start(["cost", "security"], "c", "a", "b", SPEC, run_id=R2)


def test_an_initiator_that_is_a_participant_owes_an_init_reply_like_the_others(agent):
    net = Net(agent, ["cost", "security"])
    open_run(net, "cost")
    cost = net.agents["cost"]
    assert (cost.status()["role"], cost.due()[0]["kind"], cost.due()[0]["initiator"]) == ("participant", "init-reply", "cost")
    assert list(cost.status()["timers"]) == ["init-reply|security"]
    assert net.agents["security"].due()[0]["kind"] == "init-reply"
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=70)
    net.answer_score("security", seed_cost=75)
    assert {n: a.report()["status"] for n, a in net.agents.items()} == {"cost": "confirmed", "security": "confirmed"}


def test_an_init_reply_asked_for_of_an_initiator_that_is_a_participant_is_answered_from_its_store(agent):
    net = Net(agent, ["cost", "security"])
    open_run(net, "cost")
    net.answer_init("cost")
    err = W.build_error("security", R1, "cost", "init-reply", "missing", uid="44444444-4444-4444-8444-444444444444")
    assert kinds(net.agents["cost"].receive(err)) == ["resend"]


# --- watching a run -----------------------------------------------------------------------

def run_to_the_end(net, cost_on_security=70, security_on_cost=75):
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=cost_on_security)
    net.answer_score("security", seed_cost=security_on_cost)


def test_an_initiator_that_is_not_a_participant_learns_the_outcome_from_the_result_and_computes_none(agent):
    net = watched(agent)
    open_run(net)
    alice = net.agents["alice"]
    net.answer_init("cost")
    assert sorted(alice.status()["timers"]) == ["init-reply|security"]
    net.answer_init("security")
    assert alice.status()["timers"] == {} and alice.status()["status"] == "active"           # no timer runs for the result
    net.answer_score("cost", seed_security=70)
    net.answer_score("security", seed_cost=75)
    s, rep = alice.status(), alice.report()
    assert (s["status"], s["outcome"]) == ("ended", "SUCCESS") and alice.due() == [] and alice.next_wakeup() is None
    assert (rep["status"], rep["outcome"], rep["rounds"], rep["scores"]) == ("unverified", "SUCCESS", None, {})
    assert rep["winner"] == {"id": "seed-cost", "candidate": "plan of cost"}
    assert rep["results_compared"] == {"reporter": "cost", "result": {"outcome": "SUCCESS", "winning_candidate_id": "seed-cost"},
                                       "matches": None}
    assert counts(net, "error") == [] and "alice" not in counts(net, "score") + counts(net, "repair") + counts(net, "result")


def test_the_initiator_that_watches_ignores_what_is_not_for_it_and_sends_no_answer(agent):
    net = watched(agent)
    open_run(net)
    alice = net.agents["alice"]
    net.answer_init("cost")
    net.answer_init("security")
    score = W.build_score("cost", R1, [("seed-security", 70, "r")])
    step = alice.receive(score)
    assert step.send == [] and step.events[0]["reason"] == "not for the initiator"
    assert alice.receive(W.build_result("security", R1, "SUCCESS", "seed-cost")).events[0]["reason"] == "not the reporter"
    assert alice.status()["status"] == "active"


def test_a_mismatch_error_disputes_the_report_of_an_initiator_that_is_not_a_participant(agent):
    net = watched(agent)
    open_run(net)
    run_to_the_end(net)
    alice = net.agents["alice"]
    assert alice.report()["status"] == "unverified"
    alice.receive(W.build_error("security", R1, "cost", "result", "mismatch", uid="44444444-4444-4444-8444-444444444444"))
    assert alice.report()["status"] == "disputed" and alice.status()["status"] == "ended"


def test_a_cancel_ends_the_run_of_an_initiator_that_is_not_a_participant(agent):
    net = watched(agent)
    open_run(net)
    alice = net.agents["alice"]
    step = alice.receive(W.build_cancel("cost", R1, "missing-agent", agent="security", uid="ca000000-0000-4000-8000-000000000001"))
    assert kinds(step) == ["cancel"] and (alice.status()["status"], alice.report()["status"]) == ("aborted", "aborted")
    assert alice.report()["outcome"] == "ABORTED" and alice.report()["winner"] is None


def test_an_initiator_that_watches_restarts_with_its_timers_and_its_role(agent):
    store = MemStore(agent)
    a = agent.Agent("alice", store=store, uuid_fn=uuids("aaaaaaaa"))
    a.start(["cost", "security"], "c", "a", "b", SPEC, params=PARAMS, run_id=R1, now=0)
    b = agent.Agent("alice", store=store)
    assert (b.status()["role"], b.next_wakeup()) == ("initiator", 10)
    assert kinds(b.tick(10)) == ["resend", "resend"] and len(errors(b.tick(20))) == 0


# --- sending the init again ----------------------------------------------------------------

def test_an_init_that_was_lost_is_sent_again_in_a_resend_and_the_participant_joins(agent):
    net = watched(agent, ("cost", "security", "legal"))
    net.drop = lambda text, name: name == "legal" and W.peek(text)["tag"] == "concord-init"
    open_run(net, names=("cost", "security", "legal"))
    assert net.agents["legal"].status() is None
    net.now = 10
    step = net.agents["alice"].tick(10)
    assert kinds(step) == ["resend", "resend", "resend"]                          # every reply still missing is asked for
    net.broadcast(step, "alice")
    assert net.agents["legal"].status()["run_id"] == R1 and net.agents["legal"].due()[0]["kind"] == "init-reply"
    inner = W.parse_message(W.parse_message(step.send[0])["inner"])
    assert (inner["kind"], inner["from"]) == ("init", "alice") and W.parse_message(step.send[0])["reply_to"] is None


def test_the_participants_that_did_answer_send_their_init_reply_again_and_the_run_goes_on(agent):
    net = watched(agent, ("cost", "security", "legal"))
    net.drop = lambda text, name: name == "legal" and W.peek(text)["tag"] == "concord-init"
    open_run(net, names=("cost", "security", "legal"))
    net.answer_init("cost")
    net.answer_init("security")
    tick_all(net, 10)                                      # alice sends the init again: legal joins, the others answer again
    assert net.agents["legal"].status() is not None
    assert counts(net, "resend").count("cost") == 1 and counts(net, "resend").count("security") == 1
    net.answer_init("legal")
    for n, others in (("cost", ("security", "legal")), ("security", ("cost", "legal")), ("legal", ("cost", "security"))):
        net.answer_score(n, **{f"seed_{o}": 70 for o in others})
    assert {n: a.status()["status"] for n, a in net.agents.items()} == {"alice": "ended", "cost": "ended", "security": "ended", "legal": "ended"}


def test_a_participant_that_never_answers_ends_the_run_after_retries_and_everybody_is_told(agent):
    net = watched(agent, ("cost", "security", "legal"))
    net.dead.add("legal")
    open_run(net, names=("cost", "security", "legal"))
    net.answer_init("cost")
    net.answer_init("security")
    settle(net, 0)
    for n in ("alice", "cost", "security"):
        rep = net.agents[n].report()
        assert (net.agents[n].status()["status"], rep["reason"]["agent"]) == ("aborted", "legal"), n
    assert sorted(counts(net, "cancel")) == ["alice", "cost", "security"]
    resent = [W.parse_message(t) for t in net.log if W.peek(t)["tag"] == "concord-resend" and W.peek(t)["attrs"]["from"] == "alice"]
    assert len(resent) == 2 and all(W.parse_message(m["inner"])["kind"] == "init" for m in resent)       # retries = 2


def test_with_no_retries_the_initiator_gives_up_at_the_first_expiry_and_with_no_timeout_never(agent):
    net = watched(agent)
    open_run(net, params={"timeout": 10, "retries": 0})
    assert kinds(net.agents["alice"].tick(10)) == ["cancel"]
    quiet = watched(agent)
    open_run(quiet, params={"timeout": 0, "retries": 1})
    assert quiet.agents["alice"].next_wakeup() is None and quiet.agents["alice"].tick(10 ** 9).send == []


# --- another version -------------------------------------------------------------------------

def version_error(frm="legal", to="alice", run_id=R1, text="I speak 0.2.0; this init is version 0.3.0.", version="0.2.0"):
    return W.build_error(frm, run_id, to, "init", "version", text=text, uid="44444444-4444-4444-8444-444444444444").replace(
        'version="0.3.0"', f'version="{version}"', 1)


def test_a_participant_that_refuses_the_version_ends_the_run_and_the_agents_that_joined_are_told(agent):
    net = watched(agent, ("cost", "security", "legal"))
    open_run(net, names=("cost", "security", "legal"))
    net.answer_init("cost")
    alice = net.agents["alice"]
    step = alice.receive(version_error())
    (c,) = [W.parse_message(t) for t in step.send]
    assert (c["kind"], c["reason"], c["agent"], c["superseded_by"]) == ("cancel", "version", None, None)
    assert [e["event"] for e in step.events] == ["refused", "aborted", "sent"]
    rep = alice.report()
    assert (rep["status"], rep["outcome"]) == ("aborted", "ABORTED")
    assert rep["reason"]["reason"] == "version" and rep["reason"]["note"] == {"agent": "legal", "text": "I speak 0.2.0; this init is version 0.3.0."}
    assert alice.due() == [] and alice.status()["timers"] == {}
    net.broadcast(step, "alice")
    assert {n: a.status()["status"] for n, a in net.agents.items() if n != "legal"} == {"alice": "aborted", "cost": "aborted", "security": "aborted"}
    assert net.agents["cost"].report()["reason"]["reason"] == "version"


def test_the_version_error_is_read_whatever_its_version_and_only_when_it_is_for_the_initiator_from_a_participant(agent):
    net = watched(agent, ("cost", "security", "legal"))
    open_run(net, names=("cost", "security", "legal"))
    alice = net.agents["alice"]
    assert alice.receive(version_error(frm="mallory")).events[0]["reason"] == "sender is not in the run"
    assert alice.receive(version_error(to="cost")).events[0]["reason"] == "version" and alice.status()["status"] == "active"
    cost = net.agents["cost"]
    cost.receive(W.build_init_reply("security", R1, 0, 100, "p", "r", 5, "w"))
    err = version_error(frm="security", to="cost")
    assert cost.receive(err).send == [] and cost.status()["status"] == "active"           # only the initiator acts on it
    assert alice.status()["status"] == "active"
    done = alice.receive(version_error(version="9.9.9", text="je parle 9.9.9"))
    assert alice.status()["status"] == "aborted" and kinds(done) == ["cancel"]
    assert alice.receive(version_error(frm="security", version="0.1.0")).events[0]["reason"] == "duplicate"


def test_a_busy_or_invalid_error_changes_nothing_for_the_initiator(agent):
    net = watched(agent)
    open_run(net)
    alice = net.agents["alice"]
    for reason, uid in (("busy", "44444444-4444-4444-8444-444444444441"), ("invalid", "44444444-4444-4444-8444-444444444442")):
        step = alice.receive(W.build_error("security", R1, "alice", "init", reason, uid=uid))
        assert step.send == [] and alice.status()["status"] == "active"
    assert sorted(alice.status()["timers"]) == ["init-reply|cost", "init-reply|security"]


# --- two runs at the same time ---------------------------------------------------------------------

def test_of_two_initiators_that_are_participants_the_run_with_the_lower_id_continues(agent):
    net = Net(agent, ["cost", "security"])
    cost, sec = net.agents["cost"], net.agents["security"]
    low = cost.start(["cost", "security"], "pick one", "nothing", "all agree", SPEC, params=PARAMS, run_id=R1)
    high = sec.start(["cost", "security"], "pick two", "nothing", "all agree", SPEC, params=PARAMS, run_id=R2)
    to_sec = sec.receive(low.send[0])                             # security opened the higher id: it gives way
    assert [e["event"] for e in to_sec.events] == ["aborted", "sent", "joined"] and kinds(to_sec) == ["cancel"]
    c = W.parse_message(to_sec.send[0])
    assert (c["run_id"], c["reason"], c["superseded_by"]) == (R2, "superseded", R1)
    to_cost = cost.receive(high.send[0])                          # cost opened the lower id: it answers busy
    (e,) = errors(to_cost)
    assert (e["run_id"], e["to"], e["what"], e["reason"]) == (R2, "security", "init", "busy")
    assert cost.receive(to_sec.send[0]).events[0]["reason"] == "unknown run"
    assert (sec.status()["run_id"], sec.status(R2)["status"], cost.status()["run_id"]) == (R1, "aborted", R1)
    assert sec.report(R2)["reason"] == {"reason": "superseded", "agent": None, "superseded_by": R1, "note": None}
    assert sec.due()[0]["kind"] == "init-reply" and sec.due()[0]["run_id"] == R1


def test_an_initiator_that_has_all_its_replies_does_not_give_way_it_answers_busy(agent):
    net = Net(agent, ["cost", "security"])
    open_run(net, "cost", run_id=R2)
    net.answer_init("cost")
    net.answer_init("security")
    cost = net.agents["cost"]
    other = W.build_init("bob", ["cost", "security"], "c", "a", "b", SPEC, run_id=R1)       # a lower id, but cost has its replies
    (e,) = errors(cost.receive(other))
    assert e["reason"] == "busy" and cost.status()["run_id"] == R2


def test_an_initiator_that_watches_does_not_join_a_run_it_is_not_listed_in(agent):
    net = watched(agent)
    open_run(net, run_id=R2)
    other = W.build_init("bob", ["cost", "security"], "c", "a", "b", SPEC, run_id=R1)
    assert net.agents["alice"].receive(other).events[0]["reason"] == "not a participant"
    assert net.agents["alice"].status()["run_id"] == R2


def test_an_ended_run_does_not_stop_the_initiator_from_opening_another(agent):
    net = watched(agent)
    open_run(net)
    run_to_the_end(net)
    step = net.agents["alice"].start(["cost", "security"], "c", "a", "b", SPEC, run_id=R2)
    assert net.agents["alice"].status()["run_id"] == R2 and net.agents["alice"].report(R1)["status"] == "unverified" and step.send


def test_an_initiator_that_watches_reports_the_text_of_a_winner_that_is_a_repair(agent):
    net = watched(agent)
    open_run(net)
    net.answer_init("cost")
    net.answer_init("security")
    net.answer_score("cost", seed_security=20)
    net.answer_score("security", seed_cost=30)
    net.broadcast(net.agents["security"].submit_repair(R1, "a better plan", "because", 70, "ok"), "security")
    net.answer_score("cost", r1_security=65)
    rep = net.agents["alice"].report()
    assert (rep["status"], rep["outcome"]) == ("unverified", "SUCCESS")
    assert rep["winner"] == {"id": "r1-security", "candidate": "a better plan"}


def test_a_repair_that_the_participants_ignore_cannot_take_the_place_of_the_winner_in_the_watchers_report(agent):
    net = watched(agent)
    open_run(net)
    alice = net.agents["alice"]
    first = W.build_repair("security", R1, 1, "the real one", "r", 70, "w", uid="71717171-7171-4171-8171-717171717171")
    other = W.build_repair("security", R1, 1, "an impostor", "r", 70, "w", uid="72727272-7272-4272-8272-727272727272")
    assert alice.receive(first).events[0]["event"] == "recorded"
    assert alice.receive(other).events[0]["reason"] == "already recorded"
    assert alice.state["runs"][R1]["engine"]["pool"][0]["candidate"] == "the real one"


def test_an_init_inside_a_resend_is_answered_only_when_it_comes_from_the_initiator(agent):
    names = ["cost", "security", "legal"]
    net = Net(agent, names)
    net.start(init_doc(names, params=PARAMS))
    net.answer_init("legal")
    legal = net.agents["legal"]
    again = W.build_resend("alice", R1, init_doc(names, params=PARAMS), uid="73737373-7373-4373-8373-737373737373")
    assert kinds(legal.receive(again)) == ["resend"]
    claim = W.build_resend("security", R1, init_doc(names, params=PARAMS, from_="security"), uid="74747474-7474-4474-8474-747474747474")
    step = legal.receive(claim)                                # a member that claims to be the initiator of the run
    assert step.send == [] and step.events[0]["reason"] == "run already known"
