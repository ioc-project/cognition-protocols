//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Whole runs of 2 to 5 agents on a channel that delivers every message to every agent in one order, with no loss.
//!
//! The model of each agent is a function of the seed, the agent and the candidate, so a run is the same whatever the order in which
//! the agents answer their tasks. Loss, reordering and silence come with the liveness tests.

mod common;

use std::collections::BTreeMap;

use common::{number, uuids, Rng, RUN};
use ioc_concord::agent::Agent;
use ioc_concord::protocol::Outcome;
use ioc_concord::report::{Report, ReportStatus};
use ioc_concord::state::RunStatus;
use ioc_concord::tasks::Task;
use ioc_concord::wire::{build_init, parse_message, peek, InitParams, Kind, ScoreIn};
use serde_json::json;

const NAMES: [&str; 5] = ["cost", "security", "legal", "ops", "ux"];

struct Played {
    agents: BTreeMap<String, Agent>,
    log: Vec<String>,
    budget: usize,
    tau: &'static str,
    n: usize,
}

/// Deliver every message to every agent, and what they answer after it, in the order it was sent.
fn broadcast(agents: &mut BTreeMap<String, Agent>, log: &mut Vec<String>, first: Vec<String>) {
    let mut queue: std::collections::VecDeque<String> = first.into();
    while let Some(text) = queue.pop_front() {
        log.push(text.clone());
        for agent in agents.values_mut() {
            queue.extend(agent.receive(&text, None).expect("receive").send);
        }
    }
}

/// Run the negotiation of `seed` to its end.
fn play(seed: u64, order_seed: u64) -> Played {
    let (n, budget) = (2 + (seed % 4) as usize, 1 + (seed % 4) as usize);
    let tau = ["0.5", "0.6", "0.8", "0.95"][(seed % 4) as usize];
    let names = &NAMES[..n];
    let mut agents: BTreeMap<String, Agent> = names
        .iter()
        .enumerate()
        .map(|(i, a)| (a.to_string(), Agent::builder(a).uuid_fn(uuids(&format!("{:08x}", i + 1))).build().expect("agent")))
        .collect();
    let (mut log, mut order) = (Vec::new(), Rng::new(order_seed));
    let spec = json!({"type": "string", "description": "a plan"});
    let params = InitParams::default().satisfaction_threshold(tau).budget(budget as i64);
    let init = build_init("alice", names, "pick one", "nothing", "all agree", &spec, Some(&params), Some(RUN), Some(&uuids("a0a0a0a0")()))
        .expect("init");
    broadcast(&mut agents, &mut log, vec![init]);
    for _ in 0..1000 {
        let todo: Vec<(String, Task)> = agents.iter().flat_map(|(a, ag)| ag.due().into_iter().map(move |t| (a.clone(), t))).collect();
        if todo.is_empty() {
            return Played { agents, log, budget, tau, n };
        }
        let (a, t) = order.choose(&todo).clone();
        let ag = agents.get_mut(&a).unwrap();
        let step = match t {
            Task::InitReply(_) => {
                let (rej, idl) = (number(seed, &[&a, "rej"], 0.0, 40.0), number(seed, &[&a, "idl"], 60.0, 100.0));
                ag.submit_init_reply(
                    RUN,
                    rej,
                    idl,
                    &format!("plan of {a}"),
                    "because",
                    number(seed, &[&a, "seed"], 0.0, 100.0),
                    "good",
                    None,
                )
            }
            Task::Score(t) => {
                let scores: Vec<ScoreIn> = t
                    .candidates
                    .iter()
                    .map(|c| {
                        let lo = if c.id.starts_with('r') { 45.0 } else { 0.0 };
                        ScoreIn::new(&c.id, number(seed, &[&a, &c.id], lo, 100.0), "my view")
                    })
                    .collect();
                ag.submit_score(RUN, &scores, None)
            }
            Task::Repair(t) => ag.submit_repair(
                RUN,
                &format!("fix {} by {a}", t.round),
                "because",
                number(seed, &[&a, "repair", &t.round.to_string()], 60.0, 100.0),
                "better",
                None,
            ),
            other => panic!("a task of a kind the test does not know: {other:?}"),
        }
        .expect("the answer is valid");
        broadcast(&mut agents, &mut log, step.send);
    }
    panic!("the run does not end");
}

fn report_of(played: &Played, a: &str) -> Report {
    played.agents[a].report(None).expect("a report")
}

#[test]
fn every_agent_ends_with_the_same_confirmed_outcome() {
    for seed in 0..40 {
        let played = play(seed, seed);
        let first = report_of(&played, "cost");
        for (a, ag) in &played.agents {
            let rep = report_of(&played, a);
            assert_eq!(rep.status, ReportStatus::Confirmed, "{a} in {seed}");
            assert_eq!(
                (&rep.outcome, &rep.winner, rep.rounds, &rep.scores),
                (&first.outcome, &first.winner, first.rounds, &first.scores),
                "{a}"
            );
            assert_eq!(rep.results_compared.matches, Some(true));
            assert_eq!((rep.mismatches.sent, rep.mismatches.received.len()), (0, 0));
            assert_eq!(ag.status(None).unwrap().status, RunStatus::Ended);
            assert!(ag.due().is_empty());
        }
        assert!(matches!(first.outcome, Some(Outcome::Success | Outcome::BestEffort)));
        assert!(first.rounds.unwrap() <= played.budget);
        let tau = (played.tau.parse::<f64>().unwrap() * 1_000_000.0).round() as i64;
        let success = first.outcome == Some(Outcome::Success);
        assert_eq!(*first.scores.values().min().unwrap() >= tau, success);
        assert_eq!(success, first.shortfall.is_empty());
        if !success {
            assert_eq!(first.rounds, Some(played.budget));
        }
        let kinds: Vec<Kind> = played.log.iter().map(|t| parse_message(t, None).expect("valid").kind()).collect();
        let count = |k: Kind| kinds.iter().filter(|x| **x == k).count();
        assert_eq!((count(Kind::Result), count(Kind::Init), count(Kind::InitReply)), (1, 1, played.n));
        assert_eq!(count(Kind::Repair), first.rounds.unwrap());
        let result = played.log.iter().find(|t| peek(t).unwrap().tag == "concord-result").unwrap();
        let msg = parse_message(result, None).unwrap();
        assert_eq!(msg.from, "cost");
        assert!(matches!(&msg.body, ioc_concord::wire::Body::Result(r) if r.winning_candidate_id == first.winner.as_ref().unwrap().id));
    }
}

#[test]
fn the_order_in_which_the_agents_answer_changes_nothing() {
    for seed in (0..40).step_by(3) {
        let outcomes: Vec<_> = (1..=3)
            .map(|order_seed| {
                let rep = report_of(&play(seed, order_seed), "cost");
                (rep.outcome, rep.winner.unwrap().id, rep.rounds, rep.scores.into_iter().collect::<Vec<_>>())
            })
            .collect();
        assert!(outcomes[0] == outcomes[1] && outcomes[1] == outcomes[2], "seed {seed}");
    }
}

#[test]
fn the_seeds_cover_success_best_effort_and_several_rounds() {
    let mut seen = std::collections::BTreeSet::new();
    for seed in 0..40 {
        let rep = report_of(&play(seed, seed), "cost");
        seen.insert((rep.outcome.unwrap().as_str(), rep.rounds.unwrap().min(2)));
    }
    assert!(seen.iter().any(|(o, _)| *o == "SUCCESS") && seen.iter().any(|(o, _)| *o == "BEST_EFFORT"));
    assert!(seen.iter().any(|(_, r)| *r >= 1));
}
