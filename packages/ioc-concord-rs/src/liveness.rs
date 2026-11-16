//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The messages an agent is owed, and the timers that wait for them. These are functions of the state of a run: they send nothing.
//!
//! A batch is a set of messages from one author that arrive together: its init reply, its scores on the seeds, its score on a repair,
//! the repair of the bottleneck, the result of the reporter. It is owed until nothing in it is lacking, and it has a timer from the
//! moment it becomes owed (see Messages owed in the spec). The owed batches are worked out from the state each time, never kept; only
//! the timers are, in `run.timers`, under a key that says what the batch is: `init-reply|cost`, `score|cost|seeds`,
//! `score|cost|r1-security`, `repair|security|1`, `result|cost`.

use indexmap::IndexMap;

use crate::engine::{lacking, parse_candidate_id, timers_enabled, EvalState, Evaluation};
use crate::protocol::Outcome;
use crate::state::{Role, Run, RunStatus, Timer};

/// `{agent: [candidate ids it has not rated yet], in pool order}`. Before every init reply is in nothing is owed, so it is empty.
pub fn lacking_by(run: &Run) -> IndexMap<String, Vec<String>> {
    let st = &run.engine;
    let mut out: IndexMap<String, Vec<String>> = IndexMap::new();
    if st.anchors.len() < st.agents.len() {
        return out;
    }
    for (agent, cid) in lacking(st) {
        out.entry(agent).or_default().push(cid);
    }
    out
}

/// A batch of messages that is owed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Owed {
    pub author: String,
    pub what: String,
    pub candidate: Option<String>,
}

/// The batches of messages that `me` is owed now, by timer key, given the evaluation `ev` of the engine over the run. An agent is
/// never owed its own messages. An initiator that does not negotiate is owed the init replies, and no timer runs for the result.
pub fn owed_batches(run: &Run, me: &str, ev: Option<&Evaluation>) -> IndexMap<String, Owed> {
    let mut out: IndexMap<String, Owed> = IndexMap::new();
    if run.status != RunStatus::Active {
        return out;
    }
    let st = &run.engine;
    let reporter = run.participants[0].as_str();
    let owed = |author: &str, what: &str, candidate: Option<&str>| Owed {
        author: author.to_string(),
        what: what.to_string(),
        candidate: candidate.map(str::to_string),
    };

    for a in &run.participants {
        if a != me && !st.anchors.contains_key(a) {
            out.insert(format!("init-reply|{a}"), owed(a, "init-reply", None));
        }
    }
    if run.role == Role::Initiator {
        return out;
    }
    for (author, candidates) in lacking_by(run) {
        for cid in candidates {
            if author != me {
                let seed = parse_candidate_id(&cid).is_some_and(|(round, _)| round == 0);
                let batch = if seed { "seeds" } else { cid.as_str() };
                // the first message lacking in a batch names it
                out.entry(format!("score|{author}|{batch}")).or_insert_with(|| owed(&author, "score", Some(&cid)));
            }
        }
    }
    let have: Vec<&str> = st.pool.iter().map(|c| c.id.as_str()).collect();
    for cid in &run.expected {
        let Some((round, author)) = parse_candidate_id(cid) else { continue };
        if !have.contains(&cid.as_str()) && author != me {
            if round == 0 {
                out.insert(format!("init-reply|{author}"), owed(author, "init-reply", None));
            } else {
                out.insert(format!("repair|{author}|{round}"), owed(author, "repair", Some(cid)));
            }
        }
    }
    if let Some(ev) = ev {
        if ev.state == EvalState::Repair && ev.bottleneck.as_deref() != Some(me) {
            if let (Some(b), Some(round)) = (&ev.bottleneck, ev.round) {
                out.insert(format!("repair|{b}|{round}"), owed(b, "repair", Some(&format!("r{round}-{b}"))));
            }
        }
    }
    if run.outcome.is_some_and(|o| o != Outcome::Aborted) && me != reporter && run.reported.is_none() {
        out.insert(format!("result|{reporter}"), owed(reporter, "result", None));
    }
    out
}

/// Start the timer of every batch that has become owed, and drop the timer of every batch that is complete. When the message that was
/// first lacking in a batch has arrived and the next is still owed, the wait starts again. With a `timeout` of 0 a timer has no
/// deadline: nothing is ever triggered.
pub fn sync_timers(run: &mut Run, me: &str, now: f64, ev: Option<&Evaluation>) {
    let owed = owed_batches(run, me, ev);
    run.timers.retain(|key, _| owed.contains_key(key));
    let enabled = timers_enabled(&run.params);
    let timeout = run.params.timeout as f64;
    for (key, o) in owed {
        let fresh = run.timers.get(&key).is_none_or(|t| t.candidate != o.candidate);
        if fresh {
            let deadline = enabled.then_some(now + timeout);
            run.timers.insert(
                key.clone(),
                Timer { author: String::new(), what: String::new(), candidate: o.candidate.clone(), requests: 0, asked: false, deadline },
            );
        }
        let t = run.timers.get_mut(&key).expect("the timer was just made");
        t.author = o.author;
        t.what = o.what;
        t.candidate = o.candidate;
    }
}

/// The keys of the timers of `run` that have expired at `now`, the earliest first.
pub fn expired_keys(run: &Run, now: f64) -> Vec<String> {
    let mut due: Vec<(f64, &String)> =
        run.timers.iter().filter_map(|(key, t)| t.deadline.filter(|d| *d <= now).map(|d| (d, key))).collect();
    due.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(b.1)));
    due.into_iter().map(|(_, key)| key.clone()).collect()
}

/// The earliest deadline of a timer of an active run among `runs`, or None.
pub fn earliest_deadline<'a>(runs: impl IntoIterator<Item = &'a Run>) -> Option<f64> {
    runs.into_iter()
        .filter(|run| run.status == RunStatus::Active)
        .flat_map(|run| run.timers.values().filter_map(|t| t.deadline))
        .min_by(f64::total_cmp)
}
