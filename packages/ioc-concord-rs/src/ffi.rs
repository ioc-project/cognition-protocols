//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The library for other languages, through UniFFI (feature `uniffi`): the node of this crate, as an API shaped for any foreign language.
//!
//! The API is made for the languages that call it, not for Rust: records with named fields, enums with named fields, lists instead of
//! ordered maps, numbers as an enum, and JSON documents as text (read them with the language's own JSON library). It does not follow
//! the types of the rest of the crate one to one, so that they are not shaped by what a foreign language can carry, and it holds no
//! logic of its own: a Rust program has no use for it.
//!
//! * `Agent`: one agent of a run (`receive`, `tick`, `due`, `start`, `submit_*`, `report_json`, ...)
//! * `Relay`: the centre of a star, on its own
//! * what crosses the boundary: `Step`, `Event`, `Task`, `RunParams`, `ScoreInput`, `Number`
//! * `ConcordError`: an exception per kind of error

use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::Value;

use crate::agent::{Agent as CoreAgent, Event as CoreEvent, Step as CoreStep};
use crate::protocol::Num;
use crate::relay::Relay as CoreRelay;
use crate::store::FileStore;
use crate::tasks::Task as CoreTask;
use crate::wire::{InitParams, ScoreIn};
use crate::{guidance, protocol};

/// The state behind `mutex`. A call that panicked in the library may have left it half changed, and the next call would save that: so a
/// lock that was held by a panic is an error, and the object is not used again.
fn lock<T>(mutex: &Mutex<T>) -> Result<MutexGuard<'_, T>> {
    mutex.lock().map_err(|_| ConcordError::State { message: "the object is unusable after an internal error: make a new one".to_string() })
}

fn to_json<T: serde::Serialize>(value: &T) -> Result<String> {
    serde_json::to_string(value).map_err(|exc| ConcordError::State { message: format!("not JSON ({exc})") })
}

// ---------------------------------------------------------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------------------------------------------------------

/// What the library refuses on purpose. `message` says what is wrong, in words an agent can act on.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Error)]
pub enum ConcordError {
    /// A candidate schema is not valid or not supported.
    Schema { message: String },
    /// The parameters of a run are not valid.
    InvalidRun { message: String },
    /// A message breaks a rule of the protocol.
    InvalidMessage { message: String },
    /// A saved state cannot be loaded, or a call does not fit the state of the run.
    State { message: String },
    /// Nothing has been saved yet: not a fault, only "start empty".
    MissingState { message: String },
}

impl std::fmt::Display for ConcordError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let (Self::Schema { message }
        | Self::InvalidRun { message }
        | Self::InvalidMessage { message }
        | Self::State { message }
        | Self::MissingState { message }) = self;
        f.write_str(message)
    }
}

impl From<crate::ConcordError> for ConcordError {
    fn from(error: crate::ConcordError) -> Self {
        use crate::ConcordError as E;
        let message = error.message().to_string();
        match error {
            E::Schema(_) => ConcordError::Schema { message },
            E::InvalidRun(_) => ConcordError::InvalidRun { message },
            E::InvalidMessage(_) => ConcordError::InvalidMessage { message },
            E::State(_) => ConcordError::State { message },
            E::MissingState(_) => ConcordError::MissingState { message },
        }
    }
}

impl From<ConcordError> for crate::ConcordError {
    fn from(error: ConcordError) -> Self {
        use crate::ConcordError as E;
        match error {
            ConcordError::Schema { message } => E::Schema(message),
            ConcordError::InvalidRun { message } => E::InvalidRun(message),
            ConcordError::InvalidMessage { message } => E::InvalidMessage(message),
            ConcordError::MissingState { message } => E::MissingState(message),
            ConcordError::State { message } => E::State(message),
        }
    }
}

type Result<T> = std::result::Result<T, ConcordError>;

// ---------------------------------------------------------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------------------------------------------------------

/// A number as the caller gives it: the text of a number, an integer, or a float.
#[derive(Debug, Clone, uniffi::Enum)]
pub enum Number {
    Text { value: String },
    Int { value: i64 },
    Float { value: f64 },
}

impl From<Number> for Num {
    fn from(number: Number) -> Num {
        match number {
            Number::Text { value } => Num::Text(value),
            Number::Int { value } => Num::Int(value),
            Number::Float { value } => Num::Float(value),
        }
    }
}

