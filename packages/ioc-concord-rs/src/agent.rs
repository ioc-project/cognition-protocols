//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! One agent of a run: the node that every participant runs.
//!
//! The node holds no I/O, no clock of its own and no model. The caller feeds it what happens and sends what it hands back:
//!
//! * `agent.receive(text, now)`: a message arrived (any text: what is not CONCORD is ignored)
//! * `agent.due()`: what the agent's model must write now: a list of tasks
//! * `agent.submit_init_reply(..)`: the model's answer to a task
//! * `agent.status(run_id)`: where the run stands
//!
//! Every call that changes something returns a `Step`: `step.send` are the messages to broadcast, as text, and `step.events` say what
//! happened, for a log. Nothing is ever addressed to one agent: every output is a broadcast. What is mechanical is done by the node
//! (the order of checks, the bookkeeping, the decisions of the engine, building each message with its `check`); the model supplies
//! only content. A task is derived from the state, not queued: asking `due()` twice gives the same answer, and after a restart from
//! the store it still gives the right one.
//!
//! The state is plain data (see state.rs), saved through the `Store` after every call that changes it. The clock and the source of
//! UUIDs are injected, with the system's as default, so that a run can be replayed.

use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use serde_json::{Map, Value};

use crate::engine::{add_anchor, add_proposal, add_ratings, evaluate, EvalState, Rating};
use crate::errors::{ConcordError, Result};
use crate::event::EventKind;
use crate::guidance::render_task;
use crate::liveness::{earliest_deadline, expired_keys};
use crate::protocol::Num;
use crate::relay::Relay;
use crate::report::{report_of, status_of, Report, Status};
use crate::state::{item_key, AgentState, Role, Run, RunStatus};
use crate::store::Store;
use crate::tasks::{tasks_of, to_rate, Task};
mod node;

use crate::wire::{build_init, build_init_reply, build_repair, build_score, new_uuid, parse_message, Body, InitParams, ScoreIn};
use node::Node;

/// What happened during a call, for a log: the name of the event, the time, and details.
#[derive(Debug, Clone, PartialEq)]
pub struct Event {
    /// The name of the event.
    pub event: String,
    /// When it happened.
    pub at: f64,
    /// What happened, typed.
    pub kind: EventKind,
    /// The fields of `kind` other than the name, as JSON.
    pub details: Map<String, Value>,
}

impl Serialize for Event {
    /// One flat object: `event`, `at`, then the details.
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(Some(self.details.len() + 2))?;
        map.serialize_entry("event", &self.event)?;
        map.serialize_entry("at", &self.at)?;
        for (key, value) in &self.details {
            map.serialize_entry(key, value)?;
        }
        map.end()
    }
}

impl Event {
    /// A detail of the event.
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.details.get(key)
    }

    /// A detail of the event, when it is text.
    pub fn str(&self, key: &str) -> Option<&str> {
        self.get(key).and_then(Value::as_str)
    }
}

/// What a call produced:
///
/// * `send`: the messages to broadcast (text)
/// * `forward`: the messages to pass on when the agent is the centre of a star (see relay.rs: the text received, unchanged, or nothing)
/// * `accepted`: the texts received that the agent took (it joined a run or recorded the message), which its model should read before
///   its next task (see guidance.rs)
/// * `events`: what happened, for a log
#[derive(Debug, Clone, PartialEq)]
#[must_use = "`send` holds the messages to broadcast: a step that is dropped is a message that is never sent"]
pub struct Step {
    pub now: f64,
    pub send: Vec<String>,
    pub forward: Vec<String>,
    pub accepted: Vec<String>,
    pub events: Vec<Event>,
}

impl Step {
    fn new(now: f64) -> Self {
        Step { now, send: Vec::new(), forward: Vec::new(), accepted: Vec::new(), events: Vec::new() }
    }

