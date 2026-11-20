//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Defects found in review of the port: each test is the case that showed it.

mod common;

use common::RUN;
use ioc_concord::agent::Agent;
use ioc_concord::guidance::render_init_reply;
use ioc_concord::relay::Relay;
use ioc_concord::schema::{compile_pattern, schema_text};
use ioc_concord::state::item_key;
use ioc_concord::store::{MemoryStore, Store};
use ioc_concord::tasks::InitReplyTask;
use ioc_concord::wire::{build_init, build_repair, InitParams};
use ioc_concord::xmlutil::{check_of, with_check};
use serde_json::json;

fn spec() -> serde_json::Value {
    json!({"type": "string", "description": "x"})
}

#[test]
fn the_largest_budget_does_not_overflow_when_a_repair_is_held() {
    let init = build_init("alice", &["a", "b"], "c", "r", "i", &spec(), Some(&InitParams::default().budget(1)), Some(RUN), None).unwrap();
    let huge = init.replace("<budget>1</budget>", "<budget>999999999</budget>");
    let huge = with_check(&huge.replace(&format!(" check=\"{}\"", check_of(&huge).unwrap()), "")).unwrap();
    let mut a = Agent::new("a").unwrap();
    assert!(a.receive(&huge, None).unwrap().has("joined"));
    // `b` is the bottleneck of no round yet: the repair cannot be judged, so it is held
    let repair = build_repair("b", RUN, 1, "p", "r", &5.into(), "w", None, None).unwrap();
    let step = a.receive(&repair, None).unwrap();
    assert!(step.has("held"));
}

#[test]
fn the_agent_and_the_relay_can_move_to_another_thread() {
    fn is_send<T: Send>() {}
    is_send::<Agent>();
    is_send::<Relay>();
    let agent = Agent::builder("a").store(MemoryStore::new()).clock(|| 1.0).build().unwrap();
    std::thread::spawn(move || assert_eq!(agent.me(), "a")).join().unwrap();
}

#[test]
fn a_message_without_a_uuid_is_not_kept_under_an_empty_key() {
    let init = build_init("a", &["a", "b"], "c", "r", "i", &spec(), None, Some(RUN), None).unwrap();
    let mut agent = Agent::new("a").unwrap();
    let _ = agent.start(&["a", "b"], "c", "r", "i", &spec(), None, Some(RUN), None).unwrap();
    let run = agent.state.runs.get_mut(RUN).unwrap();
    let before = (run.sent.len(), run.sent_by_uuid.len());
    assert!(run.keep_sent(&item_key("init-reply", "a", None), "<concord-init-reply from=\"a\"/>").is_err());
    assert_eq!((run.sent.len(), run.sent_by_uuid.len()), before);
    assert!(!run.sent_by_uuid.contains_key(""));
    assert!(run.keep_sent("x", &init).is_ok());
}

#[test]
fn a_value_that_names_a_placeholder_is_written_as_it_is() {
    let task = InitReplyTask {
        run_id: RUN.into(),
        initiator: "alice".into(),
        context: "c".into(),
        x_reject: "bad, and not {x_ideal}".into(),
        x_ideal: "good".into(),
        schema: spec(),
    };
    let text = render_init_reply("a", &task, &["a".to_string(), "b".to_string()], None);
    assert!(text.contains("  x_reject: bad, and not {x_ideal}\n"), "{text}");
    assert!(text.contains("  x_ideal:  good"));
}

#[test]
fn a_pattern_that_would_compile_to_more_than_the_size_limit_is_refused() {
    for pattern in ["(a{1000}){1000}", ".{60000}", "a{100000000}", "[a-z]{100000}", "(?:.{1000}){2}"] {
        let why = compile_pattern(pattern).unwrap_err().to_string();
        assert!(why.contains("not in the supported subset"), "{pattern}: {why}");
    }
    for pattern in [".{1000}", "(?:[a-z]{100}){130}", "^[a-z ]{1,80}$", "(a|b){1,500}"] {
        assert!(compile_pattern(pattern).is_ok(), "{pattern}");
    }
    assert!(compile_pattern("(?=a)").is_err());
}

