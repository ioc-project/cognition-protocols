//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the node answers: errors for an init it cannot join, the stored original for an error that asks for it, resend wrappers, and
//! the cancel.

mod agent_net;
mod common;

use agent_net::*;
use ioc_concord::agent::{Agent, Step};
use ioc_concord::protocol::Outcome;
use ioc_concord::report::ReportStatus;
use ioc_concord::state::{Abort, RunStatus};
use ioc_concord::store::MemoryStore;
use ioc_concord::tasks::Task;
use ioc_concord::wire::{build_cancel, build_error, build_init_reply, build_resend, build_score, check_ok, peek, Kind, ScoreIn};
use ioc_concord::ConcordError;

const U55: &str = "55555555-5555-4555-8555-555555555555";

fn agent(me: &str) -> Agent {
    Agent::new(me).unwrap()
}

fn rcv(a: &mut Agent, text: &str) -> Step {
    a.receive(text, None).unwrap()
}

fn reason(step: &Step) -> Option<&str> {
    step.events[0].str("reason")
}

fn v2(doc: &str) -> String {
    doc.replace("version=\"0.3.0\"", "version=\"0.2.0\"")
}

fn score_doc(from: &str, id: &str, raw: i32, uid: Option<&str>) -> String {
    build_score(from, RUN, &[ScoreIn::new(id, raw, "r")], uid).unwrap()
}

/// The init reply of `from` that the tests use: anchors 0 and 100, the score 80.
fn reply_doc(from: &str, uid: Option<&str>) -> String {
    build_init_reply(from, RUN, &0.into(), &100.into(), "plan", "because", &80.into(), "good", None, uid).unwrap()
}

fn error_doc(from: &str, to: &str, what: &str, reason: &str, candidate: Option<&str>, reference: Option<&str>, uid: &str) -> String {
    build_error(from, RUN, to, what, reason, candidate, reference, "", Some(uid)).unwrap()
}

fn resend_doc(from: &str, original: &str, uid: Option<&str>) -> String {
    build_resend(from, RUN, original, None, uid).unwrap()
}

fn three() -> Net {
    let mut net = Net::new(&["cost", "security", "legal"]);
    net.start(&InitDoc::new().names(&["cost", "security", "legal"]).build());
    net
}

// --- an init that the agent does not join ---------------------------------------

#[test]
fn an_init_of_another_version_gets_a_version_error_that_names_both_versions() {
    let mut a = agent("cost");
    let step = rcv(&mut a, &v2(&init_doc()));
    let (m, e) = error_of(&step);
    assert_eq!(
        (m.from.as_str(), e.to.as_str(), m.run_id.as_str(), e.what.as_str(), e.reason.as_str()),
        ("cost", "alice", RUN, "init", "version")
    );
    assert!(e.text.contains("0.3.0") && e.text.contains("0.2.0") && a.status(None).is_none());
    assert!(!peek(&step.send[0]).unwrap().attrs.contains_key("check"));
}

#[test]
fn an_init_of_another_version_inside_a_resend_is_refused_the_same_way() {
    let mut a = agent("cost");
    let wrapper = resend_doc("alice", &v2(&init_doc()), None);
    let (_, e) = error_of(&rcv(&mut a, &wrapper));
    assert_eq!((e.to.as_str(), e.what.as_str(), e.reason.as_str()), ("alice", "init", "version"));
    let wrapped_wrong =
        resend_doc("alice", &init_doc(), None).replace("<concord-resend version=\"0.3.0\"", "<concord-resend version=\"0.2.0\"");
    assert_eq!(error_of(&rcv(&mut a, &wrapped_wrong)).1.reason, "version");
    assert!(a.status(None).is_none());
}

#[test]
fn other_messages_of_another_version_get_no_answer() {
    let mut net = happy();
    let old = v2(&score_doc("security", "seed-cost", 5, None));
    assert!(rcv(&mut net.agents["cost"], &old).send.is_empty());
    let mut other = agent("cost");
    assert!(rcv(&mut other, &old).send.is_empty()); // an unknown run is not answered either
}

