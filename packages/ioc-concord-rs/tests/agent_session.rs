//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What a program that drives an agent keeps with its state: the outbox, the refusals, the watch, the spokes, and the participants.

use ioc_concord::agent::Agent;
use ioc_concord::store::{FileStore, MemoryStore};
use serde_json::{json, Value};

fn started(agent: &mut Agent) {
    let spec = json!({"type": "string", "description": "what one option looks like"});
    let _ = agent.start(&["cost", "security"], "which plan?", "no plan", "the best plan", &spec, None, None, None).unwrap();
}

#[test]
fn the_participants_of_the_run_are_in_the_order_of_the_init() {
    let mut agent = Agent::new("cost").unwrap();
    assert_eq!(agent.participants(None), None);
    started(&mut agent);
    assert_eq!(agent.participants(None), Some(vec!["cost".to_string(), "security".to_string()]));
}

#[test]
fn the_outbox_is_handed_out_once_and_kept_to_send_again() {
    let mut agent = Agent::new("cost").unwrap();
    assert!(agent.outbox().is_none() && agent.hand_outbox(Some(1.0)).unwrap().is_empty());
    agent.set_outbox(&["a".to_string(), "b".to_string()], false, Some(1.0)).unwrap();
    assert_eq!(agent.hand_outbox(Some(2.0)).unwrap(), ["a", "b"]);
    assert!(agent.hand_outbox(Some(3.0)).unwrap().is_empty());
    let outbox = agent.outbox().unwrap();
    assert_eq!((outbox.messages.len(), outbox.at, outbox.handed), (2, 2.0, true));
}

#[test]
fn a_refusal_counts_the_same_refusal_in_a_row_and_a_good_answer_forgets_it() {
    let mut agent = Agent::new("cost").unwrap();
    started(&mut agent);
    let first = agent.refuse("the score is out of range").unwrap();
    let second = agent.refuse("the score is out of range").unwrap();
    assert_ne!(first, second, "the second time the model is told to change its answer");
    assert_eq!(agent.refuse("something else").unwrap(), first.replace("the score is out of range", "something else"));
    let run = agent.due()[0].run_id().to_string();
    let _ = agent.submit_init_reply(&run, 0, 100, "plan", "the facts", 90, "why", None).unwrap();
    assert!(agent.state.session.refusal.is_none());
}

#[test]
fn the_watch_counts_nudges_for_one_progress_and_forgets_them_when_it_changes() {
    let mut agent = Agent::new("cost").unwrap();
    assert_eq!(agent.watch_nudge("a").unwrap(), 1);
    assert_eq!(agent.watch_nudge("a").unwrap(), 2);
    agent.watch_progress("a").unwrap();
    assert_eq!(agent.watch_nudge("a").unwrap(), 3, "the same progress: nothing was forgotten");
    agent.watch_progress("b").unwrap();
    assert_eq!(agent.watch_nudge("b").unwrap(), 1);
}

#[test]
fn what_is_kept_survives_a_restart_and_the_state_has_no_session_when_nothing_is_kept() {
    let dir = std::env::temp_dir().join(format!("ioc-concord-session-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("state.json");
    let mut agent = Agent::builder("cost").store(FileStore::new(&path)).build().unwrap();
    started(&mut agent);
    let saved: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert!(saved.get("session").is_none(), "{saved}");
    agent.set_outbox(&["m".to_string()], false, Some(1.0)).unwrap();
    let again = Agent::builder("cost").store(FileStore::new(&path)).build().unwrap();
    assert_eq!(again.outbox().unwrap().messages, ["m"]);
    std::fs::remove_dir_all(&dir).ok();
    let _ = MemoryStore::new();
}

#[test]
fn an_answer_is_checked_against_the_anchors_of_the_agent() {
    let mut agent = Agent::new("cost").unwrap();
    started(&mut agent);
    let run = agent.due()[0].run_id().to_string();
    let _ = agent.submit_init_reply(&run, 10, 90, "plan", "the facts about the plan", 80, "the reasons why it holds", None).unwrap();
    let answer =
        json!({"candidate": "better", "rationale": "the facts about it, in full", "raw": "0.5", "reasoning": "the reasons why it holds"});
    assert!(!agent.check_answer("repair", &answer).is_empty(), "a normalised score is refused against anchors of 10 and 90");
}
