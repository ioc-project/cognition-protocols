//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
#![cfg(feature = "uniffi")]
//! The API for other languages: a run between two agents through the types a foreign caller sees.

use ioc_concord::ffi::{refusal, settled, Agent, ConcordError, Number, Relay, ScoreInput, Task};
use serde_json::json;

fn spec() -> String {
    json!({"type": "string", "description": "what one option looks like"}).to_string()
}

fn names(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

fn int(value: i64) -> Number {
    Number::Int { value }
}

#[test]
fn a_run_ends_in_success() {
    let agents = [Agent::new("cost".into(), None).unwrap(), Agent::new("security".into(), None).unwrap()];
    let opening = agents[0]
        .start(names(&["cost", "security"]), "which plan?".into(), "no plan".into(), "the best plan".into(), spec(), None, None, None)
        .unwrap();
    let mut queue = opening.send;
    while !queue.is_empty() || agents.iter().any(|a| !a.due().unwrap().is_empty()) {
        for text in std::mem::take(&mut queue) {
            for agent in &agents {
                queue.extend(agent.receive(text.clone(), None).unwrap().send);
            }
        }
        for agent in &agents {
            for task in agent.due().unwrap() {
                assert!(!agent.instructions(task.clone()).unwrap().is_empty());
                let step = match task {
                    Task::InitReply { run_id, .. } => agent
                        .submit_init_reply(run_id, int(0), int(100), "plan A".into(), "facts".into(), int(90), "why".into(), None)
                        .unwrap(),
                    Task::Score { run_id, candidates } => {
                        let scores =
                            candidates.iter().map(|c| ScoreInput { id: c.id.clone(), raw: int(80), reasoning: "ok".into() }).collect();
                        agent.submit_score(run_id, scores, None).unwrap()
                    }
                    Task::Repair { run_id, .. } => {
                        agent.submit_repair(run_id, "plan B".into(), "facts".into(), int(90), "why".into(), None).unwrap()
                    }
                };
                queue.extend(step.send);
            }
        }
    }
    let report: serde_json::Value = serde_json::from_str(&agents[1].report_json(None).unwrap().unwrap()).unwrap();
    assert_eq!(report["outcome"], "SUCCESS");
}

#[test]
fn errors_say_their_kind() {
    let agent = Agent::new("cost".into(), None).unwrap();
    let start = |spec: &str| {
        agent.start(names(&["cost", "security"]), "c".into(), "r".into(), "i".into(), spec.into(), None, None, None).unwrap_err()
    };
    assert!(matches!(start("not json"), ConcordError::Schema { .. }));
    assert!(matches!(start("[]"), ConcordError::InvalidMessage { .. }));
}

#[test]
fn an_agent_that_is_not_a_centre_has_no_spokes() {
    let agent = Agent::new("cost".into(), None).unwrap();
    assert!(matches!(agent.spokes(), Err(ConcordError::State { .. })));
    assert!(matches!(agent.add_spoke("a".into()), Err(ConcordError::State { .. })));
    assert!(matches!(agent.targets(None), Err(ConcordError::State { .. })));
    assert!(matches!(agent.knows("r".into()), Err(ConcordError::State { .. })));
}

#[test]
fn a_centre_passes_on_to_all_the_spokes_but_the_origin() {
    let centre = Agent::new_centre("hub".into(), names(&["a", "b", "c"]), None, None).unwrap();
    assert_eq!(centre.targets(Some("b".into())).unwrap(), names(&["a", "c"]));
    centre.remove_spoke("c".into()).unwrap();
    assert_eq!(centre.spokes().unwrap(), names(&["a", "b"]));
    let relay = Relay::new();
    relay.add_spoke("a".into()).unwrap();
    relay.add_spoke("b".into()).unwrap();
    assert_eq!(relay.targets(Some("a".into())).unwrap(), names(&["b"]));
}

#[test]
fn a_centre_that_restarts_still_knows_its_runs() {
    let dir = std::env::temp_dir().join(format!("ioc-concord-ffi-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (state, relay) = (dir.join("hub.json").display().to_string(), dir.join("relay.json").display().to_string());
    let centre = Agent::new_centre("hub".into(), names(&["a", "b"]), Some(state.clone()), Some(relay.clone())).unwrap();
    let step = centre
        .start(
            names(&["a", "b"]),
            "which plan?".into(),
            "no plan".into(),
            "the best plan".into(),
            spec(),
            None,
            Some("6f1c2b9e-4d3a-4e8b-9a77-0c5d2e1f3a40".into()),
            None,
        )
        .unwrap();
    assert!(!step.send.is_empty());
    assert!(centre.knows("6f1c2b9e-4d3a-4e8b-9a77-0c5d2e1f3a40".into()).unwrap());
    let again = Agent::new_centre("hub".into(), names(&["a", "b"]), Some(state), Some(relay)).unwrap();
    assert!(again.knows("6f1c2b9e-4d3a-4e8b-9a77-0c5d2e1f3a40".into()).unwrap());
    again.forget("6f1c2b9e-4d3a-4e8b-9a77-0c5d2e1f3a40".into()).unwrap();
    assert!(!again.knows("6f1c2b9e-4d3a-4e8b-9a77-0c5d2e1f3a40".into()).unwrap());
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn an_instruction_is_for_the_task_owed_now() {
    let agent = Agent::new("cost".into(), None).unwrap();
    agent.start(names(&["cost", "security"]), "c".into(), "r".into(), "i".into(), spec(), None, None, None).unwrap();
    let task = agent.due().unwrap().remove(0);
    assert!(!agent.instructions(task).unwrap().is_empty());
    let other = Task::Score { run_id: "no-such-run".into(), candidates: Vec::new() };
    assert!(matches!(agent.instructions(other), Err(ConcordError::State { .. })));
}

#[test]
fn a_refusal_is_the_one_of_the_library() {
    let invalid = ConcordError::InvalidMessage { message: "the score is out of range".into() };
    assert!(refusal(invalid.clone(), 1).starts_with("refused: the score is out of range"));
    assert!(!settled(invalid));
    let state = ConcordError::State { message: "already done".into() };
    assert!(!refusal(state, 1).is_empty());
}

#[test]
fn time_passes_through_tick_and_the_status_is_json() {
    let agent = Agent::new("hub".into(), None).unwrap();
    assert!(agent.next_wakeup().unwrap().is_none());
    assert!(agent.status_json(None).unwrap().is_none());
    agent.start(names(&["a", "b"]), "c".into(), "r".into(), "i".into(), spec(), None, None, Some(1000.0)).unwrap();
    let wake = agent.next_wakeup().unwrap().expect("a reply is awaited");
    assert!(wake > 1000.0);
    assert!(agent.tick(Some(1000.0)).unwrap().send.is_empty(), "nothing is late yet");
    assert!(!agent.tick(Some(wake + 1.0)).unwrap().send.is_empty(), "the silent agents are asked again");
    let status: serde_json::Value = serde_json::from_str(&agent.status_json(None).unwrap().unwrap()).unwrap();
    assert!(status.is_object());
}

#[test]
fn a_relay_learns_a_run_and_passes_its_messages_on_and_ignores_the_rest() {
    let relay = Relay::new();
    assert!(relay.receive("not concord".into()).unwrap().is_empty());
    assert!(!relay.should_pass_on("not concord".into()).unwrap());
    let centre = Agent::new("hub".into(), None).unwrap();
    let init = centre.start(names(&["a", "b"]), "c".into(), "r".into(), "i".into(), spec(), None, None, None).unwrap().send.remove(0);
    assert!(relay.learn(init.clone()).unwrap());
    assert!(relay.should_pass_on(init.clone()).unwrap());
    assert_eq!(relay.receive(init.clone()).unwrap(), vec![init]);
}

#[test]
fn a_relay_keeps_its_state_in_the_file_it_is_given() {
    let dir = std::env::temp_dir().join(format!("ioc-concord-ffi-relay-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("relay.json").display().to_string();
    let relay = Relay::with_state_path(path.clone()).unwrap();
    let centre = Agent::new("hub".into(), None).unwrap();
    let init = centre.start(names(&["a", "b"]), "c".into(), "r".into(), "i".into(), spec(), None, None, None).unwrap().send.remove(0);
    assert!(relay.learn(init.clone()).unwrap());
    let again = Relay::with_state_path(path).unwrap();
    assert!(again.should_pass_on(init).unwrap(), "the run it learned is still known");
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn an_anchor_or_a_rating_can_be_text_an_integer_or_a_float() {
    let agent = Agent::new("cost".into(), None).unwrap();
    agent.start(names(&["cost", "security"]), "c".into(), "r".into(), "i".into(), spec(), None, None, None).unwrap();
    let run_id = match agent.due().unwrap().remove(0) {
        Task::InitReply { run_id, .. } => run_id,
        other => panic!("{other:?}"),
    };
    // an anchor and a rating may be given as the text of a number, an integer or a float
    let step = agent
        .submit_init_reply(
            run_id,
            Number::Text { value: "1".into() },
            Number::Float { value: 9.0 },
            "a plan".into(),
            "why".into(),
            Number::Int { value: 5 },
            "because".into(),
            None,
        )
        .unwrap();
    assert!(!step.send.is_empty());
}