#[test]
fn an_invalid_init_gets_an_invalid_error_with_the_reason_and_is_not_joined() {
    let mut a = agent("cost");
    let step = rcv(&mut a, &forge(&init_doc(), "timeout>300", "timeout>-1"));
    let (_, e) = error_of(&step);
    assert_eq!((e.to.as_str(), e.what.as_str(), e.reason.as_str()), ("alice", "init", "invalid"));
    assert!(e.text.contains("timeout"));
    assert!(a.status(None).is_none());
    let step = rcv(&mut a, &forge(&init_doc(), "<agent>security</agent>", "<agent>cost</agent>"));
    assert!(error_of(&step).1.text.contains("duplicate"));
}

#[test]
fn an_init_that_is_not_for_the_agent_or_is_damaged_gets_no_answer() {
    let mut a = agent("other");
    assert!(rcv(&mut a, &init_doc()).send.is_empty());
    let damaged = init_doc().replace("pick one", "pick two");
    assert!(rcv(&mut agent("cost"), &damaged).send.is_empty());
}

#[test]
fn an_init_when_the_agent_is_in_a_run_gets_a_busy_error() {
    let mut a = agent("cost");
    let _ = rcv(&mut a, &init_doc());
    let (m, e) = error_of(&rcv(&mut a, &InitDoc::new().run(OTHER).build()));
    assert_eq!((e.to.as_str(), m.run_id.as_str(), e.what.as_str(), e.reason.as_str()), ("alice", OTHER, "init", "busy"));
    assert_eq!(a.status(None).unwrap().run_id, RUN);
}

// --- damaged messages -----------------------------------------------------------

#[test]
fn a_damaged_message_of_a_known_run_is_answered_with_a_check_error_naming_its_uuid() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    let reply = reply_doc("security", Some(U55));
    let step = rcv(&mut net.agents["cost"], &reply.replace("because", "becauze"));
    let (m, e) = error_of(&step);
    assert_eq!(
        (m.from.as_str(), e.to.as_str(), e.what.as_str(), e.reason.as_str(), e.reference.as_deref(), e.candidate.as_deref()),
        ("cost", "security", "init-reply", "check", Some(U55), None)
    );
    assert_eq!(rcv(&mut net.agents["cost"], &reply).events[0].event, "recorded"); // the intact copy is still processed
    net.answer_init("cost"); // now the scores of security are owed
    let score = score_doc("security", "seed-cost", 5, Some("66666666-6666-4666-8666-666666666666"));
    assert_eq!(error_of(&rcv(&mut net.agents["cost"], &score.replace("5.0", "6.0"))).1.what, "score");
}

#[test]
fn a_damaged_message_that_is_not_owed_yet_is_not_asked_for_and_a_stranger_is_never_asked() {
    let mut net = three();
    let cost = &mut net.agents["cost"];
    let early = score_doc("security", "seed-cost", 5, Some("66666666-6666-4666-8666-666666666666"));
    let step = rcv(cost, &early.replace("5.0", "6.0")); // no score is owed before every init reply
    assert!(step.send.is_empty() && reason(&step) == Some("check"));
    let inner = build_init_reply("mallory", RUN, &0.into(), &100.into(), "p", "r", &5.into(), "w", None, None).unwrap();
    let wrapper = resend_doc("mallory", &inner.replace("<raw>5.0", "<raw>6.0"), None);
    assert_eq!(reason(&rcv(cost, &wrapper)), Some("sender is not in the run"));
    let stolen = resend_doc("mallory", &inner, None).replacen("from=\"mallory\"", "from=\"security\"", 1);
    let step = rcv(cost, &stolen); // a member's wrapper around a stranger's message
    assert!(step.send.is_empty() && reason(&step) == Some("invalid"));
}

// --- an error that asks for a stored message ----------------------------------------

fn ask_with(net: &mut Net, to: &str, what: &str, candidate: Option<&str>, uid: &str) -> Step {
    let err = error_doc("security", to, what, "missing", candidate, None, uid);
    rcv(&mut net.agents[to], &err)
}

fn ask(net: &mut Net, to: &str, what: &str) -> Step {
    ask_with(net, to, what, None, U44)
}

#[test]
fn an_error_for_an_init_reply_is_answered_with_the_stored_original_in_a_resend() {
    let mut net = happy();
    let stored = net.agents["cost"].state.runs[RUN].sent["init-reply|cost"].clone();
    let step = ask(&mut net, "cost", "init-reply");
    assert_eq!(step.send.len(), 1);
    let m = msg(&step.send[0]);
    let r = resend_body(&m);
    assert_eq!((m.kind(), m.from.as_str(), r.reply_to.as_deref(), &r.inner), (Kind::Resend, "cost", Some(U44), &stored));
    assert!(check_ok(&r.inner) && peek(&step.send[0]).unwrap().attrs["uuid"] != peek(&r.inner).unwrap().attrs["uuid"]);
    assert_eq!(step.events[0].event, "resent");
}

