#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the node answers: errors for an init it cannot join, the stored original for an error that asks for it, resend wrappers,
and the cancel."""
import pytest
from test_agent import RUN, Net, forge, happy, init_doc, uuids

from ioc_concord import wire as W

OTHER = "22222222-2222-4222-8222-222222222222"


def sent_kinds(step):
    return [W.parse_message(t)["kind"] for t in step.send]


def error_of(step):
    (text,) = step.send
    return W.parse_message(text)


def three(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    return net


# --- an init that the agent does not join ---------------------------------------

def test_an_init_of_another_version_gets_a_version_error_that_names_both_versions(agent):
    a = agent.Agent("cost")
    step = a.receive(init_doc().replace('version="0.3.0"', 'version="0.2.0"'))
    e = error_of(step)
    assert (e["from"], e["to"], e["run_id"], e["what"], e["reason"]) == ("cost", "alice", RUN, "init", "version")
    assert "0.3.0" in e["text"] and "0.2.0" in e["text"] and a.status() is None
    assert W.peek(step.send[0])["attrs"].get("check") is None


def test_an_init_of_another_version_inside_a_resend_is_refused_the_same_way(agent):
    a = agent.Agent("cost")
    inner = init_doc().replace('version="0.3.0"', 'version="0.2.0"')
    wrapper = W.build_resend("alice", RUN, inner)
    e = error_of(a.receive(wrapper))
    assert (e["to"], e["what"], e["reason"]) == ("alice", "init", "version")
    wrapped_wrong = W.build_resend("alice", RUN, init_doc()).replace('<concord-resend version="0.3.0"', '<concord-resend version="0.2.0"')
    assert error_of(a.receive(wrapped_wrong))["reason"] == "version"
    assert a.status() is None


def test_other_messages_of_another_version_get_no_answer(agent):
    net = happy(agent)
    old = W.build_score("security", RUN, [("seed-cost", 5, "r")]).replace('version="0.3.0"', 'version="0.2.0"')
    assert net.agents["cost"].receive(old).send == []
    other = agent.Agent("cost")
    assert other.receive(old).send == []                                        # an unknown run is not answered either


def test_an_invalid_init_gets_an_invalid_error_with_the_reason_and_is_not_joined(agent):
    a = agent.Agent("cost")
    step = a.receive(forge(init_doc(), "timeout>300", "timeout>-1"))
    e = error_of(step)
    assert (e["to"], e["what"], e["reason"]) == ("alice", "init", "invalid") and "timeout" in e["text"]
    assert a.status() is None
    step = a.receive(forge(init_doc(), "<agent>security</agent>", "<agent>cost</agent>"))
    assert "duplicate" in error_of(step)["text"]


def test_an_init_that_is_not_for_the_agent_or_is_damaged_gets_no_answer(agent):
    a = agent.Agent("other")
    assert a.receive(init_doc()).send == []
    damaged = init_doc().replace("pick one", "pick two")
    assert agent.Agent("cost").receive(damaged).send == []


def test_an_init_when_the_agent_is_in_a_run_gets_a_busy_error(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    e = error_of(a.receive(init_doc(run_id=OTHER)))
    assert (e["to"], e["run_id"], e["what"], e["reason"]) == ("alice", OTHER, "init", "busy")
    assert a.status()["run_id"] == RUN


# --- damaged messages -----------------------------------------------------------

def test_a_damaged_message_of_a_known_run_is_answered_with_a_check_error_naming_its_uuid(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    step = net.agents["cost"].receive(reply.replace("because", "becauze"))
    e = error_of(step)
    assert (e["from"], e["to"], e["what"], e["reason"], e["ref"], e["candidate"]) == (
        "cost", "security", "init-reply", "check", "55555555-5555-4555-8555-555555555555", None)
    assert net.agents["cost"].receive(reply).events[0]["event"] == "recorded"       # the intact copy is still processed
    score = W.build_score("security", RUN, [("seed-cost", 5, "r")], uid="66666666-6666-4666-8666-666666666666")
    assert error_of(net.agents["cost"].receive(score.replace("5.0", "6.0")))["what"] == "score"


# --- an error that asks for a stored message ----------------------------------------

def ask(net, to, what, reason="missing", **kw):
    err = W.build_error("security", RUN, to, what, reason, uid=kw.pop("uid", "44444444-4444-4444-8444-444444444444"), **kw)
    return net.agents[to].receive(err)


def test_an_error_for_an_init_reply_is_answered_with_the_stored_original_in_a_resend(agent):
    net = happy(agent)
    stored = net.agents["cost"].state["runs"][RUN]["sent"]["init-reply|cost"]
    step = ask(net, "cost", "init-reply")
    (text,) = step.send
    m = W.parse_message(text)
    assert (m["kind"], m["from"], m["reply_to"], m["inner"]) == ("resend", "cost", "44444444-4444-4444-8444-444444444444", stored)
    assert W.check_ok(m["inner"]) and W.peek(text)["attrs"]["uuid"] != W.peek(m["inner"])["attrs"]["uuid"]
    assert step.events[0]["event"] == "resent"


def test_a_score_is_resent_whole_whichever_candidate_the_error_names(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    for n in ("cost", "security", "legal"):
        net.answer_init(n)
    net.answer_score("cost", seed_security=70, seed_legal=60)
    stored = net.agents["cost"].state["runs"][RUN]["sent"]["score|cost|seed-legal"]
    for cid in ("seed-security", "seed-legal"):
        (text,) = ask(net, "cost", "score", candidate=cid, uid=uuids("e0e0e0e0")() if cid == "seed-security" else "e1e1e1e1-0000-4000-8000-000000000001").send
        assert W.parse_message(text)["inner"] == stored


def test_a_repair_and_the_result_are_resent_and_so_is_the_message_with_the_uuid_of_a_check_error(agent):
    net = happy(agent, cost_on_security=20, security_on_cost=30)
    net.broadcast(net.agents["security"].submit_repair(RUN, "better", "because", 70, "ok"), "security")
    net.answer_score("cost", r1_security=65)
    sec, cost = net.agents["security"], net.agents["cost"]
    stored = sec.state["runs"][RUN]["sent"]["repair|security|r1-security"]
    err = W.build_error("cost", RUN, "security", "repair", "missing", candidate="r1-security", uid="a1a1a1a1-0000-4000-8000-000000000001")
    assert W.parse_message(sec.receive(err).send[0])["inner"] == stored
    result = cost.state["runs"][RUN]["sent"]["result|cost"]
    err = W.build_error("security", RUN, "cost", "result", "missing", uid="a2a2a2a2-0000-4000-8000-000000000002")
    assert W.parse_message(cost.receive(err).send[0])["inner"] == result
    uid = W.peek(stored)["attrs"]["uuid"]
    err = W.build_error("cost", RUN, "security", "score", "check", ref=uid, uid="a3a3a3a3-0000-4000-8000-000000000003")
    assert W.parse_message(sec.receive(err).send[0])["inner"] == stored


def test_an_error_is_not_answered_when_it_is_for_someone_else_or_nothing_is_stored(agent):
    net = happy(agent)
    err = W.build_error("legal", RUN, "security", "init-reply", "missing", uid="b1b1b1b1-0000-4000-8000-000000000001")
    elsewhere = three(agent)
    assert elsewhere.agents["cost"].receive(err).events[0]["reason"] == "no answer expected"
    nothing = W.build_error("security", RUN, "cost", "score", "missing", candidate="r9-cost", uid="b2b2b2b2-0000-4000-8000-000000000002")
    step = net.agents["cost"].receive(nothing)
    assert step.send == [] and step.events[0]["event"] == "nothing to send"
    for reason, what, kw in (("busy", "init", {}), ("invalid", "init", {}), ("version", "init", {})):
        e = W.build_error("security", RUN, "cost", what, reason, uid=uuids("c0c0c0c0")(), **kw)
        assert net.agents["cost"].receive(e).send == []


def test_each_error_is_a_new_transmission_and_a_repeated_uuid_is_dropped(agent):
    net = happy(agent)
    first = ask(net, "cost", "init-reply")
    assert len(first.send) == 1
    assert ask(net, "cost", "init-reply").events[0]["reason"] == "duplicate"
    assert len(ask(net, "cost", "init-reply", uid="44444444-4444-4444-8444-444444444445").send) == 1


def test_a_lost_score_is_asked_for_by_hand_and_the_run_then_ends(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    cost, sec = net.agents["cost"], net.agents["security"]
    task = cost.due()[0]
    lost = cost.submit_score(RUN, [(c["id"], 70, "r") for c in task["candidates"]])        # never reaches security
    net.answer_score("security", seed_cost=75)
    assert sec.status()["state"] == "wait" and sec.status()["lacking"] == [("cost", "seed-security")]
    ask = W.build_error("security", RUN, "cost", "score", "missing", candidate="seed-security", uid="d1d1d1d1-0000-4000-8000-000000000001")
    net.log.append(ask)
    got = cost.receive(ask)
    assert sent_kinds(got) == ["resend"]
    net.broadcast(got, "cost")
    assert (sec.status()["outcome"], sec.status()["status"], cost.status()["status"]) == ("SUCCESS", "ended", "ended")
    assert lost.send[0] in [W.parse_message(t)["inner"] for t in got.send]


# --- resend wrappers --------------------------------------------------------------

def test_a_resend_is_recorded_when_nothing_is_held_for_the_item_and_ignored_otherwise(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    wrapper = W.build_resend("security", RUN, reply, uid="56565656-5656-4656-8656-565656565656")
    cost = net.agents["cost"]
    step = cost.receive(wrapper)
    assert [e["event"] for e in step.events] == ["recorded"] and cost.status()["anchors_in"] == ["cost", "security"]
    again = W.build_resend("security", RUN, reply, uid="57575757-5757-4757-8757-575757575757")
    assert cost.receive(again).events[0]["reason"] == "already recorded"
    assert cost.receive(wrapper).events[0]["reason"] == "duplicate"                    # the same wrapper


def test_the_inner_message_of_a_resend_is_checked_like_any_other(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    cost = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good", uid="55555555-5555-4555-8555-555555555555")
    damaged = W.build_resend("security", RUN, reply.replace("because", "becauze"))
    e = error_of(cost.receive(damaged))
    assert (e["what"], e["reason"], e["ref"], e["to"]) == ("init-reply", "check", "55555555-5555-4555-8555-555555555555", "security")
    old = W.build_resend("security", RUN, reply.replace('version="0.3.0"', 'version="0.2.0"'))
    step = cost.receive(old)
    assert step.send == [] and step.events[0]["reason"] == "version"
    bad = W.build_resend("security", RUN, forge(reply, "<raw>80.0</raw>", "<raw>eighty</raw>"))
    assert cost.receive(bad).events[0]["reason"] == "invalid"
    stranger = W.build_resend("legal", RUN, reply.replace('from="security"', 'from="legal"'))
    assert cost.receive(stranger).events[0]["reason"] == "sender is not in the run"
    assert cost.status()["anchors_in"] == []


def test_a_resend_must_carry_the_senders_own_message_of_the_same_run(agent):
    net = Net(agent, ["cost", "security", "legal"])
    net.start(init_doc(["cost", "security", "legal"]))
    cost = net.agents["cost"]
    reply = W.build_init_reply("security", RUN, 0, 100, "plan", "because", 80, "good")
    liar = W.build_resend("security", RUN, reply).replace('from="security" run_id', 'from="legal" run_id', 1)
    step = cost.receive(liar)
    assert step.events[0]["reason"] == "invalid" and "not the sender's own" in step.events[0]["detail"]
    elsewhere = W.build_resend("security", RUN, reply.replace(RUN, OTHER))
    assert cost.receive(elsewhere).events[0]["reason"] == "invalid"


def test_an_init_inside_a_resend_starts_a_run_the_agent_does_not_know(agent):
    a = agent.Agent("cost")
    step = a.receive(W.build_resend("alice", RUN, init_doc()))
    assert [e["event"] for e in step.events] == ["joined"] and a.due()[0]["kind"] == "init-reply"
    busy = agent.Agent("cost")
    busy.receive(init_doc(run_id=OTHER))
    assert error_of(busy.receive(W.build_resend("alice", RUN, init_doc())))["reason"] == "busy"


def test_a_participant_that_gets_the_init_again_in_a_resend_sends_its_stored_init_reply(agent):
    a = agent.Agent("cost", uuid_fn=uuids("cccccccc"))
    a.receive(init_doc())
    assert a.receive(W.build_resend("alice", RUN, init_doc())).send == []              # nothing to send yet
    sent = a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good").send[0]
    again = a.receive(W.build_resend("alice", RUN, init_doc(), uid="58585858-5858-4858-8858-585858585858"))
    (text,) = again.send
    m = W.parse_message(text)
    assert (m["kind"], m["reply_to"], m["inner"]) == ("resend", None, sent)
    bare = a.receive(init_doc(uid="59595959-5959-4959-8959-595959595959"))
    assert bare.send == [] and bare.events[0]["reason"] == "run already known"


# --- cancel -------------------------------------------------------------------------

def cancel(reason="missing-agent", frm="security", **kw):
    kw = {"agent": "legal" if reason == "missing-agent" else None, **kw}
    return W.build_cancel(frm, RUN, reason, uid=kw.pop("uid", "ca000000-0000-4000-8000-000000000001"), **kw)


def test_a_cancel_ends_the_run_aborted_and_the_agent_passes_it_on_once(agent):
    net = three(agent)
    a = net.agents["cost"]
    step = a.receive(cancel())
    assert [e["event"] for e in step.events] == ["aborted", "sent"]
    (text,) = step.send
    m = W.parse_message(text)
    assert (m["kind"], m["from"], m["reason"], m["agent"], m["superseded_by"]) == ("cancel", "cost", "missing-agent", "legal", None)
    assert W.peek(text)["attrs"].get("check") is None
    s = a.status()
    assert (s["status"], s["outcome"]) == ("aborted", "ABORTED") and a.due() == []
    again = a.receive(cancel(uid="ca000000-0000-4000-8000-000000000002", frm="legal"))
    assert again.send == [] and a.report()["status"] == "aborted"
    rep = a.report()
    assert (rep["outcome"], rep["winner"], rep["scores"], rep["reason"]) == (
        "ABORTED", None, {}, {"reason": "missing-agent", "agent": "legal", "superseded_by": None})


def test_each_reason_of_a_cancel_is_passed_on_with_its_attributes(agent):
    for reason, kw in (("version", {}), ("superseded", {"superseded_by": OTHER}), ("missing-agent", {"agent": "security"})):
        a = agent.Agent("cost")
        a.receive(init_doc())
        step = a.receive(cancel(reason, frm="alice", **kw))
        m = W.parse_message(step.send[0])
        assert (m["reason"], m["superseded_by"], m["agent"]) == (reason, kw.get("superseded_by"), kw.get("agent"))


def test_an_agent_that_is_aborted_sends_no_result_and_ignores_data_but_answers_errors(agent):
    net = Net(agent, ["cost", "security"])
    net.start()
    net.answer_init("cost")
    net.answer_init("security")
    cost = net.agents["cost"]
    cost.receive(cancel(frm="security", agent="security"))
    late = W.build_score("security", RUN, [("seed-cost", 75, "r")])
    assert cost.receive(late).events[0]["reason"] == "run over"
    assert cost.status()["outcome"] == "ABORTED" and cost.due() == []
    step = ask(net, "cost", "init-reply")
    assert sent_kinds(step) == ["resend"]
    with pytest.raises(agent.StateError, match="has aborted"):
        cost.submit_score(RUN, [("seed-security", 5, "w")])


def test_a_cancel_after_the_run_ended_keeps_the_outcome_and_disputes_the_report(agent):
    net = happy(agent)
    cost = net.agents["cost"]
    assert cost.report()["status"] == "confirmed"
    step = cost.receive(cancel(frm="security", agent="security"))
    assert [e["event"] for e in step.events] == ["disputed", "sent"] and sent_kinds(step) == ["cancel"]
    s, rep = cost.status(), cost.report()
    assert (s["status"], s["outcome"], rep["status"], rep["outcome"]) == ("ended", "SUCCESS", "disputed", "SUCCESS")
    assert cost.receive(cancel(uid="ca000000-0000-4000-8000-000000000009", frm="security", agent="security")).send == []


def test_a_cancel_that_is_not_valid_or_not_from_the_run_is_ignored(agent):
    net = three(agent)
    a = net.agents["cost"]
    assert a.receive(W.build_cancel("mallory", RUN, "version")).events[0]["reason"] == "sender is not in the run"
    forged = cancel(agent="legal").replace('agent="legal"', 'agent="mallory"')
    assert a.receive(forged).events[0]["reason"] == "invalid"
    assert a.receive(W.build_cancel("alice", OTHER, "version")).events[0]["reason"] == "unknown run"
    assert a.status()["status"] == "active"


def test_the_initiator_may_cancel(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    step = a.receive(W.build_cancel("alice", RUN, "version", uid="ca000000-0000-4000-8000-000000000001"))
    assert a.status()["status"] == "aborted" and sent_kinds(step) == ["cancel"]


def test_an_aborted_agent_can_join_another_run(agent):
    a = agent.Agent("cost")
    a.receive(init_doc())
    a.receive(W.build_cancel("alice", RUN, "version", uid="ca000000-0000-4000-8000-000000000001"))
    assert a.receive(init_doc(run_id=OTHER)).events[0]["event"] == "joined"


def test_the_state_of_an_aborted_run_survives_a_restart(agent):
    from test_agent import MemStore
    store = MemStore(agent)
    a = agent.Agent("cost", store=store)
    a.receive(init_doc())
    a.receive(W.build_cancel("alice", RUN, "version", uid="ca000000-0000-4000-8000-000000000001"))
    b = agent.Agent("cost", store=store)
    assert b.report()["status"] == "aborted" and b.report()["reason"]["reason"] == "version"
    assert b.receive(W.build_cancel("alice", RUN, "version", uid="ca000000-0000-4000-8000-000000000002")).send == []
