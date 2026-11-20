//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The initiator: opening a run, watching it when it is not a participant, sending the init again, and the races between runs.

mod agent_net;
mod common;

use std::collections::BTreeMap;

use agent_net::*;
use common::uuids;
use ioc_concord::agent::{Agent, Step};
use ioc_concord::engine::EvalState;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::{Compared, ReportStatus, Winner};
use ioc_concord::state::{Abort, AbortNote, Reported, Role, RunStatus};
use ioc_concord::store::MemoryStore;
use ioc_concord::tasks::Task;
use ioc_concord::wire::{
    build_cancel, build_error, build_init, build_init_reply, build_repair, build_resend, build_result, build_score, peek, Body, InitParams,
    Kind, ScoreIn,
};
use ioc_concord::ConcordError;

const R1: &str = "11111111-1111-4111-8111-111111111111";
const R2: &str = "22222222-2222-4222-8222-222222222222";
const NAMES: [&str; 2] = ["cost", "security"];

fn rcv(a: &mut Agent, text: &str) -> Step {
    a.receive(text, None).unwrap()
}

fn reason(step: &Step) -> Option<&str> {
    step.events[0].str("reason")
}

fn open_run_with(net: &mut Net, who: &str, names: &[&str], run_id: &str, params: &InitParams) -> Step {
    let step = net.agents[who].start(names, "pick one", "nothing", "all agree", &spec(), Some(params), Some(run_id), None).unwrap();
    net.broadcast(&step);
    step
}

fn open_run(net: &mut Net) -> Step {
    open_run_with(net, "alice", &NAMES, R1, &params())
}

fn watched(names: &[&str]) -> Net {
    let mut all = vec!["alice"];
    all.extend(names);
    Net::new(&all)
}

fn timers(a: &Agent) -> BTreeMap<String, f64> {
    a.status(None).unwrap().timers.into_iter().collect()
}

fn timer_keys(a: &Agent) -> Vec<String> {
    let mut keys: Vec<String> = timers(a).into_keys().collect();
    keys.sort();
    keys
}

fn error_text(from: &str, to: &str, what: &str, reason: &str, uid: &str) -> String {
    build_error(from, R1, to, what, reason, None, None, "", Some(uid)).unwrap()
}

// --- opening a run ---------------------------------------------------------------------

#[test]
fn start_builds_the_init_and_the_initiator_that_is_not_a_participant_only_watches() {
    let mut a = Agent::builder("alice").uuid_fn(uuids("aaaaaaaa")).build().unwrap();
    let step = a.start(&NAMES, "pick one", "nothing", "all agree", &spec(), Some(&params()), Some(R1), Some(7.0)).unwrap();
    assert_eq!(step.send.len(), 1);
    let m = msg(&step.send[0]);
    let Body::Init(init) = &m.body else { panic!("an init") };
    assert_eq!(
        (m.kind(), m.from.as_str(), m.run_id.as_str(), &init.participants, init.params.timeout),
        (Kind::Init, "alice", R1, &vec!["cost".to_string(), "security".to_string()], 10)
    );
    assert!(
        ioc_concord::wire::check_ok(&step.send[0]) && names(&step) == vec!["started"] && step.events[0].str("role") == Some("initiator")
    );
    let s = a.status(None).unwrap();
    assert_eq!((s.role, s.status, s.outcome, s.state), (Role::Initiator, RunStatus::Active, None, EvalState::Wait));
    let expected = BTreeMap::from([("init-reply|cost".to_string(), 17.0), ("init-reply|security".to_string(), 17.0)]);
    assert!(a.due().is_empty() && timers(&a) == expected && a.next_wakeup() == Some(17.0));
    assert_eq!(a.report(None).unwrap().status, ReportStatus::Pending);
}

fn invalid_start(b: &mut Agent, participants: &[&str], context: &str, spec: &serde_json::Value, params: Option<&InitParams>) {
    let err = b.start(participants, context, "a", "b", spec, params, None, None).unwrap_err();
    assert!(matches!(err, ConcordError::InvalidMessage(_)), "{err:?}");
}

