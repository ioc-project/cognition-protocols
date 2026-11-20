//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Timers, gaps and silence: what the node does when messages are lost or an agent stops answering.

mod common;

use std::collections::BTreeSet;

use common::{uuids, SCell, RUN};
use indexmap::IndexMap;
use ioc_concord::agent::{Agent, Event, Step};
use ioc_concord::engine::EvalState;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::ReportStatus;
use ioc_concord::state::{Abort, RunStatus};
use ioc_concord::store::MemoryStore;
use ioc_concord::wire::{
    build_cancel, build_init, build_init_reply, build_repair, build_result, build_score, parse_message, peek, Body, InitParams, Kind,
    ScoreIn,
};
use serde_json::json;

fn params() -> InitParams {
    InitParams::default().timeout(10).retries(2)
}

fn init_doc(participants: &[&str], p: InitParams) -> String {
    let spec = json!({"type": "string", "description": "a plan"});
    build_init("alice", participants, "pick one", "nothing", "all agree", &spec, Some(&p), Some(RUN), Some(&uuids("a0a0a0a0")())).unwrap()
}

fn kinds(step: &Step) -> Vec<Kind> {
    step.send.iter().map(|t| parse_message(t, None).unwrap().kind()).collect()
}

/// A `<concord-error>` as the tests read it.
#[derive(Debug, PartialEq)]
struct Error {
    from: String,
    to: String,
    what: String,
    reason: String,
    candidate: Option<String>,
}

fn errors(step: &Step) -> Vec<Error> {
    step.send
        .iter()
        .filter(|t| peek(t).unwrap().tag == "concord-error")
        .map(|t| {
            let m = parse_message(t, None).unwrap();
            let Body::Error(e) = m.body else { unreachable!() };
            Error { from: m.from, to: e.to, what: e.what, reason: e.reason, candidate: e.candidate }
        })
        .collect()
}

fn timers(a: &Agent) -> Vec<(String, f64)> {
    let mut t: Vec<_> = a.status(None).unwrap().timers.into_iter().collect();
    t.sort_by(|x, y| x.0.cmp(&y.0));
    t
}

fn timer_keys(a: &Agent) -> Vec<String> {
    timers(a).into_iter().map(|(k, _)| k).collect()
}

fn tm(items: &[(&str, f64)]) -> Vec<(String, f64)> {
    items.iter().map(|(k, v)| (k.to_string(), *v)).collect()
}

fn joined_with(now: f64, p: InitParams, names: &[&str], me: &str) -> Agent {
    let mut a = Agent::builder(me).uuid_fn(uuids("cccccccc")).build().unwrap();
    let _ = a.receive(&init_doc(names, p), Some(now)).unwrap();
    a
}

fn joined(now: f64) -> Agent {
    joined_with(now, params(), &["cost", "security"], "cost")
}

fn reply(who: &str, uid: Option<&str>) -> String {
    build_init_reply(who, RUN, &0.into(), &100.into(), "plan", "because", &80.into(), "good", None, uid).unwrap()
}

fn score(who: &str, items: &[(&str, i64)]) -> String {
    build_score(who, RUN, &items.iter().map(|(id, raw)| ScoreIn::new(*id, *raw, "r")).collect::<Vec<_>>(), None).unwrap()
}

const DAMAGED: &str = "55555555-5555-4555-8555-555555555555";

/// A channel on which every message reaches every agent, the sender included (a broadcast).
/// Whether the channel loses the text (first) on its way to the agent (second).
type Loss = Box<dyn Fn(&str, &str) -> bool>;

struct Net {
    now: SCell<f64>, // the clock of every agent, moved by the test
    agents: IndexMap<String, Agent>,
    stores: IndexMap<String, MemoryStore>,
    events: Vec<(String, Event)>,
    log: Vec<String>,
    /// whether the channel loses `text` on its way to the agent `name`
    drop: Loss,
    /// agents that hear nothing, say nothing and are never ticked
    dead: BTreeSet<String>,
}

impl Net {
    fn new(names: &[&str]) -> Net {
        let now = SCell::new(0.0);
        let (mut agents, mut stores) = (IndexMap::new(), IndexMap::new());
        for (i, n) in names.iter().enumerate() {
            let store = MemoryStore::new();
            let clock = now.clone();
            let agent = Agent::builder(n)
                .store(store.clone())
                .clock(move || clock.get())
                .uuid_fn(uuids(&format!("{:08x}", i + 1)))
                .build()
                .unwrap();
            agents.insert(n.to_string(), agent);
            stores.insert(n.to_string(), store);
        }
        Net { now, agents, stores, events: Vec::new(), log: Vec::new(), drop: Box::new(|_, _| false), dead: BTreeSet::new() }
    }