#[test]
fn a_score_is_resent_whole_whichever_candidate_the_error_names() {
    let mut net = three();
    for n in ["cost", "security", "legal"] {
        net.answer_init(n);
    }
    net.answer_score("cost", &[("seed-security", 70), ("seed-legal", 60)]);
    let stored = net.agents["cost"].state.runs[RUN].sent["score|cost|seed-legal"].clone();
    for (cid, uid) in [("seed-security", "e0e0e0e0-0000-4000-8000-000000000001"), ("seed-legal", "e1e1e1e1-0000-4000-8000-000000000001")] {
        let step = ask_with(&mut net, "cost", "score", Some(cid), uid);
        assert_eq!(step.send.len(), 1);
        assert_eq!(resend_body(&msg(&step.send[0])).inner, stored);
    }
}

#[test]
fn a_repair_and_the_result_are_resent_and_so_is_the_message_with_the_uuid_of_a_check_error() {
    let mut net = happy_with(20, 30);
    let step = net.agents["security"].submit_repair(RUN, "better", "because", 70, "ok", None).unwrap();
    net.broadcast(&step);
    net.answer_score("cost", &[("r1-security", 65)]);
    let stored = net.agents["security"].state.runs[RUN].sent["repair|security|r1-security"].clone();
    let err = error_doc("cost", "security", "repair", "missing", Some("r1-security"), None, "a1a1a1a1-0000-4000-8000-000000000001");
    let step = rcv(&mut net.agents["security"], &err);
    assert_eq!(resend_body(&msg(&step.send[0])).inner, stored);
    let result = net.agents["cost"].state.runs[RUN].sent["result|cost"].clone();
    let err = error_doc("security", "cost", "result", "missing", None, None, "a2a2a2a2-0000-4000-8000-000000000002");
    let step = rcv(&mut net.agents["cost"], &err);
    assert_eq!(resend_body(&msg(&step.send[0])).inner, result);
    let uid = peek(&stored).unwrap().attrs["uuid"].clone();
    let err = error_doc("cost", "security", "score", "check", None, Some(&uid), "a3a3a3a3-0000-4000-8000-000000000003");
    let step = rcv(&mut net.agents["security"], &err);
    assert_eq!(resend_body(&msg(&step.send[0])).inner, stored);
}

#[test]
fn an_error_is_not_answered_when_it_is_for_someone_else_or_nothing_is_stored() {
    let mut net = happy();
    let err = error_doc("legal", "security", "init-reply", "missing", None, None, "b1b1b1b1-0000-4000-8000-000000000001");
    let mut elsewhere = three();
    assert_eq!(reason(&rcv(&mut elsewhere.agents["cost"], &err)), Some("no answer expected"));
    let nothing = error_doc("security", "cost", "score", "missing", Some("r9-cost"), None, "b2b2b2b2-0000-4000-8000-000000000002");
    let step = rcv(&mut net.agents["cost"], &nothing);
    assert!(step.send.is_empty() && step.events[0].event == "nothing to send");
    for reason in ["busy", "invalid", "version"] {
        let e = error_doc("security", "cost", "init", reason, None, None, &common::uuids("c0c0c0c0")());
        assert!(rcv(&mut net.agents["cost"], &e).send.is_empty());
    }
}

#[test]
fn each_error_is_a_new_transmission_and_a_repeated_uuid_is_dropped() {
    let mut net = happy();
    let first = ask(&mut net, "cost", "init-reply");
    assert_eq!(first.send.len(), 1);
    assert_eq!(reason(&ask(&mut net, "cost", "init-reply")), Some("duplicate"));
    assert_eq!(ask_with(&mut net, "cost", "init-reply", None, "44444444-4444-4444-8444-444444444445").send.len(), 1);
}