#[test]
fn start_makes_a_run_id_when_none_is_given_and_refuses_what_is_invalid() {
    let mut a = Agent::builder("alice").uuid_fn(uuids("aaaaaaaa")).build().unwrap();
    let text = a.start(&NAMES, "pick one", "nothing", "all agree", &spec(), None, None, None).unwrap().send[0].clone();
    assert_eq!(msg(&text).run_id, "aaaaaaaa-0000-4000-8000-000000000001");
    let mut b = Agent::new("bob").unwrap();
    invalid_start(&mut b, &["cost"], "c", &spec(), None);
    invalid_start(&mut b, &NAMES, "c", &serde_json::json!({}), None);
    invalid_start(&mut b, &NAMES, " ", &spec(), None);
    invalid_start(&mut b, &NAMES, "c", &spec(), Some(&InitParams::default().eps(0)));
    assert!(b.status(None).is_none());
}

#[test]
fn an_agent_in_a_run_cannot_open_another() {
    let mut a = Agent::new("alice").unwrap();
    let _ = a.start(&NAMES, "c", "a", "b", &spec(), None, Some(R1), None).unwrap();
    let err = a.start(&NAMES, "c", "a", "b", &spec(), None, Some(R2), None).unwrap_err();
    assert!(matches!(err, ConcordError::State(_)) && err.to_string().contains("in a run already"), "{err:?}");
}

#[test]
fn an_initiator_that_is_a_participant_owes_an_init_reply_like_the_others() {
    let mut net = Net::new(&NAMES);
    let _ = open_run_with(&mut net, "cost", &NAMES, R1, &params());
    let cost = &net.agents["cost"];
    let due = cost.due();
    let Task::InitReply(t) = &due[0] else { panic!("an init reply is owed") };
    assert_eq!((cost.status(None).unwrap().role, t.initiator.as_str()), (Role::Participant, "cost"));
    assert_eq!(timer_keys(cost), vec!["init-reply|security"]);
    assert_eq!(net.agents["security"].due()[0].kind(), "init-reply");
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 70)]);
    net.answer_score("security", &[("seed-cost", 75)]);
    let reports: BTreeMap<String, ReportStatus> = net.agents.iter().map(|(n, a)| (n.clone(), a.report(None).unwrap().status)).collect();
    assert_eq!(reports, BTreeMap::from([("cost".to_string(), ReportStatus::Confirmed), ("security".to_string(), ReportStatus::Confirmed)]));
}

#[test]
fn an_init_reply_asked_for_of_an_initiator_that_is_a_participant_is_answered_from_its_store() {
    let mut net = Net::new(&NAMES);
    let _ = open_run_with(&mut net, "cost", &NAMES, R1, &params());
    net.answer_init("cost");
    let err = error_text("security", "cost", "init-reply", "missing", U44);
    assert_eq!(kinds(&rcv(&mut net.agents["cost"], &err)), vec![Kind::Resend]);
}

// --- watching a run -----------------------------------------------------------------------

fn run_to_the_end_with(net: &mut Net, cost_on_security: i32, security_on_cost: i32) {
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", cost_on_security)]);
    net.answer_score("security", &[("seed-cost", security_on_cost)]);
}

fn run_to_the_end(net: &mut Net) {
    run_to_the_end_with(net, 70, 75);
}