    fn a(&mut self, name: &str) -> &mut Agent {
        self.agents.get_mut(name).unwrap()
    }

    /// Deliver what `step` sends to every agent, then what the agents send in answer, in the order it was sent.
    fn broadcast(&mut self, step: Step, who: &str) {
        self.events.extend(step.events.into_iter().map(|e| (who.to_string(), e)));
        let mut queue: std::collections::VecDeque<String> = step.send.into();
        while let Some(text) = queue.pop_front() {
            self.log.push(text.clone());
            for (name, a) in self.agents.iter_mut() {
                if self.dead.contains(name) || (self.drop)(&text, name) {
                    continue;
                }
                let got = a.receive(&text, None).unwrap();
                self.events.extend(got.events.into_iter().map(|e| (name.clone(), e)));
                queue.extend(got.send);
            }
        }
    }

    fn start(&mut self, doc: String) {
        self.broadcast(Step { now: 0.0, send: vec![doc], forward: vec![], accepted: vec![], events: vec![] }, "");
    }

    fn answer_init_raw(&mut self, name: &str, raw: i64) {
        let a = self.a(name);
        let rid = a.due().into_iter().find(|t| t.kind() == "init-reply").unwrap().run_id().to_string();
        let step = a.submit_init_reply(&rid, 0, 100, &format!("plan of {name}"), "because", raw, "I like it", None).unwrap();
        self.broadcast(step, name);
    }

    fn answer_init(&mut self, name: &str) {
        self.answer_init_raw(name, 80);
    }

    fn answer_score(&mut self, name: &str, raw: &[(&str, i64)]) {
        let a = self.a(name);
        let task = a.due().into_iter().find_map(|t| if let ioc_concord::tasks::Task::Score(s) = t { Some(s) } else { None }).unwrap();
        let scores: Vec<ScoreIn> =
            task.candidates.iter().map(|c| ScoreIn::new(&c.id, raw.iter().find(|(id, _)| *id == c.id).unwrap().1, "my view")).collect();
        let step = a.submit_score(&task.run_id, &scores, None).unwrap();
        self.broadcast(step, name);
    }
}

fn tick_all(net: &mut Net, now: f64) {
    net.now.set(now);
    let names: Vec<String> = net.agents.keys().cloned().collect();
    for name in names {
        if !net.dead.contains(&name) {
            let step = net.a(&name).tick(Some(now)).unwrap();
            net.broadcast(step, &name);
        }
    }
}

/// Let time pass, every agent ticking, until nobody has a timer left.
fn settle(net: &mut Net, start: f64) -> f64 {
    let mut now = start;
    for _ in 0..40 {
        if net.agents.iter().all(|(n, a)| net.dead.contains(n) || a.next_wakeup().is_none()) {
            return now;
        }
        now += 10.0;
        tick_all(net, now);
    }
    panic!("the timers never end");
}

fn counts(net: &Net, kind: &str) -> Vec<String> {
    net.log.iter().filter(|t| peek(t).unwrap().tag == format!("concord-{kind}")).map(|t| peek(t).unwrap().attrs["from"].clone()).collect()
}

fn tag_from(text: &str) -> (String, String) {
    let p = peek(text).unwrap();
    (p.tag.clone(), p.attrs.get("from").cloned().unwrap_or_default())
}

// --- the timers --------------------------------------------------------------------

#[test]
fn a_timer_starts_for_every_message_that_becomes_owed_and_a_message_ends_it() {
    let mut a = joined(100.0);
    assert_eq!(timers(&a), tm(&[("init-reply|security", 110.0)]));
    assert_eq!(a.next_wakeup(), Some(110.0));
    let _ = a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good", Some(101.0)).unwrap();
    assert_eq!(timers(&a), tm(&[("init-reply|security", 110.0)]));
    let _ = a.receive(&reply("security", None), Some(105.0)).unwrap();
    assert_eq!(timers(&a), tm(&[("score|security|seeds", 115.0)])); // owed once every init reply is in
    assert_eq!(a.next_wakeup(), Some(115.0));
    let _ = a.receive(&score("security", &[("seed-cost", 70)]), Some(106.0)).unwrap();
    assert!(timers(&a).is_empty());
}