#[test]
fn a_lost_score_is_asked_for_by_hand_and_the_run_then_ends() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    let Some(Task::Score(task)) = net.agents["cost"].due().into_iter().next() else { panic!("a score is owed") };
    let scores: Vec<ScoreIn> = task.candidates.iter().map(|c| ScoreIn::new(&c.id, 70, "r")).collect();
    let lost = net.agents["cost"].submit_score(RUN, &scores, None).unwrap(); // never reaches security
    net.answer_score("security", &[("seed-cost", 75)]);
    let s = net.agents["security"].status(None).unwrap();
    assert_eq!(s.state, ioc_concord::engine::EvalState::Wait);
    assert_eq!(s.lacking, vec![("cost".to_string(), "seed-security".to_string())]);
    let ask = error_doc("security", "cost", "score", "missing", Some("seed-security"), None, "d1d1d1d1-0000-4000-8000-000000000001");
    net.log.push(ask.clone());
    let got = rcv(&mut net.agents["cost"], &ask);
    assert_eq!(kinds(&got), vec![Kind::Resend]);
    net.broadcast(&got);
    let (sec, cost) = (net.agents["security"].status(None).unwrap(), net.agents["cost"].status(None).unwrap());
    assert_eq!((sec.outcome, sec.status, cost.status), (Some(Outcome::Success), RunStatus::Ended, RunStatus::Ended));
    assert!(got.send.iter().map(|t| resend_body(&msg(t)).inner.clone()).any(|i| i == lost.send[0]));
}

// --- resend wrappers --------------------------------------------------------------

#[test]
fn a_resend_is_recorded_when_nothing_is_held_for_the_item_and_ignored_otherwise() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    net.answer_init("cost");
    let reply = reply_doc("security", Some(U55));
    let wrapper = resend_doc("security", &reply, Some("56565656-5656-4656-8656-565656565656"));
    let cost = &mut net.agents["cost"];
    let step = rcv(cost, &wrapper);
    assert_eq!(names(&step), vec!["recorded"]);
    assert_eq!(cost.status(None).unwrap().anchors_in, vec!["cost", "security"]);
    let again = resend_doc("security", &reply, Some("57575757-5757-4757-8757-575757575757"));
    assert_eq!(reason(&rcv(cost, &again)), Some("already recorded"));
    assert_eq!(reason(&rcv(cost, &wrapper)), Some("duplicate")); // the same wrapper
}

#[test]
fn the_inner_message_of_a_resend_is_checked_like_any_other() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    let cost = &mut net.agents["cost"];
    let reply = reply_doc("security", Some(U55));
    let damaged = resend_doc("security", &reply.replace("because", "becauze"), None);
    let (_, e) = error_of(&rcv(cost, &damaged));
    assert_eq!((e.what.as_str(), e.reason.as_str(), e.reference.as_deref(), e.to.as_str()), ("init-reply", "check", Some(U55), "security"));
    let old = resend_doc("security", &v2(&reply), None);
    let step = rcv(cost, &old);
    assert!(step.send.is_empty() && reason(&step) == Some("version"));
    let bad = resend_doc("security", &forge(&reply, "<raw>80.0</raw>", "<raw>eighty</raw>"), None);
    assert_eq!(reason(&rcv(cost, &bad)), Some("invalid"));
    let stranger = resend_doc("legal", &reply.replace("from=\"security\"", "from=\"legal\""), None);
    assert_eq!(reason(&rcv(cost, &stranger)), Some("sender is not in the run"));
    assert!(cost.status(None).unwrap().anchors_in.is_empty());
}

#[test]
fn a_resend_must_carry_the_senders_own_message_of_the_same_run() {
    let mut net = three();
    let cost = &mut net.agents["cost"];
    let reply = reply_doc("security", None);
    let liar = resend_doc("security", &reply, None).replacen("from=\"security\" run_id", "from=\"legal\" run_id", 1);
    let step = rcv(cost, &liar);
    assert_eq!(reason(&step), Some("invalid"));
    assert!(step.events[0].str("detail").unwrap().contains("not the sender's own"));
    let elsewhere = resend_doc("security", &reply.replace(RUN, OTHER), None);
    assert_eq!(reason(&rcv(cost, &elsewhere)), Some("invalid"));
}

#[test]
fn an_init_inside_a_resend_starts_a_run_the_agent_does_not_know() {
    let mut a = agent("cost");
    let step = rcv(&mut a, &resend_doc("alice", &init_doc(), None));
    assert_eq!(names(&step), vec!["joined"]);
    assert_eq!(a.due()[0].kind(), "init-reply");
    let mut busy = agent("cost");
    let _ = rcv(&mut busy, &InitDoc::new().run(OTHER).build());
    assert_eq!(error_of(&rcv(&mut busy, &resend_doc("alice", &init_doc(), None))).1.reason, "busy");
}