#[test]
fn an_initiator_that_is_not_a_participant_learns_the_outcome_from_the_result_and_computes_none() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    net.answer_init("cost");
    assert_eq!(timer_keys(&net.agents["alice"]), vec!["init-reply|security"]);
    net.answer_init("security");
    let alice = &net.agents["alice"];
    assert!(timers(alice).is_empty() && alice.status(None).unwrap().status == RunStatus::Active); // no timer runs for the result
    net.answer_score("cost", &[("seed-security", 70)]);
    net.answer_score("security", &[("seed-cost", 75)]);
    let alice = &net.agents["alice"];
    let (s, rep) = (alice.status(None).unwrap(), alice.report(None).unwrap());
    assert_eq!((s.status, s.outcome), (RunStatus::Ended, Some(Outcome::Success)));
    assert!(alice.due().is_empty() && alice.next_wakeup().is_none());
    assert_eq!(
        (rep.status, rep.outcome, rep.rounds, rep.scores.is_empty()),
        (ReportStatus::Unverified, Some(Outcome::Success), None, true)
    );
    assert_eq!(rep.winner, Some(Winner { id: "seed-cost".into(), candidate: Some("plan of cost".into()) }));
    assert_eq!(
        rep.results_compared,
        Compared {
            reporter: "cost".into(),
            result: Some(Reported { outcome: Outcome::Success, winning_candidate_id: "seed-cost".into() }),
            matches: None
        }
    );
    let mut others = counts(&net, "score");
    others.extend(counts(&net, "repair"));
    others.extend(counts(&net, "result"));
    assert!(counts(&net, "error").is_empty() && !others.contains(&"alice".to_string()));
}

#[test]
fn the_initiator_that_watches_ignores_what_is_not_for_it_and_sends_no_answer() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    net.answer_init("cost");
    net.answer_init("security");
    let alice = &mut net.agents["alice"];
    let score = build_score("cost", R1, &[ScoreIn::new("seed-security", 70, "r")], None).unwrap();
    let step = rcv(alice, &score);
    assert!(step.send.is_empty() && reason(&step) == Some("not for the initiator"));
    let result = build_result("security", R1, Outcome::Success, "seed-cost", None).unwrap();
    assert_eq!(reason(&rcv(alice, &result)), Some("not the reporter"));
    assert_eq!(alice.status(None).unwrap().status, RunStatus::Active);
}

#[test]
fn a_mismatch_error_disputes_the_report_of_an_initiator_that_is_not_a_participant() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    run_to_the_end(&mut net);
    let alice = &mut net.agents["alice"];
    assert_eq!(alice.report(None).unwrap().status, ReportStatus::Unverified);
    let _ = rcv(alice, &error_text("security", "cost", "result", "mismatch", U44));
    assert_eq!(alice.report(None).unwrap().status, ReportStatus::Disputed);
    assert_eq!(alice.status(None).unwrap().status, RunStatus::Ended);
}

#[test]
fn a_cancel_ends_the_run_of_an_initiator_that_is_not_a_participant() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    let alice = &mut net.agents["alice"];
    let cancel = build_cancel("cost", R1, "missing-agent", None, Some("security"), Some("ca000000-0000-4000-8000-000000000001")).unwrap();
    let step = rcv(alice, &cancel);
    assert_eq!(kinds(&step), vec![Kind::Cancel]);
    assert_eq!((alice.status(None).unwrap().status, alice.report(None).unwrap().status), (RunStatus::Aborted, ReportStatus::Aborted));
    let rep = alice.report(None).unwrap();
    assert!(rep.outcome == Some(Outcome::Aborted) && rep.winner.is_none());
}

#[test]
fn an_initiator_that_watches_restarts_with_its_timers_and_its_role() {
    let store = MemoryStore::new();
    let mut a = Agent::builder("alice").store(store.clone()).uuid_fn(uuids("aaaaaaaa")).build().unwrap();
    let _ = a.start(&NAMES, "c", "a", "b", &spec(), Some(&params()), Some(R1), Some(0.0)).unwrap();
    let mut b = Agent::builder("alice").store(store.clone()).build().unwrap();
    assert_eq!((b.status(None).unwrap().role, b.next_wakeup()), (Role::Initiator, Some(10.0)));
    assert_eq!(kinds(&b.tick(Some(10.0)).unwrap()), vec![Kind::Resend, Kind::Resend]);
    assert_eq!(errors(&b.tick(Some(20.0)).unwrap()).len(), 0);
}

// --- sending the init again ----------------------------------------------------------------

