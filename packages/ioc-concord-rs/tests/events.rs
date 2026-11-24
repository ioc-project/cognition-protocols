//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The serialized shape of the events: the names, the reasons and the fields a log reads.

use ioc_concord::agent::Step;
use ioc_concord::event::{EventKind, IgnoredReason, Source};
use serde_json::{json, Value};

/// A step with one event noted at time 12.5.
fn noted(kind: EventKind) -> ioc_concord::agent::Event {
    let mut step = Step { now: 12.5, send: vec![], forward: vec![], accepted: vec![], events: vec![] };
    step.note(kind);
    step.events.remove(0)
}

#[test]
fn an_ignored_event_carries_its_reason_and_its_source() {
    let event = noted(EventKind::ignored(IgnoredReason::UnknownRun, Source::of("score").from("a").run_id_attr(None)));
    assert_eq!(event.event, "ignored");
    assert_eq!(event.at, 12.5);
    assert_eq!(event.details.len(), 4);
    assert_eq!(event.str("reason"), Some("unknown run"));
    assert_eq!(event.str("kind"), Some("score"));
    assert_eq!(event.str("from"), Some("a"));
    assert_eq!(event.get("run_id"), Some(&Value::Null));
    assert!(event.get("candidate").is_none());
}

#[test]
fn an_aborted_event_keeps_its_explicit_nulls() {
    let event = noted(EventKind::Aborted { run_id: "r1".into(), reason: "version".into(), agent: None, superseded_by: None });
    assert_eq!(event.event, "aborted");
    assert_eq!(
        serde_json::to_value(&event).unwrap(),
        json!({"event": "aborted", "at": 12.5, "run_id": "r1", "reason": "version", "agent": null, "superseded_by": null})
    );
}

#[test]
fn a_recorded_score_names_its_candidates() {
    let event = noted(EventKind::scores_recorded("b", &["0|a".to_string(), "1|c".to_string()]));
    assert_eq!(event.event, "recorded");
    assert_eq!(event.get("kind"), Some(&json!("score")));
    assert_eq!(event.get("from"), Some(&json!("b")));
    assert_eq!(event.get("candidates"), Some(&json!(["0|a", "1|c"])));
    assert!(event.get("outcome").is_none());
}

#[test]
fn the_names_with_spaces_and_the_renamed_outcome_are_kept() {
    assert_eq!(noted(EventKind::NothingToSend { source: Source::of("error") }).event, "nothing to send");
    let computed = noted(EventKind::ComputedOutcome { outcome: None, winner: None, run_id: "r".into() });
    assert_eq!(computed.event, "outcome");
    assert_eq!(computed.get("winner"), Some(&Value::Null));
}

#[test]
fn an_event_serializes_flat() {
    let event = noted(EventKind::ignored(IgnoredReason::NotConcord, Source::default()));
    let value = serde_json::to_value(&event).unwrap();
    assert_eq!(value, json!({"event": "ignored", "at": 12.5, "reason": "not a CONCORD message"}));
    let keys: Vec<&String> = value.as_object().unwrap().keys().collect();
    assert_eq!(keys[..2], ["event", "at"]);
}
