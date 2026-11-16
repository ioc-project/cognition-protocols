//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the model reads: the task texts, filled in from the state of a run, the messages of the others, the end, and a refused answer.
//!
//! The texts are the reference wording of Appendix A of the specification. `the_wording_says_what_appendix_a_asks_for` keeps the two
//! together: each point of A.5 and A.6 is a phrase that must be in the text of its task.
//!
//! Not ported: `test_instructions_refuse_a_task_of_a_run_the_agent_does_not_know_or_of_another_kind` keeps only its first half (a task
//! of an unknown run); the `ValueError` for a task of a kind that does not exist cannot be written, since `Task` is an enum.

mod common;

use std::collections::BTreeMap;

use common::{uuids, RUN};
use indexmap::IndexMap;
use ioc_concord::agent::{Agent, Step};
use ioc_concord::errors::ConcordError;
use ioc_concord::guidance as g;
use ioc_concord::guidance::RawAnchors;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::{Compared, MismatchSummary, Report, ReportStatus, Winner};
use ioc_concord::state::{Abort, Reported};
use ioc_concord::tasks::{InitReplyTask, RepairTask, ScoreTask, Task};
use ioc_concord::wire::{build_init, build_resend, build_score, InitParams, ScoreIn};
use regex::Regex;
use serde_json::{json, Value};

fn flat(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn spec() -> Value {
    json!({"type": "string", "description": "a plan"})
}

fn init_with(spec: &Value, params: Option<&InitParams>) -> String {
    build_init("alice", &["cost", "security"], "pick one", "nothing", "all agree", spec, params, Some(RUN), Some(&uuids("a0a0a0a0")()))
        .expect("init")
}

fn init_doc() -> String {
    init_with(&spec(), None)
}

/// A channel on which every message reaches every agent, the sender included (a broadcast).
struct Net {
    agents: BTreeMap<String, Agent>,
}

impl Net {
    fn new(names: &[&str]) -> Net {
        let agents = names
            .iter()
            .enumerate()
            .map(|(i, n)| (n.to_string(), Agent::builder(n).uuid_fn(uuids(&format!("{:08x}", i + 1))).build().expect("agent")))
            .collect();
        Net { agents }
    }

    fn broadcast(&mut self, send: Vec<String>) {
        let mut queue: std::collections::VecDeque<String> = send.into();
        while let Some(text) = queue.pop_front() {
            for agent in self.agents.values_mut() {
                queue.extend(agent.receive(&text, None).expect("receive").send);
            }
        }
    }

    fn start(&mut self, doc: String) {
        self.broadcast(vec![doc]);
    }

    fn answer_init(&mut self, name: &str, raw_reject: f64, raw_ideal: f64, candidate: Option<&str>) {
        let a = self.agents.get_mut(name).unwrap();
        let task = init_of(a);
        let candidate = candidate.map_or_else(|| format!("plan of {name}"), str::to_string);
        let step =
            a.submit_init_reply(&task.run_id, raw_reject, raw_ideal, &candidate, "because", 80, "I like it", None).expect("init reply");
        self.broadcast(step.send);
    }

    /// Rate every candidate owed with the number `raw` gives for its id.
    fn answer_score(&mut self, name: &str, raw: &[(&str, f64)]) {
        let a = self.agents.get_mut(name).unwrap();
        let task = score_of(a);
        let scores: Vec<ScoreIn> = task
            .candidates
            .iter()
            .map(|c| ScoreIn::new(&c.id, raw.iter().find(|(id, _)| *id == c.id).expect("a rating for the candidate").1, "my view"))
            .collect();
        let step = a.submit_score(&task.run_id, &scores, None).expect("score");
        self.broadcast(step.send);
    }

    fn agent(&mut self, name: &str) -> &mut Agent {
        self.agents.get_mut(name).unwrap()
    }

    fn broadcast_step(&mut self, step: Step) {
        self.broadcast(step.send);
    }
}

fn init_of(a: &Agent) -> InitReplyTask {
    a.due().into_iter().find_map(|t| if let Task::InitReply(t) = t { Some(t) } else { None }).expect("an init-reply is due")
}

fn score_of(a: &Agent) -> ScoreTask {
    a.due().into_iter().find_map(|t| if let Task::Score(t) = t { Some(t) } else { None }).expect("a score is due")
}

fn repair_of(a: &Agent) -> RepairTask {
    a.due().into_iter().find_map(|t| if let Task::Repair(t) = t { Some(t) } else { None }).expect("a repair is due")
}

fn started(spec: &Value, params: Option<&InitParams>) -> Net {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_with(spec, params));
    net
}