fn lose_init_for_legal(net: &mut Net) {
    net.lose = Box::new(|text, name| name == "legal" && peek(text).unwrap().tag == "concord-init");
}

const THREE: [&str; 3] = ["cost", "security", "legal"];

#[test]
fn an_init_that_was_lost_is_sent_again_in_a_resend_and_the_participant_joins() {
    let mut net = watched(&THREE);
    lose_init_for_legal(&mut net);
    let _ = open_run_with(&mut net, "alice", &THREE, R1, &params());
    assert!(net.agents["legal"].status(None).is_none());
    net.now.set(10.0);
    let step = net.agents["alice"].tick(Some(10.0)).unwrap();
    assert_eq!(kinds(&step), vec![Kind::Resend, Kind::Resend, Kind::Resend]); // every reply still missing is asked for
    net.broadcast(&step);
    assert_eq!(net.agents["legal"].status(None).unwrap().run_id, R1);
    assert_eq!(net.agents["legal"].due()[0].kind(), "init-reply");
    let wrapper = msg(&step.send[0]);
    let r = resend_body(&wrapper);
    let inner = msg(&r.inner);
    assert_eq!((inner.kind(), inner.from.as_str()), (Kind::Init, "alice"));
    assert!(r.reply_to.is_none());
}

#[test]
fn the_participants_that_did_answer_send_their_init_reply_again_and_the_run_goes_on() {
    let mut net = watched(&THREE);
    lose_init_for_legal(&mut net);
    let _ = open_run_with(&mut net, "alice", &THREE, R1, &params());
    net.answer_init("cost");
    net.answer_init("security");
    tick_all(&mut net, 10.0); // alice sends the init again: legal joins, the others answer again
    assert!(net.agents["legal"].status(None).is_some());
    let resent = counts(&net, "resend");
    assert!(resent.iter().filter(|a| *a == "cost").count() == 1 && resent.iter().filter(|a| *a == "security").count() == 1);
    net.answer_init("legal");
    for (n, others) in [("cost", ["security", "legal"]), ("security", ["cost", "legal"]), ("legal", ["cost", "security"])] {
        let raw: Vec<(String, i32)> = others.iter().map(|o| (format!("seed-{o}"), 70)).collect();
        let raw: Vec<(&str, i32)> = raw.iter().map(|(id, v)| (id.as_str(), *v)).collect();
        net.answer_score(n, &raw);
    }
    let all: BTreeMap<String, RunStatus> = net.agents.iter().map(|(n, a)| (n.clone(), a.status(None).unwrap().status)).collect();
    assert_eq!(all, ["alice", "cost", "security", "legal"].iter().map(|n| (n.to_string(), RunStatus::Ended)).collect());
}

#[test]
fn a_participant_that_never_answers_ends_the_run_after_retries_and_everybody_is_told() {
    let mut net = watched(&THREE);
    net.dead.insert("legal".to_string());
    let _ = open_run_with(&mut net, "alice", &THREE, R1, &params());
    net.answer_init("cost");
    net.answer_init("security");
    settle(&mut net, 0.0);
    for n in ["alice", "cost", "security"] {
        let a = &net.agents[n];
        let rep = a.report(None).unwrap();
        assert_eq!((a.status(None).unwrap().status, rep.reason.unwrap().agent), (RunStatus::Aborted, Some("legal".to_string())), "{n}");
    }
    let mut cancels = counts(&net, "cancel");
    cancels.sort();
    assert_eq!(cancels, vec!["alice", "cost", "security"]);
    let resent: Vec<_> = net
        .log
        .iter()
        .filter(|t| {
            let p = peek(t).unwrap();
            p.tag == "concord-resend" && p.attrs["from"] == "alice"
        })
        .map(|t| msg(t))
        .collect();
    assert!(resent.len() == 2 && resent.iter().all(|m| msg(&resend_body(m).inner).kind() == Kind::Init));
    // retries = 2
}

