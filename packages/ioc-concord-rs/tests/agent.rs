//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The node of an agent: joining a run, the order of checks, the happy path, what the model submits, the saved state, the repair, the
//! result and its confirmation.

mod common;

use common::{uuids, SCell, RUN};
use indexmap::IndexMap;
use ioc_concord::agent::{Agent, Event, Step};
use ioc_concord::engine::EvalState;
use ioc_concord::errors::ConcordError;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::{Compared, MismatchSummary, ReportStatus, Winner};
use ioc_concord::state::{Reported, RunStatus};
use ioc_concord::store::{MemoryStore, Store};
use ioc_concord::tasks::{Incumbent, InitReplyTask, RepairTask, Task};
use ioc_concord::wire::{
    build_error, build_init, build_init_reply, build_repair, build_result, build_score, check_ok, parse_message, Body, InitParams, Kind,
    ScoreIn,
};
use ioc_concord::xmlutil::with_check;
use regex::Regex;
use serde_json::{json, Value};

fn spec() -> Value {
    json!({"type": "string", "description": "a plan"})
}

/// The init of the tests, with what each test may change.
struct Init {
    participants: Vec<&'static str>,
    run_id: &'static str,
    uid: &'static str,
    spec: Value,
    params: Option<InitParams>,
}

impl Default for Init {
    fn default() -> Self {
        Init {
            participants: vec!["cost", "security"],
            run_id: RUN,
            uid: "a0a0a0a0-0000-4000-8000-000000000001",
            spec: spec(),
            params: None,
        }
    }
}

impl Init {
    fn doc(&self) -> String {
        build_init(
            "alice",
            &self.participants,
            "pick one",
            "nothing",
            "all agree",
            &self.spec,
            self.params.as_ref(),
            Some(self.run_id),
            Some(self.uid),
        )
        .expect("init")
    }
}

fn init_doc() -> String {
    Init::default().doc()
}

fn init_with_params(params: InitParams) -> String {
    Init { params: Some(params), ..Init::default() }.doc()
}

fn sc(id: &str, raw: f64, reasoning: &str) -> ScoreIn {
    ScoreIn::new(id, raw, reasoning)
}

fn score_doc(from: &str, entries: &[(&str, f64, &str)], uid: Option<&str>) -> String {
    let scores: Vec<ScoreIn> = entries.iter().map(|(id, raw, why)| sc(id, *raw, why)).collect();
    build_score(from, RUN, &scores, uid).expect("score")
}

fn reply_doc(from: &str, uid: &str) -> String {
    build_init_reply(from, RUN, &0.into(), &100.into(), "plan", "because", &80.into(), "good", None, Some(uid)).expect("init reply")
}

const REPLY_UID: &str = "55555555-5555-4555-8555-555555555555";

fn agent(me: &str) -> Agent {
    Agent::new(me).expect("agent")
}

fn agent_uuids(me: &str, prefix: &str) -> Agent {
    Agent::builder(me).uuid_fn(uuids(prefix)).build().expect("agent")
}

