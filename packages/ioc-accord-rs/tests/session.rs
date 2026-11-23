//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! A session end to end: self-driving through every phase, the delivery checks and what a failed command keeps, and the commands of
//! the phase-by-phase mode.

use ioc_accord::messages::find_check;
use ioc_accord::session::{InitArgs, Session};
use serde_json::{json, Value};

fn start(specialists: &str, key_terms: &str) -> (Session, Value) {
    let mut s = Session::new();
    let out = s
        .init(&InitArgs {
            specialists: specialists.into(),
            key_terms: key_terms.into(),
            action_tool: "deploy".into(),
            task: "cut the p95 latency".into(),
            state_id: Some("0badcafe".into()),
        })
        .unwrap();
    (s, out)
}

/// The reply of an agent to the message it was sent: the delivery line echoed, then the body.
fn answer(message: &Value, body: Value) -> String {
    let line = message.as_str().unwrap().lines().next().unwrap();
    format!("{line}\n{body}")
}

#[test]
fn a_self_driving_session_runs_from_convene_to_the_locked_contract() {
    let (mut s, out) = start("ops,dev", "latency");
    assert_eq!(out["status"], "SEND");
    assert_eq!(out["awaiting"], json!(["ops", "dev"]));
    assert_eq!(s.state()["state_id"], "0badcafe");
    assert!(out["messages"]["ops"].as_str().unwrap().starts_with("<accord-delivery state=\"0badcafe\" check=\""));

    let mut messages = out["messages"].clone();
    s.handle("ops", &answer(&messages["ops"], json!({"agent": "ops", "roles": ["sre"]})), None, None).unwrap();
    let out = s.handle("dev", &answer(&messages["dev"], json!({"agent": "dev", "roles": ["SRE "]})), None, None).unwrap();
    assert_eq!(out["phase"], "frame_0");
    assert_eq!(out["flagged_items"], json!(["duplicate exclusive role 'sre' claimed by ['ops', 'dev']"]));
    messages = out["messages"].clone();

    for round in 0..2 {
        let a = json!({"agent": "ops", "atoms": [{"text": "p95 latency too high", "type": "risk"}]});
        let b = json!({"agent": "dev", "atoms": [{"text": "P95 latency too high!"}, {"text": "deploys are slow"}]});
        s.handle("ops", &answer(&messages["ops"], a), None, None).unwrap();
        let out = s.handle("dev", &answer(&messages["dev"], b), None, None).unwrap();
        assert_eq!(out["round"], round + 1);
        messages = out["messages"].clone();
    }
    assert_eq!(s.phase_key().unwrap(), "ground");
    assert_eq!(s.state()["frame"][0]["support"], 2);

    s.handle("ops", &answer(&messages["ops"], json!({"agent": "ops", "units": {"latency": "milliseconds"}})), None, None).unwrap();
    let out = s.handle("dev", &answer(&messages["dev"], json!({"agent": "dev", "units": {"latency": "ms"}})), None, None).unwrap();
    assert_eq!(out["phase"], "verify");
    messages = out["messages"].clone();

    let rk = Some(json!(["k1", "k2"]));
    let oc = Some(json!(["p95 under 200ms"]));
    let a = json!({"agent": "ops", "holds": ["k1"], "checks": [{"criterion": "load test", "covers": ["p95 under 200ms"]}]});
    s.handle("ops", &answer(&messages["ops"], a), rk, oc).unwrap();
    let out = s.handle("dev", &answer(&messages["dev"], json!({"agent": "dev", "holds": [], "checks": []})), None, None).unwrap();
    assert_eq!(out["status"], "LOCKED");
    let contract = &out["locked_intent_contract"];
    assert_eq!(contract["status"], "INTENT-LOCKED");
    assert_eq!(contract["verifiable_objective"]["owners"], json!({"k1": "ops"}));
    assert_eq!(out["gaps"], json!(["k2"]));

    let exec = s.execution_message("dev", "  ship it \n").unwrap();
    assert!(exec["message"].as_str().unwrap().ends_with("ASSIGNMENT: ship it"));
    let again = s.handle("ops", "anything", None, None).unwrap();
    assert_eq!(again, json!({"status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {}}));

    // the state file reads back to the same session
    assert_eq!(Session::load(&s.dump()).unwrap(), s);
}

#[test]
fn a_missing_check_is_refused_once_and_the_count_survives_the_refusal() {
    let (mut s, _) = start("ops", "");
    let body = json!({"agent": "ops", "roles": []}).to_string();
    let err = s.handle("ops", &body, None, None).unwrap_err();
    assert!(err.is_refusal());
    assert_eq!(s.state()["check_fail"], json!({"convene:ops": 1}));
    assert!(err.hint().starts_with("The reply is missing"));

    let out = s.handle("ops", &body, None, None).unwrap();
    assert_eq!(out["phase"], "frame_0");
    assert_eq!(s.state()["check_fail"], json!({}));
    assert_eq!(s.state()["handle_replies"]["convene"]["ops"], body);
}

#[test]
fn a_wrong_check_or_a_reply_of_the_wrong_shape_changes_nothing() {
    let (mut s, out) = start("ops", "");
    let before = s.clone();
    let wrong = "<accord-delivery state=\"x\" check=\"deadbeef\"></accord-delivery>\n{\"agent\": \"ops\", \"roles\": []}";
    let err = s.handle("ops", wrong, Some(json!(["k"])), None).unwrap_err();
    assert!(!err.is_refusal());
    assert!(err.message().contains("does not match its check (expected"));
    assert_eq!(s, before);

    let check = find_check(out["messages"]["ops"].as_str().unwrap()).unwrap().to_string();
    let summary = format!("<accord-delivery check=\"{check}\">\n{{\"agent\": \"ops\", \"summary\": \"fine\"}}");
    let err = s.handle("ops", &summary, None, None).unwrap_err();
    assert_eq!(
        err.message(),
        "handle: the reply from ops carries none of the agent-card fields. CONVENE needs an agent card: `agent`, plus `roles`, \
         `capabilities`, `access_scopes` and `holdings_summary`. Nothing was recorded."
    );
    assert_eq!(s, before);
}

#[test]
fn handle_needs_a_task() {
    let mut s = Session::new();
    let err = s.handle("a", "x", None, None).unwrap_err();
    assert_eq!(err.message(), "handle: no task in state — run init --task first");
    assert!(err.hint().starts_with("Run `init` first"));
}

#[test]
fn the_phase_by_phase_commands_build_the_same_contract() {
    let mut s = Session::new();
    let out = s.init(&InitArgs { specialists: "ops, dev,".into(), key_terms: "latency".into(), ..InitArgs::default() }).unwrap();
    assert_eq!(out["next_command"], "roster");
    assert!(out.get("messages").is_none());
    assert_eq!(s.state()["state_id"], "");

    s.roster(&json!(["{\"agent\": \"ops\"}", {"agent": "dev", "roles": ["x"]}])).unwrap();
    let err = s.frame(&json!(["prose"])).unwrap_err();
    assert!(err.message().starts_with("frame: none of the responses"));
    assert_eq!(s.state()["frame_rounds"], 0);
    let out = s.frame(&json!([{"agent": "ops", "atoms": [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}]}])).unwrap();
    assert_eq!(out["coverage"], json!({"good_turing_missing_mass": 1.0, "chao1": 10.0, "coverage": 0.0}));
    assert_eq!(out["next_command"], "frame");

    let err = s.glossary(&json!(["prose"]), None).unwrap_err();
    assert!(err.message().contains("(latency)"));
    let out = s.glossary(&json!([{"agent": "ops", "units": {"latency": "ms"}}]), None).unwrap();
    assert_eq!(out["next_command"], "verify");

    let out = s.verify(&json!([{"agent": "ops", "holds": ["k1"]}]), None, None).unwrap();
    assert_eq!(out["knowledge_map"], json!([{"item": "k1", "held_by": ["ops"], "gap": false}]));
    let flagged = &out["locked_intent_contract"]["flagged_items"];
    assert!(flagged.as_array().unwrap().iter().any(|f| f == "fragile_frame: all 4 atoms are unverified singletons"));
    assert!(out["next"]["note"].as_str().unwrap().ends_with("then call the action tool once the replies arrive"));
    assert_eq!(s.lock().unwrap(), out["locked_intent_contract"]);
    assert_eq!(s.status().unwrap()["locked"], true);
}