/// cost and security answer the init and rate each other low, so that one of them owes a repair: (net, owner, other).
fn two_with_a_repair(spec: &Value) -> (Net, String, String) {
    let mut net = started(spec, None);
    net.answer_init("cost", 10.0, 90.0, Some("Plan of cost"));
    net.answer_init("security", 10.0, 90.0, Some("Plan of security"));
    net.answer_score("cost", &[("seed-security", 30.0)]);
    net.answer_score("security", &[("seed-cost", 30.0)]);
    let owner = net
        .agents
        .iter()
        .find(|(_, a)| a.due().iter().any(|t| matches!(t, Task::Repair(_))))
        .map(|(n, _)| n.clone())
        .expect("a repair is owed");
    let other = if owner == "security" { "cost" } else { "security" }.to_string();
    (net, owner, other)
}

fn happy() -> Net {
    let mut net = started(&spec(), None);
    net.answer_init("cost", 0.0, 100.0, None);
    net.answer_init("security", 0.0, 100.0, None);
    net.answer_score("cost", &[("seed-security", 70.0)]);
    net.answer_score("security", &[("seed-cost", 75.0)]);
    net
}

fn text_of(net: &mut Net, name: &str, task: Task) -> String {
    net.agent(name).instructions(&task).expect("instructions")
}

fn has(text: &str, parts: &[&str]) {
    for p in parts {
        assert!(text.contains(p), "missing {p:?} in:\n{text}");
    }
}

fn re(pattern: &str, text: &str) -> bool {
    Regex::new(pattern).unwrap().is_match(text)
}

// --- the first answer -------------------------------------------------------------------

#[test]
fn the_first_answer_names_the_run_and_has_its_three_parts() {
    let mut net = started(&spec(), None);
    let t = Task::InitReply(init_of(&net.agents["security"]));
    let text = text_of(&mut net, "security", t);
    assert!(text.starts_with("You are security. The run was opened by alice; participants: cost, security."));
    has(&text, &["context: pick one", "candidate format: a plan"]);
    assert!(re(r"x_reject:\s+nothing", &text) && re(r"x_ideal:\s+all agree", &text));
    let found: Vec<&str> =
        ["1. ANCHORS", "2. YOUR PROPOSAL", "3. YOUR OWN SCORE FOR IT"].into_iter().filter(|p| text.contains(p)).collect();
    assert_eq!(found, ["1. ANCHORS", "2. YOUR PROPOSAL", "3. YOUR OWN SCORE FOR IT"]);
}

#[test]
fn the_first_answer_shows_the_parameters_of_the_run() {
    let params = InitParams::default().satisfaction_threshold("0.7").budget(4).timeout(30);
    let mut net = started(&spec(), Some(&params));
    let t = Task::InitReply(init_of(&net.agents["security"]));
    let text = text_of(&mut net, "security", t);
    assert!(text.contains("parameters: threshold 0.7, repair budget 4, eps 0.05, timeout 30 s, retries 3"));
}

#[test]
fn a_closed_list_is_shown_as_the_options_to_choose_from() {
    let mut net = started(&json!({"enum": ["West City", "East Town"]}), None);
    let t = Task::InitReply(init_of(&net.agents["cost"]));
    let text = text_of(&mut net, "cost", t);
    has(
        &text,
        &["candidate format: exactly one of: West City | East Town", "  - West City", "  - East Town", "must be exactly one of these"],
    );
}

// --- a score ----------------------------------------------------------------------------

#[test]
fn a_score_shows_the_anchors_of_the_agent_and_each_candidate_with_the_reasoning_of_its_proposer() {
    let mut net = started(&spec(), None);
    net.answer_init("cost", 10.0, 90.0, None);
    net.answer_init("security", 20.0, 80.0, None);
    let t = Task::Score(score_of(&net.agents["cost"]));
    let text = text_of(&mut net, "cost", t);
    has(
        &text,
        &[
            "TASK: rate 1 candidate of the others",
            "raw_reject=10.0, raw_ideal=90.0",
            "candidate seed-security (proposed by security): plan of security",
            "its rationale: because",
            "its proposer's reasoning for its own score: I like it",
        ],
    );
}

#[test]
fn a_candidate_that_the_agent_has_already_rated_is_pointed_out_with_its_rating() {
    let (mut net, owner, other) = two_with_a_repair(&spec());
    let t = repair_of(&net.agents[&owner]);
    let step = net
        .agent(&owner)
        .submit_repair(&t.run_id, &format!("plan of {other}"), "again, with a new argument", 70, "new arguments", None)
        .unwrap();
    net.broadcast_step(step);
    let t = Task::Score(score_of(&net.agents[&other]));
    let text = text_of(&mut net, &other, t);
    has(
        &text,
        &[
            &format!("candidate r1-{owner} (proposed by {owner}): plan of {other}"),
            &format!("this is the same candidate as seed-{other}, which you rated 80.0"),
            &format!("Same as seed-{other}, which I rated 80.0"),
        ],
    );
}