/// `doc` with `old` replaced by `new` and its check computed again: a message that is intact but not what an honest author sends.
fn forge(doc: &str, old: &str, new: &str) -> String {
    assert!(doc.contains(old));
    let changed = doc.replace(old, new);
    let no_check = Regex::new(r#" check="[^"]*""#).unwrap().replace(&changed, "").into_owned();
    with_check(&no_check).unwrap()
}

fn reason(step: &Step) -> &str {
    step.events[0].str("reason").unwrap_or_else(|| panic!("no reason in {:?}", step.events[0]))
}

fn names(step: &Step) -> Vec<&str> {
    step.events.iter().map(|e| e.event.as_str()).collect()
}

fn err_of<T>(result: ioc_concord::Result<T>) -> ConcordError {
    match result {
        Ok(_) => panic!("expected an error"),
        Err(e) => e,
    }
}

fn state_err(result: ioc_concord::Result<Step>, text: &str) {
    let e = err_of(result);
    assert_eq!(e.code(), "state_error", "{e}");
    assert!(e.to_string().contains(text), "{e}");
}

fn invalid_err(result: ioc_concord::Result<Step>, text: &str) {
    let e = err_of(result);
    assert_eq!(e.code(), "invalid_message", "{e}");
    assert!(e.to_string().contains(text), "{e}");
}

/// A channel on which every message reaches every agent, the sender included (a broadcast).
struct Net {
    /// The clock of every agent, moved by the test.
    now: SCell<f64>,
    agents: IndexMap<String, Agent>,
    events: Vec<(String, Event)>,
    log: Vec<String>,
}

impl Net {
    fn new(names: &[&str]) -> Net {
        let now = SCell::new(0.0);
        let agents = names
            .iter()
            .enumerate()
            .map(|(i, n)| {
                let clock = now.clone();
                let agent = Agent::builder(n).uuid_fn(uuids(&format!("{:08x}", i + 1))).clock(move || clock.get()).build().expect("agent");
                (n.to_string(), agent)
            })
            .collect();
        Net { now, agents, events: Vec::new(), log: Vec::new() }
    }

    fn a(&mut self, name: &str) -> &mut Agent {
        self.agents.get_mut(name).unwrap_or_else(|| panic!("no agent {name}"))
    }

    /// Deliver what `step` sends to every agent, then what the agents send in answer, in the order it was sent.
    fn broadcast(&mut self, step: Step, who: &str) {
        self.events.extend(step.events.into_iter().map(|e| (who.to_string(), e)));
        let mut queue: std::collections::VecDeque<String> = step.send.into();
        while let Some(text) = queue.pop_front() {
            self.log.push(text.clone());
            for (name, a) in self.agents.iter_mut() {
                let got = a.receive(&text, None).expect("receive");
                self.events.extend(got.events.into_iter().map(|e| (name.clone(), e)));
                queue.extend(got.send);
            }
        }
    }

    fn start(&mut self, doc: String) {
        let step = Step { now: 0.0, send: vec![doc], forward: vec![], accepted: vec![], events: vec![] };
        self.broadcast(step, "");
    }

    fn answer_init(&mut self, name: &str) {
        let a = self.a(name);
        let task = a.due().into_iter().find(|t| matches!(t, Task::InitReply(_))).expect("an init-reply task");
        let step =
            a.submit_init_reply(task.run_id(), 0, 100, &format!("plan of {name}"), "because", 80, "I like it", None).expect("init reply");
        self.broadcast(step, name);
    }

    /// Score every candidate owed, with the raw score given for its id.
    fn answer_score(&mut self, name: &str, raw: &[(&str, f64)]) {
        let a = self.a(name);
        let Some(Task::Score(task)) = a.due().into_iter().find(|t| matches!(t, Task::Score(_))) else { panic!("a score task") };
        let scores: Vec<ScoreIn> = task
            .candidates
            .iter()
            .map(|c| sc(&c.id, raw.iter().find(|(id, _)| *id == c.id).unwrap_or_else(|| panic!("no raw for {}", c.id)).1, "my view"))
            .collect();
        let step = a.submit_score(&task.run_id, &scores, None).expect("score");
        self.broadcast(step, name);
    }
}

fn happy_with(cost_on_security: f64, security_on_cost: f64) -> Net {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", cost_on_security)]);
    net.answer_score("security", &[("seed-cost", security_on_cost)]);
    net
}

fn happy() -> Net {
    happy_with(70.0, 75.0)
}

/// cost and security rate each other's seed low: the floor is .3 and security is the bottleneck of round 1.
fn below_tau() -> Net {
    happy_with(20.0, 30.0)
}

/// A net of cost and security that has the init and both init replies.
fn after_inits() -> Net {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    net
}

fn net_of_three() -> Net {
    let mut net = Net::new(&["cost", "security", "legal"]);
    net.start(Init { participants: vec!["cost", "security", "legal"], ..Init::default() }.doc());
    net
}

fn strings(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

fn text_map(v: &[(&str, &str)]) -> IndexMap<String, String> {
    v.iter().map(|(k, x)| (k.to_string(), x.to_string())).collect()
}

fn millionths(v: &[(&str, i64)]) -> IndexMap<String, i64> {
    v.iter().map(|(k, x)| (k.to_string(), *x)).collect()
}

// --- joining a run -----------------------------------------------------------

#[test]
fn a_participant_that_receives_the_init_joins_and_owes_an_init_reply() {
    let mut a = agent_uuids("cost", "cccccccc");
    assert!(a.due().is_empty() && a.status(None).is_none());
    let step = a.receive(&init_doc(), Some(5.0)).unwrap();
    assert_eq!(names(&step), ["joined"]);
    assert_eq!(step.events[0].at, 5.0);
    assert!(step.send.is_empty());
    let due = a.due();
    assert_eq!(
        due,
        vec![Task::InitReply(InitReplyTask {
            run_id: RUN.to_string(),
            initiator: "alice".to_string(),
            context: "pick one".to_string(),
            x_reject: "nothing".to_string(),
            x_ideal: "all agree".to_string(),
            schema: spec(),
        })]
    );
    assert_eq!(a.due(), a.due());
    let s = a.status(None).unwrap();
    assert_eq!((s.status, s.state, s.outcome), (RunStatus::Active, EvalState::Wait, None));
}

#[test]
fn the_init_may_come_wrapped_in_prose_or_a_fence() {
    let mut a = agent("cost");
    let _ = a.receive(&format!("Here you go:\n```xml\n{}\n```\n", init_doc()), None).unwrap();
    assert_eq!(a.status(None).unwrap().run_id, RUN);
}

#[test]
fn an_agent_that_is_not_listed_does_not_join() {
    let mut a = agent("other");
    let step = a.receive(&init_doc(), None).unwrap();
    assert!(a.status(None).is_none());
    assert_eq!(reason(&step), "not a participant");
}

#[test]
fn an_agent_in_an_active_run_does_not_join_a_second_one() {
    let mut a = agent("cost");
    let _ = a.receive(&init_doc(), None).unwrap();
    let other = "22222222-2222-4222-8222-222222222222";
    let step = a.receive(&Init { run_id: other, ..Init::default() }.doc(), None).unwrap();
    assert_eq!(reason(&step), "busy");
    assert_eq!(a.status(None).unwrap().run_id, RUN);
    assert!(a.status(Some(other)).is_none());
}

#[test]
fn an_init_for_a_run_the_agent_knows_is_ignored() {
    let mut a = agent("cost");
    let _ = a.receive(&init_doc(), None).unwrap();
    assert_eq!(reason(&a.receive(&init_doc(), None).unwrap()), "duplicate"); // the same transmission
    let again = Init { uid: "33333333-3333-4333-8333-333333333333", ..Init::default() }.doc();
    assert_eq!(reason(&a.receive(&again, None).unwrap()), "run already known");
    assert_eq!(a.status(None).unwrap().run_id, RUN);
}

// --- the order of checks ------------------------------------------------------

#[test]
fn what_is_not_a_concord_message_is_ignored() {
    let mut a = agent("cost");
    for text in ["hello", "<other>x</other>", "<concord-init", "", "<concord-score from='a'>never closed"] {
        let step = a.receive(text, None).unwrap();
        let reasons: Vec<_> = step.events.iter().map(|e| e.str("reason").unwrap()).collect();
        assert_eq!(reasons, ["not a CONCORD message"], "{text}");
        assert!(step.send.is_empty());
    }
}

#[test]
fn a_message_of_an_unknown_run_is_ignored() {
    let mut a = agent("cost");
    let doc = score_doc("security", &[("seed-cost", 5.0, "r")], None);
    assert_eq!(reason(&a.receive(&doc, None).unwrap()), "unknown run");
}

#[test]
fn own_messages_and_senders_outside_the_run_are_ignored() {
    let mut net = happy();
    let own = score_doc("cost", &[("seed-security", 5.0, "r")], None);
    assert_eq!(reason(&net.a("cost").receive(&own, None).unwrap()), "own message");
    let stranger = score_doc("mallory", &[("seed-security", 5.0, "r")], None);
    assert_eq!(reason(&net.a("cost").receive(&stranger, None).unwrap()), "sender is not in the run");
}

#[test]
fn the_initiator_is_a_member_of_the_run_so_its_messages_pass_the_first_step() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    let err = build_error("alice", RUN, "cost", "init", "busy", None, None, "", None).unwrap();
    assert_eq!(reason(&net.a("cost").receive(&err, None).unwrap()), "no answer expected");
}

#[test]
fn a_transmission_is_acted_on_once() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    let a = net.a("cost");
    let reply = reply_doc("security", REPLY_UID);
    assert_eq!(a.receive(&reply, None).unwrap().events[0].event, "recorded");
    let again = a.receive(&reply, None).unwrap();
    let reasons: Vec<_> = again.events.iter().map(|e| e.str("reason").unwrap()).collect();
    assert_eq!(reasons, ["duplicate"]);
}

