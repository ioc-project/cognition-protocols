//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The centre of a star: the relay on its own, an agent that has one, and whole runs through a star.
//!
//! `test_a_spoke_is_any_hashable_handle_that_the_caller_can_send_through` is ported with names as handles (a spoke is a `String` here):
//! the test sends through links that it keeps under the names of the spokes. Nothing is skipped.

mod common;

use std::collections::VecDeque;

use common::{uuids, SCell, SRef, RUN};
use indexmap::IndexMap;
use ioc_concord::agent::Agent;
use ioc_concord::errors::{ConcordError, Result};
use ioc_concord::relay::Relay;
use ioc_concord::report::ReportStatus;
use ioc_concord::state::RunStatus;
use ioc_concord::store::{MemoryStore, Store};
use ioc_concord::tasks::Task;
use ioc_concord::wire::{build_error, build_init, build_init_reply, build_resend, build_score, parse_message, peek, Body, ScoreIn};
use ioc_concord::xmlutil::with_check;
use regex::Regex;
use serde_json::{json, Value};

const OTHER: &str = "22222222-2222-4222-8222-222222222222";

fn spec() -> Value {
    json!({"type": "string", "description": "a plan"})
}

fn init_for(participants: &[&str], run_id: &str) -> String {
    build_init("alice", participants, "pick one", "nothing", "all agree", &spec(), None, Some(run_id), Some(&uuids("a0a0a0a0")())).unwrap()
}

fn init_doc() -> String {
    init_for(&["cost", "security"], RUN)
}

