//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The CONCORD decision engine: SELECT, ACCEPT, bottleneck and termination over the ratings the agents give.
//!
//! Every participant runs this engine over the same messages, so they all reach the same decision. It holds no I/O, no clock and no
//! process state: it works on the plain `EngineState` that `EngineState::new` returns, and `evaluate` reads it without changing it.
//! The decision is never kept: `evaluate` goes through the evaluations again from the seeds each time it is called.
//!
//! Every number is an integer count of millionths (see `protocol::MILLION`): a raw score of 82.5 is 82_500_000, and a normalized
//! score of 0.75 is 750_000. The normalized score is computed exactly and rounded half up, and every comparison is made on those
//! integers, so no floating-point error can change a decision.

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use crate::errors::{ConcordError, Result};
use crate::protocol::{
    format_number, is_agent_id, quote, Outcome, DEFAULT_BUDGET, DEFAULT_EPS, DEFAULT_RETRIES, DEFAULT_THRESHOLD, DEFAULT_TIMEOUT,
    MAX_INTEGER, MILLION,
};

// --------------------------------------------------------------------------
// parameters
// --------------------------------------------------------------------------

/// The parameters of a run (see Configuration Parameters in the spec): `satisfaction_threshold` and `eps` in millionths, `B` is the
/// `budget` element of the init.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Params {
    pub satisfaction_threshold: i64,
    pub eps: i64,
    #[serde(rename = "B")]
    pub budget: i64,
    pub timeout: i64,
    pub retries: i64,
}

impl Default for Params {
    fn default() -> Self {
        Params {
            satisfaction_threshold: DEFAULT_THRESHOLD,
            eps: DEFAULT_EPS,
            budget: DEFAULT_BUDGET,
            timeout: DEFAULT_TIMEOUT,
            retries: DEFAULT_RETRIES,
        }
    }
}

/// The parameters the engine itself reads. They are kept apart from `Params` because the saved state of the engine holds just these
/// three, so the saved state of the engine holds just these.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineParams {
    pub satisfaction_threshold: i64,
    pub eps: i64,
    #[serde(rename = "B")]
    pub budget: i64,
}

/// Fails with `InvalidRun` unless these are the participants and parameters of a run that can be played.
///
/// # Errors
///
/// `ConcordError::InvalidRun` when a rule of the configuration is broken.
pub fn check_params(agents: &[String], params: &Params) -> Result<()> {
    if agents.len() < 2 {
        return Err(ConcordError::invalid_run("a negotiating set needs at least 2 agents"));
    }
    let mut seen = std::collections::HashSet::new();
    if !agents.iter().all(|a| seen.insert(a)) {
        return Err(ConcordError::invalid_run("duplicate agent names"));
    }
    if let Some(bad) = agents.iter().find(|a| !is_agent_id(a)) {
        return Err(ConcordError::invalid_run(format!("an agent id is 1 to 64 letters, digits, _ or -, not {}", quote(bad))));
    }
    if !(0 < params.satisfaction_threshold && params.satisfaction_threshold <= MILLION) {
        return Err(ConcordError::invalid_run("satisfaction_threshold must be above 0 and at most 1"));
    }
    if params.eps <= 0 {
        return Err(ConcordError::invalid_run("eps must be above 0"));
    }
    if !(1..=MAX_INTEGER).contains(&params.budget) {
        return Err(ConcordError::invalid_run(format!("budget must be an integer, from 1 to {MAX_INTEGER}")));
    }
    if !(1..=MAX_INTEGER).contains(&params.timeout) {
        return Err(ConcordError::invalid_run(format!("timeout must be an integer, from 1 to {MAX_INTEGER}")));
    }
    if !(0..=MAX_INTEGER).contains(&params.retries) {
        return Err(ConcordError::invalid_run(format!("retries must be an integer, from 0 to {MAX_INTEGER}")));
    }
    Ok(())
}

// --------------------------------------------------------------------------
// candidate ids
// --------------------------------------------------------------------------

pub fn seed_id(agent: &str) -> String {
    format!("seed-{agent}")
}

pub fn repair_id(n: u32, agent: &str) -> String {
    format!("r{n}-{agent}")
}