#[test]
fn a_message_of_another_version_is_ignored_and_not_kept() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    let a = net.a("cost");
    let reply = reply_doc("security", REPLY_UID);
    let other = reply.replace("version=\"0.3.0\"", "version=\"0.2.0\"");
    assert_eq!(reason(&a.receive(&other, None).unwrap()), "version");
    assert_eq!(a.receive(&reply, None).unwrap().events[0].event, "recorded"); // same uuid, intact and of the right version
}

#[test]
fn a_damaged_message_is_ignored_and_its_intact_copy_is_still_processed() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    let a = net.a("cost");
    let reply = reply_doc("security", REPLY_UID);
    assert_eq!(reason(&a.receive(&reply.replace("because", "becauze"), None).unwrap()), "check");
    assert_eq!(a.receive(&reply, None).unwrap().events[0].event, "recorded");
}

#[test]
fn an_invalid_message_is_ignored_and_nothing_is_recorded() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    let a = net.a("cost");
    let good = reply_doc("security", REPLY_UID);
    let bad = forge(&good, "<raw>80.0</raw>", "<raw>eighty</raw>");
    let step = a.receive(&bad, None).unwrap();
    assert_eq!(reason(&step), "invalid");
    assert!(step.events[0].str("detail").unwrap().contains("number"));
    assert!(a.status(None).unwrap().anchors_in.is_empty());
    let swapped = forge(&good, "id=\"seed-security\"", "id=\"seed-cost\"");
    assert_eq!(reason(&a.receive(&swapped, None).unwrap()), "invalid");
    assert_eq!(a.receive(&good, None).unwrap().events[0].event, "recorded"); // the valid one with the same uuid is processed
}

#[test]
fn an_init_that_is_invalid_or_of_another_version_is_ignored() {
    let mut a = agent("cost");
    let doc = init_doc();
    assert_eq!(reason(&a.receive(&doc.replace("version=\"0.3.0\"", "version=\"0.1.0\""), None).unwrap()), "version");
    assert_eq!(reason(&a.receive(&doc.replace("timeout>300", "timeout>-1"), None).unwrap()), "check");
    assert_eq!(reason(&a.receive(&forge(&doc, "timeout>300", "timeout>-1"), None).unwrap()), "invalid");
    assert!(a.status(None).is_none());
}

// --- the happy path -----------------------------------------------------------

#[test]
fn the_init_reply_is_built_recorded_and_broadcast() {
    let mut a = agent_uuids("cost", "cccccccc");
    let _ = a.receive(&init_doc(), None).unwrap();
    let step = a.submit_init_reply(RUN, 0, 100, "local cache", "because", 82.5, "good", Some(9.0)).unwrap();
    assert_eq!(step.send.len(), 1);
    assert_eq!(names(&step), ["sent"]);
    assert_eq!(step.events[0].at, 9.0);
    let m = parse_message(&step.send[0], Some(&spec())).unwrap();
    let Body::InitReply(reply) = &m.body else { panic!("an init reply") };
    assert_eq!(
        (m.kind(), m.from.as_str(), m.uuid.as_str(), reply.proposal.id.as_str(), reply.proposal.raw),
        (Kind::InitReply, "cost", "cccccccc-0000-4000-8000-000000000001", "seed-cost", 82_500_000)
    );
    assert!(check_ok(&step.send[0]) && a.due().is_empty());
    let s = a.status(None).unwrap();
    assert_eq!(s.anchors_in, ["cost"]);
    assert_eq!(s.lacking, [("security".to_string(), "seed-cost".to_string())]);
}

