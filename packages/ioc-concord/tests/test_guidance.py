#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the model reads: the task texts, filled in from the state of a run, the messages of the others, the end, and a refused answer.

The texts are the reference wording of Appendix A of the specification. `test_the_wording_says_what_appendix_a_asks_for` keeps the two
together: each point of A.5 and A.6 is a phrase that must be in the text of its task.
"""
import re

import pytest
from test_agent import RUN, Net, happy, init_doc, uuids

from ioc_concord import guidance as G
from ioc_concord import wire as W


def flat(text):
    return " ".join(text.split())


def started(agent, **init):
    """cost and security, with the init delivered to both."""
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(**init))
    return net


def two_with_a_repair(agent, **init):
    """cost and security answer the init and rate each other low, so that one of them owes a repair: (net, owner, other)."""
    net = started(agent, **init)
    net.answer_init("cost", raw_reject=10, raw_ideal=90, candidate="Plan of cost")
    net.answer_init("security", raw_reject=10, raw_ideal=90, candidate="Plan of security")
    net.answer_score("cost", seed_security=30)
    net.answer_score("security", seed_cost=30)
    owner = next(n for n, a in net.agents.items() if any(t["kind"] == "repair" for t in a.due()))
    return net, owner, "cost" if owner == "security" else "security"


def task_of(agent_obj, kind):
    return next(t for t in agent_obj.due() if t["kind"] == kind)


# --- the first answer -------------------------------------------------------------------

def test_the_first_answer_names_the_run_and_has_its_three_parts(agent):
    a = started(agent).agents["security"]
    text = a.instructions(task_of(a, "init-reply"))
    assert text.startswith("You are security. The run was opened by alice; participants: cost, security.")
    assert "context: pick one" in text and "candidate format: a plan" in text
    assert re.search(r"x_reject:\s+nothing", text) and re.search(r"x_ideal:\s+all agree", text)
    assert [p for p in ("1. ANCHORS", "2. YOUR PROPOSAL", "3. YOUR OWN SCORE FOR IT") if p in text] == [
        "1. ANCHORS", "2. YOUR PROPOSAL", "3. YOUR OWN SCORE FOR IT"]


def test_the_first_answer_shows_the_parameters_of_the_run(agent):
    a = started(agent, params={"satisfaction_threshold": "0.7", "B": 4, "timeout": 30}).agents["security"]
    text = a.instructions(task_of(a, "init-reply"))
    assert "parameters: threshold 0.7, repair budget 4, eps 0.05, timeout 30 s, retries 3" in text


def test_a_closed_list_is_shown_as_the_options_to_choose_from(agent):
    a = started(agent, spec={"enum": ["West City", "East Town"]}).agents["cost"]
    text = a.instructions(task_of(a, "init-reply"))
    assert "candidate format: exactly one of: West City | East Town" in text
    assert "  - West City" in text and "  - East Town" in text and "must be exactly one of these" in text


# --- a score ----------------------------------------------------------------------------

def test_a_score_shows_the_anchors_of_the_agent_and_each_candidate_with_the_reasoning_of_its_proposer(agent):
    net = started(agent)
    net.answer_init("cost", raw_reject=10, raw_ideal=90)
    net.answer_init("security", raw_reject=20, raw_ideal=80)
    a = net.agents["cost"]
    text = a.instructions(task_of(a, "score"))
    assert "TASK: rate 1 candidate of the others" in text
    assert "raw_reject=10.0, raw_ideal=90.0" in text
    assert "candidate seed-security (proposed by security): plan of security" in text
    assert "its rationale: because" in text and "its proposer's reasoning for its own score: I like it" in text


def test_a_candidate_that_the_agent_has_already_rated_is_pointed_out_with_its_rating(agent):
    net, owner, other = two_with_a_repair(agent)
    a = net.agents[owner]
    t = task_of(a, "repair")
    net.broadcast(a.submit_repair(t["run_id"], f"plan of {other}", "again, with a new argument", 70, "new arguments"), owner)
    b = net.agents[other]
    text = b.instructions(task_of(b, "score"))
    assert f"candidate r1-{owner} (proposed by {owner}): plan of {other}" in text
    assert f"this is the same candidate as seed-{other}, which you rated 80.0" in text
    assert f"Same as seed-{other}, which I rated 80.0" in text


def test_a_candidate_that_is_new_has_no_such_hint(agent):
    net, owner, other = two_with_a_repair(agent)
    a = net.agents[owner]
    t = task_of(a, "repair")
    net.broadcast(a.submit_repair(t["run_id"], "a new plan", "a better plan", 70, "new arguments"), owner)
    b = net.agents[other]
    assert "the same candidate as" not in b.instructions(task_of(b, "score"))


# --- a repair ---------------------------------------------------------------------------

def test_a_repair_shows_the_incumbent_the_bar_the_floor_and_what_each_agent_said(agent):
    net, owner, other = two_with_a_repair(agent)
    a = net.agents[owner]
    t = task_of(a, "repair")
    text = a.instructions(t)
    assert "REPAIR ROUND, YOU ALONE" in text and f"Every rating of the incumbent {t['incumbent']['id']} is in" in text
    assert f"below the bar of {t['threshold']}" in text and "You have 8 repairs left, this one included." in text
    assert f"incumbent {t['incumbent']['id']}: {t['incumbent']['candidate']}" in text
    assert f"ratings of the incumbent (the bar is {t['threshold']}):" in text
    for who in ("cost", "security"):
        assert f"  - {who}: " in text
    assert "below the bar by" in text and "my view" in text                 # the reasoning of the other agent


def test_a_repair_lists_the_candidates_already_in_the_run_and_the_scale_to_rate_the_new_one_on(agent):
    net, owner, other = two_with_a_repair(agent)
    a = net.agents[owner]
    text = a.instructions(task_of(a, "repair"))
    assert "candidates in the run:" in text
    assert "  - seed-cost (proposed by cost): Plan of cost" in text and "  - seed-security (proposed by security): Plan of security" in text
    assert "your own scale, for rating your new candidate: raw_reject=10.0, raw_ideal=90.0" in text


def test_a_repair_shows_the_candidate_format_and_the_context_of_the_run(agent):
    net, owner, _ = two_with_a_repair(agent, spec={"enum": ["Plan of cost", "Plan of security"]})
    a = net.agents[owner]
    text = a.instructions(task_of(a, "repair"))
    assert "context: pick one" in text and "  - Plan of cost" in text and "  - Plan of security" in text


def test_instructions_refuse_a_task_of_a_run_the_agent_does_not_know_or_of_another_kind(agent):
    a = started(agent).agents["cost"]
    with pytest.raises(agent.StateError):
        a.instructions({"kind": "score", "run_id": "99999999-9999-4999-8999-999999999999"})
    with pytest.raises(ValueError):
        a.instructions({"kind": "nope", "run_id": RUN})


# --- the messages of the others -----------------------------------------------------------

def joined(agent):
    """cost and security, both in the run, and a reply of security that cost has not received yet: (cost, reply)."""
    cost = agent.Agent("cost", uuid_fn=uuids("c0c0c0c0"))
    sec = agent.Agent("security", uuid_fn=uuids("5e5e5e5e"))
    init = init_doc()
    cost.receive(init)
    sec.receive(init)
    return cost, sec.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "I like it").send[0]


def test_step_accepted_has_the_init_when_the_agent_joins_the_run(agent):
    init = init_doc()
    assert agent.Agent("cost").receive(init).accepted == [init]


def test_step_accepted_has_the_message_of_another_agent_that_the_agent_recorded(agent):
    cost, reply = joined(agent)
    assert cost.receive(reply).accepted == [reply]


def test_step_accepted_leaves_out_what_the_agent_did_not_take(agent):
    cost, reply = joined(agent)
    cost.receive(reply)
    own = cost.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "I like it").send[0]
    assert cost.receive(own).accepted == []                                    # its own message, heard on the channel
    assert cost.receive(reply).accepted == []                                  # a message it has already taken
    assert cost.receive(reply.replace('version="0.3.0"', 'version="0.2.0"')).accepted == []       # another version
    assert agent.Agent("legal").receive(W.build_score("security", RUN, [("seed-cost", 5, "r")])).accepted == []   # an unknown run
    assert cost.receive("not a CONCORD message").accepted == []


def test_the_delivery_is_the_messages_as_they_arrived_after_a_header_and_nothing_when_there_are_none(guidance):
    assert guidance.render_delivery([]) == ""
    init, other = init_doc(), W.build_score("security", RUN, [("seed-cost", 5, "r")])
    text = guidance.render_delivery([f"\n{init}\n", other])
    assert text.startswith(guidance.DELIVERY_HEADER + "\n\n<concord-init ")
    assert init.strip() in text and other.strip() in text and text.index(init.strip()) < text.index(other.strip())


def test_a_resend_is_shown_as_the_message_it_carries(guidance):
    inner = init_doc()
    wrapper = W.build_resend("alice", RUN, inner)
    assert guidance.unwrap(wrapper).strip() == inner.strip()
    assert guidance.unwrap(inner) == inner
    assert "concord-resend" not in guidance.render_delivery([wrapper])


# --- the end ----------------------------------------------------------------------------

def report(outcome="SUCCESS", **kw):
    r = {"run_id": RUN, "outcome": outcome, "winner": {"id": "seed-cost", "candidate": "plan of cost"},
         "scores": {"cost": 800_000, "security": 650_000}, "shortfall": {}, "rounds": 2, "reason": None,
         "results_compared": {"reporter": "cost", "result": {"outcome": outcome, "winning_candidate_id": "seed-cost"}, "matches": True},
         "mismatches": {"sent": 0, "received": []}, "status": "confirmed"}
    r.update(kw)
    return r


def test_the_end_tells_the_reporter_that_it_reported_and_the_others_that_the_reporter_did(guidance):
    reporter = guidance.render_end("cost", report())
    other = guidance.render_end("security", report())
    assert "You reported the result to all the participants: SUCCESS, winning candidate seed-cost (plan of cost)." in reporter
    assert "cost reported the result to all the participants: SUCCESS" in other
    assert "Final scores of the winning candidate, normalized 0-1: cost 0.8, security 0.65. Repair rounds used: 2." in other
    assert "Your own outcome matches the reporter's result." in other and "matches" not in reporter
    assert "Status of the run for you: confirmed." in other and "do not re-open the decision" in other


def test_the_end_names_who_fell_short_and_a_difference_with_the_reporter(guidance):
    text = guidance.render_end("security", report(outcome="BEST_EFFORT", shortfall={"security": 100_000},
                                                  results_compared={"reporter": "cost", "result": {}, "matches": False}))
    assert "BEST_EFFORT" in text and "Below the bar: security by 0.1." in text
    assert "Your own outcome DIFFERS from the reporter's result." in text and "naming the agents below their floor" in text


def test_the_end_of_a_disputed_run_says_it_is_not_to_be_relied_on_and_does_not_tell_the_model_to_proceed(guidance):
    text = guidance.render_end("security", report(status="disputed", mismatches={"sent": 1, "received": ["x"]},
                                                  results_compared={"reporter": "cost", "result": {}, "matches": False}))
    assert "Status of the run for you: disputed." in text and "This run is DISPUTED (mismatch errors sent: 1, received: 1)" in text
    assert "no result of a disputed run is to be relied on: do not act on this outcome" in text
    assert "proceed with the winning candidate" not in text and "do not re-open the decision" in text
    assert "proceed with the winning candidate" in guidance.render_end("security", report())               # a confirmed run still does


def test_the_end_tells_an_initiator_that_cannot_check_the_outcome_that_it_is_the_reporters(guidance):
    text = guidance.render_end("alice", report(status="unverified", results_compared={
        "reporter": "cost", "result": {"outcome": "SUCCESS", "winning_candidate_id": "seed-cost"}, "matches": None}))
    assert "You have no outcome of your own to compare it with: this is the result of the reporter." in text
    assert "DISPUTED" not in text and "proceed with the winning candidate" in text


def test_the_end_of_an_aborted_run_certifies_nothing(guidance):
    text = guidance.render_end("cost", report(outcome="ABORTED", winner=None, scores={}, rounds=None,
                                              reason={"reason": "missing-agent", "agent": "legal"}))
    assert "The run was aborted (missing-agent, agent legal)." in text and "nothing is certified" in text and "winning" not in text


def test_an_initiator_that_is_not_a_participant_learns_the_outcome_from_the_result(guidance):
    text = guidance.render_end("alice", report(outcome=None, scores={}, rounds=None, status="unverified",
                                               results_compared={"reporter": "cost", "matches": None,
                                                                 "result": {"outcome": "SUCCESS", "winning_candidate_id": "seed-cost"}}))
    assert "cost reported the result to all the participants: SUCCESS, winning candidate seed-cost" in text
    assert "Final scores" not in text and "matches" not in text and "Status of the run for you: unverified." in text
    assert "not over" in guidance.render_end("alice", report(outcome=None, winner=None, scores={}, rounds=None,
                                                             results_compared={"reporter": "cost", "result": None, "matches": None}))


def test_the_end_of_a_real_run_comes_from_its_report(agent):
    net = happy(agent)
    text = G.render_end("security", net.agents["security"].report())
    assert "The run is over. cost reported the result" in text and "Status of the run for you: confirmed." in text
    assert "You reported" in G.render_end("cost", net.agents["cost"].report())


# --- the checks that go beyond the specification -------------------------------------------

GOOD = {"raw_reject": 10, "raw_ideal": 90, "candidate": "plan", "raw": 70, "rationale": "the facts " * 4, "reasoning": "because " * 4}


def test_an_answer_with_reasons_and_a_raw_score_has_no_problem(guidance):
    assert guidance.check_answer("init-reply", GOOD) == []
    assert guidance.check_answer("repair", GOOD, {"raw_reject": 10, "raw_ideal": 90}) == []
    scores = {"scores": [{"id": "seed-cost", "raw": 70, "reasoning": "because " * 4}]}
    assert guidance.check_answer("score", scores, {"raw_reject": 10, "raw_ideal": 90}) == []


def test_a_reasoning_that_is_too_short_to_be_one_is_named(guidance):
    (p,) = guidance.check_answer("init-reply", {**GOOD, "reasoning": "ok"})
    assert p.startswith("the reasoning is too short") and '"too expensive" is not a reason' in p
    assert guidance.check_answer("repair", {**GOOD, "rationale": ""})[0].startswith("the rationale is too short")
    (p,) = guidance.check_answer("score", {"scores": [{"id": "r1-cost", "raw": 50, "reasoning": "no"}]}, {"raw_ideal": 90})
    assert p.startswith("the reasoning for r1-cost is too short")


def test_a_number_of_zero_to_one_sent_as_a_raw_score_is_taken_for_a_normalised_one(guidance):
    (p,) = guidance.check_answer("init-reply", {**GOOD, "raw": 0.8})
    assert "your score is 0.8: it looks like a normalised 0-1 score" in p
    (p,) = guidance.check_answer("score", {"scores": [{"id": "seed-cost", "raw": 0.5, "reasoning": "because " * 4}]},
                                 {"raw_reject": 10, "raw_ideal": 90})
    assert "your rating of seed-cost is 0.5" in p
    assert guidance.check_answer("score", {"scores": [{"id": "seed-cost", "raw": 0.5, "reasoning": "because " * 4}]}, None) == []


def test_a_raw_score_of_zero_or_one_is_a_raw_score(guidance):
    for raw in (0, 1, 0.0, 1.0):
        assert guidance.check_answer("init-reply", {**GOOD, "raw": raw}) == []
        assert guidance.check_answer("repair", {**GOOD, "raw": raw}, {"raw_reject": 10, "raw_ideal": 90}) == []
        assert guidance.check_answer("score", {"scores": [{"id": "seed-cost", "raw": raw, "reasoning": "because " * 4}]},
                                     {"raw_reject": 10, "raw_ideal": 90}) == []
    assert guidance.check_answer("init-reply", {**GOOD, "raw": 0.5})                    # between them it is not: a normalised score


def test_anchors_of_one_or_less_are_taken_for_normalised_ones(guidance):
    (p,) = guidance.check_answer("init-reply", {**GOOD, "raw_reject": 0, "raw_ideal": 1, "raw": 1})
    assert p.startswith("your anchors are 0 and 1: they look like normalised 0-1 scores")
    assert guidance.check_answer("init-reply", {**GOOD, "raw_reject": 0, "raw_ideal": 100, "raw": 100}) == []


def test_a_rating_beyond_an_anchor_is_not_refused_since_the_specification_clamps_it(guidance):
    assert guidance.check_answer("score", {"scores": [{"id": "seed-cost", "raw": 99, "reasoning": "because " * 4}]},
                                 {"raw_reject": 10, "raw_ideal": 90}) == []
    assert guidance.check_answer("init-reply", {**GOOD, "raw": 3}) == []


# --- a refused answer ---------------------------------------------------------------------

HINTS = [
    ("raw_ideal (20.0) must be strictly greater than raw_reject (90.0). These are satisfaction ratings", "Rate the two outcomes again"),
    ("a score message covers exactly the candidates not rated yet: owed seed-a; given seed-b", "Rate exactly the candidates the task lists"),
    ("the candidate must be exactly one of the listed options (A; B)", "put any extra detail in the rationale"),
    ("the score for seed-a must be a number from 0 to 100 with at most 6 decimal places, not 'x'", "number from 0 to 100"),
    ("your answer lacks `raw` (the score of your proposal).", "every required field"),
    ("`scores` must be a list of entries (id, raw, reasoning), not text.", "separate entries"),
    (G.UNEXPECTED.format(kind="KeyError", detail="'raw'"), "Answer once more"),
    ("something nobody foresaw", "Fix what the message says and answer again"),
]


@pytest.mark.parametrize("fault, hint", HINTS)
def test_every_refusal_says_what_to_do_and_not_only_what_is_wrong(guidance, fault, hint):
    text = guidance.refusal(fault)
    assert text.startswith("refused: " + fault.strip().rstrip(".")) and "Nothing was sent.\nNEXT: " in text
    assert hint in text and ".." not in text


def coded(code, text):
    """An error of the library, as the guidance sees it: a message and a `code` (the same in the copy of a script)."""
    e = Exception(text)
    e.code = code
    return e


@pytest.mark.parametrize("fault", ["submit_init_reply: this agent has already sent its init reply", "submit_score: this agent owes no score now",
                                   "submit_repair: no repair is owed from this agent now", "the agent knows no run 'x'"])
def test_an_answer_that_is_already_given_is_settled_and_is_not_to_be_retried(guidance, fault):
    for code in ("state_error", "missing_state"):
        error = coded(code, fault)
        assert guidance.settled(error)
        text = guidance.refusal(error, repeats=5)
        assert "already answered, or not yours to answer" in text and "refused the same way" not in text


def test_the_text_of_a_refusal_alone_is_never_settled_and_the_words_of_an_answer_cannot_make_it_so(guidance):
    assert not guidance.settled("submit_init_reply: this agent has already sent its init reply")        # a string says nothing of its type
    quoting = coded("invalid_message", "the candidate must be exactly one of the listed options (the plan has ended; B)")
    assert not guidance.settled(quoting)
    text = guidance.refusal(quoting, repeats=2)
    assert "put any extra detail in the rationale" in text and "refused the same way 2 times" in text


def test_the_same_refusal_twice_in_a_row_tells_the_model_to_change_the_answer(guidance):
    fault = "your answer lacks `raw` (the score of your proposal)."
    assert "refused the same way" not in guidance.refusal(fault)
    assert "You have been refused the same way 3 times in a row" in guidance.refusal(fault, repeats=3)


def test_what_an_answer_sent_is_shown_next_to_what_was_expected(guidance):
    text = guidance.lacks(["`raw` (the score)"], sent=["candidate", "rationale"], takes=["candidate", "rationale", "raw"])
    assert text == "your answer lacks `raw` (the score). You sent: candidate, rationale. Expected: candidate, rationale, raw."
    assert guidance.lacks(["`raw`"]) == "your answer lacks `raw`."
    assert guidance.wrong_shape("scores", "a list", "text") == "`scores` must be a list, not text."
    assert [guidance.wrong_shape("f", "x", v).split("not ")[1] for v in (3, 1.5, ["a"], {"a": 1}, True)] == [
        "a number.", "a number.", "a list.", "an object.", "true or false."]


def refused(call):
    """The error of the library that `call` raises (the error itself: its code is what the guidance reads)."""
    with pytest.raises(Exception) as e:
        call()
    return e.value


def test_the_hints_fit_the_errors_the_library_really_raises(agent):
    net = Net(agent, ["cost", "security"])
    net.start(init_doc(spec={"enum": ["A", "B"]}))
    a, run = net.agents["cost"], RUN
    assert G.hint_for(refused(lambda: a.submit_init_reply(run, 90, 20, "A", "r", 50, "r"))) == G.HINT_ANCHORS
    assert G.hint_for(refused(lambda: a.submit_init_reply(run, 0, 100, "somewhere else", "r", 50, "r"))) == G.HINT_FORMAT
    assert G.hint_for(refused(lambda: a.submit_init_reply(run, 0, 100, "A", "r", 500, "r"))) == G.HINT_NUMBER
    net.answer_init("cost", candidate="A")
    net.answer_init("security", candidate="B")
    assert G.hint_for(refused(lambda: a.submit_init_reply(run, 0, 100, "A", "r", 50, "r"))) == G.HINT_ALREADY
    assert G.hint_for(refused(lambda: a.submit_score(run, [("seed-cost", 50, "r")]))) == G.HINT_IDS
    assert G.hint_for(refused(lambda: a.submit_score(run, [("seed-security", 50, "r"), ("seed-security", 60, "r")]))) == G.HINT_IDS


# --- what the wording must say: Appendix A of the specification -----------------------------------

CONTRACT = [
    # (the point of the appendix, the text it belongs to, a phrase that must be in it)
    ("A.5 first answer: the participants and the parameters of the init", "first", r"participants: cost, security.*parameters: threshold 0\.6, repair budget 8"),
    ("A.5 first answer: the anchors are the satisfaction of the role, not today's state", "first", r"not how true it is today"),
    ("A.5 first answer: low for the reject outcome, strictly higher for the ideal one", "first", r"LOW number, x_ideal a HIGH one strictly above it"),
    ("A.5 first answer: raw, never normalized", "first", r"never a normalised 0-1 score"),
    ("A.5 first answer: fixed for the run", "first", r"stay fixed for the whole run"),
    ("A.5 first answer: one candidate, not a compromise", "first", r"do not pre-compromise"),
    ("A.5 first answer: evidence against the others, only where it has any", "first", r"only where you have evidence against them"),
    ("A.5 first answer: it rates its own proposal on the same scale", "first", r"on the same raw scale"),
    ("A.5 first answer: what it writes is read by every agent, what it leaves out is invisible", "first",
     r"invisible to every other participant.*read by every participant"),
    ("A.5 score: the candidates listed, none of its own", "score", r"rate \d+ candidates? of the others.*Rate each candidate listed"),
    ("A.5 score: the raw scale of its anchors for the whole run", "score", r"the same scale as your anchors .* for the whole run"),
    ("A.5 score: concrete reasons, evidence against included", "score", r"concrete reasons for each number .* both for and against it"),
    ("A.5 score: a number changes only for a named fact, and the reasoning says which and from whom", "score",
     r"Move a number only for a real change \(a specific fact .* what changed your mind and who shared it"),
    ("A.5 score: it does not refuse to score", "score", r"Never refuse to score"),
    ("A.5 repair: the bottleneck alone", "repair", r"YOU ALONE"),
    ("A.5 repair: the candidates already in the pool", "repair", r"candidates in the run:.*seed-cost \(proposed by cost\)"),
    ("A.5 repair: the threshold, the floor and the shortfall", "repair", r"below the bar of .*ratings of the incumbent \(the bar is"),
    ("A.5 repair: it does not push another participant below the threshold", "repair", r"does not knowingly push any other agent below the threshold"),
    ("A.5 repair: it answers the strongest argument against its position", "repair", r"answers the strongest reasoning against your position"),
    ("A.5 repair: it may re-propose a candidate of the pool, with new arguments", "repair", r"your own earlier proposal if you hold it is right .* new arguments"),
    ("A.5 repair: if none exists, the best one and the conflict", "repair", r"state the conflict plainly"),
    ("A.5 repair: the normalized numbers are never sent as raw", "repair", r"never echo one back as your own rating"),
    ("A.5 repair: the threshold and the anchors are not changed", "repair", r"Do not lower the threshold"),
    ("A.5 end: the decision is not reopened", "end", r"do not re-open the decision"),
    ("A.5 end: on BEST_EFFORT, who fell short", "end", r"BEST_EFFORT, say so when you report, naming the agents below their floor"),
    ("A.6 1: what it holds is a fact, a report of the others is evidence, not set aside because it cannot be checked", "score",
     r"What you hold is a fact\. .* do not set it aside only because you cannot check it"),
    ("A.6 2: evidence is weighed, a specific report counts for more, conflicts are said and resolved", "score",
     r"a specific report counts for more than a vague one, and if a report contradicts .* state which you rely on and why"),
    ("A.6 3: another agent's reading is not evidence against its own facts", "score", r"never because someone offers another reading of them"),
    ("A.6 4: nothing is invented", "score", r"Do not speculate, and do not assume a fix, a delay or a way around .* do not invent a cause that reconciles them"),
    ("A.6 5: the evidence is stated before the number, and the number fits", "score", r"state what the evidence implies for it, in its own words, then give the number, and check that it matches"),
    ("A.6 6: the number of another agent and the wording of a candidate are not reasons", "score", r"other agents' numbers, are not reasons"),
    ("A.6 7: ratings are honest and the criteria are not softened", "score", r"never inflate to seem agreeable or deflate for leverage, and never soften your criteria"),
    ("A.6 8: what is not written is invisible", "score", r"what you leave out is invisible to the rest of the run"),
    ("A.3 the points on evidence are part of the standing context", "run",
     r"How to weigh evidence:.*What you hold is a fact\..*never because someone offers another reading of them.*other agents' numbers, are not reasons"),
    ("A.3 the run: the four steps and what the model receives and sends", "run", r"1\. First answer.*2\. Score.*3\. Repair.*4\. Result.*What you receive.*What you send"),
    ("A.3 the initiator: every field of the init, the order of the participants and the genuine bar", "start",
     r"participants:.*first one reports the outcome.*satisfaction_threshold:.*not a level you are sure is reachable.*x_reject:.*never the means.*x_ideal:.*context:"),
]


def test_the_wording_says_what_appendix_a_asks_for(agent):
    first = started(agent)
    scoring = started(agent)
    scoring.answer_init("cost")
    scoring.answer_init("security")
    repairing, owner, _ = two_with_a_repair(agent)
    texts = {"first": first.agents["cost"].instructions(task_of(first.agents["cost"], "init-reply")),
             "score": scoring.agents["cost"].instructions(task_of(scoring.agents["cost"], "score")),
             "repair": repairing.agents[owner].instructions(task_of(repairing.agents[owner], "repair")),
             "end": G.render_end("security", happy(agent).agents["security"].report()),
             "run": G.RUN_DESCRIPTION, "start": G.INITIATOR_GUIDANCE}
    missing = [point for point, key, phrase in CONTRACT if not re.search(phrase, flat(texts[key]))]
    assert not missing, "the wording no longer says: " + "; ".join(missing)


def test_the_guidance_for_the_initiator_has_a_line_for_each_field_it_fills(guidance):
    for field in guidance.START_PARAMS:
        assert f"\n- {field}: " in guidance.INITIATOR_GUIDANCE
    assert guidance.INITIATOR_GUIDANCE.startswith("## Starting the run: you are the initiator")