/// The most digits of the round of a repair id (`r<N>-<agent>`). A limit of this library, not of the spec.
const MAX_ROUND_DIGITS: usize = 9;

/// (round, agent) of a candidate id: round 0 for `seed-<agent>`, N for `r<N>-<agent>`, with N of at most 9 digits. None for any other
/// text, which includes an id with anything after the agent (a trailing line feed too) or an agent that is not an agent id.
pub fn parse_candidate_id(cid: &str) -> Option<(u32, &str)> {
    if let Some(agent) = cid.strip_prefix("seed-") {
        return is_agent_id(agent).then_some((0, agent));
    }
    let rest = cid.strip_prefix('r')?;
    let (digits, agent) = rest.split_once('-')?;
    let is_round =
        !digits.is_empty() && digits.len() <= MAX_ROUND_DIGITS && !digits.starts_with('0') && digits.bytes().all(|b| b.is_ascii_digit());
    if !is_round || !is_agent_id(agent) {
        return None;
    }
    Some((digits.parse().ok()?, agent))
}

// --------------------------------------------------------------------------
// the state and what is recorded in it
// --------------------------------------------------------------------------

/// Where an agent's own scale starts and ends, in millionths.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Anchor {
    pub raw_reject: i64,
    pub raw_ideal: i64,
}

/// A candidate of the pool: a seed or a repair.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Candidate {
    pub id: String,
    pub proposer: String,
    pub round: u32,
    pub candidate: String,
    pub rationale: String,
}

/// The state of the decision engine:
///
/// * `params`: `satisfaction_threshold`, `eps`, `B` (the other parameters are the node's business)
/// * `agents`: the participants, in the order of `<participants>`
/// * `anchors`: the first anchors an agent sent
/// * `pool`: the seeds and every repair, in arrival order
/// * `raw`: `{agent: {candidate id: raw score}}`
/// * `reasoning`: `{agent: {candidate id: text}}`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineState {
    pub params: EngineParams,
    pub agents: Vec<String>,
    pub anchors: IndexMap<String, Anchor>,
    pub pool: Vec<Candidate>,
    pub raw: IndexMap<String, IndexMap<String, i64>>,
    pub reasoning: IndexMap<String, IndexMap<String, String>>,
}

impl EngineState {
    pub fn new(agents: &[String], params: &Params) -> Self {
        EngineState {
            params: EngineParams { satisfaction_threshold: params.satisfaction_threshold, eps: params.eps, budget: params.budget },
            agents: agents.to_vec(),
            anchors: IndexMap::new(),
            pool: Vec::new(),
            raw: agents.iter().map(|a| (a.clone(), IndexMap::new())).collect(),
            reasoning: agents.iter().map(|a| (a.clone(), IndexMap::new())).collect(),
        }
    }

    /// The participant that reports the run: the first one in `<participants>`.
    pub fn reporter(&self) -> &str {
        &self.agents[0]
    }

    /// The pool in its canonical order: the earlier round first, and within a round the proposer that comes first in
    /// `<participants>`. Never arrival order: every participant must pick the same candidate, whatever order it received them.
    pub fn ordered_pool(&self) -> Vec<&Candidate> {
        let mut pool: Vec<&Candidate> = self.pool.iter().collect();
        pool.sort_by_key(|c| (c.round, self.agents.iter().position(|a| *a == c.proposer).unwrap_or(usize::MAX)));
        pool
    }

    /// `t`: the number of repair proposals so far, whether or not they replaced the incumbent.
    pub fn repairs_in_pool(&self) -> usize {
        self.pool.iter().filter(|c| c.round > 0).count()
    }

    /// The number the next repair must carry: `t + 1`.
    pub fn next_round(&self) -> u32 {
        self.repairs_in_pool() as u32 + 1
    }
}

fn raw_value(raw: i64, what: &str) -> Result<i64> {
    if !(0..=100 * MILLION).contains(&raw) {
        return Err(ConcordError::invalid_message(format!("{what} must be a number from 0 to 100 (in millionths), not {raw}")));
    }
    Ok(raw)
}

