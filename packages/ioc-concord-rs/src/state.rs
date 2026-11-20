//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The state of an agent: every run it knows, as plain data that a `Store` can keep as JSON.
//!
//! An item is what a message is about: an init reply of an agent, a score of an agent on a candidate, a repair, the result. The first
//! message recorded for an item stands, and the original is kept word for word so that it can be sent again.

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::engine::{EngineState, Params};
use crate::errors::{ConcordError, Result};
use crate::protocol::{is_agent_id, Outcome};
use crate::wire::{peek, InitBody};

/// What the agent is in a run: a participant, or the initiator that opened the run without being one of its participants.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Participant,
    /// Negotiates nothing, and only watches the init replies and the result.
    Initiator,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RunStatus {
    Active,
    Ended,
    Aborted,
}

impl RunStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            RunStatus::Active => "active",
            RunStatus::Ended => "ended",
            RunStatus::Aborted => "aborted",
        }
    }
}

/// The reporter's result as this agent holds it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Reported {
    pub outcome: Outcome,
    pub winning_candidate_id: String,
}

/// The mismatch errors of a run: how many this agent sent, the text of the last, and the authors of those it got.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Mismatches {
    pub sent: u32,
    pub last: Option<String>,
    pub received: Vec<String>,
}

/// A timer on a batch of messages the agent is owed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Timer {
    /// How many requests have been sent.
    pub requests: u32,
    /// Whether a gap has been asked for at once.
    pub asked: bool,
    /// When the timer expires.
    pub deadline: f64,
    /// The first message lacking, which a request names.
    pub candidate: Option<String>,
    pub author: String,
    pub what: String,
}

/// Which agent refused the init because of its version, and what it said.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AbortNote {
    pub agent: String,
    pub text: Option<String>,
}

/// Why a run aborted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Abort {
    pub reason: String,
    pub agent: Option<String>,
    pub superseded_by: Option<String>,
    /// For the initiator that gave up on a version refusal.
    pub note: Option<AbortNote>,
}

/// One run, for the agent:
///
/// * `role`: participant, or initiator for the agent that opened the run without being one of its participants
/// * `sent`: `{item key: the original text this agent sent for that item}`
/// * `sent_by_uuid`: the same texts by `uuid`, to find the one a damaged-document request names
/// * `received`: `{item key: the original text it recorded for that item}`
/// * `uuids`: `{uuid: true}` for every transmission it has accepted
/// * `outcome`: the outcome this agent computed, or None while it is not known
/// * `reported`: the reporter's result, once this agent holds it
/// * `timers`: for the messages the agent is owed
/// * `held`: `{uuid: text}` of the repairs and results that cannot be judged yet, judged again as the state fills
/// * `expected`: the ids of candidates that other messages prove to exist, whose proposal this agent lacks
/// * `abort`: why the run aborted, or None
/// * `cancel_sent`: whether this agent has sent its cancel (it sends it once)
/// * `disputed`: whether a cancel reached this agent after its run had ended with an outcome
/// * `status`: active until the run has ended for this agent, then ended or aborted
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Run {
    pub role: Role,
    pub run_id: String,
    /// The agent that opened the run.
    pub initiator: String,
    /// In the order of `<participants>`.
    pub participants: Vec<String>,
    pub params: Params,
    pub context: String,
    pub x_reject: String,
    pub x_ideal: String,
    pub schema: Value,
    pub engine: EngineState,
    pub sent: IndexMap<String, String>,
    pub sent_by_uuid: IndexMap<String, String>,
    pub received: IndexMap<String, String>,
    pub uuids: IndexMap<String, bool>,
    pub outcome: Option<Outcome>,
    pub reported: Option<Reported>,
    pub mismatches: Mismatches,
    pub timers: IndexMap<String, Timer>,
    pub held: IndexMap<String, String>,
    pub expected: Vec<String>,
    pub abort: Option<Abort>,
    pub cancel_sent: bool,
    pub disputed: bool,
    pub status: RunStatus,
}

impl Run {
    /// The agent that reports the run: the first participant.
    pub fn reporter(&self) -> &str {
        &self.participants[0]
    }

