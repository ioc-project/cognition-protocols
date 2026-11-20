//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The fixtures of `protocols/concord/spec/fixtures`: fixed messages, the values a reader must get from them, messages that must be
//! refused, the integrity check, the patterns and a saved state. They are the contract between implementations, and the Rust crate is
//! the reference that makes them: `recipes` builds every file in memory, with fixed ids, uuids and clock.
//!
//! * `the_facts_the_spec_decides_hold_for_what_the_recipes_build` needs no directory: it checks what is written by hand in the recipes
//!   (who sends each message, what each refused message breaks, what each pattern matches, the canonical text and the check).
//! * `the_files_are_what_the_recipes_build` compares the files with what the recipes build. `UPDATE_FIXTURES=1 cargo test --test fixtures`
//!   writes them instead, after the facts have held.
//! * The other tests read the files and check them against the library, and each collects every fixture that differs, so that one run
//!   shows the whole list.
//!
//! The directory is outside the crate. When it is missing, the tests that read it say so and pass, unless the variable `CI` is set (a
//! path that is wrong in a workflow must not skip them for ever); everything that is built in memory is checked in every case.

mod common;
#[path = "fixtures/recipes.rs"]
mod recipes;

use std::path::{Path, PathBuf};

use common::uuids;
use ioc_concord::agent::Agent;
use ioc_concord::schema::compile_pattern;
use ioc_concord::store::{MemoryStore, Store};
use ioc_concord::wire::{check_ok, locate, parse_message, peek};
use ioc_concord::xmlutil::{canonical_text, check_of, compute_check};
use recipes::{parsed_json, spec, GENERATED};
use serde_json::{json, Value};

fn update_mode() -> bool {
    std::env::var_os("UPDATE_FIXTURES").is_some()
}

/// The fixtures directory, or None when it is missing and this is not a CI run.
fn fixtures_dir() -> Option<PathBuf> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../protocols/concord/spec/fixtures");
    if dir.is_dir() {
        return Some(dir);
    }
    assert!(std::env::var_os("CI").is_none(), "the fixtures are not at {}, and this is a CI run: they must be there", dir.display());
    eprintln!("the fixtures are not at {}: nothing to check", dir.display());
    None
}

/// The directory for a test that reads the files. While the files are being written (`UPDATE_FIXTURES`) nothing reads them.
fn dir() -> Option<PathBuf> {
    if update_mode() {
        return None;
    }
    fixtures_dir()
}

