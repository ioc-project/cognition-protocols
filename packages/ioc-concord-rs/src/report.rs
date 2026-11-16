//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Where a run stands for an agent, and the report of the run: views of the state of a run that change nothing.

use indexmap::IndexMap;
use serde::Serialize;

use crate::engine::{evaluate, lacking, EvalState};
use crate::protocol::Outcome;
use crate::state::{Abort, Reported, Role, Run, RunStatus};

/// Where a run stands for the agent: its `status`, the `outcome` this agent has computed (None before), and the engine's view.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Status {
    pub run_id: String,
    pub role: Role,
    pub status: RunStatus,
    pub outcome: Option<Outcome>,
    pub state: EvalState,
    pub x_star: Option<String>,
    pub floor: Option<i64>,
    /// Normalized scores, in millionths.
    pub scores: IndexMap<String, i64>,
    pub bottleneck: Option<String>,
    pub round: Option<u32>,
    pub reporter: String,
    /// The ratings still missing, as `(agent, candidate)`.
    pub lacking: Vec<(String, String)>,
    pub anchors_in: Vec<String>,
    /// The deadline of each timer.
    pub timers: IndexMap<String, Option<f64>>,
}

pub fn status_of(run: &Run) -> Status {
    let ev = evaluate(&run.engine);
    let mut anchors_in: Vec<String> = run.engine.anchors.keys().cloned().collect();
    anchors_in.sort();
    Status {
        run_id: run.run_id.clone(),
        role: run.role,
        status: run.status,
        outcome: run.outcome,
        state: ev.state,
        x_star: ev.x_star,
        floor: ev.floor,
        scores: ev.scores,
        bottleneck: ev.bottleneck,
        round: ev.round,
        reporter: ev.reporter,
        lacking: lacking(&run.engine),
        anchors_in,
        timers: run.timers.iter().map(|(k, t)| (k.clone(), t.deadline)).collect(),
    }
}

/// How a report stands: `pending` until the run has ended for the agent, then `confirmed` (or `unverified` for an initiator that is
/// not a participant), or `disputed` when a mismatch was found, sent or received or a cancel reached it after its run had ended. An
/// aborted run has the status `aborted`: the spec gives it none.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ReportStatus {
    Pending,
    Confirmed,
    Unverified,
    Disputed,
    Aborted,
}

impl ReportStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            ReportStatus::Pending => "pending",
            ReportStatus::Confirmed => "confirmed",
            ReportStatus::Unverified => "unverified",
            ReportStatus::Disputed => "disputed",
            ReportStatus::Aborted => "aborted",
        }
    }
}

/// The winning candidate of a run: its id, and its text when the agent holds it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Winner {
    pub id: String,
    pub candidate: Option<String>,
}

/// The reporter's result and whether it matches this agent's outcome (for the reporter, its own).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Compared {
    pub reporter: String,
    pub result: Option<Reported>,
    pub matches: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MismatchSummary {
    pub sent: u32,
    pub received: Vec<String>,
}

/// The report of a run for an agent, as the spec describes it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Report {
    pub run_id: String,
    pub outcome: Option<Outcome>,
    pub winner: Option<Winner>,
    /// Normalized scores, in millionths.
    pub scores: IndexMap<String, i64>,
    /// How far each participant is below τ, in millionths.
    pub shortfall: IndexMap<String, i64>,
    /// The number of repair rounds used (None for an initiator that is not a participant).
    pub rounds: Option<usize>,
    /// For `ABORTED` only.
    pub reason: Option<Abort>,
    pub results_compared: Compared,
    pub mismatches: MismatchSummary,
    pub status: ReportStatus,
}

/// The report of `run` for the agent `me`. A run that is aborted has the outcome `ABORTED`, no winner and its `reason`.
pub fn report_of(run: &Run, me: &str) -> Report {
    if run.role == Role::Initiator {
        return initiator_report(run);
    }
    let ev = evaluate(&run.engine);
    let done = run.outcome.is_some_and(|o| o != Outcome::Aborted);
    let winner = if done {
        ev.x_star
            .as_ref()
            .and_then(|x| run.engine.pool.iter().find(|c| c.id == *x))
            .map(|c| Winner { id: c.id.clone(), candidate: Some(c.candidate.clone()) })
    } else {
        None
    };
    let reporter = run.participants[0].clone();
    let mine = run.outcome.zip(ev.x_star.clone()).filter(|_| done);
    let compared = if me == reporter {
        Compared {
            reporter,
            result: mine.clone().map(|(outcome, winning_candidate_id)| Reported { outcome, winning_candidate_id }),
            matches: done.then_some(true),
        }
    } else {
        let rep = run.reported.clone();
        let matches = match (&rep, &mine) {
            (Some(r), Some((outcome, id))) => Some(r.outcome == *outcome && r.winning_candidate_id == *id),
            _ => None,
        };
        Compared { reporter, result: rep, matches }
    };
    let m = &run.mismatches;
    let status = if run.status == RunStatus::Aborted {
        ReportStatus::Aborted
    } else if m.sent > 0 || !m.received.is_empty() || run.disputed {
        ReportStatus::Disputed
    } else if run.status == RunStatus::Ended {
        ReportStatus::Confirmed
    } else {
        ReportStatus::Pending
    };
    Report {
        run_id: run.run_id.clone(),
        outcome: run.outcome,
        winner,
        scores: if done { ev.scores } else { IndexMap::new() },
        shortfall: if done { ev.shortfall } else { IndexMap::new() },
        rounds: Some(ev.t),
        reason: run.abort.clone(),
        results_compared: compared,
        mismatches: MismatchSummary { sent: m.sent, received: m.received.clone() },
        status,
    }
}

/// The report of an initiator that is not a participant: it has no outcome of its own, and learns how the run ended from the result of
/// the reporter. Its status is `unverified`, since there is nothing of its own to compare, unless a mismatch error reaches it
/// (`disputed`); `pending` before the result, `aborted` after a cancel or a give-up.
fn initiator_report(run: &Run) -> Report {
    let (rep, m) = (&run.reported, &run.mismatches);
    let status = if run.status == RunStatus::Aborted {
        ReportStatus::Aborted
    } else if !m.received.is_empty() || run.disputed {
        ReportStatus::Disputed
    } else if run.status == RunStatus::Ended {
        ReportStatus::Unverified
    } else {
        ReportStatus::Pending
    };
    let winner = rep.as_ref().filter(|_| run.status != RunStatus::Aborted).map(|r| Winner {
        id: r.winning_candidate_id.clone(),
        candidate: run.engine.pool.iter().find(|c| c.id == r.winning_candidate_id).map(|c| c.candidate.clone()),
    });
    Report {
        run_id: run.run_id.clone(),
        outcome: run.outcome,
        winner,
        scores: IndexMap::new(),
        shortfall: IndexMap::new(),
        rounds: None,
        reason: run.abort.clone(),
        results_compared: Compared { reporter: run.participants[0].clone(), result: rep.clone(), matches: None },
        mismatches: MismatchSummary { sent: 0, received: m.received.clone() },
        status,
    }
}