#[test]
fn a_timer_that_expires_asks_again_and_the_wait_is_retries_plus_one_timeouts() {
    let mut a = joined(0.0);
    assert!(a.tick(Some(9.0)).unwrap().send.is_empty());
    assert_eq!(a.next_wakeup(), Some(10.0));
    let step = a.tick(Some(10.0)).unwrap();
    assert_eq!(
        errors(&step),
        vec![Error { from: "cost".into(), to: "security".into(), what: "init-reply".into(), reason: "missing".into(), candidate: None }]
    );
    assert_eq!(a.next_wakeup(), Some(20.0));
    assert!(errors(&a.tick(Some(19.0)).unwrap()).is_empty());
    assert_eq!(errors(&a.tick(Some(20.0)).unwrap()).len(), 1);
    assert_eq!(a.next_wakeup(), Some(30.0));
    let step = a.tick(Some(30.0)).unwrap();
    assert_eq!(step.events.iter().map(|e| e.event.as_str()).collect::<Vec<_>>(), ["aborted", "sent"]);
    assert_eq!(step.send.len(), 1);
    let c = parse_message(&step.send[0], None).unwrap();
    let Body::Cancel(cancel) = &c.body else { panic!("a cancel") };
    assert_eq!(
        (c.kind(), c.from.as_str(), cancel.reason.as_str(), cancel.agent.as_deref()),
        (Kind::Cancel, "cost", "missing-agent", Some("security"))
    );
    let (s, rep) = (a.status(None).unwrap(), a.report(None).unwrap());
    assert_eq!((s.status, s.outcome, s.timers.is_empty()), (RunStatus::Aborted, Some(Outcome::Aborted), true));
    assert_eq!(a.next_wakeup(), None);
    assert_eq!(rep.reason, Some(Abort { reason: "missing-agent".into(), agent: Some("security".into()), superseded_by: None, note: None }));
    assert!(a.tick(Some(1000.0)).unwrap().send.is_empty() && a.due().is_empty());
}

#[test]
fn with_no_retries_the_first_expiry_gives_up() {
    let mut a = joined_with(0.0, InitParams::default().timeout(10).retries(0), &["cost", "security"], "cost");
    let step = a.tick(Some(10.0)).unwrap();
    assert_eq!(kinds(&step), [Kind::Cancel]);
    assert_eq!(a.status(None).unwrap().status, RunStatus::Aborted);
}

#[test]
fn the_timer_of_each_author_is_its_own_and_a_batch_is_done_only_when_nothing_in_it_is_lacking() {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.start(init_doc(&names, params()));
    for n in names {
        net.answer_init(n);
    }
    let cost = net.a("cost");
    assert_eq!(timer_keys(cost), ["score|legal|seeds", "score|security|seeds"]);
    let _ = cost.receive(&score("security", &[("seed-cost", 70), ("seed-legal", 70)]), None).unwrap();
    assert_eq!(timer_keys(cost), ["score|legal|seeds"]);
    let _ = cost.receive(&score("legal", &[("seed-cost", 70)]), None).unwrap();
    assert_eq!(timer_keys(cost), ["score|legal|seeds"]); // seed-security is still lacking from legal
    let wake = cost.next_wakeup();
    let e = errors(&cost.tick(wake).unwrap());
    assert_eq!(e.len(), 1);
    assert_eq!((e[0].to.as_str(), e[0].what.as_str(), e[0].candidate.as_deref()), ("legal", "score", Some("seed-security")));
}

#[test]
fn a_repair_that_is_owed_has_a_timer_and_a_repair_that_arrives_ends_it() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 20)]);
    net.answer_score("security", &[("seed-cost", 30)]);
    let cost = net.a("cost");
    assert_eq!(timer_keys(cost), ["repair|security|1"]);
    let wake = cost.next_wakeup();
    let e = errors(&cost.tick(wake).unwrap());
    assert_eq!(e.len(), 1);
    assert_eq!((e[0].to.as_str(), e[0].what.as_str(), e[0].candidate.as_deref()), ("security", "repair", Some("r1-security")));
    let step = net.a("security").submit_repair(RUN, "better", "because", 70, "ok", None).unwrap();
    net.broadcast(step, "security");
    assert!(timers(net.a("cost")).is_empty());
}