/// Record where an agent's own scale starts and ends. The first anchors of an agent stand: later ones are ignored, since replacing
/// them would invalidate every earlier comparison. Returns whether they were recorded. Fails with `InvalidMessage` for a value
/// outside 0 to 100 and for an inverted scale.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` for a value outside 0 to 100 and for an inverted scale.
pub fn add_anchor(st: &mut EngineState, agent: &str, raw_reject: i64, raw_ideal: i64) -> Result<bool> {
    raw_value(raw_reject, "raw_reject")?;
    raw_value(raw_ideal, "raw_ideal")?;
    if raw_ideal <= raw_reject {
        return Err(ConcordError::invalid_message(format!(
            "raw_ideal ({}) must exceed raw_reject ({}). These are satisfaction ratings of the two outcomes (higher = better for \
             your role), not measurements of the current state.",
            format_number(raw_ideal),
            format_number(raw_reject)
        )));
    }
    if st.anchors.contains_key(agent) {
        return Ok(false);
    }
    st.anchors.insert(agent.to_string(), Anchor { raw_reject, raw_ideal });
    Ok(true)
}

/// Record a seed or a repair together with its proposer's own score for it. The id must be `seed-<agent>` or `r<N>-<agent>` for this
/// agent; that `N` is `next_round` is for the caller to judge, since a copy of the run state that is incomplete must first ask for
/// what it lacks. The first proposal for an id stands. Returns whether it was recorded. Fails with `InvalidMessage` for a wrong id or
/// a score outside 0 to 100.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` for a wrong id or a score outside 0 to 100.
pub fn add_proposal(
    st: &mut EngineState,
    agent: &str,
    cid: &str,
    candidate: &str,
    rationale: &str,
    raw: i64,
    reasoning: &str,
) -> Result<bool> {
    let round = match parse_candidate_id(cid) {
        Some((round, who)) if who == agent && st.agents.iter().any(|a| a == agent) => round,
        _ => return Err(ConcordError::invalid_message(format!("the proposal id {} is not seed-{agent} or r<N>-{agent}", quote(cid)))),
    };
    raw_value(raw, "raw")?;
    if st.pool.iter().any(|c| c.id == cid) {
        return Ok(false);
    }
    st.pool.push(Candidate {
        id: cid.to_string(),
        proposer: agent.to_string(),
        round,
        candidate: candidate.to_string(),
        rationale: rationale.to_string(),
    });
    st.raw.entry(agent.to_string()).or_default().insert(cid.to_string(), raw);
    st.reasoning.entry(agent.to_string()).or_default().insert(cid.to_string(), reasoning.to_string());
    Ok(true)
}

/// What `add_ratings` did with the entries of a `<concord-score>`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Ratings {
    pub recorded: Vec<String>,
    pub unknown: Vec<String>,
    pub ignored: Vec<String>,
}

/// One entry of a score: a candidate id, a raw score in millionths and the reasoning.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rating {
    pub id: String,
    pub raw: i64,
    pub reasoning: String,
}

/// Record a `<concord-score>`. An entry that rates a candidate the agent proposed, or one it has already rated (in an earlier
/// message or earlier in this one), is ignored and the others are kept. A rating of a candidate that is not in the pool is not
/// invalid, the proposal was lost, so the caller asks for it, and that rating is not recorded. Fails with `InvalidMessage`, recording
/// nothing, when a raw score is outside 0 to 100.
///
/// # Errors
///
/// `ConcordError::InvalidMessage`, recording nothing, when a raw score is outside 0 to 100.
pub fn add_ratings(st: &mut EngineState, agent: &str, ratings: &[Rating]) -> Result<Ratings> {
    for r in ratings {
        raw_value(r.raw, &format!("the score for {}", r.id))?;
    }
    let mut done = Ratings::default();
    for r in ratings {
        let owner = st.pool.iter().find(|c| c.id == r.id).map(|c| c.proposer.as_str());
        let proposer = owner.or_else(|| parse_candidate_id(&r.id).map(|(_, who)| who));
        let rated = st.raw.get(agent).is_some_and(|m| m.contains_key(&r.id));
        if proposer == Some(agent) || rated {
            done.ignored.push(r.id.clone());
        } else if owner.is_none() {
            if !done.unknown.contains(&r.id) {
                done.unknown.push(r.id.clone());
            }
        } else {
            st.raw.entry(agent.to_string()).or_default().insert(r.id.clone(), r.raw);
            st.reasoning.entry(agent.to_string()).or_default().insert(r.id.clone(), r.reasoning.clone());
            done.recorded.push(r.id.clone());
        }
    }
    Ok(done)
}