/// `doc` with `old` replaced by `new` and its check computed again: a message that is intact but not what an honest author sends.
fn forge(doc: &str, old: &str, new: &str) -> String {
    assert!(doc.contains(old));
    let no_check = Regex::new(r#" check="[^"]*""#).unwrap();
    with_check(&no_check.replace(&doc.replace(old, new), "")).unwrap()
}

/// A score message of `who` for a run: always the same text for the same arguments, and never of an agent for its own candidate.
fn score_of(run_id: &str, who: &str) -> String {
    let uid = "55555555-5555-4555-8555-555555555555";
    let id = if who == "security" { "seed-cost" } else { "seed-security" };
    build_score(who, run_id, &[ScoreIn::new(id, 5, "r")], Some(uid)).unwrap()
}

fn score() -> String {
    score_of(RUN, "cost")
}

fn runs(r: &Relay) -> Vec<String> {
    r.state.runs.keys().cloned().collect()
}

/// A store that keeps the state as a file would, counts its saves, and can be made to fail its loads.
#[derive(Clone, Default)]
struct TestStore {
    inner: MemoryStore,
    saves: SCell<usize>,
    broken: SRef<Option<String>>,
}

impl TestStore {
    fn holding(state: Value) -> Self {
        let mut store = TestStore::default();
        store.inner.save(&state).unwrap();
        store
    }
}

impl Store for TestStore {
    fn load(&self) -> Result<Value> {
        match &*self.broken.borrow() {
            Some(why) => Err(ConcordError::state(why.clone())),
            None => self.inner.load(),
        }
    }

    fn save(&mut self, state: &Value) -> Result<()> {
        self.saves.set(self.saves.get() + 1);
        self.inner.save(state)
    }
}

fn relay_of(store: &TestStore) -> Relay {
    Relay::with_store(store.clone()).unwrap()
}

fn relay_with_spokes(spokes: &[&str]) -> Relay {
    let mut r = Relay::new();
    for s in spokes {
        r.add_spoke(*s);
    }
    r
}

// --- the relay on its own -------------------------------------------------------------------

#[test]
fn the_init_of_a_run_is_passed_on_and_makes_the_run_known() {
    let mut r = Relay::new();
    let init = init_doc();
    assert_eq!(r.receive(&init).unwrap(), [init]);
    assert_eq!(runs(&r), [RUN]);
    assert_eq!(r.receive(&score()).unwrap(), [score()]); // a message of a run it knows
    assert!(r.receive(&score_of(OTHER, "cost")).unwrap().is_empty()); // and one of a run it has never seen goes nowhere
    assert_eq!(runs(&r), [RUN]);
}

#[test]
fn an_init_inside_a_resend_makes_the_run_known_too() {
    let mut r = Relay::new();
    let wrapper = build_resend("alice", RUN, &init_doc(), None, None).unwrap();
    assert_eq!(r.receive(&wrapper).unwrap(), [wrapper]);
    assert_eq!(runs(&r), [RUN]);
    let mut unknown = Relay::new();
    let resent_score = build_resend("cost", RUN, &score(), None, None).unwrap();
    assert!(unknown.receive(&resent_score).unwrap().is_empty()); // a resend of anything else does not
    assert!(unknown.state.runs.is_empty());
    assert_eq!(r.receive(&resent_score).unwrap(), [resent_score]);
}

#[test]
fn the_message_is_passed_on_exactly_as_it_came() {
    let mut r = Relay::new();
    let text = format!("Here it is:\n```xml\n{}\n```\n", init_doc());
    assert_eq!(r.receive(&text).unwrap(), [text]);
    let padded = format!("  {}  \n", score());
    assert_eq!(r.receive(&padded).unwrap(), [padded]);
}

#[test]
fn what_is_not_a_concord_message_is_not_passed_on() {
    for text in ["", "hello", "<other/>", "<concord-score from='a'>never closed", "<concord-init", "{}", "<<>>"] {
        let mut r = Relay::new();
        assert!(r.receive(text).unwrap().is_empty(), "{text:?}");
        assert!(r.state.runs.is_empty(), "{text:?}");
    }
}

#[test]
fn a_run_id_that_is_not_a_uuid_names_no_run() {
    let mut r = Relay::new();
    let odd = init_doc().replace(RUN, "RUNID");
    assert!(r.receive(&odd).unwrap().is_empty());
    assert!(r.state.runs.is_empty());
    let no_run_id = init_doc().replace(&format!(" run_id=\"{RUN}\""), "");
    assert!(r.receive(&no_run_id).unwrap().is_empty());
}

#[test]
fn it_reads_only_the_name_and_the_run_and_so_it_does_not_look_at_versions_checks_or_senders() {
    let mut r = Relay::new();
    let other_version = init_doc().replace("version=\"0.3.0\"", "version=\"9.9.9\"");
    assert_eq!(r.receive(&other_version).unwrap(), [other_version]); // an init of a version it does not speak makes the run known
    let damaged = score().replace("5.0", "6.0");
    assert_eq!(r.receive(&damaged).unwrap(), [damaged]); // a check that fails
    let stranger = score_of(RUN, "mallory");
    assert_eq!(r.receive(&stranger).unwrap(), [stranger]); // a sender that is not in the run
    let future =
        format!("<concord-future version=\"0.4.0\" from=\"cost\" run_id=\"{RUN}\" uuid=\"33333333-3333-4333-8333-333333333333\"/>");
    assert_eq!(r.receive(&future).unwrap(), [future]); // a message it has never heard of
}

#[test]
fn a_run_stays_known_until_it_is_forgotten_whatever_happens_to_it() {
    let mut r = Relay::new();
    r.receive(&init_doc()).unwrap();
    r.receive(&init_for(&["cost", "security"], OTHER)).unwrap();
    r.forget(RUN).unwrap();
    assert!(r.receive(&score()).unwrap().is_empty());
    assert_eq!(r.receive(&score_of(OTHER, "cost")).unwrap(), [score_of(OTHER, "cost")]);
    r.forget(RUN).unwrap(); // a run it does not know: nothing happens
    assert_eq!(runs(&r), [OTHER]);
}

#[test]
fn learn_remembers_the_run_of_an_init_without_passing_anything_on() {
    let mut r = Relay::new();
    assert!(!r.learn(&score()).unwrap());
    assert!(r.state.runs.is_empty());
    assert!(r.learn(&init_doc()).unwrap());
    assert_eq!(runs(&r), [RUN]);
    assert!(r.should_pass_on(&score()).unwrap());
    assert!(!r.should_pass_on(&score_of(OTHER, "cost")).unwrap());
}

#[test]
fn the_state_is_kept_and_a_restarted_relay_knows_the_same_runs() {
    let store = TestStore::default();
    let mut r = relay_of(&store);
    r.receive(&init_doc()).unwrap();
    r.receive(&build_resend("alice", OTHER, &init_for(&["cost", "security"], OTHER), None, None).unwrap()).unwrap();
    let mut again = relay_of(&store);
    let mut known = runs(&again);
    known.sort();
    assert_eq!(known, [RUN, OTHER]);
    assert_eq!(again.receive(&score_of(OTHER, "cost")).unwrap(), [score_of(OTHER, "cost")]);
    // the state round-trips through JSON unchanged
    let saved = serde_json::to_value(&again.state).unwrap();
    assert_eq!(serde_json::from_value::<ioc_concord::relay::RelayState>(saved).unwrap(), again.state);
}

#[test]
fn it_saves_only_when_a_run_is_learned_or_forgotten() {
    let store = TestStore::default();
    let mut r = relay_of(&store);
    let resend = build_resend("alice", RUN, &init_doc(), None, None).unwrap();
    for text in [score(), "hello".to_string(), init_doc(), init_doc(), score(), resend] {
        r.receive(&text).unwrap();
    }
    assert_eq!(store.saves.get(), 1);
    r.forget(OTHER).unwrap();
    assert_eq!(store.saves.get(), 1);
    r.forget(RUN).unwrap();
    assert_eq!(store.saves.get(), 2);
}

#[test]
fn a_saved_state_that_is_not_a_relay_state_is_refused() {
    for text in [r#"{"runs": 5}"#, "[]", "null", "5", r#"{"other": {}}"#] {
        let store = TestStore::holding(serde_json::from_str(text).unwrap());
        match Relay::with_store(store) {
            Err(ConcordError::State(m)) => assert!(m.contains("holds no runs"), "{text}: {m}"),
            other => panic!("{text}: expected a State error, got {:?}", other.map(|_| ())),
        }
    }
}

#[test]
fn a_state_that_cannot_be_read_does_not_make_the_relay_forget_its_runs() {
    let store = TestStore::default();
    let mut r = relay_of(&store);
    r.receive(&init_doc()).unwrap();
    *store.broken.borrow_mut() = Some("state file is not valid JSON".to_string());
    match Relay::with_store(store.clone()) {
        // not an empty relay: the saved runs are not thrown away
        Err(ConcordError::State(m)) => assert!(m.contains("not valid JSON"), "{m}"),
        other => panic!("expected a State error, got {:?}", other.map(|_| ())),
    }
    assert_eq!(store.inner.get().unwrap()["runs"], json!({RUN: true}));
    assert!(Relay::with_store(MemoryStore::new()).unwrap().state.runs.is_empty());
    // nothing saved at all: a fresh start
}

#[test]
fn a_run_id_in_capitals_is_not_a_uuid_and_names_no_run() {
    let mut r = Relay::new();
    let lower = "abcdefab-abcd-4abc-8abc-abcdefabcdef";
    let init = init_for(&["cost", "security"], lower);
    assert!(r.receive(&init.replace(lower, &lower.to_uppercase())).unwrap().is_empty());
    assert!(r.state.runs.is_empty());
    assert_eq!(r.receive(&init).unwrap(), [init]);
    assert_eq!(runs(&r), [lower]);
}

// --- the spokes --------------------------------------------------------------------------------

#[test]
fn the_targets_are_every_spoke_but_the_origin_and_all_of_them_for_an_own_message() {
    let mut r = Relay::new();
    assert!(r.spokes().is_empty() && r.targets(None).is_empty() && r.targets(Some("a")).is_empty());
    for spoke in ["a", "b", "c"] {
        r.add_spoke(spoke);
    }
    assert_eq!(r.spokes(), ["a", "b", "c"]);
    assert_eq!(r.targets(Some("b")), ["a", "c"]);
    assert_eq!(r.targets(None), ["a", "b", "c"]);
    assert_eq!(r.targets(Some("nobody")), ["a", "b", "c"]); // an origin that is not registered leaves them all
}

#[test]
fn spokes_are_added_once_in_order_and_removed_when_they_go() {
    let mut r = relay_with_spokes(&["a", "b"]);
    r.add_spoke("c");
    r.add_spoke("a"); // twice: nothing changes, and the order stays
    assert_eq!(r.spokes(), ["a", "b", "c"]);
    r.remove_spoke("b");
    r.remove_spoke("b"); // not registered any more: nothing happens
    assert_eq!(r.spokes(), ["a", "c"]);
    assert_eq!(r.targets(Some("a")), ["c"]);
    r.add_spoke("b");
    assert_eq!(r.spokes(), ["a", "c", "b"]); // a spoke that comes back is last
}

#[test]
fn a_spoke_is_any_name_that_the_caller_can_send_through() {
    let mut links: IndexMap<String, Vec<&str>> = IndexMap::new();
    links.insert("one".into(), vec![]);
    links.insert("two".into(), vec![]);
    let r = relay_with_spokes(&["one", "two"]);
    for spoke in r.targets(Some("one")) {
        links.get_mut(&spoke).unwrap().push("x");
    }
    assert!(links["one"].is_empty());
    assert_eq!(links["two"], ["x"]);
}

#[test]
fn the_spokes_are_not_part_of_the_saved_state_and_a_restarted_relay_has_none() {
    let store = TestStore::default();
    let mut r = relay_of(&store);
    r.add_spoke("a");
    r.add_spoke("b");
    r.receive(&init_doc()).unwrap();
    let saved = store.inner.get().unwrap();
    assert!(saved.get("spokes").is_none());
    assert_eq!(saved["runs"], json!({RUN: true}));
    let mut again = relay_of(&store);
    assert!(again.spokes().is_empty());
    assert_eq!(runs(&again), [RUN]); // the runs are kept, the links connect again
    again.add_spoke("a");
    assert!(again.targets(Some("a")).is_empty());
}

#[test]
fn registering_spokes_changes_nothing_about_what_is_passed_on() {
    let (mut bare, mut linked) = (Relay::new(), relay_with_spokes(&["a", "b"]));
    let resend = build_resend("alice", OTHER, &init_for(&["cost", "security"], OTHER), None, None).unwrap();
    for text in [init_doc(), score(), score_of(OTHER, "cost"), "hello".to_string(), resend] {
        assert_eq!(bare.receive(&text).unwrap(), linked.receive(&text).unwrap());
    }
}

// --- an agent that has a relay -----------------------------------------------------------------

fn centre(relay: Relay) -> Agent {
    Agent::builder("hub").relay(relay).uuid_fn(uuids("cccccccc")).build().unwrap()
}

fn event_names(step: &ioc_concord::agent::Step) -> Vec<&str> {
    step.events.iter().map(|e| e.event.as_str()).collect()
}

#[test]
fn an_agent_without_a_relay_never_says_to_pass_anything_on() {
    let mut a = Agent::new("cost").unwrap();
    let resend = build_resend("alice", RUN, &init_for(&["cost", "security"], OTHER), None, None).unwrap();
    for text in [init_doc(), score(), resend] {
        assert!(a.receive(&text, None).unwrap().forward.is_empty());
    }
}

#[test]
fn an_agent_with_a_relay_says_which_messages_to_pass_on_and_still_does_what_an_agent_does() {
    let mut a = centre(Relay::new());
    let init = init_for(&["hub", "security"], RUN);
    let step = a.receive(&init, None).unwrap();
    assert_eq!(step.forward, [init]);
    assert_eq!(event_names(&step), ["joined"]);
    assert_eq!(a.due()[0].kind(), "init-reply");
    assert!(a.receive(&score_of(OTHER, "cost"), None).unwrap().forward.is_empty());
    // a run it does not know
}

#[test]
fn a_message_that_the_agent_drops_is_passed_on_anyway() {
    let mut a = centre(Relay::new());
    let _ = a.receive(&init_for(&["hub", "security"], RUN), None).unwrap();
    let seed_hub = || build_score("security", RUN, &[ScoreIn::new("seed-hub", 5, "r")], None).unwrap();
    let cases = [
        ("a duplicate", init_for(&["hub", "security"], RUN), Some("duplicate")),
        ("a sender that is not in the run", score_of(RUN, "mallory"), Some("sender is not in the run")),
        ("another version", seed_hub().replace("version=\"0.3.0\"", "version=\"0.2.0\""), Some("version")),
        (
            "a damaged message",
            build_init_reply("security", RUN, &0.into(), &100.into(), "p", "r", &5.into(), "w", None, None)
                .unwrap()
                .replace("<raw>5.0", "<raw>6.0"),
            Some("check"),
        ),
        ("an invalid message", forge(&seed_hub(), "<raw>5.0", "<raw>500.0"), None),
    ];
    for (label, text, reason) in cases {
        let step = a.receive(&text, None).unwrap();
        assert_eq!(step.forward, std::slice::from_ref(&text), "{label}");
        assert_eq!(step.events[0].event, "ignored", "{label}");
        if let Some(reason) = reason {
            assert_eq!(step.events[0].str("reason"), Some(reason), "{label}");
        }
    }
}

#[test]
fn a_message_of_an_ended_or_aborted_run_is_still_passed_on() {
    let mut a = centre(Relay::new());
    let _ = a.receive(&init_for(&["hub", "security"], RUN), None).unwrap();
    a.state.runs.get_mut(RUN).unwrap().status = RunStatus::Ended;
    let late = build_score("security", RUN, &[ScoreIn::new("seed-hub", 5, "r")], None).unwrap();
    let step = a.receive(&late, None).unwrap();
    assert_eq!(step.forward, [late]);
    assert_eq!(step.events[0].str("reason"), Some("run over"));
    a.state.runs.get_mut(RUN).unwrap().status = RunStatus::Aborted;
    let later = build_score("security", RUN, &[ScoreIn::new("seed-hub", 6, "r")], Some("66666666-6666-4666-8666-666666666666")).unwrap();
    assert_eq!(a.receive(&later, None).unwrap().forward, [later]);
}

#[test]
fn a_centre_that_opens_a_run_knows_it_and_passes_on_what_comes_back() {
    let mut a = centre(Relay::with_store(MemoryStore::new()).unwrap());
    let step = a.start(&["hub", "cost"], "pick one", "nothing", "all agree", &spec(), None, Some(RUN), None).unwrap();
    assert!(step.forward.is_empty());
    assert_eq!(step.send.len(), 1);
    assert_eq!(runs(a.relay().unwrap()), [RUN]);
    let reply = build_init_reply("cost", RUN, &0.into(), &100.into(), "plan", "because", &80.into(), "good", None, None).unwrap();
    assert_eq!(a.receive(&reply, None).unwrap().forward, [reply]);
}

#[test]
fn what_the_agent_sends_itself_is_never_in_forward_and_submissions_forward_nothing() {
    let mut a = centre(Relay::new());
    let _ = a.receive(&init_for(&["hub", "security"], RUN), None).unwrap();
    let step = a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good", None).unwrap();
    assert!(!step.send.is_empty());
    assert!(step.forward.is_empty());
    assert!(a.tick(Some(1e9)).unwrap().forward.is_empty());
}

// --- whole runs through a star ----------------------------------------------------------------------

/// The centre: a `Relay` alone, or an `Agent` that has one.
enum Hub {
    Relay(Relay),
    Agent(Box<Agent>),
}

/// Spokes that have one link each, to the centre. A spoke sends to the centre only. The centre passes what its relay says to every
/// spoke but the one it came from, and sends its own messages to every spoke.
struct Star {
    spokes: IndexMap<String, Agent>,
    hub: Hub,
    queue: VecDeque<(String, String)>,
    log: Vec<(String, String)>,
}

impl Star {
    fn new(spokes: IndexMap<String, Agent>, hub: Hub) -> Star {
        let mut star = Star { spokes, hub, queue: VecDeque::new(), log: Vec::new() };
        let names: Vec<String> = star.spokes.keys().cloned().collect();
        for name in names {
            star.relay_mut().add_spoke(name); // the links of the centre: here, the names of the spokes
        }
        star
    }

    fn relay(&self) -> &Relay {
        match &self.hub {
            Hub::Relay(r) => r,
            Hub::Agent(a) => a.relay().expect("the centre has a relay"),
        }
    }

    fn relay_mut(&mut self) -> &mut Relay {
        match &mut self.hub {
            Hub::Relay(r) => r,
            Hub::Agent(a) => a.relay_mut().expect("the centre has a relay"),
        }
    }

    fn heard_from_spoke(&mut self, name: &str, send: Vec<String>) {
        self.queue.extend(send.into_iter().map(|text| (name.to_string(), text)));
    }

    fn heard_from_hub(&mut self, send: Vec<String>) {
        for text in send {
            self.deliver(&text, None);
        }
    }

    fn deliver(&mut self, text: &str, origin: Option<&str>) {
        for name in self.relay().targets(origin) {
            let send = self.spokes.get_mut(&name).unwrap().receive(text, None).unwrap().send;
            self.heard_from_spoke(&name, send);
        }
    }

    fn pump(&mut self) {
        while let Some((origin, text)) = self.queue.pop_front() {
            self.log.push((origin.clone(), text.clone()));
            let (forward, own) = match &mut self.hub {
                Hub::Relay(r) => (r.receive(&text).unwrap(), Vec::new()), // a centre that is only a relay
                Hub::Agent(a) => {
                    let step = a.receive(&text, None).unwrap();
                    (step.forward, step.send)
                }
            };
            for out in forward {
                self.deliver(&out, Some(&origin));
            }
            for out in own {
                self.deliver(&out, None);
            }
        }
    }

    fn agent(&mut self, name: &str) -> &mut Agent {
        match (&mut self.hub, name) {
            (Hub::Agent(a), "hub") => a,
            _ => self.spokes.get_mut(name).unwrap(),
        }
    }

    fn names(&self) -> Vec<String> {
        let hub = matches!(self.hub, Hub::Agent(_)).then(|| "hub".to_string());
        self.spokes.keys().cloned().chain(hub).collect()
    }

    /// Answer every task of every agent until none is left.
    fn drive(&mut self) {
        for _ in 0..60 {
            let todo: Vec<(String, Task)> =
                self.names().into_iter().flat_map(|n| self.agent(&n).due().into_iter().map(move |t| (n.clone(), t))).collect();
            if todo.is_empty() {
                return;
            }
            for (name, task) in todo {
                let a = self.agent(&name);
                let step = match task {
                    Task::InitReply(t) => a.submit_init_reply(&t.run_id, 0, 100, &format!("plan of {name}"), "because", 80, "good", None),
                    Task::Score(t) => {
                        let scores: Vec<ScoreIn> = t.candidates.iter().map(|c| ScoreIn::new(&c.id, 70, "fine")).collect();
                        a.submit_score(&t.run_id, &scores, None)
                    }
                    Task::Repair(t) => a.submit_repair(&t.run_id, "better", "because", 90, "ok", None),
                    other => panic!("a task of a kind the test does not know: {other:?}"),
                }
                .unwrap();
                if name == "hub" {
                    self.heard_from_hub(step.send);
                } else {
                    self.heard_from_spoke(&name, step.send);
                }
            }
            self.pump();
        }
        panic!("the run does not end");
    }

    fn open(&mut self, from: &str, participants: &[&str], context: &str, run_id: &str) {
        let step = self
            .spokes
            .get_mut(from)
            .unwrap()
            .start(participants, context, "nothing", "all agree", &spec(), None, Some(run_id), None)
            .unwrap();
        self.heard_from_spoke(from, step.send);
    }

    fn statuses(&mut self, run_id: Option<&str>) -> Vec<(String, ReportStatus)> {
        self.names().into_iter().map(|n| (n.clone(), self.agent(&n).report(run_id).unwrap().status)).collect()
    }
}

fn spokes(names: &[&str]) -> IndexMap<String, Agent> {
    names
        .iter()
        .enumerate()
        .map(|(i, n)| (n.to_string(), Agent::builder(n).uuid_fn(uuids(&format!("{:08x}", i + 1))).build().unwrap()))
        .collect()
}

fn status(name: &str, status: ReportStatus) -> (String, ReportStatus) {
    (name.to_string(), status)
}

#[test]
fn a_run_goes_through_a_star_whose_centre_is_only_a_relay() {
    let mut star = Star::new(spokes(&["alice", "cost", "security"]), Hub::Relay(Relay::new()));
    star.open("alice", &["cost", "security"], "pick one", RUN);
    star.pump();
    star.drive();
    let reports: Vec<_> = star.spokes.iter().map(|(n, a)| (n.clone(), a.report(None).unwrap())).collect();
    let summary: Vec<_> = reports.iter().map(|(n, r)| (n.as_str(), r.status, r.outcome.map(|o| o.as_str()))).collect();
    assert_eq!(
        summary,
        [
            ("alice", ReportStatus::Unverified, Some("SUCCESS")),
            ("cost", ReportStatus::Confirmed, Some("SUCCESS")),
            ("security", ReportStatus::Confirmed, Some("SUCCESS"))
        ]
    );
    assert_eq!(reports[1].1.winner, reports[0].1.winner);
    assert_eq!(runs(star.relay()), [RUN]);
}

#[test]
fn a_run_goes_through_a_star_whose_centre_is_a_participant_and_the_reporter() {
    let mut star = Star::new(spokes(&["alice", "cost", "security"]), Hub::Agent(Box::new(centre(Relay::new()))));
    star.open("alice", &["hub", "cost", "security"], "pick one", RUN);
    star.pump();
    star.drive();
    let hub = star.agent("hub").report(None).unwrap();
    assert_eq!((hub.status, hub.outcome.map(|o| o.as_str())), (ReportStatus::Confirmed, Some("SUCCESS")));
    let all = star.statuses(None);
    assert_eq!(
        all,
        [
            status("alice", ReportStatus::Unverified),
            status("cost", ReportStatus::Confirmed),
            status("security", ReportStatus::Confirmed),
            status("hub", ReportStatus::Confirmed)
        ]
    );
}

#[test]
fn a_run_goes_through_a_star_whose_centre_opens_it_and_takes_part() {
    let mut star = Star::new(spokes(&["cost", "security"]), Hub::Agent(Box::new(centre(Relay::new()))));
    let Hub::Agent(hub) = &mut star.hub else { unreachable!() };
    let step = hub.start(&["hub", "cost", "security"], "pick one", "nothing", "all agree", &spec(), None, Some(RUN), None).unwrap();
    star.heard_from_hub(step.send);
    star.pump();
    star.drive();
    let mut all = star.statuses(None);
    all.sort_by(|a, b| a.0.cmp(&b.0));
    assert_eq!(
        all,
        [status("cost", ReportStatus::Confirmed), status("hub", ReportStatus::Confirmed), status("security", ReportStatus::Confirmed)]
    );
}

#[test]
fn a_relay_that_restarts_in_the_middle_of_a_run_keeps_passing_it_on() {
    let store = TestStore::default();
    let mut star = Star::new(spokes(&["alice", "cost", "security"]), Hub::Relay(relay_of(&store)));
    star.open("alice", &["cost", "security"], "pick one", RUN);
    star.pump();
    star.hub = Hub::Relay(relay_of(&store)); // the centre restarts: only the saved run ids remain
    assert!(star.relay().spokes().is_empty());
    assert_eq!(runs(star.relay()), [RUN]);
    for name in ["alice", "cost", "security"] {
        star.relay_mut().add_spoke(name); // the links connect again and register again
    }
    star.drive();
    assert_eq!(
        star.statuses(None),
        [status("alice", ReportStatus::Unverified), status("cost", ReportStatus::Confirmed), status("security", ReportStatus::Confirmed)]
    );
}

#[test]
fn errors_and_resends_after_the_end_still_reach_the_spokes_and_two_runs_share_the_star() {
    let mut star = Star::new(spokes(&["alice", "cost", "security"]), Hub::Relay(Relay::new()));
    star.open("alice", &["cost", "security"], "pick one", RUN);
    star.pump();
    star.drive();
    let asked = "44444444-4444-4444-8444-444444444444";
    let ask = build_error("security", RUN, "cost", "init-reply", "missing", None, None, "", Some(asked)).unwrap();
    star.heard_from_spoke("security", vec![ask]);
    star.pump();
    let resends: Vec<&String> =
        star.log.iter().filter(|(o, t)| o == "cost" && peek(t).unwrap().tag == "concord-resend").map(|(_, t)| t).collect();
    let last = resends.last().expect("cost sends the init reply again");
    assert!(matches!(&parse_message(last, None).unwrap().body, Body::Resend(r) if r.reply_to.as_deref() == Some(asked)));
    // a second run goes through the same centre; messages of a run it never saw go nowhere
    star.open("alice", &["cost", "security"], "again", OTHER);
    star.pump();
    star.drive();
    assert_eq!(star.spokes["alice"].report(Some(OTHER)).unwrap().status, ReportStatus::Unverified);
    assert_eq!(star.spokes["cost"].report(Some(OTHER)).unwrap().status, ReportStatus::Confirmed);
    let before = star.log.len();
    star.heard_from_spoke("security", vec![score_of("33333333-3333-4333-8333-333333333333", "security")]);
    star.pump();
    assert_eq!(star.log.len(), before + 1);
    let last = &star.log.last().unwrap().1;
    assert!(!star.log[..before].iter().any(|(_, t)| t == last)); // it reached the centre and went no further
}
