//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `instructions_for` and `ending`: what a caller that holds no `Task` uses.

use ioc_concord::agent::Agent;
use ioc_concord::errors::ConcordError;
use ioc_concord::tasks::Task;
use serde_json::json;

fn started() -> (Agent, String) {
    let mut agent = Agent::new("cost").unwrap();
    let spec = json!({"type": "string", "description": "what one option looks like"});
    let step = agent.start(&["cost", "security"], "which plan?", "no plan", "the best plan", &spec, None, None, None).unwrap();
    assert!(!step.send.is_empty());
    let run_id = agent.due().first().map(|t| t.run_id().to_string()).expect("a task is owed");
    (agent, run_id)
}

#[test]
fn instructions_for_is_instructions_of_the_task_owed() {
    let (agent, run_id) = started();
    let task: Task = agent.due().remove(0);
    assert_eq!(agent.instructions_for(task.kind(), &run_id, task.round()).unwrap(), agent.instructions(&task).unwrap());
}

#[test]
fn instructions_for_a_task_that_is_not_owed_is_a_state_error() {
    let (agent, run_id) = started();
    assert!(matches!(agent.instructions_for("score", &run_id, None), Err(ConcordError::State(_))));
    assert!(matches!(agent.instructions_for("init-reply", "no-such-run", None), Err(ConcordError::State(_))));
}

#[test]
fn instructions_for_a_repair_of_another_round_is_a_state_error() {
    let (agent, run_id) = started();
    // an init reply has no round: a round that is given does not match it
    assert!(matches!(agent.instructions_for("init-reply", &run_id, Some(3)), Err(ConcordError::State(_))));
}

#[test]
fn ending_is_the_end_of_the_report_and_none_without_a_run() {
    assert_eq!(Agent::new("cost").unwrap().ending(None), None);
    let (agent, run_id) = started();
    let report = agent.report(Some(&run_id)).unwrap();
    assert_eq!(agent.ending(Some(&run_id)), Some(ioc_concord::guidance::render_end("cost", &report)));
}