#[test]
fn a_result_that_does_not_arrive_is_asked_for_and_the_resend_ends_the_run() {
    let mut net = Net::new(&["cost", "security"]);
    net.drop = Box::new(|text, name| name == "security" && peek(text).unwrap().tag == "concord-result");
    net.start(init_doc(&["cost", "security"], params()));
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 70)]);
    net.answer_score("security", &[("seed-cost", 75)]);
    let sec = &net.agents["security"];
    let s = sec.status(None).unwrap();
    assert_eq!((s.outcome, s.status), (Some(Outcome::Success), RunStatus::Active));
    assert_eq!(timer_keys(sec), ["result|cost"]);
    settle(&mut net, 0.0);
    let sec = &net.agents["security"];
    assert_eq!((sec.status(None).unwrap().status, sec.report(None).unwrap().status), (RunStatus::Ended, ReportStatus::Confirmed));
    assert_eq!(counts(&net, "error"), ["security"]);
    assert_eq!(counts(&net, "resend"), ["cost"]);
}

#[test]
fn the_timers_are_kept_with_the_state_and_survive_a_restart() {
    let store = MemoryStore::new();
    let mut a = Agent::builder("cost").store(store.clone()).build().unwrap();
    let _ = a.receive(&init_doc(&["cost", "security"], params()), Some(50.0)).unwrap();
    let mut b = Agent::builder("cost").store(store.clone()).build().unwrap();
    assert_eq!(timers(&b), tm(&[("init-reply|security", 60.0)]));
    assert_eq!(b.next_wakeup(), Some(60.0));
    assert_eq!(kinds(&b.tick(Some(60.0)).unwrap()), [Kind::Error]);
    assert_eq!(Agent::builder("cost").store(store).build().unwrap().next_wakeup(), Some(70.0));
}

// --- lost messages ---------------------------------------------------------------------

fn lossy_three(drop: impl Fn(&str, &str) -> bool + 'static, p: InitParams) -> Net {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.drop = Box::new(drop);
    net.start(init_doc(&names, p));
    for n in names {
        net.answer_init_raw(n, 40);
    }
    net
}

fn all_score(net: &mut Net, raw: i64) {
    for (n, others) in [("cost", ["security", "legal"]), ("security", ["cost", "legal"]), ("legal", ["cost", "security"])] {
        let items: Vec<(String, i64)> = others.iter().map(|o| (format!("seed-{o}"), raw)).collect();
        net.answer_score(n, &items.iter().map(|(a, b)| (a.as_str(), *b)).collect::<Vec<_>>());
    }
}

#[test]
fn a_repair_that_proves_a_lost_score_makes_the_agent_ask_at_once_and_it_is_judged_when_the_score_comes() {
    let lost = |text: &str, name: &str| name == "security" && tag_from(text) == ("concord-score".to_string(), "legal".to_string());
    let mut net = lossy_three(lost, params());
    all_score(&mut net, 40);
    assert_eq!(net.agents["security"].status(None).unwrap().state, EvalState::Wait);
    assert_eq!(net.agents["cost"].status(None).unwrap().bottleneck.as_deref(), Some("cost"));
    let before = net.events.len();
    let step = net.a("cost").submit_repair(RUN, "better", "because", 70, "ok", None).unwrap();
    net.broadcast(step, "cost");
    let mine: Vec<&Event> = net.events[before..].iter().filter(|(who, _)| who == "security").map(|(_, e)| e).collect();
    let named: Vec<&str> = mine.iter().map(|e| e.event.as_str()).filter(|e| *e == "held" || *e == "asked").collect();
    assert_eq!(named, ["held", "asked"]);
    let asked = mine.iter().find(|e| e.event == "asked").unwrap();
    assert_eq!((asked.str("author"), asked.str("what"), asked.str("candidate")), (Some("legal"), Some("score"), Some("seed-cost")));
    assert_eq!(counts(&net, "resend"), ["legal"]); // legal answered with the stored message
    let sec = &net.agents["security"];
    assert!(sec.state.runs[RUN].engine.pool.iter().any(|c| c.id == "r1-cost"));
    assert_eq!(sec.due()[0].kind(), "score");
    assert_eq!(
        sec.status(None).unwrap().lacking,
        [("security".to_string(), "r1-cost".to_string()), ("legal".to_string(), "r1-cost".to_string())]
    );
}

