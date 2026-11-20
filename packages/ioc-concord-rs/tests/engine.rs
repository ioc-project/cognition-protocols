//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The decision engine: record what the agents say, then SELECT, ACCEPT, bottleneck and the outcome, all in millionths.
// A float or a bool where the engine wants an integer cannot be written: `Params` and the recording functions take `i64`, and
// `parse_candidate_id` takes a `&str`.

use ioc_concord::engine::*;
use ioc_concord::protocol::{Outcome, MILLION as M};
use ioc_concord::ConcordError;

fn params() -> Params {
    Params { satisfaction_threshold: 600_000, eps: 50_000, budget: 3, timeout: 300, retries: 3 }
}

/// `x` (a raw score or a normalized score written as in the spec) in millionths.
fn m(x: f64) -> i64 {
    (x * M as f64).round() as i64
}

fn names(agents: &[&str]) -> Vec<String> {
    agents.iter().map(|a| a.to_string()).collect()
}

/// A state of `agents` that all gave their anchors (0 to 100 unless `anchors` says otherwise).
fn new_with(agents: &[&str], anchors: &[(&str, (f64, f64))], params: Params) -> EngineState {
    let mut st = EngineState::new(&names(agents), &params);
    for a in agents {
        let (lo, hi) = anchors.iter().find(|(who, _)| who == a).map_or((0.0, 100.0), |(_, v)| *v);
        add_anchor(&mut st, a, m(lo), m(hi)).unwrap();
    }
    st
}

fn new(agents: &[&str]) -> EngineState {
    new_with(agents, &[], params())
}

fn seed_text(st: &mut EngineState, agent: &str, raw: f64, text: Option<&str>) {
    let text = text.map_or_else(|| format!("s{agent}"), str::to_string);
    add_proposal(st, agent, &format!("seed-{agent}"), &text, "because", m(raw), "mine").unwrap();
}

fn seed(st: &mut EngineState, agent: &str, raw: f64) {
    seed_text(st, agent, raw, None);
}

fn ratings(entries: &[(&str, f64)]) -> Vec<Rating> {
    entries.iter().map(|(id, r)| Rating { id: id.to_string(), raw: m(*r), reasoning: "reasons".to_string() }).collect()
}

fn rate(st: &mut EngineState, agent: &str, entries: &[(&str, f64)]) {
    add_ratings(st, agent, &ratings(entries)).unwrap();
}

fn repair(st: &mut EngineState, agent: &str, n: u32, raw: f64) {
    add_proposal(st, agent, &format!("r{n}-{agent}"), &format!("r{n}"), "because", m(raw), "mine").unwrap();
}

fn invalid_message(why: Result<impl std::fmt::Debug, ConcordError>, containing: &str) {
    match why {
        Err(ConcordError::InvalidMessage(text)) => assert!(text.contains(containing), "{text:?} does not contain {containing:?}"),
        other => panic!("expected an invalid message containing {containing:?}, got {other:?}"),
    }
}

fn map<V: Clone>(entries: &[(&str, V)]) -> indexmap::IndexMap<String, V> {
    entries.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
}

// --- parameters and ids ----------------------------------------------------

#[test]
fn check_params_accepts_defaults_and_the_smallest_and_largest_values() {
    let ab = names(&["a", "b"]);
    check_params(&ab, &Params::default()).unwrap();
    check_params(&ab, &Params { timeout: 1, retries: 0, ..Params::default() }).unwrap();
    let top = 999_999_999;
    check_params(&ab, &Params { budget: top, timeout: top, retries: top, ..Params::default() }).unwrap();
    check_params(&ab, &Params { satisfaction_threshold: 1_000_000, eps: 1, ..Params::default() }).unwrap();
}