/// The parameters of a run; what is not given has its default.
#[derive(Debug, Clone, Default, uniffi::Record)]
pub struct RunParams {
    #[uniffi(default = None)]
    pub satisfaction_threshold: Option<Number>,
    #[uniffi(default = None)]
    pub eps: Option<Number>,
    #[uniffi(default = None)]
    pub budget: Option<i64>,
    /// 0 switches the timers off.
    #[uniffi(default = None)]
    pub timeout: Option<i64>,
    #[uniffi(default = None)]
    pub retries: Option<i64>,
}

impl From<RunParams> for InitParams {
    fn from(p: RunParams) -> InitParams {
        InitParams {
            satisfaction_threshold: p.satisfaction_threshold.map(Num::from),
            eps: p.eps.map(Num::from),
            budget: p.budget,
            timeout: p.timeout,
            retries: p.retries,
        }
    }
}

/// The rating of one candidate.
#[derive(Debug, Clone, uniffi::Record)]
pub struct ScoreInput {
    pub id: String,
    pub raw: Number,
    pub reasoning: String,
}

/// What happened during a call, for a log.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Event {
    /// The name of the event.
    pub event: String,
    pub at: f64,
    /// The fields of the event other than its name, as a JSON object.
    pub details_json: String,
}

impl From<&CoreEvent> for Event {
    fn from(e: &CoreEvent) -> Event {
        Event { event: e.event.clone(), at: e.at, details_json: Value::Object(e.details.clone()).to_string() }
    }
}

/// What a call produced: `send` are the messages to broadcast, `forward` those to pass on when the agent is the centre of a star,
/// `accepted` the texts received that the agent took, and `events` what happened.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Step {
    pub now: f64,
    pub send: Vec<String>,
    pub forward: Vec<String>,
    pub accepted: Vec<String>,
    pub events: Vec<Event>,
}

impl From<CoreStep> for Step {
    fn from(s: CoreStep) -> Step {
        Step { now: s.now, send: s.send, forward: s.forward, accepted: s.accepted, events: s.events.iter().map(Event::from).collect() }
    }
}

/// A candidate to rate, with what its proposer wrote.
#[derive(Debug, Clone, uniffi::Record)]
pub struct ScoreCandidate {
    pub id: String,
    /// The proposer.
    pub author: String,
    pub candidate: String,
    pub rationale: String,
    /// The proposer's reasoning for its own score.
    pub reasoning: String,
}

/// The incumbent a repair must improve on.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Incumbent {
    pub id: String,
    pub candidate: String,
    pub rationale: String,
}

/// A number of an agent, in millionths (normalized scores between 0 and 1: 500_000 is 0.5).
#[derive(Debug, Clone, uniffi::Record)]
pub struct AgentScore {
    pub agent: String,
    pub millionths: i64,
}

/// A text of an agent.
#[derive(Debug, Clone, uniffi::Record)]
pub struct AgentText {
    pub agent: String,
    pub text: String,
}

/// What the agent's model owes now (see `Agent::due`).
#[derive(Debug, Clone, uniffi::Enum)]
pub enum Task {
    /// The first answer after the init: anchors, a seed and a score for it. `schema_json` is the candidate schema.
    InitReply { run_id: String, initiator: String, context: String, x_reject: String, x_ideal: String, schema_json: String },
    /// A rating for each candidate, on the same scale.
    Score { run_id: String, candidates: Vec<ScoreCandidate> },
    /// A better candidate, which only the bottleneck of the round writes.
    Repair {
        run_id: String,
        round: u32,
        incumbent: Incumbent,
        threshold: i64,
        floor: i64,
        scores: Vec<AgentScore>,
        shortfall: Vec<AgentScore>,
        reasoning: Vec<AgentText>,
        repairs_left: i64,
    },
}

fn scores<'a>(map: impl IntoIterator<Item = (&'a String, &'a i64)>) -> Vec<AgentScore> {
    map.into_iter().map(|(agent, millionths)| AgentScore { agent: agent.clone(), millionths: *millionths }).collect()
}