    /// Record that `kind` happened.
    pub fn note(&mut self, kind: EventKind) {
        let mut details = match serde_json::to_value(&kind) {
            Ok(Value::Object(map)) => map,
            _ => Map::new(),
        };
        let event = match details.shift_remove("event") {
            Some(Value::String(name)) => name,
            _ => String::new(),
        };
        self.events.push(Event { event, at: self.now, kind, details });
    }

    /// Whether an event of that name happened.
    pub fn has(&self, event: &str) -> bool {
        self.events.iter().any(|e| e.event == event)
    }

    /// The events of that name.
    pub fn events_named(&self, event: &str) -> Vec<&Event> {
        self.events.iter().filter(|e| e.event == event).collect()
    }
}

fn system_clock() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or_else(|before| -before.duration().as_secs_f64(), |d| d.as_secs_f64())
}

/// The pieces an agent is made of, besides its id.
pub struct AgentBuilder {
    me: String,
    store: Option<Box<dyn Store + Send>>,
    clock: Box<dyn Fn() -> f64 + Send>,
    uuid_fn: Box<dyn FnMut() -> String + Send>,
    relay: Option<Relay>,
}

impl AgentBuilder {
    /// Keep the state between calls in `store` (without one it stays in memory only).
    pub fn store(mut self, store: impl Store + Send + 'static) -> Self {
        self.store = Some(Box::new(store));
        self
    }

    /// The clock: the time in seconds.
    pub fn clock(mut self, clock: impl Fn() -> f64 + Send + 'static) -> Self {
        self.clock = Box::new(clock);
        self
    }

    /// The source of UUIDs, as text.
    pub fn uuid_fn(mut self, uuid_fn: impl FnMut() -> String + Send + 'static) -> Self {
        self.uuid_fn = Box::new(uuid_fn);
        self
    }

    /// Make the agent the centre of a star (see relay.rs): besides what it does as an agent, it says in `step.forward` which of the
    /// messages it receives to pass on to the spokes.
    pub fn relay(mut self, relay: Relay) -> Self {
        self.relay = Some(relay);
        self
    }

    /// The agent, with the state its store holds. Fails with `State` when the id is not an agent id, or the saved state cannot be
    /// loaded or is not this agent's.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the id is not an agent id, or the saved state cannot be loaded or is not this agent's.
    pub fn build(self) -> Result<Agent> {
        let state = match &self.store {
            None => AgentState::new(&self.me)?,
            Some(store) => match store.load() {
                // nothing saved yet; a state that cannot be read is an error, never a fresh start
                Err(ConcordError::MissingState(_)) => AgentState::new(&self.me)?,
                Err(other) => return Err(other),
                Ok(value) => {
                    let state: AgentState = serde_json::from_value(value)
                        .map_err(|exc| ConcordError::state(format!("the saved state is not the state of an agent ({exc})")))?;
                    if state.me != self.me {
                        return Err(ConcordError::state(format!(
                            "the saved state belongs to {}, not to {}",
                            crate::protocol::quote(&state.me),
                            crate::protocol::quote(&self.me)
                        )));
                    }
                    state
                }
            },
        };
        Ok(Agent { me: self.me, store: self.store, clock: self.clock, uuid_fn: self.uuid_fn, relay: self.relay, state })
    }
}

/// The node of an agent. See the module documentation.
///
/// An `Agent` is `Send`, so it can move to another thread or task, but not `Sync`: its clock and source of UUIDs are `FnMut`. To share
/// one, put it behind a `Mutex`.
pub struct Agent {
    me: String,
    store: Option<Box<dyn Store + Send>>,
    clock: Box<dyn Fn() -> f64 + Send>,
    uuid_fn: Box<dyn FnMut() -> String + Send>,
    relay: Option<Relay>,
    /// The state of the agent: every run it knows.
    pub state: AgentState,
}

impl Agent {
    /// An agent with no store, the system clock and random UUIDs. Fails with `State` when `me` is not an agent id.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when `me` is not an agent id.
    pub fn new(me: &str) -> Result<Agent> {
        Agent::builder(me).build()
    }