#[test]
fn check_params_refuses_what_the_spec_forbids() {
    let d = Params::default;
    let long = "x".repeat(65);
    let cases: Vec<(Vec<&str>, Params, &str)> = vec![
        (vec!["a"], d(), "at least 2 agents"),
        (vec!["a", "a"], d(), "duplicate"),
        (vec!["a", " "], d(), "1 to 64 letters"),
        (vec!["a", "b c"], d(), "1 to 64 letters"),
        (vec!["a", &long], d(), "1 to 64 letters"),
        (vec!["a", "b"], Params { satisfaction_threshold: 0, ..d() }, "above 0 and at most 1"),
        (vec!["a", "b"], Params { satisfaction_threshold: 1_000_001, ..d() }, "above 0 and at most 1"),
        (vec!["a", "b"], Params { eps: 0, ..d() }, "eps"),
        (vec!["a", "b"], Params { budget: 0, ..d() }, "budget"),
        (vec!["a", "b"], Params { timeout: -1, ..d() }, "timeout"),
        (vec!["a", "b"], Params { timeout: 0, ..d() }, "timeout must be an integer, from 1 to"),
        (vec!["a", "b"], Params { retries: -1, ..d() }, "retries"),
        (vec!["a", "b"], Params { budget: 1_000_000_000, ..d() }, "budget must be an integer, from 1 to 999999999"),
        (vec!["a", "b"], Params { timeout: 1_000_000_000, ..d() }, "timeout must be an integer, from 1 to 999999999"),
        (vec!["a", "b"], Params { retries: 1_000_000_000, ..d() }, "retries must be an integer, from 0 to 999999999"),
    ];
    for (agents, p, message) in cases {
        match check_params(&names(&agents), &p) {
            Err(ConcordError::InvalidRun(why)) => assert!(why.contains(message), "{agents:?}: {why:?} does not contain {message:?}"),
            other => panic!("{agents:?} {p:?}: {other:?}"),
        }
    }
}

#[test]
fn candidate_ids_round_trip() {
    assert_eq!((seed_id("cost").as_str(), repair_id(2, "security").as_str()), ("seed-cost", "r2-security"));
    assert_eq!(parse_candidate_id("seed-cost"), Some((0, "cost")));
    assert_eq!(parse_candidate_id("r12-my-agent"), Some((12, "my-agent")));
    for bad in ["r0-a", "seed-", "x-a", "r1", ""] {
        assert_eq!(parse_candidate_id(bad), None, "{bad:?}");
    }
}

#[test]
fn a_candidate_id_has_nothing_around_the_agent_id_and_a_round_of_at_most_nine_digits() {
    let long = format!("seed-{}", "a".repeat(65));
    for bad in ["seed-ops\n", "r1-ops\n", "seed-ops ", "seed-o ps", long.as_str(), "r1-ops\u{e9}", "r1000000000-ops", "r4294967296-ops"] {
        assert_eq!(parse_candidate_id(bad), None, "{bad:?}");
    }
    let huge = format!("r{}-ops", "9".repeat(5000));
    assert_eq!(parse_candidate_id(&huge), None);
    let longest = format!("seed-{}", "a".repeat(64));
    assert_eq!(parse_candidate_id(&longest), Some((0, &longest[5..])));
    assert_eq!(parse_candidate_id("r999999999-ops"), Some((999_999_999, "ops")));
}

// --- recording -------------------------------------------------------------

#[test]
fn the_first_anchors_stand_and_a_bad_scale_is_refused() {
    let mut st = EngineState::new(&names(&["a", "b"]), &params());
    assert!(add_anchor(&mut st, "a", m(20.0), m(80.0)).unwrap());
    assert!(!add_anchor(&mut st, "a", m(0.0), m(100.0)).unwrap());
    assert_eq!(st.anchors["a"], Anchor { raw_reject: 20 * M, raw_ideal: 80 * M });
    for (lo, hi) in [(m(50.0), m(50.0)), (m(80.0), m(20.0)), (-1, m(50.0)), (m(10.0), m(101.0))] {
        assert!(matches!(add_anchor(&mut st, "b", lo, hi), Err(ConcordError::InvalidMessage(_))), "{lo} {hi}");
    }
    assert!(!st.anchors.contains_key("b"));
}