#[test]
fn a_candidate_that_is_new_has_no_such_hint() {
    let (mut net, owner, other) = two_with_a_repair(&spec());
    let t = repair_of(&net.agents[&owner]);
    let step = net.agent(&owner).submit_repair(&t.run_id, "a new plan", "a better plan", 70, "new arguments", None).unwrap();
    net.broadcast_step(step);
    let t = Task::Score(score_of(&net.agents[&other]));
    assert!(!text_of(&mut net, &other, t).contains("the same candidate as"));
}

// --- a repair ---------------------------------------------------------------------------

#[test]
fn a_repair_shows_the_incumbent_the_bar_the_floor_and_what_each_agent_said() {
    let (mut net, owner, _) = two_with_a_repair(&spec());
    let t = repair_of(&net.agents[&owner]);
    let text = text_of(&mut net, &owner, Task::Repair(t.clone()));
    let inc = &t.incumbent;
    has(
        &text,
        &[
            "REPAIR ROUND, YOU ALONE",
            &format!("Every rating of the incumbent {} is in", inc.id),
            &format!("below the bar of {}", ioc_concord::protocol::format_number(t.threshold)),
            "You have 8 repairs left, this one included.",
            &format!("incumbent {}: {}", inc.id, inc.candidate),
            &format!("ratings of the incumbent (the bar is {}):", ioc_concord::protocol::format_number(t.threshold)),
        ],
    );
    for who in ["cost", "security"] {
        assert!(text.contains(&format!("  - {who}: ")));
    }
    has(&text, &["below the bar by", "my view"]); // the reasoning of the other agent
}

#[test]
fn a_repair_lists_the_candidates_already_in_the_run_and_the_scale_to_rate_the_new_one_on() {
    let (mut net, owner, _) = two_with_a_repair(&spec());
    let t = Task::Repair(repair_of(&net.agents[&owner]));
    let text = text_of(&mut net, &owner, t);
    has(
        &text,
        &[
            "candidates in the run:",
            "  - seed-cost (proposed by cost): Plan of cost",
            "  - seed-security (proposed by security): Plan of security",
            "your own scale, for rating your new candidate: raw_reject=10.0, raw_ideal=90.0",
        ],
    );
}

#[test]
fn a_repair_shows_the_candidate_format_and_the_context_of_the_run() {
    let (mut net, owner, _) = two_with_a_repair(&json!({"enum": ["Plan of cost", "Plan of security"]}));
    let t = Task::Repair(repair_of(&net.agents[&owner]));
    let text = text_of(&mut net, &owner, t);
    has(&text, &["context: pick one", "  - Plan of cost", "  - Plan of security"]);
}

#[test]
fn instructions_refuse_a_task_of_a_run_the_agent_does_not_know_or_of_another_kind() {
    let mut net = started(&spec(), None);
    let task = Task::Score(ScoreTask { run_id: "99999999-9999-4999-8999-999999999999".to_string(), candidates: Vec::new() });
    let err = net.agent("cost").instructions(&task).expect_err("an unknown run");
    assert!(matches!(err, ConcordError::State(_)), "{err:?}");
    // the second half (a task of a kind that does not exist raises ValueError) is impossible: `Task` is an enum
}

// --- the messages of the others -----------------------------------------------------------

/// cost and security, both in the run, and a reply of security that cost has not received yet: (cost, reply).
fn joined() -> (Agent, String) {
    let mut cost = Agent::builder("cost").uuid_fn(uuids("c0c0c0c0")).build().unwrap();
    let mut sec = Agent::builder("security").uuid_fn(uuids("5e5e5e5e")).build().unwrap();
    let init = init_doc();
    let _ = cost.receive(&init, None).unwrap();
    let _ = sec.receive(&init, None).unwrap();
    let reply = sec.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "I like it", None).unwrap().send.remove(0);
    (cost, reply)
}

#[test]
fn step_accepted_has_the_init_when_the_agent_joins_the_run() {
    let init = init_doc();
    assert_eq!(Agent::new("cost").unwrap().receive(&init, None).unwrap().accepted, [init]);
}

#[test]
fn step_accepted_has_the_message_of_another_agent_that_the_agent_recorded() {
    let (mut cost, reply) = joined();
    assert_eq!(cost.receive(&reply, None).unwrap().accepted, [reply]);
}

#[test]
fn step_accepted_leaves_out_what_the_agent_did_not_take() {
    let (mut cost, reply) = joined();
    let _ = cost.receive(&reply, None).unwrap();
    let own = cost.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "I like it", None).unwrap().send.remove(0);
    assert!(cost.receive(&own, None).unwrap().accepted.is_empty()); // its own message, heard on the channel
    assert!(cost.receive(&reply, None).unwrap().accepted.is_empty()); // a message it has already taken
    assert!(cost.receive(&reply.replace("version=\"0.3.0\"", "version=\"0.2.0\""), None).unwrap().accepted.is_empty()); // another version
    let unknown = build_score("security", RUN, &[ScoreIn::new("seed-cost", 5, "r")], None).unwrap();
    assert!(Agent::new("legal").unwrap().receive(&unknown, None).unwrap().accepted.is_empty()); // an unknown run
    assert!(cost.receive("not a CONCORD message", None).unwrap().accepted.is_empty());
}