    /// The pieces of an agent, to choose its store, clock, source of UUIDs and relay.
    pub fn builder(me: &str) -> AgentBuilder {
        AgentBuilder { me: me.to_string(), store: None, clock: Box::new(system_clock), uuid_fn: Box::new(new_uuid), relay: None }
    }

    pub fn me(&self) -> &str {
        &self.me
    }

    /// The relay of the agent, when it is the centre of a star.
    pub fn relay(&self) -> Option<&Relay> {
        self.relay.as_ref()
    }

    pub fn relay_mut(&mut self) -> Option<&mut Relay> {
        self.relay.as_mut()
    }

    // ------------------------------------------------------------------
    // state
    // ------------------------------------------------------------------

    fn save(&mut self) -> Result<()> {
        match &mut self.store {
            Some(store) => {
                let value =
                    serde_json::to_value(&self.state).map_err(|exc| ConcordError::state(format!("the state is not JSON ({exc})")))?;
                store.save(&value)
            }
            None => Ok(()),
        }
    }

    /// The time of a call: the caller's, or the clock's. A time that is not a finite number would make every deadline `null` in the saved
    /// state, which reads back as "no timer", so it is refused.
    fn now(&self, now: Option<f64>) -> Result<f64> {
        let now = now.unwrap_or_else(|| (self.clock)());
        if now.is_finite() {
            Ok(now)
        } else {
            Err(ConcordError::state("the time is not a finite number"))
        }
    }