#[test]
fn a_score_for_a_candidate_the_agent_does_not_hold_makes_it_ask_the_author_of_the_candidate() {
    let lost = |text: &str, name: &str| name == "security" && peek(text).unwrap().tag == "concord-repair";
    let mut net = lossy_three(lost, params());
    all_score(&mut net, 40);
    let step = net.a("cost").submit_repair(RUN, "better", "because", 70, "ok", None).unwrap();
    net.broadcast(step, "cost");
    assert!(net.agents["security"].status(None).unwrap().lacking.is_empty());
    net.answer_score("legal", &[("r1-cost", 50)]); // security has no r1-cost: a gap
    assert!(net.events.iter().any(|(who, e)| who == "security" && e.event == "gap"));
    assert_eq!(counts(&net, "error"), ["security"]);
    assert_eq!(counts(&net, "resend"), ["cost"]);
    assert!(net.agents["security"].state.runs[RUN].engine.pool.iter().any(|c| c.id == "r1-cost"));
}

#[test]
fn a_gap_is_asked_for_once_and_the_timer_repeats_the_request() {
    let lost = |text: &str, name: &str| name == "security" && peek(text).unwrap().tag == "concord-repair";
    let mut net = lossy_three(lost, params());
    all_score(&mut net, 40);
    let step = net.a("cost").submit_repair(RUN, "better", "because", 70, "ok", None).unwrap();
    net.broadcast(step, "cost");
    net.dead.insert("cost".to_string()); // nobody answers any more
    for _ in 0..3 {
        let doc = build_score("legal", RUN, &[ScoreIn::new("r1-cost", 50, "r")], Some(&uuids("d0d0d0d0")())).unwrap();
        let step = net.a("security").receive(&doc, None).unwrap();
        net.broadcast(step, "security");
    }
    assert_eq!(net.events.iter().filter(|(who, e)| who == "security" && e.event == "asked").count(), 1);
}

#[test]
fn a_result_that_cannot_be_judged_asks_for_what_is_lacking() {
    let mut net = lossy_three(|_, _| false, params());
    let step = net.a("security").receive(&build_result("cost", RUN, Outcome::BestEffort, "r2-legal", None).unwrap(), None).unwrap();
    assert_eq!(step.events.first().map(|e| e.event.as_str()), Some("held"));
    let got: Vec<(String, String, Option<String>)> = errors(&step).into_iter().map(|e| (e.to, e.what, e.candidate)).collect();
    assert_eq!(
        got,
        [
            ("legal".to_string(), "score".to_string(), Some("seed-cost".to_string())),
            ("cost".to_string(), "score".to_string(), Some("seed-security".to_string()))
        ]
    );
}

// --- silence and the cancel -----------------------------------------------------------

#[test]
fn an_agent_that_never_answers_ends_the_run_for_everyone_who_waits_for_it() {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.dead.insert("legal".to_string());
    net.start(init_doc(&names, params()));
    net.answer_init("cost");
    net.answer_init("security");
    settle(&mut net, 0.0);
    for n in ["cost", "security"] {
        let (s, rep) = (net.agents[n].status(None).unwrap(), net.agents[n].report(None).unwrap());
        assert_eq!((s.status, s.outcome, rep.status), (RunStatus::Aborted, Some(Outcome::Aborted), ReportStatus::Aborted));
        let reason = rep.reason.unwrap();
        assert!(reason.reason == "missing-agent" && reason.agent.as_deref() == Some("legal"));
        assert!(net.agents[n].due().is_empty());
    }
    let mut cancels = counts(&net, "cancel");
    cancels.sort();
    assert_eq!(cancels, ["cost", "security"]); // each agent sends its cancel once
    assert!(counts(&net, "result").is_empty());
}

#[test]
fn one_agent_that_gives_up_is_enough_the_cancel_reaches_the_others_and_each_passes_it_on_once() {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.start(init_doc(&names, params()));
    for n in names {
        net.answer_init(n);
    }
    let doc = build_cancel("legal", RUN, "missing-agent", None, Some("security"), Some(&uuids("f0f0f0f0")())).unwrap();
    let step = net.a("cost").receive(&doc, None).unwrap();
    net.broadcast(step, "cost");
    assert!(net.agents.values().all(|a| a.status(None).unwrap().status == RunStatus::Aborted));
    let mut cancels = counts(&net, "cancel");
    cancels.sort();
    assert_eq!(cancels, ["cost", "legal", "security"]);
    assert!(net.agents.values().all(|a| a.report(None).unwrap().reason.unwrap().agent.as_deref() == Some("security")));
}