#[test]
fn a_proposal_carries_its_own_score_and_the_first_one_stands() {
    let mut st = new(&["a", "b"]);
    assert!(add_proposal(&mut st, "a", "seed-a", "X", "why", m(80.0), "good").unwrap());
    assert_eq!(st.raw["a"], map(&[("seed-a", 80 * M)]));
    assert_eq!(st.reasoning["a"], map(&[("seed-a", "good".to_string())]));
    assert!(!add_proposal(&mut st, "a", "seed-a", "Y", "other", m(10.0), "bad").unwrap());
    assert_eq!(st.pool.iter().map(|c| c.candidate.as_str()).collect::<Vec<_>>(), ["X"]);
    assert_eq!(st.raw["a"]["seed-a"], 80 * M);
}

#[test]
fn a_proposal_with_the_wrong_id_or_score_is_refused() {
    let mut st = new(&["a", "b"]);
    for cid in ["seed-b", "r1-b", "r0-a", "a", "seed-z"] {
        invalid_message(add_proposal(&mut st, "a", cid, "X", "r", m(50.0), "w"), "is not seed-a or");
    }
    for raw in [m(101.0), -1] {
        invalid_message(add_proposal(&mut st, "a", "seed-a", "X", "r", raw, "w"), "0 to 100");
    }
    assert!(st.pool.is_empty());
}

#[test]
fn ratings_are_recorded_once_and_unknown_candidates_are_reported_not_refused() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 80.0);
    let got = add_ratings(&mut st, "b", &ratings(&[("seed-a", 60.0), ("seed-x", 10.0)])).unwrap();
    assert_eq!(got, Ratings { recorded: vec!["seed-a".into()], unknown: vec!["seed-x".into()], ignored: vec![] });
    assert_eq!(st.raw["b"], map(&[("seed-a", 60 * M)]));
}

#[test]
fn a_rating_the_agent_already_gave_is_ignored_and_the_first_one_stands() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 80.0);
    let first = Ratings { recorded: vec!["seed-a".into()], ..Ratings::default() };
    assert_eq!(add_ratings(&mut st, "b", &[Rating { id: "seed-a".into(), raw: m(60.0), reasoning: "ok".into() }]).unwrap(), first);
    let again = add_ratings(&mut st, "b", &[Rating { id: "seed-a".into(), raw: m(5.0), reasoning: "again".into() }]).unwrap();
    assert_eq!(again, Ratings { ignored: vec!["seed-a".into()], ..Ratings::default() });
    assert_eq!(st.raw["b"], map(&[("seed-a", 60 * M)]));
    assert_eq!(st.reasoning["b"], map(&[("seed-a", "ok".to_string())]));
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 80.0);
    let twice = add_ratings(
        &mut st,
        "b",
        &[
            Rating { id: "seed-a".into(), raw: m(50.0), reasoning: "x".into() },
            Rating { id: "seed-a".into(), raw: m(60.0), reasoning: "y".into() },
        ],
    )
    .unwrap();
    assert_eq!(twice, Ratings { recorded: vec!["seed-a".into()], unknown: vec![], ignored: vec!["seed-a".into()] });
    assert_eq!(st.raw["b"], map(&[("seed-a", 50 * M)]));
}

#[test]
fn an_entry_that_rates_the_senders_own_candidate_is_ignored_and_the_others_are_kept() {
    let mut st = new(&["a", "b", "c"]);
    seed(&mut st, "a", 80.0);
    seed(&mut st, "b", 70.0);
    seed(&mut st, "c", 60.0);
    let got = add_ratings(&mut st, "b", &ratings(&[("seed-a", 50.0), ("seed-b", 50.0), ("seed-c", 40.0)])).unwrap();
    assert_eq!(got, Ratings { recorded: vec!["seed-a".into(), "seed-c".into()], unknown: vec![], ignored: vec!["seed-b".into()] });
    assert_eq!(st.raw["b"], map(&[("seed-b", 70 * M), ("seed-a", 50 * M), ("seed-c", 40 * M)]));
    // an own candidate the receiver does not hold is ignored too, not asked for
    let own = add_ratings(&mut st, "b", &ratings(&[("r1-b", 50.0)])).unwrap();
    assert_eq!(own, Ratings { ignored: vec!["r1-b".into()], ..Ratings::default() });
}