    /// The agents that may send a message of the run: its participants and its initiator.
    pub fn is_member(&self, agent: &str) -> bool {
        self.initiator == agent || self.participants.iter().any(|p| p == agent)
    }

    /// Remember that the transmission `uid` has been accepted.
    pub fn keep_uuid(&mut self, uid: &str) {
        self.uuids.insert(uid.to_string(), true);
    }

    /// Keep `text`, the original that this agent sent for the item `key`, to send it again word for word.
    /// Fails with `State` when `text` has no `uuid` to find it by: nothing is kept.
    pub fn keep_sent(&mut self, key: &str, text: &str) -> Result<()> {
        let uid = peek(text)
            .and_then(|p| p.attrs.get("uuid").cloned())
            .ok_or_else(|| ConcordError::state("keep_sent: the message has no uuid"))?;
        self.sent.insert(key.to_string(), text.to_string());
        self.sent_by_uuid.insert(uid, text.to_string());
        Ok(())
    }
}

/// The state of an agent: `{"me": id, "runs": {run id: run}, "active": run id or None}`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentState {
    pub me: String,
    pub runs: IndexMap<String, Run>,
    pub active: Option<String>,
}

impl AgentState {
    /// The state of an agent that knows no run yet.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when `me` is not an agent id.
    pub fn new(me: &str) -> Result<Self> {
        if !is_agent_id(me) {
            return Err(ConcordError::state(format!("an agent id is 1 to 64 letters, digits, _ or -, not {}", crate::protocol::quote(me))));
        }
        Ok(AgentState { me: me.to_string(), runs: IndexMap::new(), active: None })
    }

    /// The run that is active for the agent, or None.
    pub fn active_run(&self) -> Option<&Run> {
        let run = self.runs.get(self.active.as_deref()?)?;
        (run.status == RunStatus::Active).then_some(run)
    }

    pub fn active_run_mut(&mut self) -> Option<&mut Run> {
        let run = self.runs.get_mut(self.active.as_deref()?)?;
        (run.status == RunStatus::Active).then_some(run)
    }
}

/// The key of an item: `init-reply|cost`, `score|cost|seed-security`, `result|cost`.
pub fn item_key(kind: &str, author: &str, item: Option<&str>) -> String {
    [Some(kind), Some(author), item].into_iter().flatten().filter(|p| !p.is_empty()).collect::<Vec<_>>().join("|")
}

/// The run that the parsed init `init` (see `wire::parse_message`) opens for `me`, with the init text `doc`: as received, or as sent
/// when `me` is the initiator. An initiator that is not a participant has the role `Initiator`: it negotiates nothing, and only
/// watches the init replies and the result.
///
/// # Errors
///
/// `ConcordError::State` when `doc` has no `uuid` to find it by.
pub fn new_run(me: &str, from: &str, run_id: &str, uid: &str, init: &InitBody, doc: &str) -> Result<Run> {
    let mine = from == me;
    let mut run = Run {
        role: if init.participants.iter().any(|p| p == me) { Role::Participant } else { Role::Initiator },
        run_id: run_id.to_string(),
        initiator: from.to_string(),
        participants: init.participants.clone(),
        params: init.params.clone(),
        context: init.context.clone(),
        x_reject: init.x_reject.clone(),
        x_ideal: init.x_ideal.clone(),
        schema: init.schema.clone(),
        engine: EngineState::new(&init.participants, &init.params),
        sent: IndexMap::new(),
        sent_by_uuid: IndexMap::new(),
        received: IndexMap::new(),
        uuids: IndexMap::from([(uid.to_string(), true)]),
        outcome: None,
        reported: None,
        mismatches: Mismatches::default(),
        timers: IndexMap::new(),
        held: IndexMap::new(),
        expected: Vec::new(),
        abort: None,
        cancel_sent: false,
        disputed: false,
        status: RunStatus::Active,
    };
    if mine {
        run.keep_sent(&item_key("init", me, None), doc)?;
    } else {
        run.received.insert(item_key("init", from, None), doc.to_string());
    }
    Ok(run)
}