#[test]
fn a_participant_that_gets_the_init_again_in_a_resend_sends_its_stored_init_reply() {
    let mut a = Agent::builder("cost").uuid_fn(common::uuids("cccccccc")).build().unwrap();
    let _ = rcv(&mut a, &init_doc());
    assert!(rcv(&mut a, &resend_doc("alice", &init_doc(), None)).send.is_empty()); // nothing to send yet
    let sent = a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good", None).unwrap().send[0].clone();
    let again = rcv(&mut a, &resend_doc("alice", &init_doc(), Some("58585858-5858-4858-8858-585858585858")));
    assert_eq!(again.send.len(), 1);
    let m = msg(&again.send[0]);
    let r = resend_body(&m);
    assert_eq!((m.kind(), r.reply_to.as_deref(), &r.inner), (Kind::Resend, None, &sent));
    let bare = rcv(&mut a, &InitDoc::new().uid("59595959-5959-4959-8959-595959595959").build());
    assert!(bare.send.is_empty() && reason(&bare) == Some("run already known"));
}

// --- cancel -------------------------------------------------------------------------

const CA1: &str = "ca000000-0000-4000-8000-000000000001";
const CA2: &str = "ca000000-0000-4000-8000-000000000002";

fn cancel_doc(reason: &str, from: &str, superseded_by: Option<&str>, agent: Option<&str>, uid: &str) -> String {
    build_cancel(from, RUN, reason, superseded_by, agent, Some(uid)).unwrap()
}

/// A cancel for a missing agent: from `security`, about `legal`.
fn cancel() -> String {
    cancel_doc("missing-agent", "security", None, Some("legal"), CA1)
}

fn version_cancel(uid: &str) -> String {
    cancel_doc("version", "alice", None, None, uid)
}

#[test]
fn a_cancel_ends_the_run_aborted_and_the_agent_passes_it_on_once() {
    let mut net = three();
    let a = &mut net.agents["cost"];
    let step = rcv(a, &cancel());
    assert_eq!(names(&step), vec!["aborted", "sent"]);
    assert_eq!(step.send.len(), 1);
    let m = msg(&step.send[0]);
    let c = cancel_body(&m);
    assert_eq!(
        (m.kind(), m.from.as_str(), c.reason.as_str(), c.agent.as_deref(), c.superseded_by.as_deref()),
        (Kind::Cancel, "cost", "missing-agent", Some("legal"), None)
    );
    assert!(!peek(&step.send[0]).unwrap().attrs.contains_key("check"));
    let s = a.status(None).unwrap();
    assert_eq!((s.status, s.outcome), (RunStatus::Aborted, Some(Outcome::Aborted)));
    assert!(a.due().is_empty());
    let again = rcv(a, &cancel_doc("missing-agent", "legal", None, Some("legal"), CA2));
    assert!(again.send.is_empty());
    assert_eq!(a.report(None).unwrap().status, ReportStatus::Aborted);
    let rep = a.report(None).unwrap();
    assert_eq!((rep.outcome, rep.winner.clone(), rep.scores.is_empty()), (Some(Outcome::Aborted), None, true));
    assert_eq!(rep.reason, Some(Abort { reason: "missing-agent".into(), agent: Some("legal".into()), superseded_by: None, note: None }));
}

#[test]
fn each_reason_of_a_cancel_is_passed_on_with_its_attributes() {
    for (reason, superseded_by, agent_id) in
        [("version", None, None), ("superseded", Some(OTHER), None), ("missing-agent", None, Some("security"))]
    {
        let mut a = agent("cost");
        let _ = rcv(&mut a, &init_doc());
        let step = rcv(&mut a, &cancel_doc(reason, "alice", superseded_by, agent_id, CA1));
        let m = msg(&step.send[0]);
        let c = cancel_body(&m);
        assert_eq!((c.reason.as_str(), c.superseded_by.as_deref(), c.agent.as_deref()), (reason, superseded_by, agent_id));
    }
}