#[test]
fn a_score_out_of_range_makes_the_whole_message_invalid_and_records_nothing() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 80.0);
    seed(&mut st, "b", 80.0);
    let bad = [vec![("seed-a", m(150.0))], vec![("seed-a", m(50.0)), ("seed-b", -1)]];
    for entries in bad {
        let list: Vec<Rating> = entries.iter().map(|(id, raw)| Rating { id: id.to_string(), raw: *raw, reasoning: "x".into() }).collect();
        invalid_message(add_ratings(&mut st, "b", &list), "0 to 100");
    }
    assert_eq!(st.raw["b"], map(&[("seed-b", 80 * M)]));
}

// --- scores ----------------------------------------------------------------

#[test]
fn norm_is_relative_to_the_anchors_and_clamped() {
    let st = new_with(&["a", "b"], &[("a", (20.0, 80.0))], params());
    assert_eq!(norm(&st, "a", m(50.0)), 500_000);
    assert!(norm(&st, "a", m(10.0)) == 0 && norm(&st, "a", m(95.0)) == M);
    assert!(norm(&st, "a", m(20.0)) == 0 && norm(&st, "a", m(80.0)) == M);
}

#[test]
fn norm_is_exact_and_rounds_half_up_to_six_places() {
    let st = new_with(&["a", "b"], &[("a", (0.0, 3.0))], params());
    assert_eq!(norm(&st, "a", m(1.0)), 333_333); // 0.3333333...
    assert_eq!(norm(&st, "a", m(2.0)), 666_667); // 0.6666666...
    let st = new_with(&["a", "b"], &[("a", (0.0, 2.0))], params());
    assert_eq!(norm(&st, "a", 1), 1); // 0.0000005 exactly, half up
    assert_eq!(norm(&st, "a", 3), 2); // 0.0000015 exactly
    assert_eq!(norm(&st, "a", 5), 3);
    let st = new_with(&["a", "b"], &[("a", (0.0, 8.0))], params());
    assert_eq!(norm(&st, "a", 1), 0); // 0.000000125 -> 0
    assert_eq!(norm(&st, "a", 7), 1); // 0.000000875 -> 1
    let st = new_with(&["a", "b"], &[("a", (0.1, 0.9))], params());
    assert_eq!(norm(&st, "a", m(0.5)), 500_000); // no float error in 0.5 - 0.1
}

#[test]
fn pool_order_is_round_then_place_in_the_roster_never_arrival() {
    let mut st = new(&["a", "b", "c"]);
    seed(&mut st, "c", 50.0);
    seed(&mut st, "a", 50.0);
    seed(&mut st, "b", 50.0);
    assert_eq!(st.ordered_pool().iter().map(|c| c.id.as_str()).collect::<Vec<_>>(), ["seed-a", "seed-b", "seed-c"]);
    repair(&mut st, "b", 1, 50.0);
    assert_eq!(st.ordered_pool().last().unwrap().id, "r1-b");
    assert_eq!((st.repairs_in_pool(), st.next_round()), (1, 2));
}

#[test]
fn the_reporter_is_the_first_participant() {
    assert_eq!(EngineState::new(&names(&["z", "a"]), &params()).reporter(), "z");
}

#[test]
fn lex_gt_compares_position_by_position() {
    assert!(lex_gt(&[500_000, 900_000], &[500_000, 800_000]) && !lex_gt(&[400_000, M], &[500_000, 0]));
    assert!(!lex_gt(&[500_000, 500_000], &[500_000, 500_000]));
    assert!(lex_gt(&[500_001], &[500_000]) && !lex_gt(&[500_000], &[500_001]));
}

// --- SELECT and ACCEPT -----------------------------------------------------

#[test]
fn select_takes_the_best_leximin_and_the_first_in_the_pool_on_a_tie() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 70.0);
    rate(&mut st, "a", &[("seed-b", 70.0)]);
    rate(&mut st, "b", &[("seed-a", 70.0)]);
    // seed-a: (0.7, 0.9) -> sorted [0.7, 0.9]; seed-b: [0.7, 0.7]
    assert_eq!(select(&st, &["seed-a", "seed-b"]), "seed-a");
    assert_eq!(select(&st, &["seed-b", "seed-a"]), "seed-a");
    let mut st2 = new(&["a", "b"]);
    seed(&mut st2, "a", 70.0);
    seed(&mut st2, "b", 70.0);
    rate(&mut st2, "a", &[("seed-b", 70.0)]);
    rate(&mut st2, "b", &[("seed-a", 70.0)]);
    assert_eq!(select(&st2, &["seed-a", "seed-b"]), "seed-a");
    assert_eq!(select(&st2, &["seed-b", "seed-a"]), "seed-b");
}