// --------------------------------------------------------------------------
// scores
// --------------------------------------------------------------------------

/// `raw` on the scale of `agent`, in millionths between 0 and 1_000_000: the exact ratio of `raw - raw_reject` to
/// `raw_ideal - raw_reject`, clamped, rounded half up.
///
/// # Panics
///
/// When `agent` has no anchors in `st`: ask `score`, which says None then.
pub fn norm(st: &EngineState, agent: &str, raw: i64) -> i64 {
    let a = &st.anchors[agent];
    let (num, den) = (raw - a.raw_reject, a.raw_ideal - a.raw_reject);
    if num <= 0 {
        0
    } else if num >= den {
        MILLION
    } else {
        (2 * num * MILLION + den) / (2 * den)
    }
}

/// The normalized score of `agent` on `cid`, or None when it has none (or its anchors are not in).
pub fn score(st: &EngineState, agent: &str, cid: &str) -> Option<i64> {
    let raw = *st.raw.get(agent)?.get(cid)?;
    st.anchors.contains_key(agent).then(|| norm(st, agent, raw))
}

/// `{agent: normalized score}` of the participants that have rated `cid`, in the order of `<participants>`.
pub fn scores_of(st: &EngineState, cid: &str) -> IndexMap<String, i64> {
    st.agents.iter().filter_map(|a| score(st, a, cid).map(|s| (a.clone(), s))).collect()
}

pub fn floor_of(st: &EngineState, cid: &str) -> Option<i64> {
    scores_of(st, cid).values().copied().min()
}

/// The normalized scores of `cid`, sorted from the lowest.
pub fn leximin(st: &EngineState, cid: &str) -> Vec<i64> {
    let mut v: Vec<i64> = scores_of(st, cid).values().copied().collect();
    v.sort_unstable();
    v
}

/// Whether vector `v` beats `u`: the larger value at the first position where they differ.
pub fn lex_gt(v: &[i64], u: &[i64]) -> bool {
    v.iter().zip(u).find(|(a, b)| a != b).is_some_and(|(a, b)| a > b)
}

/// SELECT: the candidate of `ids` (in pool order) with the best leximin vector; the first one wins an exact tie.
pub fn select<'a>(st: &EngineState, ids: &[&'a str]) -> &'a str {
    let mut best = ids[0];
    for &cid in &ids[1..] {
        if lex_gt(&leximin(st, cid), &leximin(st, best)) {
            best = cid;
        }
    }
    best
}

/// ACCEPT: whether candidate `cand` replaces the incumbent `inc`. True if `cand` clears τ for everyone and `inc` does not, or if some
/// position k of its leximin vector gains at least eps while no position before it loses.
pub fn accept(st: &EngineState, cand: &str, inc: &str) -> bool {
    let (tau, eps) = (st.params.satisfaction_threshold, st.params.eps);
    if floor_of(st, cand).is_some_and(|f| f >= tau) && floor_of(st, inc).is_none_or(|f| f < tau) {
        return true;
    }
    let (c, i) = (leximin(st, cand), leximin(st, inc));
    (0..c.len().min(i.len())).any(|k| c[k] >= i[k] + eps && (0..k).all(|j| c[j] >= i[j]))
}

/// The participant with the lowest normalized score on `cid`; a tie goes to the one that comes first in `<participants>`.
pub fn bottleneck(st: &EngineState, cid: &str) -> Option<String> {
    // `scores_of` lists the agents in the order of `<participants>`, and `min_by_key` keeps the first of equal scores.
    scores_of(st, cid).into_iter().min_by_key(|(_, s)| *s).map(|(a, _)| a)
}

/// `[(agent, candidate id)]`: the ratings that are not in yet, over the whole pool.
pub fn lacking(st: &EngineState) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for c in st.ordered_pool() {
        for a in &st.agents {
            if !st.raw.get(a).is_some_and(|m| m.contains_key(&c.id)) {
                out.push((a.clone(), c.id.clone()));
            }
        }
    }
    out
}

// --------------------------------------------------------------------------
// the evaluation
// --------------------------------------------------------------------------