#[test]
fn an_agent_that_is_aborted_sends_no_result_and_ignores_data_but_answers_errors() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    let _ = rcv(&mut net.agents["cost"], &cancel_doc("missing-agent", "security", None, Some("security"), CA1));
    let late = score_doc("security", "seed-cost", 75, None);
    let cost = &mut net.agents["cost"];
    assert_eq!(reason(&rcv(cost, &late)), Some("run over"));
    assert!(cost.status(None).unwrap().outcome == Some(Outcome::Aborted) && cost.due().is_empty());
    let step = ask(&mut net, "cost", "init-reply");
    assert_eq!(kinds(&step), vec![Kind::Resend]);
    let err = net.agents["cost"].submit_score(RUN, &[ScoreIn::new("seed-security", 5, "w")], None).unwrap_err();
    assert!(matches!(err, ConcordError::State(_)) && err.to_string().contains("has aborted"), "{err}");
}

#[test]
fn a_cancel_after_the_run_ended_keeps_the_outcome_and_disputes_the_report() {
    let mut net = happy();
    let cost = &mut net.agents["cost"];
    assert_eq!(cost.report(None).unwrap().status, ReportStatus::Confirmed);
    let step = rcv(cost, &cancel_doc("missing-agent", "security", None, Some("security"), CA1));
    assert_eq!(names(&step), vec!["disputed", "sent"]);
    assert_eq!(kinds(&step), vec![Kind::Cancel]);
    let (s, rep) = (cost.status(None).unwrap(), cost.report(None).unwrap());
    assert_eq!(
        (s.status, s.outcome, rep.status, rep.outcome),
        (RunStatus::Ended, Some(Outcome::Success), ReportStatus::Disputed, Some(Outcome::Success))
    );
    let again = cancel_doc("missing-agent", "security", None, Some("security"), "ca000000-0000-4000-8000-000000000009");
    assert!(rcv(cost, &again).send.is_empty());
}

#[test]
fn a_cancel_that_is_not_valid_or_not_from_the_run_is_ignored() {
    let mut net = three();
    let a = &mut net.agents["cost"];
    let mallory = build_cancel("mallory", RUN, "version", None, None, None).unwrap();
    assert_eq!(reason(&rcv(a, &mallory)), Some("sender is not in the run"));
    let forged = cancel().replace("agent=\"legal\"", "agent=\"mallory\"");
    assert_eq!(reason(&rcv(a, &forged)), Some("invalid"));
    let elsewhere = build_cancel("alice", OTHER, "version", None, None, None).unwrap();
    assert_eq!(reason(&rcv(a, &elsewhere)), Some("unknown run"));
    assert_eq!(a.status(None).unwrap().status, RunStatus::Active);
}

#[test]
fn the_initiator_may_cancel() {
    let mut a = agent("cost");
    let _ = rcv(&mut a, &init_doc());
    let step = rcv(&mut a, &version_cancel(CA1));
    assert_eq!(a.status(None).unwrap().status, RunStatus::Aborted);
    assert_eq!(kinds(&step), vec![Kind::Cancel]);
}

#[test]
fn an_aborted_agent_can_join_another_run() {
    let mut a = agent("cost");
    let _ = rcv(&mut a, &init_doc());
    let _ = rcv(&mut a, &version_cancel(CA1));
    assert_eq!(rcv(&mut a, &InitDoc::new().run(OTHER).build()).events[0].event, "joined");
}

#[test]
fn the_state_of_an_aborted_run_survives_a_restart() {
    let store = MemoryStore::new();
    let mut a = Agent::builder("cost").store(store.clone()).build().unwrap();
    let _ = rcv(&mut a, &init_doc());
    let _ = rcv(&mut a, &version_cancel(CA1));
    let mut b = Agent::builder("cost").store(store.clone()).build().unwrap();
    let rep = b.report(None).unwrap();
    assert_eq!(rep.status, ReportStatus::Aborted);
    assert_eq!(rep.reason.unwrap().reason, "version");
    assert!(rcv(&mut b, &version_cancel(CA2)).send.is_empty());
}

#[test]
fn a_resend_of_a_message_of_a_run_the_agent_does_not_know_is_ignored() {
    let mut a = agent("cost"); // it never received the init
    let step = rcv(&mut a, &resend_doc("security", &reply_doc("security", None), None));
    assert!(reason(&step) == Some("unknown run") && step.send.is_empty() && a.status(None).is_none());
    let score = score_doc("security", "seed-cost", 5, None);
    assert_eq!(reason(&rcv(&mut a, &resend_doc("security", &score, None))), Some("unknown run"));
}