/// Two agents that give both candidates the same raw score: `inc` for seed-a, `cand` for seed-b.
fn duel_with(inc: f64, cand: f64, params: Params) -> EngineState {
    let mut st = new_with(&["a", "b"], &[], params);
    seed(&mut st, "a", inc);
    seed(&mut st, "b", cand);
    rate(&mut st, "a", &[("seed-b", cand)]);
    rate(&mut st, "b", &[("seed-a", inc)]);
    st
}

fn duel(inc: f64, cand: f64) -> EngineState {
    duel_with(inc, cand, params())
}

#[test]
fn accept_needs_eps_on_the_same_side_of_tau_but_not_above_it() {
    assert!(!accept(&duel(50.0, 52.0), "seed-b", "seed-a")); // [.52,.52] vs [.50,.50]: gain .02 < eps
    assert!(!accept(&duel(61.0, 60.0), "seed-a", "seed-b")); // both clear tau: gain .01 < eps
    assert!(accept(&duel(59.0, 62.0), "seed-b", "seed-a")); // [.62,.62] clears tau, [.59,.59] does not
}

#[test]
fn accept_compares_exactly_at_the_edge_of_eps_and_tau() {
    // 0.3 - 0.2 is 0.09999999999999998 in floating point, so a float engine would refuse a gain of exactly eps
    let edge = Params { satisfaction_threshold: M, eps: 100_000, ..params() };
    assert!(accept(&duel_with(20.0, 30.0, edge.clone()), "seed-b", "seed-a"));
    assert!(!accept(&duel_with(20.0, 29.99994, edge), "seed-b", "seed-a"));
    // the floor of the candidate is exactly tau (0.599999 is below it, and 59.99994 on a scale of 100 rounds to that)
    assert!(accept(&duel(59.99994, 60.0), "seed-b", "seed-a"));
    assert!(!accept(&duel(59.99992, 59.99994), "seed-b", "seed-a"));
}

#[test]
fn accept_refuses_a_gain_that_costs_an_earlier_position() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 40.0);
    seed(&mut st, "b", 30.0);
    rate(&mut st, "a", &[("seed-b", 90.0)]); // seed-b: a=.90, b=.30 -> [.30, .90]; seed-a: b rates .50 -> [.40, .50]
    rate(&mut st, "b", &[("seed-a", 50.0)]);
    assert!(!accept(&st, "seed-b", "seed-a")); // position 0 loses .10
    assert!(accept(&st, "seed-a", "seed-b")); // position 0 gains .10
}

// --- evaluate --------------------------------------------------------------

#[test]
fn evaluate_waits_for_init_replies_and_for_ratings() {
    let mut st = EngineState::new(&names(&["a", "b"]), &params());
    assert_eq!(evaluate(&st).state, EvalState::Wait);
    add_anchor(&mut st, "a", 0, m(100.0)).unwrap();
    seed(&mut st, "a", 90.0);
    assert_eq!(evaluate(&st).state, EvalState::Wait);
    add_anchor(&mut st, "b", 0, m(100.0)).unwrap();
    seed(&mut st, "b", 90.0);
    let ev = evaluate(&st);
    assert!(ev.state == EvalState::Wait && ev.x_star.is_none());
    let mut lacking = lacking(&st);
    lacking.sort();
    assert_eq!(lacking, [("a".to_string(), "seed-b".to_string()), ("b".to_string(), "seed-a".to_string())]);
}

