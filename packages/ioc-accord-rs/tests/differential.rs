//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The library against the script: every case of `fixtures/golden.json` is a function of `accord_initiator.py` run on an input, with
//! the text of its output as Python prints it (`json.dumps(..., indent=2)`). The library must print the same text, byte for byte.
//!
//! `fixtures/README.md` says how the cases are made. `ACCORD_DIFF_CASES=<file>` runs the cases of another file of the same shape as
//! well (the larger randomized set the golden cases are taken from).

use indexmap::IndexMap;
use ioc_accord::algorithms::{assemble_lock, assign_checks, assign_holdings, build_glossary, chao1, good_turing_missing_mass, group_atoms};
use ioc_accord::ingest::{ingest_frame_round, ingest_glossary, ingest_roster, ingest_verify, parse_responses, strip_fences, Grounding};
use ioc_accord::messages::reply_check;
use ioc_accord::py;
use ioc_accord::pyjson::{canonical_json, dumps_pretty};
use ioc_accord::session::{InitArgs, Session};
use ioc_accord::similarity::{normalize_text, ratio, similarity};
use ioc_accord::state::{resolve_state_path, with_defaults};
use ioc_accord::{hint_for, AccordError, Result};
use serde_json::{json, Map, Value};

fn wrap(r: Result<Value>) -> Value {
    match r {
        Ok(v) => json!({"ok": v}),
        Err(e) => error(&e),
    }
}

fn error(e: &AccordError) -> Value {
    json!({"error": e.message(), "refusal": e.is_refusal()})
}

fn s<'a>(input: &'a Value, key: &str) -> &'a str {
    input[key].as_str().unwrap_or_else(|| panic!("{key} is not a string"))
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

fn covers(v: &Value) -> IndexMap<String, Vec<String>> {
    v.as_object().unwrap().iter().map(|(k, v)| (k.clone(), strings(v))).collect()
}

fn opt<'a>(step: &'a Value, key: &str) -> Option<&'a Value> {
    step.as_object().unwrap().get(key)
}

/// A session of commands, as the harness runs them: the state goes through its JSON text between two commands.
fn run_session(steps: &[Value]) -> Value {
    let mut session: Option<Session> = None;
    let mut results = Vec::new();
    for step in steps {
        let cmd = s(step, "cmd");
        let mut current = session.clone().unwrap_or_default();
        let r = match cmd {
            "handle" => {
                if session.is_none() {
                    results.push(json!({"error": "no state", "refusal": false, "state": null}));
                    continue;
                }
                current.handle(
                    s(step, "agent"),
                    s(step, "reply"),
                    opt(step, "required_knowledge").cloned(),
                    opt(step, "objective_clauses").cloned(),
                )
            }
            "init" => current.init(&InitArgs {
                specialists: step["specialists"].as_str().unwrap_or("").into(),
                key_terms: step["key_terms"].as_str().unwrap_or("").into(),
                action_tool: step["action_tool"].as_str().unwrap_or("").into(),
                task: step["task"].as_str().unwrap_or("").into(),
                state_id: Some(step["state_id"].as_str().unwrap_or("00000000").into()),
            }),
            "roster" => current.roster(&step["responses"]),
            "frame" => current.frame(&step["responses"]).map(|mut out| {
                // the script's order of `atoms_by_agent` is Python's hash order: the harness sorts it
                let sorted: Map<String, Value> = {
                    let mut items: Vec<(String, Value)> = out["atoms_by_agent"].as_object().unwrap().clone().into_iter().collect();
                    items.sort_by(|a, b| a.0.cmp(&b.0));
                    items.into_iter().collect()
                };
                out["atoms_by_agent"] = Value::Object(sorted);
                out
            }),
            "glossary" => current.glossary(&step["responses"], step["key_terms"].as_str()),
            "verify" => current.verify(&step["responses"], opt(step, "required_knowledge"), opt(step, "objective_clauses")),
            "lock" => current.lock(),
            "execution_message" => current.execution_message(s(step, "agent"), s(step, "assignment")),
            "status" => current.status(),
            other => panic!("unknown command {other}"),
        };
        let keep = match &r {
            Ok(_) => true,
            Err(e) => cmd == "handle" && e.is_refusal(),
        };
        if keep {
            session = Some(current);
        }
        let mut out = wrap(r);
        out["state"] = session.as_ref().map(|s| s.state().clone()).unwrap_or(Value::Null);
        results.push(out);
    }
    json!({"steps": results, "state_text": session.map(|s| s.dump())})
}

