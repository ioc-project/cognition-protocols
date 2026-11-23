//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Whole runs on a bad network, in virtual time: messages are delayed (so they arrive in another order), duplicated and lost, agents
//! are restarted from their saved state, and some stop for good.
//!
//! What must hold: agents that end with an outcome agree with the run on a perfect network; when messages are only delayed or
//! duplicated every run still ends well; and whatever happens, every run ends, with no timer left and no flood of messages.

mod chaos;
mod common;

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use chaos::{setup, Chaos, Opts, NAMES, RETRIES, RUN, RUN2, TIMEOUT};
use common::Rng;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::{Report, ReportStatus};
use ioc_concord::state::RunStatus;
use ioc_concord::wire::{kind_of_tag, parse_message, peek, Body, DATA_TAGS, RESEND};

/// The authors of the messages of that kind, in the order they were sent.
fn counts(c: &Chaos, tag: &str) -> Vec<String> {
    c.log.iter().filter(|(_, _, t)| peek(t).unwrap().tag == format!("concord-{tag}")).map(|(_, who, _)| who.clone()).collect()
}

fn count_of(c: &Chaos, tag: &str, who: &str) -> usize {
    counts(c, tag).iter().filter(|w| *w == who).count()
}

/// The report of the first agent on a perfect network.
fn reference(seed: u64, watcher: bool) -> Report {
    let (n, budget, tau) = setup(seed);
    let mut base = Chaos::new(seed, n, budget, tau, Opts { delay: 0.0, watcher, ..Opts::default() });
    base.run();
    base.agents[&base.names[0]].report(None).expect("a report")
}

/// What holds on any network.
fn check_common(c: &Chaos, rep0: &Report, seed: u64) {
    for who in &c.names {
        let a = &c.agents[who];
        if !c.alive(who) {
            continue;
        }
        let (Some(s), Some(rep)) = (a.status(None), a.report(None)) else {
            // the init never reached it (and nothing else can start a run)
            let init = c.delivered.get(who).into_iter().flatten().any(|t| {
                let p = peek(t).unwrap();
                p.tag == "concord-init" && p.attr("version") == Some("0.3.0")
            });
            assert!(!init, "{who} in {seed}");
            continue;
        };
        assert!(matches!(s.status, RunStatus::Ended | RunStatus::Aborted), "{who} {:?} in {seed}", s.status);
        assert!(s.timers.is_empty() && a.next_wakeup().is_none() && a.due().is_empty(), "{who} in {seed}");
        if s.status == RunStatus::Ended {
            assert_eq!(
                (&rep.outcome, &rep.winner, rep.rounds, &rep.scores, &rep.shortfall),
                (&rep0.outcome, &rep0.winner, rep0.rounds, &rep0.scores, &rep0.shortfall),
                "{who} in {seed}"
            );
            assert!(rep.mismatches.sent == 0 && rep.mismatches.received.is_empty(), "{who} in {seed}");
        }
    }
    assert!(count_of(c, "cancel", &c.names[0]) <= 1);
    for who in c.agents.keys() {
        assert!(count_of(c, "cancel", who) <= 1 && count_of(c, "result", who) <= 1, "{who}");
    }
    assert!(count_of(c, "result", &c.names[0]) <= 1 && counts(c, "result").iter().all(|w| *w == c.names[0]));
    let (n, budget) = (c.names.len() as i64, c.budget);
    assert!(c.log.len() as i64 <= 40 * n * n * (budget + 4) * (c.retries + 2), "no flood: {} messages in {seed}", c.log.len());
    check_log(c);
}

/// The items a data message is the message for: (author, kind, candidate or None) for each.
fn items_of(text: &str) -> Vec<(String, String, Option<String>)> {
    let info = peek(text).unwrap();
    let kind = kind_of_tag(&info.tag).unwrap().to_string();
    let who = info.attr("from").unwrap().to_string();
    match parse_message(text, None).unwrap().body {
        Body::Score(s) => s.scores.iter().map(|e| (who.clone(), kind.clone(), Some(e.id.clone()))).collect(),
        Body::Repair(p) => vec![(who, kind, Some(p.id))],
        _ => vec![(who, kind, None)],
    }
}