#[test]
fn the_reporters_result_reaches_one_agent_but_not_the_other_and_then_the_reporter_goes_silent() {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.drop = Box::new(|text, name| name == "legal" && peek(text).unwrap().tag == "concord-result");
    net.start(init_doc(&names, params()));
    for n in names {
        net.answer_init_raw(n, 80);
    }
    all_score(&mut net, 70);
    let got: Vec<(Option<Outcome>, RunStatus)> =
        names.iter().map(|n| net.agents[*n].status(None).map(|s| (s.outcome, s.status)).unwrap()).collect();
    assert_eq!(
        got,
        [
            (Some(Outcome::Success), RunStatus::Ended),
            (Some(Outcome::Success), RunStatus::Ended),
            (Some(Outcome::Success), RunStatus::Active)
        ]
    );
    net.dead.insert("cost".to_string()); // the reporter stops answering
    settle(&mut net, 0.0);
    let (legal, sec) = (&net.agents["legal"], &net.agents["security"]);
    let ls = legal.status(None).unwrap();
    assert_eq!((ls.status, ls.outcome), (RunStatus::Aborted, Some(Outcome::Aborted))); // computed an outcome, but never confirmed
    assert_eq!(
        legal.report(None).unwrap().reason,
        Some(Abort { reason: "missing-agent".into(), agent: Some("cost".into()), superseded_by: None, note: None })
    );
    let ss = sec.status(None).unwrap();
    assert_eq!(
        (ss.status, ss.outcome, sec.report(None).unwrap().status),
        (RunStatus::Ended, Some(Outcome::Success), ReportStatus::Disputed)
    );
    let cancels = counts(&net, "cancel");
    assert!(cancels.iter().filter(|w| *w == "legal").count() == 1 && cancels.iter().filter(|w| *w == "security").count() == 1);
}

#[test]
fn damaged_copies_are_asked_for_only_retries_times_per_author_and_kind() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    let a = net.a("cost");
    let doc = reply("security", Some(DAMAGED));
    let sent: Vec<usize> = (0..4).map(|i| a.receive(&doc.replace("because", &format!("becauz{i}")), None).unwrap().send.len()).collect();
    assert_eq!(sent, [1, 1, 0, 0]);
    assert_eq!(a.receive(&doc, None).unwrap().events[0].event, "recorded");
}

#[test]
fn a_message_in_a_run_that_has_ended_starts_no_timer() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", 70)]);
    net.answer_score("security", &[("seed-cost", 75)]);
    for a in net.agents.values_mut() {
        assert!(timers(a).is_empty() && a.next_wakeup().is_none() && a.tick(Some(1e6)).unwrap().send.is_empty());
    }
}

#[test]
fn a_message_that_was_stored_but_never_sent_is_still_sent_when_it_is_asked_for() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    net.answer_init("cost");
    // the node crashes before the send (the state was saved after the submit)
    let lost = net.a("security").submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good", None).unwrap();
    let clock = net.now.clone();
    let again = Agent::builder("security").store(net.stores["security"].clone()).clock(move || clock.get()).build().unwrap();
    net.agents.insert("security".to_string(), again);
    assert_eq!(net.agents["cost"].status(None).unwrap().anchors_in, ["cost"]);
    tick_all(&mut net, 10.0);
    assert_eq!(net.agents["cost"].status(None).unwrap().anchors_in, ["cost", "security"]);
    let sent = net.agents["security"].state.runs[RUN].sent["init-reply|security"].clone();
    assert_eq!(lost.send, std::slice::from_ref(&sent));
    assert_eq!(counts(&net, "resend"), ["security"]);
    let last = net.log.iter().rfind(|t| peek(t).unwrap().tag == "concord-resend").unwrap();
    let Body::Resend(r) = parse_message(last, None).unwrap().body else { panic!("a resend") };
    assert_eq!(parse_message(&r.inner, None).unwrap().uuid, peek(&sent).unwrap().attrs["uuid"]);
}

#[test]
fn a_damaged_message_whose_uuid_cannot_be_read_does_not_use_up_the_requests() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    let a = net.a("cost");
    let doc = reply("security", Some(DAMAGED));
    for i in 0..5 {
        // no uuid to name: no request can be sent
        let bad = doc.replace("because", &format!("becauz{i}")).replace(&format!("uuid=\"{DAMAGED}\""), "uuid=\"x\"");
        let step = a.receive(&bad, None).unwrap();
        assert!(step.send.is_empty() && a.state.runs[RUN].timers["init-reply|security"].requests == 0);
    }
    assert_eq!(a.receive(&doc.replace("because", "becauze"), None).unwrap().send.len(), 1);
    // the budget is intact
}