#[test]
fn after_every_init_reply_the_agent_owes_a_score_for_each_seed_of_the_others() {
    let mut net = net_of_three();
    net.answer_init("cost");
    net.answer_init("security");
    assert!(net.a("cost").due().is_empty()); // legal's reply is not in yet
    net.answer_init("legal");
    let due = net.a("cost").due();
    assert_eq!(due.len(), 1);
    let Task::Score(task) = &due[0] else { panic!("a score task") };
    let got: Vec<_> = task.candidates.iter().map(|c| (c.id.as_str(), c.from.as_str())).collect();
    assert_eq!(got, [("seed-security", "security"), ("seed-legal", "legal")]);
    assert_eq!(task.candidates[0].candidate, "plan of security");
    assert_eq!(task.candidates[0].rationale, "because");
    assert_eq!(task.candidates[0].reasoning, "I like it");
}

#[test]
fn two_agents_reach_success_and_each_computes_the_same_outcome() {
    let mut net = happy();
    for name in ["cost", "security"] {
        let a = net.a(name);
        let s = a.status(None).unwrap();
        assert_eq!(
            (s.state, s.outcome, s.x_star.as_deref(), s.floor, s.status),
            (EvalState::Done, Some(Outcome::Success), Some("seed-cost"), Some(750_000), RunStatus::Ended)
        );
        assert!(s.lacking.is_empty() && a.due().is_empty());
    }
    let outcomes: Vec<_> = net.events.iter().filter(|(_, e)| e.event == "outcome").map(|(_, e)| e.str("outcome").unwrap()).collect();
    assert_eq!(outcomes, ["SUCCESS", "SUCCESS"]);
}

#[test]
fn a_run_whose_floor_is_below_tau_is_not_done_and_names_the_bottleneck() {
    let mut net = below_tau();
    for name in ["cost", "security"] {
        let s = net.a(name).status(None).unwrap();
        assert_eq!((s.state, s.outcome, s.bottleneck.as_deref(), s.round), (EvalState::Repair, None, Some("security"), Some(1)));
    }
    let owed: Vec<_> = net.events.iter().filter(|(_, e)| e.event == "repair owed").map(|(_, e)| e.str("bottleneck").unwrap()).collect();
    assert_eq!(owed, ["security"; 2]);
}

#[test]
fn three_agents_reach_success_and_the_last_to_score_waits_for_the_others() {
    let mut net = net_of_three();
    for n in ["cost", "security", "legal"] {
        net.answer_init(n);
    }
    net.answer_score("cost", &[("seed-security", 70.0), ("seed-legal", 70.0)]);
    net.answer_score("security", &[("seed-cost", 70.0), ("seed-legal", 70.0)]);
    assert_eq!(net.a("legal").status(None).unwrap().state, EvalState::Wait);
    net.answer_score("legal", &[("seed-cost", 70.0), ("seed-security", 70.0)]);
    for n in ["cost", "security", "legal"] {
        assert_eq!(net.a(n).status(None).unwrap().outcome, Some(Outcome::Success), "{n}");
    }
}

#[test]
fn score_entries_for_the_senders_own_candidate_are_ignored_and_the_others_kept() {
    let mut net = after_inits();
    let a = net.a("cost");
    let own =
        forge(&score_doc("legal", &[("seed-cost", 75.0, "r"), ("seed-security", 5.0, "own")], None), "from=\"legal\"", "from=\"security\"");
    let step = a.receive(&own, None).unwrap();
    assert_eq!(names(&step), ["entries ignored", "recorded"]);
    assert_eq!(step.events[0].get("candidates"), Some(&json!(["seed-security"])));
    assert_eq!(step.events[1].get("candidates"), Some(&json!(["seed-cost"])));
}

#[test]
fn a_rating_of_a_candidate_the_agent_does_not_hold_is_a_gap_not_an_invalid_message() {
    let mut net = net_of_three();
    net.answer_init("cost");
    let a = net.a("cost");
    let step = a.receive(&score_doc("security", &[("r1-legal", 5.0, "r")], None), None).unwrap();
    assert_eq!(step.events[0].event, "gap");
    assert_eq!(step.events[0].get("candidates"), Some(&json!(["r1-legal"])));
}

#[test]
fn a_rating_of_a_candidate_of_the_agent_that_it_never_proposed_is_ignored_and_never_owed_to_itself() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_with_params(InitParams::default().timeout(10).retries(2)));
    net.answer_init("cost");
    net.answer_init("security");
    let a = net.a("cost");
    let step = a.receive(&score_doc("security", &[("seed-cost", 70.0, "r"), ("r1-cost", 50.0, "a faulty peer")], None), Some(1.0)).unwrap();
    assert_eq!(names(&step), ["entries ignored", "recorded"]);
    assert_eq!(step.events[0].get("candidates"), Some(&json!(["r1-cost"])));
    assert!(a.state.runs[RUN].expected.is_empty() && a.status(None).unwrap().timers.is_empty());
    net.now.set(1000.0);
    let a = net.a("cost");
    assert!(a.tick(Some(1000.0)).unwrap().send.is_empty()); // nobody is missing
    assert_eq!(a.status(None).unwrap().status, RunStatus::Active);
}

// --- what the model submits ---------------------------------------------------