fn text(dir: &Path, name: &str) -> String {
    std::fs::read_to_string(dir.join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

fn json_of(dir: &Path, name: &str) -> Value {
    serde_json::from_str(&text(dir, name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

fn nothing_differs(family: &str, differences: &[String]) {
    assert!(differences.is_empty(), "{} of the {family} fixtures differ:\n  {}", differences.len(), differences.join("\n  "));
}

/// The files that are under the generated folders of `dir`, as paths under `dir`.
fn generated_on_disk(dir: &Path) -> Vec<String> {
    fn walk(base: &Path, at: &Path, out: &mut Vec<String>) {
        for entry in std::fs::read_dir(at).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(base, &path, out);
            } else {
                out.push(path.strip_prefix(base).unwrap().to_string_lossy().replace('\\', "/"));
            }
        }
    }
    let mut out = Vec::new();
    for name in GENERATED {
        let at = dir.join(name);
        if at.is_dir() {
            walk(dir, &at, &mut out);
        } else if at.is_file() {
            out.push(name.to_string());
        }
    }
    out.sort();
    out
}

#[test]
fn the_facts_the_spec_decides_hold_for_what_the_recipes_build() {
    let (_, failures) = recipes::generate();
    nothing_differs("hand-written fact of the recipes", &failures);
}

#[test]
fn the_recipes_build_the_same_files_twice() {
    assert_eq!(recipes::generate().0, recipes::generate().0);
}

#[test]
fn the_files_are_what_the_recipes_build() {
    let (files, failures) = recipes::generate();
    nothing_differs("hand-written fact of the recipes", &failures);
    let Some(dir) = fixtures_dir() else { return };
    if update_mode() {
        for name in GENERATED {
            let at = dir.join(name);
            if at.is_dir() {
                std::fs::remove_dir_all(&at).unwrap();
            } else if at.is_file() {
                std::fs::remove_file(&at).unwrap();
            }
        }
        for (path, content) in &files {
            let at = dir.join(path);
            std::fs::create_dir_all(at.parent().unwrap()).unwrap();
            std::fs::write(at, content).unwrap();
        }
        eprintln!("{} fixtures written to {}", files.len(), dir.display());
    }
    let mut differences = Vec::new();
    for (path, content) in &files {
        match std::fs::read_to_string(dir.join(path)) {
            Err(_) => differences.push(format!("{path}: the file is missing")),
            Ok(on_disk) if path.ends_with(".json") => {
                let (a, b) = (serde_json::from_str::<Value>(&on_disk), serde_json::from_str::<Value>(content));
                if a.ok() != b.ok() {
                    differences.push(format!("{path}: the values differ from what the recipes build"));
                }
            }
            Ok(on_disk) if &on_disk != content => differences.push(format!("{path}: the text differs from what the recipes build")),
            Ok(_) => {}
        }
    }
    for path in generated_on_disk(&dir) {
        if !files.contains_key(&path) {
            differences.push(format!("{path}: the file is not built by any recipe"));
        }
    }
    nothing_differs("file", &differences);
}

#[test]
fn a_valid_message_is_read_as_the_fixture_says() {
    let Some(dir) = dir() else { return };
    let mut differences = Vec::new();
    for case in json_of(&dir, "wire/valid.json").as_array().unwrap() {
        let file = case["file"].as_str().unwrap();
        let doc = text(&dir, file);
        let mut wrong =
            |what: &str, got: String, want: &Value| differences.push(format!("{file}: {what}: got {got}, the fixture says {want}"));
        match locate(&doc) {
            Ok(found) if found == doc.trim() => {}
            other => wrong("locate", format!("{other:?}"), &json!("the document")),
        }
        let Some(info) = peek(&doc) else {
            wrong("peek", "nothing".into(), &json!("the root"));
            continue;
        };
        for key in ["from", "run_id", "uuid", "check"] {
            if info.attr(key).map(str::to_string) != case[key].as_str().map(str::to_string) {
                wrong(&format!("peek of {key}"), format!("{:?}", info.attr(key)), &case[key]);
            }
        }
        if case["check"].is_string() && !check_ok(&doc) {
            wrong("the check", "not ok".into(), &case["check"]);
        }
        let schema = spec();
        let spec = (case["kind"] != "init").then_some(&schema);
        match parse_message(&doc, spec) {
            Ok(m) => {
                let got = parsed_json(&m);
                if got != case["parsed"] {
                    wrong("parsed", got.to_string(), &case["parsed"]);
                }
            }
            Err(e) => wrong("parse_message", format!("refused: {e}"), &case["parsed"]),
        }
    }
    nothing_differs("valid message", &differences);
}

#[test]
fn a_message_that_must_be_refused_is_refused_and_changes_nothing() {
    let Some(dir) = dir() else { return };
    let init = text(&dir, "wire/init.xml");
    let mut differences = Vec::new();
    for case in json_of(&dir, "wire/refused.json").as_array().unwrap() {
        let file = case["file"].as_str().unwrap();
        let doc = text(&dir, file);
        if let Ok(m) = parse_message(&doc, Some(&spec())) {
            differences.push(format!("{file}: read as a {} message, but {}", m.kind().as_str(), case["why"]));
        }
        let fresh = Agent::new("cost").unwrap();
        let mut a = Agent::new("cost").unwrap();
        match a.receive(&doc, None) {
            Ok(_) if serde_json::to_value(&a.state).unwrap() == serde_json::to_value(&fresh.state).unwrap() => {}
            Ok(_) => differences.push(format!("{file}: an agent that has not joined learned something from it")),
            Err(e) => differences.push(format!("{file}: an agent that has not joined failed on it: {e}")),
        }
        let mut joined = Agent::new("cost").unwrap();
        let _ = joined.receive(&init, None).unwrap();
        let before = serde_json::to_value(&joined.state).unwrap();
        match joined.receive(&doc, None) {
            Ok(_) if serde_json::to_value(&joined.state).unwrap() == before => {}
            Ok(_) => differences.push(format!("{file}: it changed the state of an agent that had joined the run")),
            Err(e) => differences.push(format!("{file}: an agent that had joined failed on it: {e}")),
        }
    }
    nothing_differs("refused message", &differences);
}

#[test]
fn the_canonical_text_and_the_check_are_those_of_the_fixture() {
    let Some(dir) = dir() else { return };
    let mut differences = Vec::new();
    for case in json_of(&dir, "check.json").as_array().unwrap() {
        let (name, doc) = (case["name"].as_str().unwrap(), case["document"].as_str().unwrap());
        if canonical_text(doc) != case["canonical"].as_str().unwrap() {
            differences.push(format!("{name}: the canonical text is {:?}, the fixture says {}", canonical_text(doc), case["canonical"]));
        }
        if compute_check(doc) != case["check"].as_str().unwrap() {
            differences.push(format!("{name}: the check is {}, the fixture says {}", compute_check(doc), case["check"]));
        }
        if check_of(doc) != case["declared"].as_str().map(str::to_string) {
            differences.push(format!("{name}: the declared check is {:?}, the fixture says {}", check_of(doc), case["declared"]));
        }
    }
    nothing_differs("check", &differences);
}

#[test]
fn a_pattern_matches_what_the_fixture_says_and_the_others_are_refused() {
    let Some(dir) = dir() else { return };
    let patterns = json_of(&dir, "patterns.json");
    let mut differences = Vec::new();
    for case in patterns["valid"].as_array().unwrap() {
        let pattern = case["pattern"].as_str().unwrap();
        let re = match compile_pattern(pattern) {
            Ok(re) => re,
            Err(e) => {
                differences.push(format!("{pattern:?}: refused ({e}), the fixture says it is in the subset"));
                continue;
            }
        };
        for (key, want) in [("match", true), ("no_match", false)] {
            for t in case[key].as_array().unwrap() {
                let t = t.as_str().unwrap();
                if re.is_match(t) != want {
                    differences.push(format!("{pattern:?} on {t:?}: the fixture says it {}matches", if want { "" } else { "does not " }));
                }
            }
        }
    }
    for pattern in patterns["refused"].as_array().unwrap() {
        let pattern = pattern.as_str().unwrap();
        if compile_pattern(pattern).is_ok() {
            differences.push(format!("{pattern:?}: compiled, the fixture says it is outside the subset"));
        }
    }
    nothing_differs("pattern", &differences);
}

fn standing(a: &Agent) -> Value {
    let s = a.status(None).expect("the agent is in a run");
    json!({"status": s.status, "state": s.state, "lacking": s.lacking})
}

#[test]
fn a_saved_state_is_loaded_and_the_next_message_moves_it_as_the_fixture_says() {
    let Some(dir) = dir() else { return };
    let want = json_of(&dir, "state/expected.json");
    let mut store = MemoryStore::new();
    store.save(&json_of(&dir, "state/cost-after-init-replies.json")).unwrap();
    let mut agent = Agent::builder(want["agent"].as_str().unwrap())
        .store(store)
        .clock(|| 0.0)
        .uuid_fn(uuids("c1000000"))
        .build()
        .unwrap_or_else(|e| panic!("the saved state of the fixtures is not read: {e}"));
    assert_eq!(standing(&agent), want["before"], "before the next message");
    let step = agent.receive(&text(&dir, want["next"].as_str().unwrap()), want["now"].as_f64()).unwrap();
    assert_eq!(standing(&agent), want["after"], "after the next message");
    let sent: Vec<String> = step.send.iter().map(|t| peek(t).unwrap().tag).collect();
    assert_eq!(json!(sent), want["sends"], "what the agent sends");
}