#[test]
fn with_no_retries_the_initiator_gives_up_at_the_first_expiry() {
    let mut net = watched(&NAMES);
    let _ = open_run_with(&mut net, "alice", &NAMES, R1, &InitParams::default().timeout(10).retries(0));
    assert_eq!(kinds(&net.agents["alice"].tick(Some(10.0)).unwrap()), vec![Kind::Cancel]);
}

// --- another version -------------------------------------------------------------------------

const VERSION_TEXT: &str = "I speak 0.2.0; this init is version 0.3.0.";

fn version_error_with(frm: &str, to: &str, text: &str, version: &str) -> String {
    build_error(frm, R1, to, "init", "version", None, None, text, Some(U44)).unwrap().replacen(
        "version=\"0.3.0\"",
        &format!("version=\"{version}\""),
        1,
    )
}

fn version_error() -> String {
    version_error_with("legal", "alice", VERSION_TEXT, "0.2.0")
}

#[test]
fn a_participant_that_refuses_the_version_ends_the_run_and_the_agents_that_joined_are_told() {
    let mut net = watched(&THREE);
    let _ = open_run_with(&mut net, "alice", &THREE, R1, &params());
    net.answer_init("cost");
    let alice = &mut net.agents["alice"];
    let step = rcv(alice, &version_error());
    assert_eq!(step.send.len(), 1);
    let m = msg(&step.send[0]);
    let c = cancel_body(&m);
    assert_eq!((m.kind(), c.reason.as_str(), c.agent.as_deref(), c.superseded_by.as_deref()), (Kind::Cancel, "version", None, None));
    assert_eq!(names(&step), vec!["refused", "aborted", "sent"]);
    let rep = alice.report(None).unwrap();
    assert_eq!((rep.status, rep.outcome), (ReportStatus::Aborted, Some(Outcome::Aborted)));
    let why = rep.reason.unwrap();
    assert_eq!(why.reason, "version");
    assert_eq!(why.note, Some(AbortNote { agent: "legal".into(), text: Some(VERSION_TEXT.into()) }));
    assert!(alice.due().is_empty() && timers(alice).is_empty());
    net.broadcast(&step);
    let all: BTreeMap<String, RunStatus> =
        net.agents.iter().filter(|(n, _)| *n != "legal").map(|(n, a)| (n.clone(), a.status(None).unwrap().status)).collect();
    assert_eq!(all, ["alice", "cost", "security"].iter().map(|n| (n.to_string(), RunStatus::Aborted)).collect());
    assert_eq!(net.agents["cost"].report(None).unwrap().reason.unwrap().reason, "version");
}

#[test]
fn the_version_error_is_read_whatever_its_version_and_only_when_it_is_for_the_initiator_from_a_participant() {
    let mut net = watched(&THREE);
    let _ = open_run_with(&mut net, "alice", &THREE, R1, &params());
    let alice = &mut net.agents["alice"];
    assert_eq!(reason(&rcv(alice, &version_error_with("mallory", "alice", VERSION_TEXT, "0.2.0"))), Some("sender is not in the run"));
    assert_eq!(reason(&rcv(alice, &version_error_with("legal", "cost", VERSION_TEXT, "0.2.0"))), Some("version"));
    assert_eq!(alice.status(None).unwrap().status, RunStatus::Active);
    let cost = &mut net.agents["cost"];
    let _ = rcv(cost, &build_init_reply("security", R1, &0.into(), &100.into(), "p", "r", &5.into(), "w", None, None).unwrap());
    let err = version_error_with("security", "cost", VERSION_TEXT, "0.2.0");
    assert!(rcv(cost, &err).send.is_empty() && cost.status(None).unwrap().status == RunStatus::Active); // only the initiator acts on it
    let alice = &mut net.agents["alice"];
    assert_eq!(alice.status(None).unwrap().status, RunStatus::Active);
    let done = rcv(alice, &version_error_with("legal", "alice", "je parle 9.9.9", "9.9.9"));
    assert!(alice.status(None).unwrap().status == RunStatus::Aborted && kinds(&done) == vec![Kind::Cancel]);
    assert_eq!(reason(&rcv(alice, &version_error_with("security", "alice", VERSION_TEXT, "0.1.0"))), Some("duplicate"));
}