fn other_score() -> String {
    build_score("security", RUN, &[ScoreIn::new("seed-cost", 5, "r")], None).unwrap()
}

#[test]
fn the_delivery_is_the_messages_as_they_arrived_after_a_header_and_nothing_when_there_are_none() {
    assert_eq!(g::render_delivery::<&str>(&[]), "");
    let (init, other) = (init_doc(), other_score());
    let text = g::render_delivery(&[format!("\n{init}\n"), other.clone()]);
    assert!(text.starts_with(&format!("{}\n\n<concord-init ", g::DELIVERY_HEADER)));
    let (i, o) = (text.find(init.trim()).expect("init"), text.find(other.trim()).expect("other"));
    assert!(i < o);
}

#[test]
fn a_resend_is_shown_as_the_message_it_carries() {
    let inner = init_doc();
    let wrapper = build_resend("alice", RUN, &inner, None, None).unwrap();
    assert_eq!(g::unwrap(&wrapper).trim(), inner.trim());
    assert_eq!(g::unwrap(&inner), inner);
    assert!(!g::render_delivery(&[wrapper]).contains("concord-resend"));
}

// --- the end ----------------------------------------------------------------------------

fn report() -> Report {
    Report {
        run_id: RUN.to_string(),
        outcome: Some(Outcome::Success),
        winner: Some(Winner { id: "seed-cost".to_string(), candidate: Some("plan of cost".to_string()) }),
        scores: IndexMap::from([("cost".to_string(), 800_000), ("security".to_string(), 650_000)]),
        shortfall: IndexMap::new(),
        rounds: Some(2),
        reason: None,
        results_compared: Compared {
            reporter: "cost".to_string(),
            result: Some(Reported { outcome: Outcome::Success, winning_candidate_id: "seed-cost".to_string() }),
            matches: Some(true),
        },
        mismatches: MismatchSummary { sent: 0, received: Vec::new() },
        status: ReportStatus::Confirmed,
    }
}

fn reported(outcome: Outcome) -> Option<Reported> {
    Some(Reported { outcome, winning_candidate_id: "seed-cost".to_string() })
}

#[test]
fn the_end_tells_the_reporter_that_it_reported_and_the_others_that_the_reporter_did() {
    let reporter = g::render_end("cost", &report());
    let other = g::render_end("security", &report());
    assert!(reporter.contains("You reported the result to all the participants: SUCCESS, winning candidate seed-cost (plan of cost)."));
    has(
        &other,
        &[
            "cost reported the result to all the participants: SUCCESS",
            "Final scores of the winning candidate, normalized 0-1: cost 0.8, security 0.65. Repair rounds used: 2.",
            "Your own outcome matches the reporter's result.",
            "Status of the run for you: confirmed.",
            "do not re-open the decision",
        ],
    );
    assert!(!reporter.contains("matches"));
}

#[test]
fn the_end_names_who_fell_short_and_a_difference_with_the_reporter() {
    let mut r = report();
    r.outcome = Some(Outcome::BestEffort);
    r.shortfall = IndexMap::from([("security".to_string(), 100_000)]);
    r.results_compared = Compared { reporter: "cost".to_string(), result: None, matches: Some(false) };
    let text = g::render_end("security", &r);
    has(
        &text,
        &[
            "BEST_EFFORT",
            "Below the bar: security by 0.1.",
            "Your own outcome DIFFERS from the reporter's result.",
            "naming the agents below their floor",
        ],
    );
}

#[test]
fn the_end_of_a_disputed_run_says_it_is_not_to_be_relied_on_and_does_not_tell_the_model_to_proceed() {
    let mut r = report();
    r.status = ReportStatus::Disputed;
    r.mismatches = MismatchSummary { sent: 1, received: vec!["x".to_string()] };
    r.results_compared = Compared { reporter: "cost".to_string(), result: None, matches: Some(false) };
    let text = g::render_end("security", &r);
    has(
        &text,
        &[
            "Status of the run for you: disputed.",
            "This run is DISPUTED (mismatch errors sent: 1, received: 1)",
            "no result of a disputed run is to be relied on: do not act on this outcome",
            "do not re-open the decision",
        ],
    );
    assert!(!text.contains("proceed with the winning candidate"));
    assert!(g::render_end("security", &report()).contains("proceed with the winning candidate"));
    // a confirmed run still does
}