#[test]
fn success_when_everyone_clears_tau() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 80.0);
    rate(&mut st, "a", &[("seed-b", 70.0)]);
    rate(&mut st, "b", &[("seed-a", 75.0)]);
    let ev = evaluate(&st);
    assert_eq!(
        (ev.state, ev.outcome, ev.x_star.as_deref(), ev.reporter.as_str()),
        (EvalState::Done, Some(Outcome::Success), Some("seed-a"), "a")
    );
    assert_eq!(ev.floor, Some(750_000));
    assert!(ev.shortfall.is_empty());
    assert_eq!(ev.scores, map(&[("a", 900_000), ("b", 750_000)]));
}

#[test]
fn the_floor_exactly_at_tau_is_success() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 60.0);
    rate(&mut st, "a", &[("seed-b", 70.0)]);
    rate(&mut st, "b", &[("seed-a", 60.0)]);
    assert_eq!(evaluate(&st).outcome, Some(Outcome::Success));
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 59.99994);
    rate(&mut st, "a", &[("seed-b", 70.0)]);
    rate(&mut st, "b", &[("seed-a", 59.99994)]);
    let ev = evaluate(&st);
    assert_eq!(ev.state, EvalState::Repair);
    assert_eq!(ev.shortfall, map(&[("b", 1)]));
}

#[test]
fn the_bottleneck_proposes_a_repair_and_the_round_is_numbered() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 80.0);
    rate(&mut st, "a", &[("seed-b", 20.0)]);
    rate(&mut st, "b", &[("seed-a", 30.0)]);
    let ev = evaluate(&st);
    // seed-a [.30,.90], seed-b [.20,.80] -> incumbent seed-a; the bottleneck is b (.30)
    assert_eq!(
        (ev.state, ev.x_star.as_deref(), ev.bottleneck.as_deref(), ev.round, ev.t),
        (EvalState::Repair, Some("seed-a"), Some("b"), Some(1), 0)
    );
    assert_eq!(ev.shortfall, map(&[("b", 300_000)]));
}

#[test]
fn a_tie_for_bottleneck_goes_to_the_first_in_participants() {
    let mut st = new(&["a", "b", "c"]);
    for ag in ["a", "b", "c"] {
        seed_text(&mut st, ag, 50.0, Some(&format!("s{ag}")));
    }
    for ag in ["a", "b", "c"] {
        for other in ["a", "b", "c"] {
            if other != ag {
                rate(&mut st, ag, &[(&format!("seed-{other}"), 50.0)]);
            }
        }
    }
    assert_eq!(evaluate(&st).bottleneck.as_deref(), Some("a"));
}

#[test]
fn a_repair_that_clears_tau_for_everyone_ends_in_success() {
    let mut st = new(&["a", "b"]);
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 80.0);
    rate(&mut st, "a", &[("seed-b", 20.0)]);
    rate(&mut st, "b", &[("seed-a", 30.0)]);
    repair(&mut st, "b", 1, 70.0);
    assert_eq!(evaluate(&st).state, EvalState::Wait);
    rate(&mut st, "a", &[("r1-b", 65.0)]);
    let ev = evaluate(&st);
    assert_eq!((ev.outcome, ev.x_star.as_deref(), ev.t), (Some(Outcome::Success), Some("r1-b"), 1));
    assert_eq!(ev.log.iter().map(|e| e.replaced).collect::<Vec<_>>(), [true, true]);
}

#[test]
fn best_effort_when_the_budget_is_spent_and_not_before() {
    let mut st = new_with(&["a", "b"], &[], Params { budget: 1, ..params() });
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 80.0);
    rate(&mut st, "a", &[("seed-b", 20.0)]);
    rate(&mut st, "b", &[("seed-a", 30.0)]);
    assert_eq!(evaluate(&st).state, EvalState::Repair);
    repair(&mut st, "b", 1, 30.0);
    rate(&mut st, "a", &[("r1-b", 30.0)]);
    let ev = evaluate(&st);
    assert_eq!((ev.outcome, ev.t, ev.x_star.as_deref()), (Some(Outcome::BestEffort), 1, Some("seed-a")));
    assert_eq!(ev.shortfall, map(&[("b", 300_000)]));
    assert_eq!(ev.log.iter().map(|e| e.replaced).collect::<Vec<_>>(), [true, false]);
}

