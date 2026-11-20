//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the agent's model must write now, worked out from the state of the run, never queued: asking twice gives the same tasks, and
//! after a restart from the store they are still right.

use indexmap::IndexMap;
use serde::Serialize;
use serde_json::Value;

use crate::engine::{evaluate, EvalState};
use crate::liveness::lacking_by;
use crate::state::{item_key, Role, Run};

/// The candidates of the others that `me` has not rated, once every init reply is in.
pub fn to_rate(run: &Run, me: &str) -> Vec<String> {
    lacking_by(run).shift_remove(me).unwrap_or_default()
}

/// A candidate to rate, with what its proposer wrote.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ScoreCandidate {
    pub id: String,
    pub from: String,
    pub candidate: String,
    pub rationale: String,
    /// The proposer's reasoning for its own score.
    pub reasoning: String,
}

/// The incumbent a repair must improve on.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Incumbent {
    pub id: String,
    pub candidate: String,
    pub rationale: String,
}

/// The first answer after the init: anchors, a seed and a score for it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct InitReplyTask {
    pub run_id: String,
    pub initiator: String,
    pub context: String,
    pub x_reject: String,
    pub x_ideal: String,
    pub schema: Value,
}

/// A rating for each candidate, on the same scale.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ScoreTask {
    pub run_id: String,
    pub candidates: Vec<ScoreCandidate>,
}

/// A better candidate, which only the bottleneck of the round writes. The numbers are normalized scores between 0 and 1, in millionths
/// (`guidance` writes them as text, with at most 6 decimal places).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RepairTask {
    pub run_id: String,
    pub round: u32,
    pub incumbent: Incumbent,
    pub threshold: i64,
    pub floor: i64,
    /// `{agent: normalized score of the incumbent}`
    pub scores: IndexMap<String, i64>,
    /// `{agent: how far below the threshold}`
    pub shortfall: IndexMap<String, i64>,
    /// `{agent: what it said about the incumbent}`
    pub reasoning: IndexMap<String, String>,
    pub repairs_left: i64,
}

/// What the agent's model owes now.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
#[non_exhaustive]
pub enum Task {
    InitReply(InitReplyTask),
    Score(ScoreTask),
    Repair(RepairTask),
}

impl Task {
    pub fn kind(&self) -> &'static str {
        match self {
            Task::InitReply(_) => "init-reply",
            Task::Score(_) => "score",
            Task::Repair(_) => "repair",
        }
    }

    /// The round of a repair; none for the other tasks.
    pub fn round(&self) -> Option<u32> {
        match self {
            Task::Repair(t) => Some(t.round),
            _ => None,
        }
    }

    pub fn run_id(&self) -> &str {
        match self {
            Task::InitReply(t) => &t.run_id,
            Task::Score(t) => &t.run_id,
            Task::Repair(t) => &t.run_id,
        }
    }
}

/// The tasks `me` owes in `run` (none when `run` is None, or `me` does not negotiate in it). See `Agent::due` for what each holds.
pub fn tasks_of(run: Option<&Run>, me: &str) -> Vec<Task> {
    let Some(run) = run.filter(|r| r.role == Role::Participant) else { return Vec::new() };
    let st = &run.engine;
    if !run.sent.contains_key(&item_key("init-reply", me, None)) {
        return vec![Task::InitReply(InitReplyTask {
            run_id: run.run_id.clone(),
            initiator: run.initiator.clone(),
            context: run.context.clone(),
            x_reject: run.x_reject.clone(),
            x_ideal: run.x_ideal.clone(),
            schema: run.schema.clone(),
        })];
    }
    let owed = to_rate(run, me);
    if owed.is_empty() {
        return repair_task(run, me).into_iter().collect();
    }
    let candidates = owed
        .iter()
        .filter_map(|cid| st.pool.iter().find(|c| c.id == *cid))
        .map(|c| ScoreCandidate {
            id: c.id.clone(),
            from: c.proposer.clone(),
            candidate: c.candidate.clone(),
            rationale: c.rationale.clone(),
            reasoning: st.reasoning.get(&c.proposer).and_then(|m| m.get(&c.id)).cloned().unwrap_or_default(),
        })
        .collect();
    vec![Task::Score(ScoreTask { run_id: run.run_id.clone(), candidates })]
}

fn repair_task(run: &Run, me: &str) -> Option<Task> {
    let ev = evaluate(&run.engine);
    let (round, x) = (ev.round?, ev.x_star.as_ref()?);
    if ev.state != EvalState::Repair
        || ev.bottleneck.as_deref() != Some(me)
        || run.sent.contains_key(&item_key("repair", me, Some(&format!("r{round}-{me}"))))
    {
        return None;
    }
    let st = &run.engine;
    let inc = st.pool.iter().find(|c| c.id == *x)?;
    Some(Task::Repair(RepairTask {
        run_id: run.run_id.clone(),
        round,
        incumbent: Incumbent { id: x.clone(), candidate: inc.candidate.clone(), rationale: inc.rationale.clone() },
        threshold: st.params.satisfaction_threshold,
        floor: ev.floor.unwrap_or(0),
        scores: ev.scores.clone(),
        shortfall: ev.shortfall.clone(),
        reasoning: st.agents.iter().map(|a| (a.clone(), st.reasoning.get(a).and_then(|m| m.get(x)).cloned().unwrap_or_default())).collect(),
        repairs_left: st.params.budget - ev.t as i64,
    }))
}