#[test]
fn the_end_tells_an_initiator_that_cannot_check_the_outcome_that_it_is_the_reporters() {
    let mut r = report();
    r.status = ReportStatus::Unverified;
    r.results_compared = Compared { reporter: "cost".to_string(), result: reported(Outcome::Success), matches: None };
    let text = g::render_end("alice", &r);
    assert!(text.contains("You have no outcome of your own to compare it with: this is the result of the reporter."));
    assert!(!text.contains("DISPUTED"));
    assert!(text.contains("proceed with the winning candidate"));
}

#[test]
fn the_end_of_an_aborted_run_certifies_nothing() {
    let mut r = report();
    r.outcome = Some(Outcome::Aborted);
    r.winner = None;
    r.scores = IndexMap::new();
    r.rounds = None;
    r.reason = Some(Abort { reason: "missing-agent".to_string(), agent: Some("legal".to_string()), superseded_by: None, note: None });
    let text = g::render_end("cost", &r);
    has(&text, &["The run was aborted (missing-agent, agent legal).", "nothing is certified"]);
    assert!(!text.contains("winning"));
}

#[test]
fn an_initiator_that_is_not_a_participant_learns_the_outcome_from_the_result() {
    let mut r = report();
    r.outcome = None;
    r.scores = IndexMap::new();
    r.rounds = None;
    r.status = ReportStatus::Unverified;
    r.results_compared = Compared { reporter: "cost".to_string(), result: reported(Outcome::Success), matches: None };
    let text = g::render_end("alice", &r);
    has(
        &text,
        &[
            "cost reported the result to all the participants: SUCCESS, winning candidate seed-cost",
            "Status of the run for you: unverified.",
        ],
    );
    assert!(!text.contains("Final scores") && !text.contains("matches"));
    let mut open = report();
    open.outcome = None;
    open.winner = None;
    open.scores = IndexMap::new();
    open.rounds = None;
    open.results_compared = Compared { reporter: "cost".to_string(), result: None, matches: None };
    assert!(g::render_end("alice", &open).contains("not over"));
}

#[test]
fn the_end_of_a_real_run_comes_from_its_report() {
    let net = happy();
    let text = g::render_end("security", &net.agents["security"].report(None).unwrap());
    has(&text, &["The run is over. cost reported the result", "Status of the run for you: confirmed."]);
    assert!(g::render_end("cost", &net.agents["cost"].report(None).unwrap()).contains("You reported"));
}

// --- the checks that go beyond the specification -------------------------------------------

fn good(over: Value) -> Value {
    let mut base = json!({"raw_reject": 10, "raw_ideal": 90, "candidate": "plan", "raw": 70, "rationale": "the facts ".repeat(4),
                          "reasoning": "because ".repeat(4)});
    for (k, v) in over.as_object().unwrap() {
        base[k] = v.clone();
    }
    base
}

fn rating(id: &str, raw: Value) -> Value {
    json!({"scores": [{"id": id, "raw": raw, "reasoning": "because ".repeat(4)}]})
}

const ANCHORS: Option<RawAnchors> = Some(RawAnchors { raw_reject: 10.0, raw_ideal: 90.0 });

#[test]
fn an_answer_with_reasons_and_a_raw_score_has_no_problem() {
    assert!(g::check_answer("init-reply", &good(json!({})), None).is_empty());
    assert!(g::check_answer("repair", &good(json!({})), ANCHORS).is_empty());
    assert!(g::check_answer("score", &rating("seed-cost", json!(70)), ANCHORS).is_empty());
}

#[test]
fn a_reasoning_that_is_too_short_to_be_one_is_named() {
    let found = g::check_answer("init-reply", &good(json!({"reasoning": "ok"})), None);
    assert_eq!(found.len(), 1);
    assert!(found[0].starts_with("the reasoning is too short") && found[0].contains("\"too expensive\" is not a reason"));
    assert!(g::check_answer("repair", &good(json!({"rationale": ""})), None)[0].starts_with("the rationale is too short"));
    let short = json!({"scores": [{"id": "r1-cost", "raw": 50, "reasoning": "no"}]});
    let found = g::check_answer("score", &short, Some(RawAnchors { raw_reject: 0.0, raw_ideal: 90.0 }));
    assert_eq!(found.len(), 1);
    assert!(found[0].starts_with("the reasoning for r1-cost is too short"));
}

#[test]
fn a_number_of_zero_to_one_sent_as_a_raw_score_is_taken_for_a_normalised_one() {
    let found = g::check_answer("init-reply", &good(json!({"raw": 0.8})), None);
    assert_eq!(found.len(), 1);
    assert!(found[0].contains("your score is 0.8: it looks like a normalised 0-1 score"));
    let found = g::check_answer("score", &rating("seed-cost", json!(0.5)), ANCHORS);
    assert_eq!(found.len(), 1);
    assert!(found[0].contains("your rating of seed-cost is 0.5"));
    assert!(g::check_answer("score", &rating("seed-cost", json!(0.5)), None).is_empty());
}

