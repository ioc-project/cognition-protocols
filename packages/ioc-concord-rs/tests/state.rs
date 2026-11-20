//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The state of an agent: its runs, the keys of the items a run is made of, and what a run keeps of its init.
// An agent id that is not a `&str` cannot be written, so it is not tried.

use ioc_concord::state::*;
use ioc_concord::wire::{build_init, build_init_reply, parse_message, Body, Message};
use ioc_concord::ConcordError;
use serde_json::json;

const RUN: &str = "11111111-1111-4111-8111-111111111111";

/// The init of `alice` to `cost` and `security`, parsed, and as text.
fn parsed_init() -> (Message, String) {
    let spec = json!({"type": "string", "description": "a plan"});
    let doc = build_init("alice", &["cost", "security"], "pick one", "nothing", "all agree", &spec, None, Some(RUN), None).unwrap();
    (parse_message(&doc, None).unwrap(), doc)
}

fn run_of(me: &str, init: &Message, doc: &str) -> Run {
    match &init.body {
        Body::Init(body) => new_run(me, &init.from, &init.run_id, &init.uuid, body, doc).expect("a run"),
        other => panic!("not an init: {other:?}"),
    }
}

fn init_params(init: &Message) -> ioc_concord::engine::Params {
    match &init.body {
        Body::Init(body) => body.params.clone(),
        other => panic!("not an init: {other:?}"),
    }
}

fn as_json(run: &Run) -> serde_json::Value {
    serde_json::to_value(run).unwrap()
}

#[test]
fn a_new_agent_state_is_empty_and_the_agent_id_is_checked() {
    let s = AgentState::new("cost").unwrap();
    assert_eq!((s.me.as_str(), s.runs.len(), s.active.clone()), ("cost", 0, None));
    assert_eq!(serde_json::to_value(&s).unwrap(), json!({"me": "cost", "runs": {}, "active": null}));
    for bad in ["", "a b", &"x".repeat(65)] {
        match AgentState::new(bad) {
            Err(ConcordError::State(why)) => assert!(why.contains("agent id"), "{why}"),
            other => panic!("{bad:?}: {other:?}"),
        }
    }
}

#[test]
fn item_keys_name_what_a_message_is_about() {
    assert_eq!(item_key("init-reply", "cost", None), "init-reply|cost");
    assert_eq!(item_key("score", "cost", Some("seed-security")), "score|cost|seed-security");
    assert_eq!(item_key("result", "cost", None), "result|cost");
}

#[test]
fn a_run_holds_what_the_init_says_and_is_json_able() {
    let (init, doc) = parsed_init();
    let run = run_of("cost", &init, &doc);
    assert_eq!(run.run_id, RUN);
    assert_eq!(run.initiator, "alice");
    assert_eq!(run.participants, ["cost", "security"]);
    assert_eq!(run.role, Role::Participant);
    assert_eq!(run.status, RunStatus::Active);
    assert_eq!(run.outcome, None);
    assert_eq!(run.received.iter().collect::<Vec<_>>(), [(&"init|alice".to_string(), &doc)]);
    assert_eq!(run.uuids.keys().collect::<Vec<_>>(), [&init.uuid]);
    assert!(run.sent.is_empty());
    assert_eq!(run.engine.agents, ["cost", "security"]);
    assert_eq!(run.params, init_params(&init));
    let back: Run = serde_json::from_str(&serde_json::to_string(&run).unwrap()).unwrap();
    assert_eq!(back, run);
}

#[test]
fn the_members_of_a_run_are_its_participants_and_its_initiator() {
    let (init, doc) = parsed_init();
    let run = run_of("cost", &init, &doc);
    for member in ["alice", "cost", "security"] {
        assert!(run.is_member(member), "{member}");
    }
    assert!(!run.is_member("legal"));
    assert_eq!(run.reporter(), "cost");
}

#[test]
fn the_active_run_is_the_one_that_is_still_running() {
    let mut s = AgentState::new("cost").unwrap();
    assert!(s.active_run().is_none());
    let (init, doc) = parsed_init();
    s.runs.insert(RUN.to_string(), run_of("cost", &init, &doc));
    s.active = Some(RUN.to_string());
    assert!(std::ptr::eq(s.active_run().unwrap(), &s.runs[RUN]));
    s.runs.get_mut(RUN).unwrap().status = RunStatus::Ended;
    assert!(s.active_run().is_none());
}

#[test]
fn a_sent_original_is_kept_by_item_and_by_uuid_and_an_accepted_uuid_is_remembered() {
    let (init, doc) = parsed_init();
    let mut run = run_of("cost", &init, &doc);
    let uid = "55555555-5555-4555-8555-555555555555";
    let reply = build_init_reply("cost", RUN, &0.into(), &100.into(), "plan", "because", &80.into(), "good", None, Some(uid)).unwrap();
    run.keep_sent("init-reply|cost", &reply).expect("a message with a uuid");
    assert_eq!(run.sent.iter().collect::<Vec<_>>(), [(&"init-reply|cost".to_string(), &reply)]);
    assert_eq!(run.sent_by_uuid.iter().collect::<Vec<_>>(), [(&uid.to_string(), &reply)]);
    assert!(!run.uuids.contains_key("x"));
    run.keep_uuid("x");
    run.keep_uuid("x");
    assert_eq!(run.uuids.keys().collect::<Vec<_>>(), [&init.uuid, &"x".to_string()]);
    let back: Run = serde_json::from_value(as_json(&run)).unwrap();
    assert_eq!(back, run);
}

#[test]
fn the_initiator_keeps_the_init_it_sent_in_the_same_way() {
    let (init, doc) = parsed_init();
    let run = run_of("alice", &init, &doc);
    assert_eq!(run.role, Role::Initiator);
    assert_eq!(run.sent.iter().collect::<Vec<_>>(), [(&"init|alice".to_string(), &doc)]);
    assert!(run.received.is_empty());
    assert_eq!(run.sent_by_uuid.iter().collect::<Vec<_>>(), [(&init.uuid, &doc)]);
}