#[test]
fn every_repair_counts_in_t_even_when_the_incumbent_stays() {
    let mut st = new_with(&["a", "b"], &[], Params { budget: 2, ..params() });
    seed(&mut st, "a", 90.0);
    seed(&mut st, "b", 80.0);
    rate(&mut st, "a", &[("seed-b", 20.0)]);
    rate(&mut st, "b", &[("seed-a", 30.0)]);
    repair(&mut st, "b", 1, 30.0);
    rate(&mut st, "a", &[("r1-b", 30.0)]);
    let ev = evaluate(&st);
    assert_eq!((ev.state, ev.round, ev.t), (EvalState::Repair, Some(2), 1));
}

#[test]
fn the_run_has_no_other_outcome_than_success_or_best_effort() {
    let mut st = new_with(&["a", "b"], &[], Params { budget: 1, ..params() });
    seed(&mut st, "a", 10.0);
    seed(&mut st, "b", 10.0);
    rate(&mut st, "a", &[("seed-b", 10.0)]);
    rate(&mut st, "b", &[("seed-a", 10.0)]);
    repair(&mut st, "a", 1, 10.0);
    rate(&mut st, "b", &[("r1-a", 10.0)]);
    assert_eq!(evaluate(&st).outcome, Some(Outcome::BestEffort));
}

/// A run of a, b and c whose raw scores are `raw[agent]`, a list of `(candidate, raw)`: each agent's seed is its own `seed-<agent>`.
fn table(raw: &[(&str, [(&str, f64); 3])]) -> EngineState {
    let mut st = new(&["a", "b", "c"]);
    for (ag, scores) in raw {
        let own = scores.iter().find(|(cid, _)| *cid == format!("seed-{ag}")).unwrap().1;
        seed(&mut st, ag, own);
    }
    for (ag, scores) in raw {
        let others: Vec<(&str, f64)> = scores.iter().filter(|(cid, _)| *cid != format!("seed-{ag}")).copied().collect();
        add_ratings(
            &mut st,
            ag,
            &others.iter().map(|(id, r)| Rating { id: id.to_string(), raw: m(*r), reasoning: "w".into() }).collect::<Vec<_>>(),
        )
        .unwrap();
    }
    st
}

fn three() -> EngineState {
    table(&[
        ("a", [("seed-a", 90.0), ("seed-b", 50.0), ("seed-c", 30.0)]),
        ("b", [("seed-a", 70.0), ("seed-b", 80.0), ("seed-c", 30.0)]),
        ("c", [("seed-a", 30.0), ("seed-b", 30.0), ("seed-c", 30.0)]),
    ])
}

#[test]
fn three_agents_pick_the_best_leximin_and_the_lowest_scorer_repairs() {
    let ev = evaluate(&three());
    assert_eq!(
        (ev.state, ev.x_star.as_deref(), ev.bottleneck.as_deref(), ev.round, ev.reporter.as_str()),
        (EvalState::Repair, Some("seed-a"), Some("c"), Some(1), "a")
    );
    assert_eq!(ev.scores, map(&[("a", 900_000), ("b", 700_000), ("c", 300_000)]));
}

#[test]
fn evaluate_does_not_change_the_state() {
    let st = three();
    let before = st.clone();
    evaluate(&st);
    evaluate(&st);
    assert_eq!(st, before);
}

#[test]
fn agents_that_hold_the_same_messages_decide_alike_whatever_the_arrival_order() {
    let build = |order: &[&str]| {
        let mut st = new(&["a", "b", "c"]);
        for ag in order {
            seed(&mut st, ag, 70.0);
        }
        for ag in order {
            let others: Vec<(String, f64)> = ["a", "b", "c"].iter().filter(|o| *o != ag).map(|o| (format!("seed-{o}"), 70.0)).collect();
            rate(&mut st, ag, &others.iter().map(|(id, r)| (id.as_str(), *r)).collect::<Vec<_>>());
        }
        evaluate(&st)
    };
    assert_eq!(build(&["a", "b", "c"]), build(&["c", "b", "a"]));
    assert_eq!(build(&["c", "b", "a"]), build(&["b", "c", "a"]));
}