#[test]
fn a_raw_score_of_zero_or_one_is_a_raw_score() {
    for raw in [json!(0), json!(1), json!(0.0), json!(1.0)] {
        assert!(g::check_answer("init-reply", &good(json!({"raw": raw})), None).is_empty(), "{raw}");
        assert!(g::check_answer("repair", &good(json!({"raw": raw})), ANCHORS).is_empty(), "{raw}");
        assert!(g::check_answer("score", &rating("seed-cost", raw.clone()), ANCHORS).is_empty(), "{raw}");
    }
    assert!(!g::check_answer("init-reply", &good(json!({"raw": 0.5})), None).is_empty());
    // between them it is not: a normalised score
}

#[test]
fn anchors_of_one_or_less_are_taken_for_normalised_ones() {
    let found = g::check_answer("init-reply", &good(json!({"raw_reject": 0, "raw_ideal": 1, "raw": 1})), None);
    assert_eq!(found.len(), 1);
    assert!(found[0].starts_with("your anchors are 0 and 1: they look like normalised 0-1 scores"));
    assert!(g::check_answer("init-reply", &good(json!({"raw_reject": 0, "raw_ideal": 100, "raw": 100})), None).is_empty());
}

#[test]
fn a_rating_beyond_an_anchor_is_not_refused_since_the_specification_clamps_it() {
    assert!(g::check_answer("score", &rating("seed-cost", json!(99)), ANCHORS).is_empty());
    assert!(g::check_answer("init-reply", &good(json!({"raw": 3})), None).is_empty());
}

// --- a refused answer ---------------------------------------------------------------------

#[test]
fn every_refusal_says_what_to_do_and_not_only_what_is_wrong() {
    let unexpected = g::unexpected("KeyError", "'raw'");
    let hints: Vec<(&str, &str)> = vec![
        ("raw_ideal (20.0) must be strictly greater than raw_reject (90.0). These are satisfaction ratings", "Rate the two outcomes again"),
        (
            "a score message covers exactly the candidates not rated yet: owed seed-a; given seed-b",
            "Rate exactly the candidates the task lists",
        ),
        ("the candidate must be exactly one of the listed options (A; B)", "put any extra detail in the rationale"),
        ("the score for seed-a must be a number from 0 to 100 with at most 6 decimal places, not 'x'", "number from 0 to 100"),
        ("your answer lacks `raw` (the score of your proposal).", "every required field"),
        ("`scores` must be a list of entries (id, raw, reasoning), not text.", "separate entries"),
        (&unexpected, "Answer once more"),
        ("something nobody foresaw", "Fix what the message says and answer again"),
    ];
    for (fault, hint) in hints {
        let text = g::refusal_text(fault, 0);
        assert!(text.starts_with(&format!("refused: {}", fault.trim().trim_end_matches('.'))), "{text}");
        assert!(text.contains("Nothing was sent.\nNEXT: "), "{text}");
        assert!(text.contains(hint) && !text.contains(".."), "{fault}: {text}");
    }
}

#[test]
fn an_answer_that_is_already_given_is_settled_and_is_not_to_be_retried() {
    let faults = [
        "submit_init_reply: this agent has already sent its init reply",
        "submit_score: this agent owes no score now",
        "submit_repair: no repair is owed from this agent now",
        "the agent knows no run 'x'",
    ];
    for fault in faults {
        for error in [ConcordError::state(fault), ConcordError::missing_state(fault)] {
            assert!(g::settled(&error));
            let text = g::refusal(&error, 5);
            assert!(text.contains("already answered, or not yours to answer") && !text.contains("refused the same way"), "{text}");
        }
    }
}

#[test]
fn the_text_of_a_refusal_alone_is_never_settled_and_the_words_of_an_answer_cannot_make_it_so() {
    // a string says nothing of its type: `settled` takes an error only, and `refusal_text` is never settled
    let text = g::refusal_text("submit_init_reply: this agent has already sent its init reply", 5);
    assert!(!text.contains("already answered, or not yours to answer") && text.contains("refused the same way 5 times"), "{text}");
    let quoting = ConcordError::invalid_message("the candidate must be exactly one of the listed options (the plan has ended; B)");
    assert!(!g::settled(&quoting));
    let text = g::refusal(&quoting, 2);
    assert!(text.contains("put any extra detail in the rationale") && text.contains("refused the same way 2 times"), "{text}");
}

#[test]
fn the_same_refusal_twice_in_a_row_tells_the_model_to_change_the_answer() {
    let fault = "your answer lacks `raw` (the score of your proposal).";
    assert!(!g::refusal_text(fault, 0).contains("refused the same way"));
    assert!(g::refusal_text(fault, 3).contains("You have been refused the same way 3 times in a row"));
}