#[test]
fn a_busy_or_invalid_error_changes_nothing_for_the_initiator() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    let alice = &mut net.agents["alice"];
    for (why, uid) in [("busy", "44444444-4444-4444-8444-444444444441"), ("invalid", "44444444-4444-4444-8444-444444444442")] {
        let step = rcv(alice, &error_text("security", "alice", "init", why, uid));
        assert!(step.send.is_empty() && alice.status(None).unwrap().status == RunStatus::Active);
    }
    assert_eq!(timer_keys(alice), vec!["init-reply|cost", "init-reply|security"]);
}

// --- two runs at the same time ---------------------------------------------------------------------

#[test]
fn of_two_initiators_that_are_participants_the_run_with_the_lower_id_continues() {
    let mut net = Net::new(&NAMES);
    let low = net.agents["cost"].start(&NAMES, "pick one", "nothing", "all agree", &spec(), Some(&params()), Some(R1), None).unwrap();
    let high = net.agents["security"].start(&NAMES, "pick two", "nothing", "all agree", &spec(), Some(&params()), Some(R2), None).unwrap();
    let sec = &mut net.agents["security"];
    let to_sec = rcv(sec, &low.send[0]); // security opened the higher id: it gives way
    assert_eq!(names(&to_sec), vec!["aborted", "sent", "joined"]);
    assert_eq!(kinds(&to_sec), vec![Kind::Cancel]);
    let m = msg(&to_sec.send[0]);
    let c = cancel_body(&m);
    assert_eq!((m.run_id.as_str(), c.reason.as_str(), c.superseded_by.as_deref()), (R2, "superseded", Some(R1)));
    let cost = &mut net.agents["cost"];
    let to_cost = rcv(cost, &high.send[0]); // cost opened the lower id: it answers busy
    let errs = errors(&to_cost);
    assert_eq!(errs.len(), 1);
    let (m, e) = &errs[0];
    assert_eq!((m.run_id.as_str(), e.to.as_str(), e.what.as_str(), e.reason.as_str()), (R2, "security", "init", "busy"));
    assert_eq!(reason(&rcv(cost, &to_sec.send[0])), Some("unknown run"));
    let (sec, cost) = (&net.agents["security"], &net.agents["cost"]);
    assert_eq!(
        (sec.status(None).unwrap().run_id, sec.status(Some(R2)).unwrap().status, cost.status(None).unwrap().run_id),
        (R1.to_string(), RunStatus::Aborted, R1.to_string())
    );
    assert_eq!(
        sec.report(Some(R2)).unwrap().reason,
        Some(Abort { reason: "superseded".into(), agent: None, superseded_by: Some(R1.into()), note: None })
    );
    let due = sec.due();
    assert!(due[0].kind() == "init-reply" && due[0].run_id() == R1);
}

#[test]
fn an_initiator_that_has_all_its_replies_does_not_give_way_it_answers_busy() {
    let mut net = Net::new(&NAMES);
    let _ = open_run_with(&mut net, "cost", &NAMES, R2, &params());
    net.answer_init("cost");
    net.answer_init("security");
    let cost = &mut net.agents["cost"];
    // a lower id, but cost has its replies
    let other = build_init("bob", &NAMES, "c", "a", "b", &spec(), None, Some(R1), None).unwrap();
    let errs = errors(&rcv(cost, &other));
    assert_eq!(errs.len(), 1);
    assert_eq!(errs[0].1.reason, "busy");
    assert_eq!(cost.status(None).unwrap().run_id, R2);
}

#[test]
fn an_initiator_that_watches_does_not_join_a_run_it_is_not_listed_in() {
    let mut net = watched(&NAMES);
    let _ = open_run_with(&mut net, "alice", &NAMES, R2, &params());
    let other = build_init("bob", &NAMES, "c", "a", "b", &spec(), None, Some(R1), None).unwrap();
    assert_eq!(reason(&rcv(&mut net.agents["alice"], &other)), Some("not a participant"));
    assert_eq!(net.agents["alice"].status(None).unwrap().run_id, R2);
}