#[test]
fn a_check_request_and_the_timer_requests_share_the_retries_of_the_batch() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params())); // retries = 2
    let a = net.a("cost");
    let doc = reply("security", Some(DAMAGED));
    let asked: Vec<usize> =
        (0..4).map(|i| a.receive(&doc.replace("because", &format!("becauz{i}")), Some(1.0 + i as f64)).unwrap().send.len()).collect();
    assert_eq!(asked, [1, 1, 0, 0]);
    assert_eq!(a.state.runs[RUN].timers["init-reply|security"].requests, 2);
    assert!(a.receive(&doc.replace("because", "becauzz"), Some(5.0)).unwrap().send.is_empty());
    let step = a.tick(Some(5.0 + 10.0)).unwrap(); // the two requests are used: the next expiry gives up
    assert_eq!(kinds(&step), [Kind::Cancel]);
    assert_eq!(a.status(None).unwrap().status, RunStatus::Aborted);
}

#[test]
fn one_damaged_copy_and_one_timer_request_make_two_of_the_retries() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], params()));
    let a = net.a("cost");
    let doc = reply("security", Some(DAMAGED));
    assert_eq!(errors(&a.receive(&doc.replace("because", "becauze"), Some(1.0)).unwrap()).len(), 1); // request 1, deadline 11
    let reasons: Vec<String> = errors(&a.tick(Some(11.0)).unwrap()).into_iter().map(|e| e.reason).collect();
    assert_eq!(reasons, ["missing"]); // request 2, deadline 21
    assert_eq!(kinds(&a.tick(Some(21.0)).unwrap()), [Kind::Cancel]);
}

#[test]
fn a_batch_whose_first_lacking_message_has_arrived_starts_a_new_wait() {
    let names = ["cost", "security", "legal"];
    let mut net = Net::new(&names);
    net.start(init_doc(&names, params()));
    for n in names {
        net.answer_init(n);
    }
    net.now.set(0.0);
    let cost = net.a("cost");
    let timer = |a: &Agent| a.status(None).unwrap().timers["score|legal|seeds"];
    let _ = cost.tick(Some(0.0)).unwrap();
    assert_eq!(timer(cost), 10.0);
    let e: Vec<Error> = errors(&cost.tick(Some(10.0)).unwrap()).into_iter().filter(|e| e.to == "legal").collect();
    assert_eq!(e.len(), 1);
    assert_eq!((timer(cost), e[0].candidate.as_deref()), (20.0, Some("seed-cost")));
    let _ = cost.receive(&score("legal", &[("seed-cost", 70)]), Some(15.0)).unwrap(); // only part of the batch arrives
    assert_eq!(timer(cost), 25.0); // the next one is a new wait
    assert_eq!(cost.state.runs[RUN].timers["score|legal|seeds"].requests, 0);
    assert!(errors(&cost.tick(Some(20.0)).unwrap()).into_iter().all(|e| e.to != "legal"));
    // legal has its new wait until 25
}

#[test]
fn a_held_message_is_bounded() {
    let mut net = Net::new(&["cost", "security"]);
    net.start(init_doc(&["cost", "security"], InitParams::default().timeout(10).retries(2).budget(1)));
    net.answer_init("cost");
    net.answer_init("security");
    let a = net.a("cost");
    let _ = a.receive(&score("security", &[("seed-cost", 20)]), None).unwrap(); // cost's own score is not in: wait
    let mut seen: Vec<String> = Vec::new();
    for i in 1..8u32 {
        let doc = build_repair(
            "security",
            RUN,
            i,
            &format!("c{i}"),
            "r",
            &50.into(),
            "w",
            None,
            Some(&format!("7{i}777777-7777-4777-8777-777777777777")),
        )
        .unwrap();
        let first = a.receive(&doc, None).unwrap().events[0].clone();
        seen.push(match first.str("reason") {
            Some(r) => r.to_string(),
            None => a.receive(&doc, None).unwrap().events[0].event.clone(),
        });
    }
    assert_eq!(a.state.runs[RUN].held.len(), 4);
    assert!(seen.iter().any(|s| s == "too many messages held"), "{seen:?}");
}