#[test]
fn what_an_answer_sent_is_shown_next_to_what_was_expected() {
    let strings = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let text = g::lacks(&["`raw` (the score)"], &strings(&["candidate", "rationale"]), &strings(&["candidate", "rationale", "raw"]));
    assert_eq!(text, "your answer lacks `raw` (the score). You sent: candidate, rationale. Expected: candidate, rationale, raw.");
    assert_eq!(g::lacks(&["`raw`"], &[], &[]), "your answer lacks `raw`.");
    assert_eq!(g::wrong_shape("scores", "a list", &json!("text")), "`scores` must be a list, not text.");
    let kinds: Vec<String> = [json!(3), json!(1.5), json!(["a"]), json!({"a": 1}), json!(true)]
        .iter()
        .map(|v| g::wrong_shape("f", "x", v).split("not ").nth(1).unwrap().to_string())
        .collect();
    assert_eq!(kinds, ["a number.", "a number.", "a list.", "an object.", "true or false."]);
}

#[test]
fn the_hints_fit_the_errors_the_library_really_raises() {
    let mut net = started(&json!({"enum": ["A", "B"]}), None);
    let a = net.agent("cost");
    let refused = |r: ioc_concord::Result<Step>| r.expect_err("the library refuses");
    assert_eq!(g::hint_for(&refused(a.submit_init_reply(RUN, 90, 20, "A", "r", 50, "r", None))), g::HINT_ANCHORS);
    assert_eq!(g::hint_for(&refused(a.submit_init_reply(RUN, 0, 100, "somewhere else", "r", 50, "r", None))), g::HINT_FORMAT);
    assert_eq!(g::hint_for(&refused(a.submit_init_reply(RUN, 0, 100, "A", "r", 500, "r", None))), g::HINT_NUMBER);
    net.answer_init("cost", 0.0, 100.0, Some("A"));
    net.answer_init("security", 0.0, 100.0, Some("B"));
    let a = net.agent("cost");
    assert_eq!(g::hint_for(&refused(a.submit_init_reply(RUN, 0, 100, "A", "r", 50, "r", None))), g::HINT_ALREADY);
    assert_eq!(g::hint_for(&refused(a.submit_score(RUN, &[ScoreIn::new("seed-cost", 50, "r")], None))), g::HINT_IDS);
    let twice = [ScoreIn::new("seed-security", 50, "r"), ScoreIn::new("seed-security", 60, "r")];
    assert_eq!(g::hint_for(&refused(a.submit_score(RUN, &twice, None))), g::HINT_IDS);
}

// --- what the wording must say: Appendix A of the specification -----------------------------------

