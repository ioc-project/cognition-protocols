//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Garbage in: messages of real runs, damaged in every way we can think of, handed to agents in realistic states. Nothing may panic,
//! a message that is ignored must change nothing, and the state must stay JSON.

mod chaos;
mod common;

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::LazyLock;

use chaos::{setup, Chaos, Opts};
use common::Rng;
use ioc_concord::agent::Step;
use ioc_concord::wire::{build_resend, peek};
use regex::Regex;
use serde_json::Value;

const ATTR_VALUES: [&str; 14] = [
    "",
    "x",
    "0.3.0",
    "0.4.0",
    "a b",
    "<",
    "&",
    "seed-x",
    "r0-cost",
    "r99999999999999999999-cost",
    "cost",
    "alice",
    "00000000-0000-4000-8000-000000000000",
    "UPPER-CASE",
];

static ATTR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"(\w+)="([^"]*)""#).unwrap());
static OPEN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<(\w[\w-]*)").unwrap());

/// Every message of a few runs on a bad network, once each.
fn corpus() -> Vec<String> {
    let mut texts: Vec<String> = Vec::new();
    for seed in 0..6 {
        let (n, budget, tau) = setup(seed);
        let mut c = Chaos::new(seed, n, budget, tau, Opts { dup: 0.2, delay: 4.0, loss: 0.1, ..Opts::default() });
        c.run();
        for (_, _, t) in &c.log {
            if !texts.contains(t) {
                texts.push(t.clone());
            }
        }
    }
    texts
}

fn chars_to(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

fn mutate(text: &str, rng: &mut Rng) -> String {
    let len = text.chars().count();
    match rng.below(11) {
        0 => chars_to(text, rng.below(len)),
        1 => {
            let i = rng.below(len);
            let c = char::from_u32(32 + rng.below(0x2fff - 32) as u32).unwrap();
            text.chars().enumerate().map(|(k, x)| if k == i { c } else { x }).collect()
        }
        2 => {
            let head: String = chars_to(text, 400);
            let attrs: Vec<(String, String)> = ATTR_RE.captures_iter(&head).map(|c| (c[1].to_string(), c[2].to_string())).collect();
            if attrs.is_empty() {
                return text.to_string();
            }
            let (name, value) = rng.choose(&attrs).clone();
            let new = if rng.below(15) == 5 { "9".repeat(70) } else { rng.choose(&ATTR_VALUES[..]).to_string() };
            text.replacen(&format!("{name}=\"{value}\""), &format!("{name}=\"{new}\""), 1)
        }
        3 => {
            if text.contains("<raw>") {
                text.replacen("<raw>", "<raw>-", 1)
            } else {
                text.repeat(2)
            }
        }
        4 => match peek(text) {
            Some(info) => {
                let (from, run_id) = (info.attr("from").unwrap_or_default(), info.attr("run_id").unwrap_or_default());
                build_resend(from, run_id, text, None, None).unwrap_or_else(|_| text.to_string())
            }
            None => text.to_string(),
        },
        5 => {
            let rest = match text.find(' ') {
                Some(i) => &text[i..],
                None => &text[text.char_indices().last().map_or(0, |(i, _)| i)..],
            };
            format!("<concord-resend {rest}")
        }
        6 => text.replacen("</", "<", 1),
        7 => format!("{text}{text}"),
        8 => text.replacen("seed-", "r999999999999999999999-", 1),
        9 => text.replacen('>', &format!(">{}", "x".repeat(5000)), 1),
        _ => OPEN_RE.replace(text, |m: &regex::Captures| format!("<{} extra=\"1\"", &m[1])).into_owned(),
    }
}

/// The state as JSON, without the list of accepted uuids.
fn shape(state: &impl serde::Serialize) -> String {
    let mut st: Value = serde_json::to_value(state).expect("the state is JSON");
    for run in st["runs"].as_object_mut().unwrap().values_mut() {
        run.as_object_mut().unwrap().remove("uuids");
    }
    serde_json::to_string(&st).unwrap()
}

fn uuids_of(a: &ioc_concord::agent::Agent) -> String {
    serde_json::to_string(&a.state.runs.values().map(|r| &r.uuids).collect::<Vec<_>>()).unwrap()
}

fn receive(a: &mut ioc_concord::agent::Agent, text: &str, now: f64) -> Step {
    match catch_unwind(AssertUnwindSafe(|| a.receive(text, Some(now)))) {
        Ok(step) => step.unwrap_or_else(|e| panic!("receive failed with {e:?} on {text:?}")),
        Err(_) => panic!("receive panicked on {text:?}"),
    }
}

#[test]
fn no_message_panics_and_a_message_that_is_ignored_changes_nothing() {
    let texts = corpus();
    for trial in 0..40u64 {
        let mut rng = Rng::new(trial);
        let seed = rng.below(6) as u64;
        let (n, budget, tau) = setup(seed);
        let mut c = Chaos::new(seed, n, budget, tau, Opts { delay: 0.0, ..Opts::default() });
        let who = c.names[rng.below(n)].clone();
        let a = c.agents.get_mut(&who).unwrap();
        let upto = rng.below(texts.len());
        for t in &texts[..upto] {
            let _ = receive(a, t, 0.0); // a realistic state: some of what a real run says
        }
        for _ in 0..40 {
            let t = mutate(&texts[rng.below(texts.len())], &mut rng);
            let (before, uuids_before) = (shape(&a.state), uuids_of(a));
            let step = receive(a, &t, 1.0);
            let after = shape(&a.state); // also proves the state is JSON
            let quiet = step.send.is_empty()
                && !step.events.is_empty()
                && step.events.iter().all(|e| e.event == "ignored" || e.event == "cannot answer");
            if quiet {
                let events: Vec<_> = step.events.iter().map(|e| (&e.event, &e.details)).collect();
                assert_eq!(after, before, "{} {events:?} in trial {trial}", chars_to(&t, 200));
                if !step.events.iter().any(|e| e.str("reason") == Some("already recorded")) {
                    // only then is a uuid kept
                    assert_eq!(uuids_of(a), uuids_before, "{} {events:?} in trial {trial}", chars_to(&t, 200));
                }
            }
            let _ = a.tick(Some(2.0)).expect("tick");
            serde_json::to_string(&a.state).expect("the state is JSON");
        }
    }
}