#[test]
fn a_submission_that_is_invalid_changes_nothing_and_may_be_tried_again() {
    use ioc_concord::protocol::Num;
    let mut a = agent("cost");
    let _ = a.receive(&init_doc(), None).unwrap();
    // (raw_reject, raw_ideal, candidate, raw, reasoning)
    let bad: Vec<(i64, i64, &str, Num, &str)> = vec![
        (50, 50, "x", Num::Int(50), "w"),
        (0, 100, "x", Num::Int(101), "w"),
        (0, 100, "x", Num::Float(0.1234567), "w"),
        (0, 100, " ", Num::Int(50), "w"),
        (0, 100, "x", Num::Int(50), ""),
    ];
    for (reject, ideal, candidate, raw, reasoning) in bad {
        let e = err_of(a.submit_init_reply(RUN, reject, ideal, candidate, "r", raw, reasoning, None));
        assert_eq!(e.code(), "invalid_message", "{e}");
    }
    assert!(a.status(None).unwrap().anchors_in.is_empty());
    assert_eq!(a.due().len(), 1);
    assert!(!a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w", None).unwrap().send.is_empty());
}

#[test]
fn the_candidate_must_satisfy_the_schema_of_the_run() {
    let mut a = agent("cost");
    let _ = a.receive(&Init { spec: json!({"enum": ["A", "B"]}), ..Init::default() }.doc(), None).unwrap();
    invalid_err(a.submit_init_reply(RUN, 0, 100, "C", "r", 50, "w", None), "exactly one of");
    assert!(!a.submit_init_reply(RUN, 0, 100, "A", "r", 50, "w", None).unwrap().send.is_empty());
}

#[test]
fn a_submission_in_the_wrong_state_is_refused() {
    let mut a = agent("cost");
    state_err(a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w", None), "knows no run");
    let _ = a.receive(&init_doc(), None).unwrap();
    state_err(a.submit_score(RUN, &[sc("seed-security", 5.0, "w")], None), "owes no score");
    let _ = a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w", None).unwrap();
    state_err(a.submit_init_reply(RUN, 0, 100, "x", "r", 50, "w", None), "already sent");
}

#[test]
fn a_score_covers_exactly_the_candidates_owed() {
    let mut net = net_of_three();
    for n in ["cost", "security", "legal"] {
        net.answer_init(n);
    }
    let a = net.a("cost");
    let given: Vec<Vec<ScoreIn>> = vec![
        vec![sc("seed-security", 5.0, "w")],
        vec![],
        vec![sc("seed-security", 5.0, "w"), sc("seed-legal", 5.0, "w"), sc("seed-cost", 5.0, "w")],
        vec![sc("seed-security", 5.0, "w"), sc("seed-security", 5.0, "w")],
    ];
    for scores in given {
        invalid_err(a.submit_score(RUN, &scores, None), "exactly the candidates not rated yet");
    }
    invalid_err(a.submit_score(RUN, &[sc("seed-security", 500.0, "w"), sc("seed-legal", 5.0, "w")], None), "0 to 100");
    let Task::Score(task) = &a.due()[0] else { panic!("a score task") };
    assert_eq!(task.candidates.len(), 2);
    assert!(!a.submit_score(RUN, &[sc("seed-legal", 5.0, "w"), sc("seed-security", 5.0, "w")], None).unwrap().send.is_empty());
}

// --- the state is kept ---------------------------------------------------------

#[test]
fn the_state_is_saved_and_a_restarted_agent_owes_the_same() {
    let store = MemoryStore::new();
    let mut net = Net::new(&["cost", "security"]);
    let a = Agent::builder("cost").store(store.clone()).uuid_fn(uuids("cccccccc")).build().unwrap();
    net.agents.insert("cost".to_string(), a);
    net.start(init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    let before = net.a("cost").due();
    assert!(matches!(before.first(), Some(Task::Score(_))));
    let again = Agent::builder("cost").store(store.clone()).build().unwrap();
    assert_eq!(again.due(), before);
    assert_eq!(again.status(None), net.a("cost").status(None));
    net.agents.insert("cost".to_string(), again);
    net.answer_score("cost", &[("seed-security", 70.0)]);
    net.answer_score("security", &[("seed-cost", 75.0)]);
    assert_eq!(net.a("cost").status(None).unwrap().outcome, Some(Outcome::Success));
    let last = Agent::builder("cost").store(store).build().unwrap();
    assert_eq!(last.status(None).unwrap().outcome, Some(Outcome::Success));
}

#[test]
fn a_saved_state_of_another_agent_is_refused() {
    let store = MemoryStore::new();
    let _ = Agent::builder("cost").store(store.clone()).build().unwrap().receive(&init_doc(), None).unwrap();
    let e = err_of(Agent::builder("security").store(store).build());
    assert_eq!(e.code(), "state_error");
    assert!(e.to_string().contains("belongs to 'cost'"), "{e}");
}

#[test]
fn the_clock_is_injected_and_a_given_time_wins() {
    let mut a = Agent::builder("cost").clock(|| 1234.0).build().unwrap();
    assert_eq!(a.receive(&init_doc(), None).unwrap().events[0].at, 1234.0);
    assert_eq!(a.receive("hello", Some(7.0)).unwrap().events[0].at, 7.0);
}

// --- the repair ---------------------------------------------------------------

#[test]
fn the_bottleneck_owes_a_repair_and_the_task_says_what_to_fix() {
    let mut net = below_tau();
    assert!(net.a("cost").due().is_empty());
    let due = net.a("security").due();
    assert_eq!(
        due,
        vec![Task::Repair(RepairTask {
            run_id: RUN.to_string(),
            round: 1,
            incumbent: Incumbent { id: "seed-cost".to_string(), candidate: "plan of cost".to_string(), rationale: "because".to_string() },
            threshold: 600_000,
            floor: 300_000,
            scores: IndexMap::from([("cost".to_string(), 800_000), ("security".to_string(), 300_000)]),
            shortfall: IndexMap::from([("security".to_string(), 300_000)]),
            reasoning: text_map(&[("cost", "I like it"), ("security", "my view")]),
            repairs_left: 8,
        })]
    );
    assert_eq!(net.a("security").due(), net.a("security").due());
}

#[test]
fn a_repair_that_clears_tau_for_everyone_ends_the_run_in_success() {
    let mut net = below_tau();
    let step = net.a("security").submit_repair(RUN, "a better plan", "because", 70, "mine is fine", None).unwrap();
    net.broadcast(step, "security");
    let due = net.a("cost").due();
    assert_eq!(due.len(), 1);
    let Task::Score(task) = &due[0] else { panic!("a score task") };
    let ids: Vec<_> = task.candidates.iter().map(|c| c.id.as_str()).collect();
    assert_eq!(ids, ["r1-security"]);
    assert!(net.a("security").due().is_empty());
    net.answer_score("cost", &[("r1-security", 65.0)]);
    for name in ["cost", "security"] {
        let s = net.a(name).status(None).unwrap();
        assert_eq!(
            (s.state, s.outcome, s.x_star.as_deref(), s.floor, s.status),
            (EvalState::Done, Some(Outcome::Success), Some("r1-security"), Some(650_000), RunStatus::Ended)
        );
    }
    let results = net.log.iter().filter(|t| parse_message(t, None).unwrap().kind() == Kind::Result).count();
    assert_eq!(results, 1);
}

#[test]
fn only_the_bottleneck_can_submit_a_repair_and_only_when_one_is_owed() {
    let mut net = below_tau();
    state_err(net.a("cost").submit_repair(RUN, "x", "r", 50, "w", None), "no repair is owed");
    let mut ok = happy();
    let e = err_of(ok.a("security").submit_repair(RUN, "x", "r", 50, "w", None));
    assert_eq!(e.code(), "state_error");
    assert!(e.to_string().contains("no repair is owed") || e.to_string().contains("has ended"), "{e}");
    invalid_err(net.a("security").submit_repair(RUN, "x", "r", 500, "w", None), "0 to 100");
    assert_eq!(net.a("security").due().len(), 1);
}

#[test]
fn a_repair_from_another_agent_or_when_none_is_owed_is_ignored() {
    let mut net = below_tau();
    let from_cost = build_repair("cost", RUN, 1, "x", "r", &50.into(), "w", None, None).unwrap();
    assert_eq!(reason(&net.a("security").receive(&from_cost, None).unwrap()), "no repair owed from this agent");
    assert!(net.a("security").status(None).unwrap().lacking.is_empty());
    let mut ok = happy();
    let late = build_repair("security", RUN, 1, "x", "r", &50.into(), "w", None, None).unwrap();
    assert_eq!(reason(&ok.a("cost").receive(&late, None).unwrap()), "run over");
    let wrong_round = build_repair("security", RUN, 2, "x", "r", &50.into(), "w", None, None).unwrap();
    let cost = net.a("cost");
    assert_eq!(reason(&cost.receive(&wrong_round, None).unwrap()), "no repair owed from this agent");
    assert!(cost.status(None).unwrap().lacking.is_empty());
}

#[test]
fn a_repair_that_arrives_before_the_state_is_complete_is_held_and_judged_when_it_is() {
    let mut net = after_inits();
    net.answer_score("cost", &[("seed-security", 20.0)]); // security's score on seed-cost is not in yet
    let repair_doc =
        build_repair("security", RUN, 1, "better", "because", &70.into(), "ok", None, Some("77777777-7777-4777-8777-777777777777"))
            .unwrap();
    let cost = net.a("cost");
    let step = cost.receive(&repair_doc, None).unwrap();
    assert_eq!(step.events[0].event, "held");
    assert_eq!(cost.status(None).unwrap().state, EvalState::Wait);
    assert_eq!(cost.receive(&repair_doc, None).unwrap().events[0].event, "held"); // still held, and not asked for again
    net.answer_score("security", &[("seed-cost", 30.0)]); // now the state is complete: the held repair is judged
    let cost = net.a("cost");
    assert_eq!(cost.status(None).unwrap().lacking, [("cost".to_string(), "r1-security".to_string())]);
    assert!(matches!(cost.due()[0], Task::Score(_)));
    assert_eq!(reason(&cost.receive(&repair_doc, None).unwrap()), "duplicate");
}

// --- the result and the confirmation --------------------------------------------

#[test]
fn the_reporter_sends_its_result_by_itself_and_its_run_ends() {
    let mut net = happy();
    let results: Vec<&String> = net.log.iter().filter(|t| parse_message(t, None).unwrap().kind() == Kind::Result).collect();
    assert_eq!(results.len(), 1);
    let m = parse_message(results[0], None).unwrap();
    let Body::Result(r) = &m.body else { panic!("a result") };
    assert_eq!((m.from.as_str(), r.outcome, r.winning_candidate_id.as_str()), ("cost", Outcome::Success, "seed-cost"));
    assert!(check_ok(results[0]));
    let sent = net.events.iter().filter(|(_, e)| e.event == "sent" && e.str("kind") == Some("result")).count();
    assert_eq!(sent, 1);
    assert_eq!(net.a("cost").status(None).unwrap().status, RunStatus::Ended);
    assert!(net.a("cost").due().is_empty());
}

#[test]
fn a_participant_that_is_not_the_reporter_ends_when_it_holds_the_result_and_it_matches() {
    let mut net = happy();
    let rep = net.a("security").report(None).unwrap();
    assert_eq!((rep.status, rep.outcome), (ReportStatus::Confirmed, Some(Outcome::Success)));
    assert_eq!(rep.winner, Some(Winner { id: "seed-cost".to_string(), candidate: Some("plan of cost".to_string()) }));
    assert_eq!(rep.scores, millionths(&[("cost", 800_000), ("security", 750_000)]));
    assert!(rep.shortfall.is_empty());
    assert_eq!(rep.rounds, Some(0));
    assert_eq!(
        rep.results_compared,
        Compared {
            reporter: "cost".to_string(),
            result: Some(Reported { outcome: Outcome::Success, winning_candidate_id: "seed-cost".to_string() }),
            matches: Some(true),
        }
    );
    let own = net.a("cost").report(None).unwrap();
    assert_eq!(own.results_compared.matches, Some(true));
    assert_eq!(own.status, ReportStatus::Confirmed);
}

#[test]
fn a_non_reporter_with_its_outcome_computed_is_not_ended_until_it_holds_the_result() {
    let mut net = after_inits();
    let sec = net.a("security");
    let _ = sec.receive(&score_doc("cost", &[("seed-security", 70.0, "r")], None), None).unwrap();
    let _ = sec.submit_score(RUN, &[sc("seed-cost", 75.0, "r")], None).unwrap();
    let s = sec.status(None).unwrap();
    assert_eq!((s.outcome, s.status), (Some(Outcome::Success), RunStatus::Active));
    assert_eq!(sec.report(None).unwrap().status, ReportStatus::Pending);
    assert!(sec.due().is_empty());
    let _ = sec.receive(&build_result("cost", RUN, Outcome::Success, "seed-cost", None).unwrap(), None).unwrap();
    assert_eq!(sec.status(None).unwrap().status, RunStatus::Ended);
    assert_eq!(sec.report(None).unwrap().status, ReportStatus::Confirmed);
}

#[test]
fn a_result_that_arrives_before_the_agent_has_its_outcome_is_recorded_and_compared_later() {
    let mut net = after_inits();
    let sec = net.a("security");
    let step = sec.receive(&build_result("cost", RUN, Outcome::Success, "seed-cost", None).unwrap(), None).unwrap();
    assert_eq!(names(&step), ["recorded"]);
    assert_eq!(sec.status(None).unwrap().status, RunStatus::Active);
    let _ = sec.receive(&score_doc("cost", &[("seed-security", 70.0, "r")], None), None).unwrap();
    let step = sec.submit_score(RUN, &[sc("seed-cost", 75.0, "r")], None).unwrap();
    assert_eq!(names(&step), ["sent", "outcome", "ended"]);
    assert_eq!(sec.report(None).unwrap().status, ReportStatus::Confirmed);
}

#[test]
fn a_result_is_held_when_its_winner_is_unknown_and_the_agent_has_no_outcome_yet() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc());
    net.answer_init("cost");
    let doc = build_result("cost", RUN, Outcome::BestEffort, "r3-cost", Some("88888888-8888-4888-8888-888888888888")).unwrap();
    assert_eq!(net.a("security").receive(&doc, None).unwrap().events[0].event, "held");
    net.answer_init("security");
    let sec = net.a("security");
    assert_eq!(sec.receive(&doc, None).unwrap().events[0].event, "held"); // same uuid, still no outcome: not kept
    let _ = sec.receive(&score_doc("cost", &[("seed-security", 70.0, "r")], None), None).unwrap();
    let _ = sec.submit_score(RUN, &[sc("seed-cost", 75.0, "r")], None).unwrap();
    let step = sec.receive(&build_result("cost", RUN, Outcome::BestEffort, "r3-cost", None).unwrap(), None).unwrap();
    assert_eq!(reason(&step), "invalid");
    assert!(step.events[0].str("detail").unwrap().contains("not a candidate of the run"));
    assert_eq!(sec.status(None).unwrap().status, RunStatus::Active);
}

#[test]
fn a_result_from_an_agent_that_is_not_the_reporter_is_ignored() {
    let mut net = net_of_three();
    let fake = build_result("security", RUN, Outcome::Success, "seed-security", None).unwrap();
    assert_eq!(reason(&net.a("legal").receive(&fake, None).unwrap()), "not the reporter");
    assert!(net.a("legal").report(None).unwrap().results_compared.result.is_none());
}

#[test]
fn a_result_that_differs_from_the_outcome_sends_a_mismatch_error_and_the_run_is_disputed() {
    let mut net = after_inits();
    let sec = net.a("security");
    let _ = sec.receive(&score_doc("cost", &[("seed-security", 70.0, "r")], None), None).unwrap();
    let _ = sec.submit_score(RUN, &[sc("seed-cost", 75.0, "r")], None).unwrap();
    let step = sec.receive(&build_result("cost", RUN, Outcome::BestEffort, "seed-security", None).unwrap(), None).unwrap();
    assert_eq!(names(&step), ["recorded", "mismatch", "ended"]);
    assert_eq!(step.send.len(), 1);
    let m = parse_message(&step.send[0], None).unwrap();
    let Body::Error(e) = &m.body else { panic!("an error") };
    assert_eq!(
        (m.kind(), m.from.as_str(), e.to.as_str(), e.what.as_str(), e.reason.as_str()),
        (Kind::Error, "security", "cost", "result", "mismatch")
    );
    let s = sec.status(None).unwrap();
    assert_eq!((s.outcome, s.status), (Some(Outcome::Success), RunStatus::Ended)); // a result is not a vote
    let rep = sec.report(None).unwrap();
    assert_eq!(rep.status, ReportStatus::Disputed);
    assert_eq!(rep.results_compared.matches, Some(false));
    assert_eq!(rep.mismatches.sent, 1);
}

#[test]
fn a_mismatch_error_is_read_by_every_agent_also_after_its_run_has_ended() {
    let mut net = happy();
    let err = build_error("security", RUN, "cost", "result", "mismatch", None, None, "differs", None).unwrap();
    for name in ["cost", "security"] {
        assert_eq!(net.a(name).report(None).unwrap().status, ReportStatus::Confirmed);
    }
    let got = net.a("cost").receive(&err, None).unwrap();
    assert_eq!(names(&got), ["mismatch received"]);
    assert_eq!(net.a("cost").status(None).unwrap().status, RunStatus::Ended);
    let rep = net.a("cost").report(None).unwrap();
    assert_eq!(rep.status, ReportStatus::Disputed);
    assert_eq!(rep.mismatches, MismatchSummary { sent: 0, received: strings(&["security"]) });
    let other = build_error("security", RUN, "security", "result", "mismatch", None, None, "", None).unwrap();
    assert_eq!(reason(&net.a("cost").receive(&other, None).unwrap()), "not about the result of the reporter");
}

#[test]
fn the_agent_is_free_for_a_new_run_once_its_run_has_ended() {
    let mut net = happy();
    let cost = net.a("cost");
    let again = "22222222-2222-4222-8222-222222222222";
    assert_eq!(cost.receive(&Init { run_id: again, ..Init::default() }.doc(), None).unwrap().events[0].event, "joined");
    assert_eq!(cost.status(None).unwrap().run_id, again);
    assert_eq!(cost.status(Some(RUN)).unwrap().status, RunStatus::Ended);
    assert_eq!(cost.report(Some(RUN)).unwrap().status, ReportStatus::Confirmed);
}

#[test]
fn data_messages_of_an_ended_run_are_ignored() {
    let mut net = happy();
    let late = score_doc("security", &[("seed-cost", 5.0, "r")], Some("99999999-9999-4999-8999-999999999999"));
    assert_eq!(reason(&net.a("cost").receive(&late, None).unwrap()), "run over");
}

#[test]
fn a_repair_run_that_spends_its_budget_ends_in_best_effort() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_with_params(InitParams::default().budget(1)));
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 20.0)]);
    net.answer_score("security", &[("seed-cost", 30.0)]);
    let Task::Repair(task) = &net.a("security").due()[0] else { panic!("a repair task") };
    assert_eq!(task.repairs_left, 1);
    let step = net.a("security").submit_repair(RUN, "weak", "because", 30, "meh", None).unwrap();
    net.broadcast(step, "security");
    net.answer_score("cost", &[("r1-security", 30.0)]);
    for name in ["cost", "security"] {
        let (s, rep) = (net.a(name).status(None).unwrap(), net.a(name).report(None).unwrap());
        assert_eq!(
            (s.outcome, s.x_star.as_deref(), s.status, rep.rounds),
            (Some(Outcome::BestEffort), Some("seed-cost"), RunStatus::Ended, Some(1))
        );
        assert_eq!(rep.shortfall, millionths(&[("security", 300_000)]));
        assert_eq!(rep.status, ReportStatus::Confirmed);
    }
}