/// (the point of the appendix, the text it belongs to, a phrase that must be in it)
const CONTRACT: &[(&str, &str, &str)] = &[
    (
        "A.5 first answer: the participants and the parameters of the init",
        "first",
        r"participants: cost, security.*parameters: threshold 0\.6, repair budget 8",
    ),
    ("A.5 first answer: the anchors are the satisfaction of the role, not today's state", "first", r"not how true it is today"),
    (
        "A.5 first answer: low for the reject outcome, strictly higher for the ideal one",
        "first",
        r"LOW number, x_ideal a HIGH one strictly above it",
    ),
    ("A.5 first answer: raw, never normalized", "first", r"never a normalised 0-1 score"),
    ("A.5 first answer: fixed for the run", "first", r"stay fixed for the whole run"),
    ("A.5 first answer: one candidate, not a compromise", "first", r"do not pre-compromise"),
    ("A.5 first answer: evidence against the others, only where it has any", "first", r"only where you have evidence against them"),
    ("A.5 first answer: it rates its own proposal on the same scale", "first", r"on the same raw scale"),
    (
        "A.5 first answer: what it writes is read by every agent, what it leaves out is invisible",
        "first",
        r"invisible to every other participant.*read by every participant",
    ),
    ("A.5 score: the candidates listed, none of its own", "score", r"rate \d+ candidates? of the others.*Rate each candidate listed"),
    ("A.5 score: the raw scale of its anchors for the whole run", "score", r"the same scale as your anchors .* for the whole run"),
    ("A.5 score: concrete reasons, evidence against included", "score", r"concrete reasons for each number .* both for and against it"),
    (
        "A.5 score: a number changes only for a named fact, and the reasoning says which and from whom",
        "score",
        r"Move a number only for a real change \(a specific fact .* what changed your mind and who shared it",
    ),
    ("A.5 score: it does not refuse to score", "score", r"Never refuse to score"),
    ("A.5 repair: the bottleneck alone", "repair", r"YOU ALONE"),
    ("A.5 repair: the candidates already in the pool", "repair", r"candidates in the run:.*seed-cost \(proposed by cost\)"),
    ("A.5 repair: the threshold, the floor and the shortfall", "repair", r"below the bar of .*ratings of the incumbent \(the bar is"),
    (
        "A.5 repair: it does not push another participant below the threshold",
        "repair",
        r"does not knowingly push any other agent below the threshold",
    ),
    (
        "A.5 repair: it answers the strongest argument against its position",
        "repair",
        r"answers the strongest reasoning against your position",
    ),
    (
        "A.5 repair: it may re-propose a candidate of the pool, with new arguments",
        "repair",
        r"your own earlier proposal if you hold it is right .* new arguments",
    ),
    ("A.5 repair: if none exists, the best one and the conflict", "repair", r"state the conflict plainly"),
    ("A.5 repair: the normalized numbers are never sent as raw", "repair", r"never echo one back as your own rating"),
    ("A.5 repair: the threshold and the anchors are not changed", "repair", r"Do not lower the threshold"),
    ("A.5 end: the decision is not reopened", "end", r"do not re-open the decision"),
    ("A.5 end: on BEST_EFFORT, who fell short", "end", r"BEST_EFFORT, say so when you report, naming the agents below their floor"),
    (
        "A.6 1: what it holds is a fact, a report of the others is evidence, not set aside because it cannot be checked",
        "score",
        r"What you hold is a fact\. .* do not set it aside only because you cannot check it",
    ),
    (
        "A.6 2: evidence is weighed, a specific report counts for more, conflicts are said and resolved",
        "score",
        r"a specific report counts for more than a vague one, and if a report contradicts .* state which you rely on and why",
    ),
    (
        "A.6 3: another agent's reading is not evidence against its own facts",
        "score",
        r"never because someone offers another reading of them",
    ),
    (
        "A.6 4: nothing is invented",
        "score",
        r"Do not speculate, and do not assume a fix, a delay or a way around .* do not invent a cause that reconciles them",
    ),
    (
        "A.6 5: the evidence is stated before the number, and the number fits",
        "score",
        r"state what the evidence implies for it, in its own words, then give the number, and check that it matches",
    ),
    (
        "A.6 6: the number of another agent and the wording of a candidate are not reasons",
        "score",
        r"other agents' numbers, are not reasons",
    ),
    (
        "A.6 7: ratings are honest and the criteria are not softened",
        "score",
        r"never inflate to seem agreeable or deflate for leverage, and never soften your criteria",
    ),
    ("A.6 8: what is not written is invisible", "score", r"what you leave out is invisible to the rest of the run"),
    (
        "A.3 the points on evidence are part of the standing context",
        "run",
        r"How to weigh evidence:.*What you hold is a fact\..*never because someone offers another reading of them.*other agents' numbers, are not reasons",
    ),
    (
        "A.3 the run: the four steps and what the model receives and sends",
        "run",
        r"1\. First answer.*2\. Score.*3\. Repair.*4\. Result.*What you receive.*What you send",
    ),
    (
        "A.3 the initiator: every field of the init, the order of the participants and the genuine bar",
        "start",
        r"participants:.*first one reports the outcome.*satisfaction_threshold:.*not a level you are sure is reachable.*x_reject:.*never the means.*x_ideal:.*context:",
    ),
];

#[test]
fn the_wording_says_what_appendix_a_asks_for() {
    let mut first = started(&spec(), None);
    let t = Task::InitReply(init_of(&first.agents["cost"]));
    let first_text = text_of(&mut first, "cost", t);
    let mut scoring = started(&spec(), None);
    scoring.answer_init("cost", 0.0, 100.0, None);
    scoring.answer_init("security", 0.0, 100.0, None);
    let t = Task::Score(score_of(&scoring.agents["cost"]));
    let score_text = text_of(&mut scoring, "cost", t);
    let (mut repairing, owner, _) = two_with_a_repair(&spec());
    let t = Task::Repair(repair_of(&repairing.agents[&owner]));
    let repair_text = text_of(&mut repairing, &owner, t);
    let end_text = g::render_end("security", &happy().agents["security"].report(None).unwrap());
    let texts: BTreeMap<&str, String> = BTreeMap::from([
        ("first", first_text),
        ("score", score_text),
        ("repair", repair_text),
        ("end", end_text),
        ("run", g::RUN_DESCRIPTION.clone()),
        ("start", g::INITIATOR_GUIDANCE.clone()),
    ]);
    let missing: Vec<&str> =
        CONTRACT.iter().filter(|(_, key, phrase)| !re(phrase, &flat(&texts[key]))).map(|(point, _, _)| *point).collect();
    assert!(missing.is_empty(), "the wording no longer says: {}", missing.join("; "));
}

#[test]
fn the_guidance_for_the_initiator_has_a_line_for_each_field_it_fills() {
    for (field, _) in g::START_PARAMS {
        assert!(g::INITIATOR_GUIDANCE.contains(&format!("\n- {field}: ")), "{field}");
    }
    assert!(g::INITIATOR_GUIDANCE.starts_with("## Starting the run: you are the initiator"));
}