    /// The active run `run_id` of this agent as a participant, or the reason it is not one.
    fn participant_run<'a>(state: &'a mut AgentState, run_id: &str, what: &str) -> Result<&'a mut Run> {
        Self::check_participant(state, run_id, what)?;
        Ok(state.runs.get_mut(run_id).expect("the run was just found"))
    }

    fn check_participant<'a>(state: &'a AgentState, run_id: &str, what: &str) -> Result<&'a Run> {
        let Some(run) = state.runs.get(run_id) else {
            return Err(ConcordError::state(format!("{what}: the agent knows no run {}", crate::protocol::quote(run_id))));
        };
        if run.status != RunStatus::Active {
            return Err(ConcordError::state(format!("{what}: the run {run_id} has {}", run.status.as_str())));
        }
        if run.role != Role::Participant {
            return Err(ConcordError::state(format!("{what}: the agent only opened the run {run_id}, it is not a participant")));
        }
        Ok(run)
    }

    // ------------------------------------------------------------------
    // what arrives
    // ------------------------------------------------------------------

    /// Handle `text`, whatever it is, in the order of checks of the spec. `now` is the time, when the caller does not want the clock's.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save the state, or of the relay when it cannot save a run.
    ///
    /// When it fails the state of the agent is as it was, in memory and in its store. The relay has a store of its own and may already know a
    /// run that the call learned, which only means that the centre passes on the messages of that run: calling again does what it should.
    pub fn receive(&mut self, text: &str, now: Option<f64>) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            let mut step = Step::new(self.now(now)?);
            if let Some(relay) = &mut self.relay {
                step.forward = relay.receive(text)?; // first, and whatever the agent does with the message: it is mechanical
            }
            {
                let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
                node.handle(&mut self.state, text, &mut step)?;
            }
            if step.events.iter().any(|e| e.event == "joined" || e.event == "recorded") {
                step.accepted.push(text.to_string());
            }
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    /// Time has passed: for every timer that has expired, ask again, or, when `retries` requests have not been answered, give up on
    /// the author: the run ends `ABORTED` with the reason "missing-agent" and the cancel goes out.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save the state.
    ///
    /// When it fails the state of the agent is as it was, in memory and in its store.
    pub fn tick(&mut self, now: Option<f64>) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            let mut step = Step::new(self.now(now)?);
            {
                let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
                for run in self.state.runs.values_mut() {
                    if run.status != RunStatus::Active {
                        continue;
                    }
                    node.sync_timers(run, &step, None);
                    for key in expired_keys(run, step.now) {
                        let Some(t) = run.timers.get(&key) else { break }; // the run was aborted
                        if i64::from(t.requests) >= run.params.retries {
                            let author = t.author.clone();
                            node.abort(run, &mut step, "missing-agent", Some(&author), None)?;
                            break;
                        }
                        node.request(run, &key, &mut step, None, true)?;
                    }
                }
            }
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    /// The time of the earliest timer of an active run, or None: when to call `tick` next.
    #[must_use]
    pub fn next_wakeup(&self) -> Option<f64> {
        earliest_deadline(self.state.runs.values())
    }

    // ------------------------------------------------------------------
    // what the model must write
    // ------------------------------------------------------------------

    /// The text that tells the agent's model what a task of `due()` asks of it (see guidance.rs).
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the agent knows no such run, the run is over, or the agent only opened it.
    pub fn instructions(&self, task: &Task) -> Result<String> {
        let run = Self::check_participant(&self.state, task.run_id(), "instructions")?;
        Ok(render_task(run, &self.me, task))
    }

    /// The text for the task of this `kind` (see `Task::kind`) that the agent owes in `run_id` now, and in this `round` when it is a
    /// repair: `instructions` for a caller that holds the identity of a task, not the task.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the agent owes no such task now (a repair of an earlier round is not owed), and as `instructions`.
    pub fn instructions_for(&self, kind: &str, run_id: &str, round: Option<u32>) -> Result<String> {
        let task = self
            .due()
            .into_iter()
            .find(|t| t.kind() == kind && t.run_id() == run_id && t.round() == round)
            .ok_or_else(|| ConcordError::state("instructions: the agent owes no such task now"))?;
        self.instructions(&task)
    }

    /// The tasks the agent's model owes now:
    ///
    /// * `InitReply`: anchors, a seed and a score for it
    /// * `Score`: a rating for each candidate, on the same scale
    /// * `Repair`: a better candidate, which only the bottleneck of the round writes
    ///
    /// The numbers in a task are normalized scores between 0 and 1, as text with at most 6 decimal places.
    #[must_use]
    pub fn due(&self) -> Vec<Task> {
        tasks_of(self.state.active_run(), &self.me)
    }

    // ------------------------------------------------------------------
    // what the model wrote
    // ------------------------------------------------------------------

    /// Open a run: build the `<concord-init>`, which the caller broadcasts, and keep it to send again. This agent is the initiator; it
    /// is a participant too when it is listed in `participants`, and then it owes an init reply like the others. `spec` is the
    /// candidate schema as a JSON object, `params` any of the parameters (see `wire::build_init`). A fresh run id is made when none is
    /// given. Fails with `State` when the agent is in a run already, and `InvalidMessage` when the run is not valid.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the agent is in a run already, and `ConcordError::InvalidMessage` when the run is not valid.
    ///
    /// When it fails the state of the agent is as it was, in memory and in its store. The relay has a store of its own and may already know the
    /// run, which only means that the centre passes on the messages of that run: calling again does what it should.
    #[allow(clippy::too_many_arguments)]
    pub fn start<S: AsRef<str>>(
        &mut self,
        participants: &[S],
        context: &str,
        x_reject: &str,
        x_ideal: &str,
        spec: &Value,
        params: Option<&InitParams>,
        run_id: Option<&str>,
        now: Option<f64>,
    ) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            if self.state.active_run().is_some() {
                return Err(ConcordError::state("start: the agent is in a run already"));
            }
            let run_id = run_id.map_or_else(|| (self.uuid_fn)(), str::to_string);
            let uid = (self.uuid_fn)();
            let text = build_init(&self.me, participants, context, x_reject, x_ideal, spec, params, Some(&run_id), Some(&uid))?;
            let init = parse_message(&text, None)?;
            let mut step = Step::new(self.now(now)?);
            {
                let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
                let run = node.open_run(&mut self.state, &init, &text)?;
                step.send.push(text.clone());
                let Body::Init(body) = &init.body else { unreachable!("an init was built") };
                step.note(EventKind::Started { participants: body.participants.clone(), run_id: init.run_id.clone(), role: run.role });
                if let Some(relay) = &mut self.relay {
                    relay.learn(&text)?; // the centre sends this init to the spokes: what comes back for the run is passed on
                }
                node.advance(run, &mut step)?;
            }
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    /// Answer the `InitReply` task: the anchors, the seed candidate with its rationale, and the agent's own score for it with its
    /// reasoning. Scores are numbers from 0 to 100 with at most 6 decimal places (as text, int or float). Fails with `InvalidMessage`
    /// when something is invalid, and nothing is sent or recorded: the caller fixes it and submits again.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the run is not one the agent takes part in, or the init reply is already sent, and
    /// `ConcordError::InvalidMessage` when something is invalid: nothing is sent or recorded, so the caller can fix it and submit again.
    #[allow(clippy::too_many_arguments)]
    pub fn submit_init_reply(
        &mut self,
        run_id: &str,
        raw_reject: impl Into<Num>,
        raw_ideal: impl Into<Num>,
        candidate: &str,
        rationale: &str,
        raw: impl Into<Num>,
        reasoning: &str,
        now: Option<f64>,
    ) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            let step = Step::new(self.now(now)?);
            let run = Self::participant_run(&mut self.state, run_id, "submit_init_reply")?;
            let key = item_key("init-reply", &self.me, None);
            if run.sent.contains_key(&key) {
                return Err(ConcordError::state("submit_init_reply: this agent has already sent its init reply"));
            }
            let uid = (self.uuid_fn)();
            let text = build_init_reply(
                &self.me,
                run_id,
                &raw_reject.into(),
                &raw_ideal.into(),
                candidate,
                rationale,
                &raw.into(),
                reasoning,
                Some(&run.schema),
                Some(&uid),
            )?;
            let msg = parse_message(&text, Some(&run.schema))?;
            let Body::InitReply(reply) = &msg.body else { unreachable!("an init reply was built") };
            let p = &reply.proposal;
            add_anchor(&mut run.engine, &self.me, reply.raw_reject, reply.raw_ideal)?;
            add_proposal(&mut run.engine, &self.me, &p.id, &p.candidate, &p.rationale, p.raw, &p.reasoning)?;
            run.keep_sent(&key, &text)?;
            let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
            let step = node.sent(run, &text, "init-reply", step)?;
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    /// Answer the `Score` task: `scores` are the candidates the task listed, each rated. Fails with `State` when no score is owed,
    /// and `InvalidMessage` when the list is not the one owed or something is invalid.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the run is not one the agent takes part in or no score is owed, and `ConcordError::InvalidMessage` when
    /// the list is not the one owed or something is invalid.
    pub fn submit_score(&mut self, run_id: &str, scores: &[ScoreIn], now: Option<f64>) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            let step = Step::new(self.now(now)?);
            let run = Self::participant_run(&mut self.state, run_id, "submit_score")?;
            let owed = to_rate(run, &self.me);
            if owed.is_empty() {
                return Err(ConcordError::state("submit_score: this agent owes no score now"));
            }
            let given: Vec<&str> = scores.iter().map(|s| s.id.as_str()).collect();
            let (mut a, mut b) = (given.clone(), owed.iter().map(String::as_str).collect::<Vec<_>>());
            a.sort_unstable();
            b.sort_unstable();
            if a != b {
                return Err(ConcordError::invalid_message(format!(
                    "a score message covers exactly the candidates not rated yet: owed {}; given {}",
                    owed.join(", "),
                    if given.is_empty() { "none".to_string() } else { given.join(", ") }
                )));
            }
            let uid = (self.uuid_fn)();
            let text = build_score(&self.me, run_id, scores, Some(&uid))?;
            let msg = parse_message(&text, Some(&run.schema))?;
            let Body::Score(body) = &msg.body else { unreachable!("a score was built") };
            let ratings: Vec<Rating> =
                body.scores.iter().map(|e| Rating { id: e.id.clone(), raw: e.raw, reasoning: e.reasoning.clone() }).collect();
            add_ratings(&mut run.engine, &self.me, &ratings)?;
            for cid in &owed {
                run.keep_sent(&item_key("score", &self.me, Some(cid)), &text)?;
            }
            let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
            let step = node.sent(run, &text, "score", step)?;
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    /// Answer the `Repair` task: a better candidate, with its rationale and the agent's own score for it with its reasoning. The round
    /// is the one the task named. Fails with `State` when no repair is owed from this agent, and `InvalidMessage` when something is
    /// invalid.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the run is not one the agent takes part in or no repair is owed from it, and
    /// `ConcordError::InvalidMessage` when something is invalid.
    pub fn submit_repair(
        &mut self,
        run_id: &str,
        candidate: &str,
        rationale: &str,
        raw: impl Into<Num>,
        reasoning: &str,
        now: Option<f64>,
    ) -> Result<Step> {
        // a call that fails leaves the state of the agent as it was, in memory and in its store; a relay keeps its own store and may already know
        // a run that the call learned, which changes nothing but what it passes on
        let backup = self.state.clone();
        let result = (|| -> Result<Step> {
            let step = Step::new(self.now(now)?);
            let run = Self::participant_run(&mut self.state, run_id, "submit_repair")?;
            let ev = evaluate(&run.engine);
            let round = match (ev.state, &ev.bottleneck, ev.round) {
                (EvalState::Repair, Some(b), Some(round)) if *b == self.me => round,
                _ => return Err(ConcordError::state("submit_repair: no repair is owed from this agent now")),
            };
            let uid = (self.uuid_fn)();
            let text = build_repair(&self.me, run_id, round, candidate, rationale, &raw.into(), reasoning, Some(&run.schema), Some(&uid))?;
            let msg = parse_message(&text, Some(&run.schema))?;
            let Body::Repair(p) = &msg.body else { unreachable!("a repair was built") };
            add_proposal(&mut run.engine, &self.me, &p.id, &p.candidate, &p.rationale, p.raw, &p.reasoning)?;
            run.keep_sent(&item_key("repair", &self.me, Some(&p.id)), &text)?;
            let mut node = Node { me: &self.me, new_uuid: &mut *self.uuid_fn };
            let step = node.sent(run, &text, "repair", step)?;
            self.save()?;
            Ok(step)
        })();
        if result.is_err() {
            self.state = backup;
        }
        result
    }

    // ------------------------------------------------------------------
    // where the run stands
    // ------------------------------------------------------------------

    fn run_of(&self, run_id: Option<&str>) -> Option<&Run> {
        self.state.runs.get(run_id.or(self.state.active.as_deref())?)
    }

    /// Where a run stands for this agent (the active run when `run_id` is None; None when there is none): its `status`, the `outcome`
    /// this agent has computed (None before), and the engine's view.
    #[must_use]
    pub fn status(&self, run_id: Option<&str>) -> Option<Status> {
        self.run_of(run_id).map(status_of)
    }

    /// What the agent's model reads when a run is over: `guidance::render_end` of its report (the active run when `run_id` is None; None
    /// when there is none).
    #[must_use]
    pub fn ending(&self, run_id: Option<&str>) -> Option<String> {
        self.report(run_id).map(|report| crate::guidance::render_end(&self.me, &report))
    }

    /// The report of a run (the active one when `run_id` is None; None when there is none), as the spec describes it (see report.rs).
    #[must_use]
    pub fn report(&self, run_id: Option<&str>) -> Option<Report> {
        self.run_of(run_id).map(|run| report_of(run, &self.me))
    }
}