fn run(func: &str, input: &Value) -> Value {
    match func {
        "ratio" => json!({"ok": ratio(s(input, "a"), s(input, "b"))}),
        "similarity" => json!({"ok": similarity(s(input, "a"), s(input, "b"))}),
        "normalize" => json!({"ok": normalize_text(s(input, "text"))}),
        "strip_fences" => json!({"ok": strip_fences(s(input, "text"))}),
        "reply_check" => json!({"ok": reply_check(s(input, "text"))}),
        "dumps" => json!({"ok": dumps_pretty(&input["value"])}),
        "canonical_json" => json!({"ok": canonical_json(&input["value"])}),
        "round" => json!({"ok": py::round(input["x"].as_f64().unwrap(), input["n"].as_u64().unwrap() as usize)}),
        "parse_responses" => wrap(py::iter(&input["raw"]).map(|raw| json!(parse_responses(&raw)))),
        "ingest_roster" => wrap(ingest_roster(input["raw"].as_array().unwrap()).map(Value::Array)),
        "ingest_frame_round" => {
            let r = ingest_frame_round(input["raw"].as_array().unwrap());
            json!({"ok": [r.by_agent, r.truncated, r.saw_atoms]})
        }
        "ingest_glossary" => wrap(
            ingest_glossary(input["raw"].as_array().unwrap(), &strings(&input["key_terms"]))
                .map(|g| json!(g.iter().map(|t| json!([t.term, t.unit, t.agent])).collect::<Vec<_>>())),
        ),
        "ingest_verify" => wrap(ingest_verify(input["raw"].as_array().unwrap()).map(|v| json!([v.covers, v.checks, v.all_holds]))),
        "group_atoms" => wrap(group_atoms(input["observations"].as_array().unwrap()).map(Value::Array)),
        "coverage" => {
            let support: Vec<i64> = input["support"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap()).collect();
            json!({"ok": [good_turing_missing_mass(&support), chao1(&support)]})
        }
        "build_glossary" => {
            let terms: Vec<Grounding> = input["terms"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| Grounding {
                    term: t[0].as_str().unwrap().into(),
                    unit: t[1].as_str().unwrap().into(),
                    agent: t[2].as_str().unwrap().into(),
                })
                .collect();
            let g = build_glossary(&terms);
            json!({"ok": [g.entries, g.ambiguous]})
        }
        "assign_holdings" => {
            wrap(assign_holdings(input["universe"].as_array().unwrap(), &covers(&input["covers"])).map(|(o, u)| json!([o, u])))
        }
        "assign_checks" => wrap(assign_checks(input["checks"].as_array().unwrap(), &input["clauses"]).map(Value::Array)),
        "assemble_lock" => wrap(with_defaults(input["state"].clone()).and_then(|st| assemble_lock(&st))),
        "resolve_state_path" => {
            json!({"ok": resolve_state_path(s(input, "path"), input["run_key"].as_str(), input.get("state_dir").and_then(Value::as_str))})
        }
        "hint_for" => json!({"ok": hint_for(s(input, "msg"))}),
        "session" => json!({"ok": run_session(input["steps"].as_array().unwrap())}),
        other => panic!("unknown function {other}"),
    }
}

/// Run every case of a file; the failures, all of them, in one panic.
fn check_file(path: &std::path::Path) -> usize {
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    let mut failures = Vec::new();
    for (i, case) in cases.iter().enumerate() {
        let func = s(case, "fn");
        let got = dumps_pretty(&run(func, &case["input"]));
        let want = s(case, "expected");
        if got != want {
            let at = got.chars().zip(want.chars()).take_while(|(a, b)| a == b).count();
            let ctx = |t: &str| t.chars().skip(at.saturating_sub(80)).take(200).collect::<String>();
            failures.push(format!("case {i} ({func}) differs at char {at}:\n  rust:   {:?}\n  python: {:?}", ctx(&got), ctx(want)));
        }
    }
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
    cases.len()
}

#[test]
fn the_golden_cases_print_what_the_script_prints() {
    let n = check_file(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/golden.json"));
    assert!(n >= 40, "only {n} golden cases");
}

#[test]
fn the_cases_of_accord_diff_cases_print_what_the_script_prints() {
    if let Some(path) = std::env::var_os("ACCORD_DIFF_CASES") {
        let n = check_file(std::path::Path::new(&path));
        eprintln!("{n} cases of {} agree", path.to_string_lossy());
    }
}