#[test]
fn an_ended_run_does_not_stop_the_initiator_from_opening_another() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    run_to_the_end(&mut net);
    let alice = &mut net.agents["alice"];
    let step = alice.start(&NAMES, "c", "a", "b", &spec(), None, Some(R2), None).unwrap();
    assert_eq!(alice.status(None).unwrap().run_id, R2);
    assert_eq!(alice.report(Some(R1)).unwrap().status, ReportStatus::Unverified);
    assert!(!step.send.is_empty());
}

#[test]
fn an_initiator_that_watches_reports_the_text_of_a_winner_that_is_a_repair() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 20)]);
    net.answer_score("security", &[("seed-cost", 30)]);
    let step = net.agents["security"].submit_repair(R1, "a better plan", "because", 70, "ok", None).unwrap();
    net.broadcast(&step);
    net.answer_score("cost", &[("r1-security", 65)]);
    let rep = net.agents["alice"].report(None).unwrap();
    assert_eq!((rep.status, rep.outcome), (ReportStatus::Unverified, Some(Outcome::Success)));
    assert_eq!(rep.winner, Some(Winner { id: "r1-security".into(), candidate: Some("a better plan".into()) }));
}

#[test]
fn a_repair_that_the_participants_ignore_cannot_take_the_place_of_the_winner_in_the_watchers_report() {
    let mut net = watched(&NAMES);
    let _ = open_run(&mut net);
    let alice = &mut net.agents["alice"];
    let repair = |text: &str, uid: &str| build_repair("security", R1, 1, text, "r", &70.into(), "w", None, Some(uid)).unwrap();
    let first = repair("the real one", "71717171-7171-4171-8171-717171717171");
    let other = repair("an impostor", "72727272-7272-4272-8272-727272727272");
    assert_eq!(rcv(alice, &first).events[0].event, "recorded");
    assert_eq!(reason(&rcv(alice, &other)), Some("already recorded"));
    assert_eq!(alice.state.runs[R1].engine.pool[0].candidate, "the real one");
}

#[test]
fn an_init_inside_a_resend_is_answered_only_when_it_comes_from_the_initiator() {
    let mut net = Net::new(&THREE);
    let doc = || InitDoc::new().names(&THREE).params(params());
    net.start(&doc().build());
    net.answer_init("legal");
    let legal = &mut net.agents["legal"];
    let again = build_resend("alice", R1, &doc().build(), None, Some("73737373-7373-4373-8373-737373737373")).unwrap();
    assert_eq!(kinds(&rcv(legal, &again)), vec![Kind::Resend]);
    let claim = build_resend("security", R1, &doc().from("security").build(), None, Some("74747474-7474-4474-8474-747474747474")).unwrap();
    let step = rcv(legal, &claim); // a member that claims to be the initiator of the run
    assert!(step.send.is_empty() && reason(&step) == Some("run already known"));
}

#[test]
fn start_never_reuses_a_run_id_the_agent_knows_and_the_old_run_is_kept() {
    let mut a = Agent::builder("alice").uuid_fn(uuids("aaaaaaaa")).build().unwrap();
    let first = a.start(&NAMES, "first", "a", "b", &spec(), None, Some(R1), None).unwrap().send[0].clone();
    a.state.runs.get_mut(R1).unwrap().status = RunStatus::Ended;
    let err = a.start(&NAMES, "second", "a", "b", &spec(), None, Some(R1), None).unwrap_err();
    assert!(matches!(err, ConcordError::State(_)) && err.to_string().contains("knows the run"), "{err:?}");
    assert!(a.state.runs[R1].sent["init|alice"] == first && a.state.runs[R1].context == "first");
    assert!(!a.start(&NAMES, "second", "a", "b", &spec(), None, Some(R2), None).unwrap().send.is_empty());
}