#[test]
fn groups_nest_up_to_100_levels_in_a_pattern_and_deeper_is_refused_with_that_reason() {
    for (open, close) in [("(", ")"), ("(?:", ")"), ("(?:", ")*")] {
        let nested = |n: usize| format!("{}a{}", open.repeat(n), close.repeat(n));
        assert!(compile_pattern(&nested(100)).is_ok(), "{open} 100 levels");
        for n in [101, 300, 5_000] {
            let why = compile_pattern(&nested(n)).unwrap_err().to_string();
            assert!(why.contains("groups nest deeper than 100 levels"), "{open} {n}: {why}");
        }
    }
}

#[test]
fn matching_a_large_pattern_that_is_accepted_stays_fast_and_a_larger_one_is_refused_at_once() {
    let started = std::time::Instant::now();
    let re = compile_pattern("(?:[a-z]{30}){100}").unwrap();
    let _ = re.is_match(&("a".repeat(20_000) + "!"));
    assert!(started.elapsed() < std::time::Duration::from_secs(5), "{:?}", started.elapsed()); // a test build is far slower than a release one
    let started = std::time::Instant::now();
    assert!(compile_pattern(".{60000}").is_err());
    assert!(started.elapsed() < std::time::Duration::from_secs(1));
}

#[test]
fn the_saved_state_keeps_the_order_of_its_keys() {
    let mut a = Agent::builder("a").store(MemoryStore::new()).build().unwrap();
    let _ = a.start(&["a", "b"], "c", "r", "i", &spec(), None, Some(RUN), None).unwrap();
    let mut store = MemoryStore::new();
    store.save(&serde_json::to_value(&a.state).unwrap()).unwrap();
    let state = store.get().unwrap();
    let keys = |v: &serde_json::Value| v.as_object().unwrap().keys().cloned().collect::<Vec<_>>();
    assert_eq!(keys(&state), ["me", "runs", "active"]);
    assert_eq!(
        keys(&state["runs"][RUN]),
        [
            "role",
            "run_id",
            "initiator",
            "participants",
            "params",
            "context",
            "x_reject",
            "x_ideal",
            "schema",
            "engine",
            "sent",
            "sent_by_uuid",
            "received",
            "uuids",
            "outcome",
            "reported",
            "mismatches",
            "timers",
            "held",
            "expected",
            "abort",
            "cancel_sent",
            "disputed",
            "status"
        ]
    );
    assert_eq!(keys(&state["runs"][RUN]["params"]), ["satisfaction_threshold", "eps", "B", "timeout", "retries"]);
}

#[test]
fn the_schema_in_an_init_has_its_keys_sorted() {
    assert_eq!(
        schema_text(&json!({"type": "string", "description": "d", "maxLength": 5})),
        r#"{"description": "d", "maxLength": 5, "type": "string"}"#
    );
}

// ---- the second review ----

#[test]
fn a_from_hidden_in_another_attribute_is_not_taken_for_the_sender() {
    let run = RUN;
    // the quoted value of `a` holds a `from`, which is not an attribute: both readers of the root read `mallory`, who is not in the run
    let doc = format!(
        r#"<concord-cancel version="0.3.0" from="mallory" a=' from="b"' run_id="{run}" uuid="aaaaaaaa-0000-4000-8000-000000000001" reason="version"/>"#
    );
    assert_eq!(ioc_concord::wire::peek(&doc).unwrap().attr("from"), Some("mallory"));
    assert_eq!(ioc_concord::wire::parse_message(&doc, None).unwrap().from, "mallory");
    let mut a = Agent::new("a").unwrap();
    let _ = a.start(&["a", "b"], "c", "r", "i", &spec(), None, Some(RUN), None).unwrap();
    let step = a.receive(&doc, None).unwrap();
    assert!(step.events.iter().any(|e| e.str("reason") == Some("sender is not in the run")), "{:?}", step.events);
    assert_eq!(a.status(None).unwrap().status, ioc_concord::state::RunStatus::Active);
    assert!(!a.state.runs[RUN].uuids.contains_key("aaaaaaaa-0000-4000-8000-000000000001"));
}