fn complete(st: &EngineState, ids: &[&str]) -> bool {
    st.agents.iter().all(|a| st.anchors.contains_key(a) && ids.iter().all(|cid| st.raw.get(a).is_some_and(|m| m.contains_key(*cid))))
}

/// What follows from the ratings so far.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EvalState {
    /// Ratings or init replies are still owed.
    Wait,
    /// The bottleneck must propose.
    Repair,
    /// Every evaluation is made and the run has an outcome.
    Done,
}

/// One evaluation of the log: the stage, the candidate SELECT gave, whether it replaced the incumbent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LogEntry {
    pub stage: usize,
    pub select: String,
    pub replaced: bool,
    pub floor: Option<i64>,
}

/// The result of `evaluate`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Evaluation {
    pub state: EvalState,
    /// SUCCESS or BEST_EFFORT once the state is `Done`, else None.
    pub outcome: Option<Outcome>,
    /// The incumbent: the winner when done, the incumbent so far while waiting (None before the seeds are rated).
    pub x_star: Option<String>,
    /// The number of repairs in the pool.
    pub t: usize,
    /// `floor(x*)`, in millionths.
    pub floor: Option<i64>,
    /// `{agent: normalized score on x*}`, in millionths.
    pub scores: IndexMap<String, i64>,
    /// `{agent: how far below τ}`, in millionths, for each participant under τ.
    pub shortfall: IndexMap<String, i64>,
    /// The participant that must propose, when the state is `Repair`.
    pub bottleneck: Option<String>,
    /// The number its repair must carry, when the state is `Repair`.
    pub round: Option<u32>,
    /// The first participant in `<participants>`.
    pub reporter: String,
    /// One entry per evaluation.
    pub log: Vec<LogEntry>,
}

/// Go through the evaluations from the first, for the seeds and then for each repair in the order of the pool, and say what follows.
/// Reads `st`, changes nothing.
pub fn evaluate(st: &EngineState) -> Evaluation {
    let mut out = Evaluation {
        state: EvalState::Wait,
        outcome: None,
        x_star: None,
        t: st.repairs_in_pool(),
        floor: None,
        scores: IndexMap::new(),
        shortfall: IndexMap::new(),
        bottleneck: None,
        round: None,
        reporter: st.reporter().to_string(),
        log: Vec::new(),
    };
    if st.agents.iter().any(|a| !st.anchors.contains_key(a)) {
        return out; // an init reply is still owed
    }
    let pool = st.ordered_pool();
    let seeds: Vec<&str> = pool.iter().filter(|c| c.round == 0).map(|c| c.id.as_str()).collect();
    let repairs: Vec<&str> = pool.iter().filter(|c| c.round > 0).map(|c| c.id.as_str()).collect();
    let tau = st.params.satisfaction_threshold;
    let mut x_star: Option<&str> = None;
    let mut done = true;
    for k in 0..=repairs.len() {
        let ids: Vec<&str> = seeds.iter().chain(&repairs[..k]).copied().collect();
        if ids.is_empty() || !complete(st, &ids) {
            done = false;
            break;
        }
        let x_cand = select(st, &ids);
        let replaced = match x_star {
            None => true,
            Some(x) => x_cand != x && accept(st, x_cand, x),
        };
        out.log.push(LogEntry { stage: k, select: x_cand.to_string(), replaced, floor: floor_of(st, x_cand) });
        if replaced {
            x_star = Some(x_cand);
        }
    }
    if done {
        out.state = EvalState::Done; // every evaluation is made: the pool is rated through
    }
    let Some(x) = x_star else { return out };
    out.x_star = Some(x.to_string());
    out.floor = floor_of(st, x);
    out.scores = scores_of(st, x);
    out.shortfall = out.scores.iter().filter(|(_, s)| **s < tau).map(|(a, s)| (a.clone(), tau - s)).collect();
    if out.state != EvalState::Done {
        return out;
    }
    if out.floor.is_some_and(|f| f >= tau) {
        out.outcome = Some(Outcome::Success);
    } else if out.t as i64 >= st.params.budget {
        out.outcome = Some(Outcome::BestEffort);
    } else {
        out.state = EvalState::Repair;
        out.bottleneck = bottleneck(st, x);
        out.round = Some(out.t as u32 + 1);
    }
    out
}