impl Task {
    /// The task as a foreign caller sees it.
    fn from_core(task: &CoreTask) -> Task {
        match task {
            CoreTask::InitReply(t) => Task::InitReply {
                run_id: t.run_id.clone(),
                initiator: t.initiator.clone(),
                context: t.context.clone(),
                x_reject: t.x_reject.clone(),
                x_ideal: t.x_ideal.clone(),
                schema_json: t.schema.to_string(),
            },
            CoreTask::Score(t) => Task::Score {
                run_id: t.run_id.clone(),
                candidates: t
                    .candidates
                    .iter()
                    .map(|c| ScoreCandidate {
                        id: c.id.clone(),
                        author: c.from.clone(),
                        candidate: c.candidate.clone(),
                        rationale: c.rationale.clone(),
                        reasoning: c.reasoning.clone(),
                    })
                    .collect(),
            },
            CoreTask::Repair(t) => Task::Repair {
                run_id: t.run_id.clone(),
                round: t.round,
                incumbent: Incumbent {
                    id: t.incumbent.id.clone(),
                    candidate: t.incumbent.candidate.clone(),
                    rationale: t.incumbent.rationale.clone(),
                },
                threshold: t.threshold,
                floor: t.floor,
                scores: scores(&t.scores),
                shortfall: scores(&t.shortfall),
                reasoning: t.reasoning.iter().map(|(agent, text)| AgentText { agent: agent.clone(), text: text.clone() }).collect(),
                repairs_left: t.repairs_left,
            },
        }
    }