/// A store that refuses to save while `fail` holds.
#[derive(Clone)]
struct Flaky {
    inner: MemoryStore,
    fail: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl Store for Flaky {
    fn load(&self) -> ioc_concord::Result<serde_json::Value> {
        self.inner.load()
    }

    fn save(&mut self, state: &serde_json::Value) -> ioc_concord::Result<()> {
        if self.fail.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(ioc_concord::ConcordError::state("the disk is full"));
        }
        self.inner.save(state)
    }
}

#[test]
fn a_call_that_fails_changes_nothing_so_the_same_call_can_be_made_again() {
    let flaky = Flaky { inner: MemoryStore::new(), fail: Default::default() };
    let mut a = Agent::builder("a").store(flaky.clone()).build().unwrap();
    let init = build_init("alice", &["a", "b"], "c", "r", "i", &spec(), None, Some(RUN), None).unwrap();
    let _ = a.receive(&init, None).unwrap();
    let before = a.state.clone();
    flaky.fail.store(true, std::sync::atomic::Ordering::SeqCst);
    let err = a.submit_init_reply(RUN, 10, 90, "p", "because", 50, "reason", None).unwrap_err();
    assert_eq!(err.code(), "state_error");
    assert_eq!(a.state, before, "the engine and the sent messages are as they were");
    assert!(a.due().iter().any(|t| t.kind() == "init-reply"), "the answer is still owed");
    flaky.fail.store(false, std::sync::atomic::Ordering::SeqCst);
    let step = a.submit_init_reply(RUN, 10, 90, "p", "because", 50, "reason", None).unwrap();
    assert_eq!(step.send.len(), 1);
    assert_eq!(flaky.inner.get().unwrap(), serde_json::to_value(&a.state).unwrap());
}

#[test]
fn a_time_that_is_not_a_number_is_refused() {
    let mut a = Agent::new("a").unwrap();
    for now in [f64::NAN, f64::INFINITY] {
        assert_eq!(a.tick(Some(now)).unwrap_err().code(), "state_error");
    }
}

#[test]
fn a_file_store_reports_a_missing_file_and_writes_whole_files() {
    let dir = std::env::temp_dir().join(format!("ioc-concord-hardening-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut store = ioc_concord::store::FileStore::new(dir.join("state.json"));
    assert_eq!(store.load().unwrap_err().code(), "missing_state");
    // two stores of one process, writing the same file name in two folders, and one file written again and again
    let mut other = ioc_concord::store::FileStore::new(dir.join("state.json"));
    for n in 0..20 {
        store.save(&json!({"n": n})).unwrap();
        other.save(&json!({"n": n + 100})).unwrap();
    }
    assert_eq!(store.load().unwrap(), json!({"n": 119}));
    let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
    assert_eq!(leftovers, ["state.json"]);
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_document_that_is_not_a_tag_gets_no_check() {
    assert_eq!(with_check("not a tag").unwrap_err().code(), "invalid_message");
}

#[test]
fn a_store_whose_lock_was_poisoned_still_works() {
    let store = MemoryStore::new();
    let held = store.clone();
    let _ = std::thread::spawn(move || {
        let mut held = held;
        held.save(&json!(1)).unwrap();
        panic!("a thread that dies while it uses the store");
    })
    .join();
    let mut store = store;
    store.save(&json!(2)).unwrap();
    assert_eq!(store.load().unwrap(), json!(2));
}