/// What holds for every message that was sent: each transmission has its own uuid, an author writes one message for an item, and a
/// resend carries a message that its author sent before, byte for byte.
fn check_log(c: &Chaos) {
    let mut sent_data: HashMap<&str, HashSet<&str>> = HashMap::new();
    let mut by_item: HashMap<(String, String, Option<String>), &str> = HashMap::new();
    let mut seen = HashSet::new();
    for (_, who, text) in &c.log {
        let info = peek(text).unwrap();
        let uid = info.attr("uuid").unwrap().to_string();
        assert!(seen.insert(uid), "a uuid was used for two transmissions");
        if DATA_TAGS.contains(&info.tag.as_str()) {
            sent_data.entry(who).or_default().insert(text);
            for item in items_of(text) {
                assert_eq!(*by_item.entry(item.clone()).or_insert(text), text.as_str(), "two messages for {item:?}");
            }
        }
    }
    for (_, who, text) in &c.log {
        if peek(text).unwrap().tag == RESEND {
            let Body::Resend(r) = parse_message(text, None).unwrap().body else { panic!("a resend") };
            assert!(
                sent_data.get(who.as_str()).is_some_and(|s| s.contains(r.inner.as_str())),
                "a resend carries a message that was never sent"
            );
        }
    }
}

#[test]
fn messages_that_arrive_late_in_another_order_or_twice_change_nothing() {
    for seed in 0..30 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        assert_eq!(rep0.status, ReportStatus::Confirmed);
        let mut c = Chaos::new(seed, n, budget, tau, Opts { dup: 0.3, delay: 6.0, ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        for who in &c.names {
            let rep = c.agents[who].report(None).unwrap();
            assert_eq!(
                (rep.status, &rep.outcome, &rep.winner, rep.rounds),
                (ReportStatus::Confirmed, &rep0.outcome, &rep0.winner, rep0.rounds),
                "{who} in {seed}"
            );
        }
        assert!(counts(&c, "cancel").is_empty(), "seed {seed}");
    }
}

#[test]
fn agents_that_restart_from_their_saved_state_at_any_moment_lose_nothing() {
    for seed in 0..30 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        let mut c = Chaos::new(seed, n, budget, tau, Opts { dup: 0.2, delay: 6.0, restart: 0.3, ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        let statuses: BTreeSet<&str> = c.names.iter().map(|w| c.agents[w].report(None).unwrap().status.as_str()).collect();
        assert_eq!(statuses, BTreeSet::from(["confirmed"]), "seed {seed}");
    }
}

#[test]
fn when_messages_are_lost_every_run_still_ends_and_those_that_end_agree() {
    for seed in 0..30 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        let loss = [0.1, 0.25, 0.4][(seed % 3) as usize];
        let mut c = Chaos::new(seed, n, budget, tau, Opts { loss, dup: 0.1, delay: 6.0, restart: 0.1, ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        for who in &c.names {
            let rep = c.agents[who].report(None).unwrap();
            if rep.status == ReportStatus::Aborted {
                let reason = rep.reason.as_ref().expect("a reason");
                assert!(
                    rep.outcome == Some(Outcome::Aborted)
                        && reason.reason == "missing-agent"
                        && reason.agent.as_ref().is_some_and(|a| c.names.contains(a)),
                    "{who} in {seed}"
                );
            } else {
                // an agent whose run ended with an outcome is disputed exactly when a cancel of another agent reached it afterwards
                let cancelled = c.delivered.get(who).into_iter().flatten().any(|t| {
                    let p = peek(t).unwrap();
                    p.tag == "concord-cancel" && p.attr("from") != Some(who.as_str())
                });
                assert_eq!(rep.status == ReportStatus::Disputed, cancelled, "{who} {:?} in {seed}", rep.status);
            }
        }
    }
}

#[test]
fn an_agent_that_stops_for_good_ends_the_run_for_the_others_unless_it_was_over() {
    for seed in 0..20 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        let victim = NAMES[seed as usize % n].to_string();
        let when = 0.5 + 19.5 * Rng::new(seed).unit();
        let mut c =
            Chaos::new(seed, n, budget, tau, Opts { delay: 3.0, crash: BTreeMap::from([(victim.clone(), when)]), ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        let others: Vec<&String> = c.names.iter().filter(|w| **w != victim).collect();
        let states: Vec<RunStatus> = others.iter().map(|w| c.agents[*w].status(None).unwrap().status).collect();
        if c.agents[&victim].status(None).is_some_and(|s| s.status == RunStatus::Ended) {
            continue; // it had finished before it stopped: the others may finish too
        }
        for (w, st) in others.iter().zip(&states) {
            if *st == RunStatus::Aborted {
                let rep = c.agents[*w].report(None).unwrap();
                assert!(rep.reason.unwrap().agent.is_some_and(|a| c.names.contains(&a)), "{w} in {seed}");
            }
        }
        assert!(states.contains(&RunStatus::Aborted) || states.iter().all(|s| *s == RunStatus::Ended), "seed {seed}");
    }
}

#[test]
fn an_initiator_that_only_watches_ends_with_the_run_on_a_bad_network() {
    for seed in 0..12 {
        let (n, budget, tau) = setup(seed);
        let mut base = Chaos::new(seed, n, budget, tau, Opts { delay: 0.0, watcher: true, ..Opts::default() });
        base.run();
        let rep0 = base.agents[&base.names[0]].report(None).unwrap();
        assert_eq!(base.agents["alice"].report(None).unwrap().status, ReportStatus::Unverified);
        let mut c = Chaos::new(seed, n, budget, tau, Opts { dup: 0.3, delay: 6.0, restart: 0.2, watcher: true, ..Opts::default() });
        c.run();
        let rep = c.agents["alice"].report(None).unwrap();
        assert_eq!(
            (rep.status, rep.outcome, rep.winner.map(|w| w.id)),
            (ReportStatus::Unverified, rep0.outcome, rep0.winner.map(|w| w.id)),
            "seed {seed}"
        );
        assert!(c.agents["alice"].status(None).unwrap().timers.is_empty());
    }
}

#[test]
fn whatever_the_parameters_every_run_ends_and_those_that_end_agree() {
    for seed in 0..24 {
        let mut rng = Rng::new(seed);
        let n = *rng.choose(&[2usize, 3, 4, 5]);
        let budget = *rng.choose(&[1i64, 2, 5]);
        let tau = *rng.choose(&["0.5", "0.8"]);
        let retries = *rng.choose(&[0i64, 1, 3]);
        let timeout = *rng.choose(&[3i64, 30]);
        let mut base = Chaos::new(seed, n, budget, tau, Opts { delay: 0.0, timeout, retries, ..Opts::default() });
        base.run();
        let rep0 = base.agents[&base.names[0]].report(None).unwrap();
        let mut c =
            Chaos::new(seed, n, budget, tau, Opts { loss: 0.1, dup: 0.2, delay: 4.0, restart: 0.1, timeout, retries, ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
    }
}

// --- another version ---------------------------------------------------------------------

#[test]
fn an_agent_that_speaks_another_version_makes_the_initiator_end_the_run_and_nobody_hangs() {
    for seed in 0..10 {
        let (n, budget, tau) = (3 + (seed % 2) as usize, 2, "0.6");
        let legal = NAMES[n - 1];
        let mut c = Chaos::new(seed, n, budget, tau, Opts { dup: 0.2, delay: 4.0, rewrite_in: vec![legal.to_string()], ..Opts::default() });
        c.run();
        assert!(c.agents[legal].status(None).is_none(), "it refused the init and never joined, seed {seed}");
        for who in &c.names[..n - 1] {
            let (s, rep) = (c.agents[who].status(None).unwrap(), c.agents[who].report(None).unwrap());
            assert!(s.status == RunStatus::Aborted && rep.reason.unwrap().reason == "version" && s.timers.is_empty(), "{who} in {seed}");
        }
        let refusals: Vec<&String> =
            c.log.iter().filter(|(_, w, t)| w == legal && peek(t).unwrap().tag == "concord-error").map(|(_, _, t)| t).collect();
        assert!(!refusals.is_empty(), "seed {seed}");
        for t in refusals {
            let Body::Error(e) = parse_message(t, None).unwrap().body else { panic!("an error") };
            assert_eq!(e.reason, "version");
        }
    }
}

#[test]
fn an_agent_whose_messages_are_of_another_version_goes_unheard_and_the_timers_end_the_run() {
    for seed in 0..10 {
        let mut c = Chaos::new(seed, 3, 2, "0.6", Opts { dup: 0.2, delay: 4.0, rewrite_out: vec!["legal".to_string()], ..Opts::default() });
        c.run();
        for who in ["cost", "security"] {
            let (s, rep) = (c.agents[who].status(None).unwrap(), c.agents[who].report(None).unwrap());
            let reason = rep.reason.unwrap();
            assert!(
                s.status == RunStatus::Aborted
                    && reason.reason == "missing-agent"
                    && reason.agent.as_deref() == Some("legal")
                    && s.timers.is_empty(),
                "{who} in {seed}"
            );
        }
        let legal = c.agents["legal"].status(None).unwrap();
        assert!(legal.status == RunStatus::Aborted && legal.timers.is_empty(), "seed {seed}");
    }
}

// --- an agent that is alive but slow --------------------------------------------------------

#[test]
fn an_agent_that_answers_before_the_timeout_costs_the_run_nothing() {
    for seed in 0..8 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        let slow = NAMES[..n].iter().map(|a| (a.to_string(), 20.0)).collect();
        let mut c = Chaos::new(seed, n, budget, tau, Opts { delay: 2.0, slow, ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        let statuses: BTreeSet<&str> = c.names.iter().map(|w| c.agents[w].report(None).unwrap().status.as_str()).collect();
        assert_eq!(statuses, BTreeSet::from(["confirmed"]), "seed {seed}");
    }
}

#[test]
fn an_agent_that_is_slower_than_all_the_requests_of_the_others_ends_the_run_for_everybody() {
    for seed in 0..8 {
        let (n, budget, tau) = setup(seed);
        let rep0 = reference(seed, false);
        let slow = NAMES[n - 1];
        let wait = (RETRIES + 2) as f64 * TIMEOUT as f64;
        let mut c =
            Chaos::new(seed, n, budget, tau, Opts { delay: 2.0, slow: BTreeMap::from([(slow.to_string(), wait)]), ..Opts::default() });
        c.run();
        check_common(&c, &rep0, seed);
        for who in &c.names {
            let rep = c.agents[who].report(None).unwrap();
            assert!(rep.status == ReportStatus::Aborted && rep.reason.unwrap().agent.as_deref() == Some(slow), "{who} in {seed}");
        }
    }
}

// --- two runs at once ---------------------------------------------------------------------------

#[test]
fn two_runs_that_start_together_leave_every_agent_in_one_run_at_a_time_and_end() {
    for seed in 0..30u64 {
        let (n, budget, tau) = (2 + (seed % 3) as usize, 1 + (seed % 3) as i64, "0.6");
        let loss = [0.0, 0.1, 0.3][(seed % 3) as usize];
        let mut c = Chaos::new(seed, n, budget, tau, Opts { loss, dup: 0.2, delay: 5.0, restart: 0.1, ..Opts::default() });
        let (first, last) = (c.names[0].clone(), c.names[n - 1].clone());
        c.run_with(Some(vec![(first, RUN.to_string()), (last, RUN2.to_string())]));
        for who in &c.names {
            let a = &c.agents[who];
            assert!(a.state.active.as_ref().is_none_or(|id| a.state.runs[id].status != RunStatus::Active), "{who} in {seed}");
            for (rid, run) in &a.state.runs {
                assert!(run.status != RunStatus::Active && run.timers.is_empty(), "{who} {rid} in {seed}");
            }
        }
        for rid in [RUN, RUN2] {
            let ended: BTreeSet<(&str, String)> = c
                .names
                .iter()
                .filter_map(|w| c.agents[w].report(Some(rid)))
                .filter(|r| matches!(r.status, ReportStatus::Confirmed | ReportStatus::Disputed))
                .map(|r| (r.outcome.unwrap().as_str(), r.winner.unwrap().id))
                .collect();
            assert!(ended.len() <= 1, "{rid} {ended:?} in {seed}"); // the agents that end a run agree on it
        }
        if loss == 0.0 {
            let done: Vec<&str> = [RUN, RUN2]
                .into_iter()
                .filter(|rid| c.names.iter().all(|w| c.agents[w].report(Some(rid)).is_some_and(|r| r.status == ReportStatus::Confirmed)))
                .collect();
            assert!(done == [RUN] || done.is_empty(), "only the run with the lower id can finish: {done:?} in {seed}");
        }
    }
}