    fn kind(&self) -> &'static str {
        match self {
            Task::InitReply { .. } => "init-reply",
            Task::Score { .. } => "score",
            Task::Repair { .. } => "repair",
        }
    }

    fn run_id(&self) -> &str {
        match self {
            Task::InitReply { run_id, .. } | Task::Score { run_id, .. } | Task::Repair { run_id, .. } => run_id,
        }
    }

    fn round(&self) -> Option<u32> {
        match self {
            Task::Repair { round, .. } => Some(*round),
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// the agent
// ---------------------------------------------------------------------------------------------------------------------------

/// One agent of a run. See `crate::agent::Agent`.
#[derive(uniffi::Object)]
pub struct Agent {
    me: String,
    inner: Mutex<CoreAgent>,
}

fn build(me: &str, state_path: Option<String>, centre: Option<(Vec<String>, Option<String>)>) -> Result<Arc<Agent>> {
    let mut builder = CoreAgent::builder(me);
    if let Some(path) = state_path {
        builder = builder.store(FileStore::new(path));
    }
    if let Some((spokes, relay_state_path)) = centre {
        let mut relay = match relay_state_path {
            Some(path) => CoreRelay::with_store(FileStore::new(path))?,
            None => CoreRelay::new(),
        };
        for spoke in spokes {
            relay.add_spoke(spoke);
        }
        builder = builder.relay(relay);
    }
    Ok(Arc::new(Agent { me: me.to_string(), inner: Mutex::new(builder.build()?) }))
}

fn not_a_centre() -> ConcordError {
    ConcordError::State { message: "the agent is not the centre of a star".to_string() }
}

#[uniffi::export]
impl Agent {
    /// An agent. Its state is kept in the JSON file `state_path` when it is given, and in memory only when it is not.
    #[uniffi::constructor(default(state_path = None))]
    pub fn new(me: String, state_path: Option<String>) -> Result<Arc<Self>> {
        build(&me, state_path, None)
    }

    /// An agent that is the centre of a star: besides what it does as an agent, `Step::forward` says which of the messages it
    /// receives to pass on to `spokes`. The runs the centre knows are kept in the JSON file `relay_state_path` when it is given: give
    /// it when `state_path` is given, or a restarted centre stops passing on the messages of the runs it knew.
    #[uniffi::constructor(default(state_path = None, relay_state_path = None))]
    pub fn new_centre(me: String, spokes: Vec<String>, state_path: Option<String>, relay_state_path: Option<String>) -> Result<Arc<Self>> {
        build(&me, state_path, Some((spokes, relay_state_path)))
    }

    pub fn me(&self) -> String {
        self.me.clone()
    }

    /// A message arrived: any text, what is not CONCORD is ignored. `now` is the time in seconds (the system's when absent).
    #[uniffi::method(default(now = None))]
    pub fn receive(&self, text: String, now: Option<f64>) -> Result<Step> {
        Ok(lock(&self.inner)?.receive(&text, now)?.into())
    }

    /// Time has passed: ask again, or give up on a silent agent.
    #[uniffi::method(default(now = None))]
    pub fn tick(&self, now: Option<f64>) -> Result<Step> {
        Ok(lock(&self.inner)?.tick(now)?.into())
    }

    /// When to call `tick` next; absent when nothing is awaited.
    pub fn next_wakeup(&self) -> Result<Option<f64>> {
        Ok(lock(&self.inner)?.next_wakeup())
    }

    /// What the agent's model must write now.
    pub fn due(&self) -> Result<Vec<Task>> {
        Ok(lock(&self.inner)?.due().iter().map(Task::from_core).collect())
    }

    /// The text that tells the agent's model what `task` (one of `due()`) asks of it.
    pub fn instructions(&self, task: Task) -> Result<String> {
        Ok(lock(&self.inner)?.instructions_for(task.kind(), task.run_id(), task.round())?)
    }

    /// Open a run: the message in `Step::send` is the one to broadcast. `spec_json` is the candidate schema, a JSON object.
    #[allow(clippy::too_many_arguments)]
    #[uniffi::method(default(params = None, run_id = None, now = None))]
    pub fn start(
        &self,
        participants: Vec<String>,
        context: String,
        x_reject: String,
        x_ideal: String,
        spec_json: String,
        params: Option<RunParams>,
        run_id: Option<String>,
        now: Option<f64>,
    ) -> Result<Step> {
        let spec: Value =
            serde_json::from_str(&spec_json).map_err(|exc| ConcordError::Schema { message: format!("the schema is not JSON ({exc})") })?;
        let params = params.map(InitParams::from);
        let step =
            lock(&self.inner)?.start(&participants, &context, &x_reject, &x_ideal, &spec, params.as_ref(), run_id.as_deref(), now)?;
        Ok(step.into())
    }

    /// Answer the `InitReply` task.
    #[allow(clippy::too_many_arguments)]
    #[uniffi::method(default(now = None))]
    pub fn submit_init_reply(
        &self,
        run_id: String,
        raw_reject: Number,
        raw_ideal: Number,
        candidate: String,
        rationale: String,
        raw: Number,
        reasoning: String,
        now: Option<f64>,
    ) -> Result<Step> {
        let step = lock(&self.inner)?.submit_init_reply(&run_id, raw_reject, raw_ideal, &candidate, &rationale, raw, &reasoning, now)?;
        Ok(step.into())
    }

    /// Answer the `Score` task.
    #[uniffi::method(default(now = None))]
    pub fn submit_score(&self, run_id: String, scores: Vec<ScoreInput>, now: Option<f64>) -> Result<Step> {
        let scores: Vec<ScoreIn> = scores.into_iter().map(|s| ScoreIn::new(s.id, s.raw, s.reasoning)).collect();
        Ok(lock(&self.inner)?.submit_score(&run_id, &scores, now)?.into())
    }

    /// Answer the `Repair` task.
    #[uniffi::method(default(now = None))]
    pub fn submit_repair(
        &self,
        run_id: String,
        candidate: String,
        rationale: String,
        raw: Number,
        reasoning: String,
        now: Option<f64>,
    ) -> Result<Step> {
        Ok(lock(&self.inner)?.submit_repair(&run_id, &candidate, &rationale, raw, &reasoning, now)?.into())
    }

    /// Where a run stands, as a JSON object (the active run when `run_id` is absent; absent when there is none).
    #[uniffi::method(default(run_id = None))]
    pub fn status_json(&self, run_id: Option<String>) -> Result<Option<String>> {
        lock(&self.inner)?.status(run_id.as_deref()).map(|s| to_json(&s)).transpose()
    }

    /// The report of a run, as a JSON object (the active run when `run_id` is absent; absent when there is none).
    #[uniffi::method(default(run_id = None))]
    pub fn report_json(&self, run_id: Option<String>) -> Result<Option<String>> {
        lock(&self.inner)?.report(run_id.as_deref()).map(|r| to_json(&r)).transpose()
    }

    /// What the agent's model reads when its run is over (absent when there is no such run).
    #[uniffi::method(default(run_id = None))]
    pub fn ending(&self, run_id: Option<String>) -> Result<Option<String>> {
        Ok(lock(&self.inner)?.ending(run_id.as_deref()))
    }

    /// The spokes of the star, when the agent is its centre. Fails when it is not.
    pub fn spokes(&self) -> Result<Vec<String>> {
        lock(&self.inner)?.relay().map(CoreRelay::spokes).ok_or_else(not_a_centre)
    }

    /// Add a spoke. Fails when the agent is not the centre of a star. The spokes are not kept in the state files: a restarted centre has the
    /// spokes it is created with.
    pub fn add_spoke(&self, spoke: String) -> Result<()> {
        lock(&self.inner)?.relay_mut().ok_or_else(not_a_centre)?.add_spoke(spoke);
        Ok(())
    }

    /// Remove a spoke. Fails when the agent is not the centre of a star.
    pub fn remove_spoke(&self, spoke: String) -> Result<()> {
        lock(&self.inner)?.relay_mut().ok_or_else(not_a_centre)?.remove_spoke(&spoke);
        Ok(())
    }

    /// The spokes a message from `origin` goes to: all of them but the origin. Fails when the agent is not the centre of a star.
    #[uniffi::method(default(origin = None))]
    pub fn targets(&self, origin: Option<String>) -> Result<Vec<String>> {
        lock(&self.inner)?.relay().map(|relay| relay.targets(origin.as_deref())).ok_or_else(not_a_centre)
    }

    /// Whether the centre knows this run. Fails when the agent is not the centre of a star.
    pub fn knows(&self, run_id: String) -> Result<bool> {
        lock(&self.inner)?.relay().map(|relay| relay.knows(&run_id)).ok_or_else(not_a_centre)
    }

    /// Make the centre forget a run, to keep its state small once the run is over. Fails when the agent is not the centre of a star.
    pub fn forget(&self, run_id: String) -> Result<()> {
        Ok(lock(&self.inner)?.relay_mut().ok_or_else(not_a_centre)?.forget(&run_id)?)
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// the relay
// ---------------------------------------------------------------------------------------------------------------------------

/// The centre of a star, on its own: which messages it passes on to the spokes. See `crate::relay::Relay`.
#[derive(uniffi::Object)]
pub struct Relay {
    inner: Mutex<CoreRelay>,
}

#[uniffi::export]
impl Relay {
    /// A relay that keeps its state in memory.
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> {
        Arc::new(Relay { inner: Mutex::new(CoreRelay::new()) })
    }

    /// A relay that keeps its state in the JSON file `state_path`.
    #[uniffi::constructor]
    pub fn with_state_path(state_path: String) -> Result<Arc<Self>> {
        Ok(Arc::new(Relay { inner: Mutex::new(CoreRelay::with_store(FileStore::new(state_path))?) }))
    }

    pub fn learn(&self, text: String) -> Result<bool> {
        Ok(lock(&self.inner)?.learn(&text)?)
    }

    pub fn should_pass_on(&self, text: String) -> Result<bool> {
        Ok(lock(&self.inner)?.should_pass_on(&text)?)
    }

    /// The messages to pass on for `text`: the text itself, or nothing.
    pub fn receive(&self, text: String) -> Result<Vec<String>> {
        Ok(lock(&self.inner)?.receive(&text)?)
    }

    pub fn knows(&self, run_id: String) -> Result<bool> {
        Ok(lock(&self.inner)?.knows(&run_id))
    }

    pub fn forget(&self, run_id: String) -> Result<()> {
        Ok(lock(&self.inner)?.forget(&run_id)?)
    }

    pub fn spokes(&self) -> Result<Vec<String>> {
        Ok(lock(&self.inner)?.spokes())
    }

    /// Add a spoke. It is not kept in the state file: a restarted relay has the spokes it is created with.
    pub fn add_spoke(&self, spoke: String) -> Result<()> {
        lock(&self.inner)?.add_spoke(spoke);
        Ok(())
    }

    pub fn remove_spoke(&self, spoke: String) -> Result<()> {
        lock(&self.inner)?.remove_spoke(&spoke);
        Ok(())
    }

    /// The spokes a message from `origin` goes to: all of them but the origin.
    #[uniffi::method(default(origin = None))]
    pub fn targets(&self, origin: Option<String>) -> Result<Vec<String>> {
        Ok(lock(&self.inner)?.targets(origin.as_deref()))
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// functions
// ---------------------------------------------------------------------------------------------------------------------------

/// The version of this library (the wheel and the package have it).
#[uniffi::export]
pub fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// The version of the protocol the library speaks.
#[uniffi::export]
pub fn protocol_version() -> String {
    protocol::PROTOCOL_VERSION.to_string()
}

/// What the model reads for the messages received since its last answer (empty when there are none).
#[uniffi::export]
pub fn render_delivery(texts: Vec<String>) -> String {
    guidance::render_delivery(&texts)
}

/// What the model reads for a refused answer: the fault, that nothing was sent, and what to do. After the same refusal twice in a row the
/// model is also told to change the answer, unless the error is one that is settled (see `settled`).
#[uniffi::export]
pub fn refusal(error: ConcordError, repeats: u32) -> String {
    guidance::refusal(&error.into(), repeats)
}

/// What the model reads for a refusal that is not an error of the library.
#[uniffi::export]
pub fn refusal_text(text: String, repeats: u32) -> String {
    guidance::refusal_text(&text, repeats)
}

/// Whether the error says that what was asked is done already (so there is nothing to retry).
#[uniffi::export]
pub fn settled(error: ConcordError) -> bool {
    guidance::settled(&error.into())
}