/// A store that holds whatever it was given, or cannot be read.
struct Fixed(ioc_concord::Result<Value>);

impl Store for Fixed {
    fn load(&self) -> ioc_concord::Result<Value> {
        self.0.clone()
    }
    fn save(&mut self, _state: &Value) -> ioc_concord::Result<()> {
        Ok(())
    }
}

#[test]
fn a_saved_state_that_cannot_be_read_is_an_error_and_never_a_fresh_start() {
    let unreadable = Err(ConcordError::state("state file is not valid JSON"));
    for (text, loaded) in [("{oops", unreadable), ("[]", Ok(json!([]))), ("null", Ok(Value::Null)), ("5", Ok(json!(5)))] {
        let e = err_of(Agent::builder("cost").store(Fixed(loaded)).build());
        assert_eq!(e.code(), "state_error", "{text}");
        assert!(!matches!(e, ConcordError::MissingState(_)), "{text}");
    }
    // the file store, with a file that is not JSON
    let path = std::env::temp_dir().join(format!("concord-agent-test-{}.json", std::process::id()));
    std::fs::write(&path, "{oops").unwrap();
    let e = err_of(Agent::builder("cost").store(ioc_concord::store::FileStore::new(&path)).build());
    std::fs::remove_file(&path).unwrap();
    assert_eq!(e.code(), "state_error");
    // nothing saved: a fresh start
    assert!(Agent::builder("cost").store(MemoryStore::new()).build().unwrap().status(None).is_none());
}
