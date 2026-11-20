//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! ioc-concord: the CONCORD protocol as a library.
//!
//! The library does no I/O: it has no transport, no model and no threads, it never prints or exits, and it keeps no global state. You
//! give it the messages that arrive and the time that passes; it hands back the messages to send and what happened.
//!
//! Modules:
//!
//! * `errors`: `ConcordError`: what the library returns on purpose
//! * `protocol`: the protocol version, the default parameters and the outcomes: one place to change them
//! * `xmlutil`: escaping, and the integrity check of a document (computed on its text, never on a parsed copy)
//! * `schema`: the candidate schema: parse, validate, check a proposal, describe it
//! * `engine`: the decision engine: record the messages, evaluate (SELECT / ACCEPT / bottleneck / outcome) from the seeds
//! * `wire`: the messages: build each one (the author's checks), and read each one in the layers of the Order of checks
//! * `store`: the `Store` trait, and `FileStore` (atomic writes) and `MemoryStore` as implementations
//! * `state`: the state of an agent: its runs, and the keys of the items a run is made of
//! * `liveness`: the messages an agent is owed and the timers that wait for them, as functions of the state of a run
//! * `tasks`: what the agent's model must write now, derived from the state
//! * `report`: where a run stands, and the report of the run
//! * `relay`: the centre of a star: which messages it passes on to the spokes
//! * `guidance`: what the agent's model reads: the wording of the run and of each task, the messages of the others, the end, a refusal
//! * `event`: the events an agent reports, typed
//! * `cli`: the `ioc-concord` command as a function (feature `cli`)
//! * `agent`: the node: receive messages in the order of checks, ask again, give up, build what it sends
//! * `ffi`: the API for other languages (feature `uniffi`)
//!
//! # Quick start
//!
//! Two agents on a channel that delivers every message to both. `alice` opens the run, and each agent answers the tasks that `due()`
//! gives it; here the answers are fixed, where an integration would ask a model.
//!
//! ```
//! use ioc_concord::agent::Agent;
//! use ioc_concord::tasks::Task;
//! use ioc_concord::wire::ScoreIn;
//! use serde_json::json;
//!
//! # fn main() -> ioc_concord::Result<()> {
//! let mut agents = vec![Agent::new("cost")?, Agent::new("security")?];
//! let spec = json!({"type": "string", "description": "what one option looks like"});
//!
//! // `cost` opens the run, and is a participant of it
//! let opening = agents[0].start(&["cost", "security"], "which plan?", "no plan", "the best plan", &spec, None, None, None)?;
//! let mut queue: Vec<String> = opening.send;
//!
//! while !queue.is_empty() || agents.iter().any(|a| !a.due().is_empty()) {
//!     // every message goes to every agent
//!     for text in std::mem::take(&mut queue) {
//!         for agent in &mut agents {
//!             queue.extend(agent.receive(&text, None)?.send);
//!         }
//!     }
//!     // what each agent owes now: the content a model would write
//!     for agent in &mut agents {
//!         for task in agent.due() {
//!             let step = match task {
//!                 Task::InitReply(t) => agent.submit_init_reply(&t.run_id, 0, 100, "plan A", "the facts", 90, "why", None)?,
//!                 Task::Score(t) => {
//!                     let scores: Vec<ScoreIn> = t.candidates.iter().map(|c| ScoreIn::new(&c.id, 80, "it holds")).collect();
//!                     agent.submit_score(&t.run_id, &scores, None)?
//!                 }
//!                 Task::Repair(t) => agent.submit_repair(&t.run_id, "plan B", "the facts", 90, "why", None)?,
//!                 _ => continue,
//!             };
//!             queue.extend(step.send);
//!         }
//!     }
//! }
//!
//! let report = agents[1].report(None).expect("a run");
//! assert_eq!(report.outcome.map(|o| o.as_str()), Some("SUCCESS"));
//! # Ok(())
//! # }
//! ```

pub mod agent;
#[cfg(feature = "cli")]
pub mod cli;
pub mod engine;
pub mod errors;
pub mod event;
#[cfg(feature = "uniffi")]
pub mod ffi;
pub mod guidance;
pub mod liveness;
pub mod protocol;
pub mod relay;
pub mod report;
pub mod schema;
pub mod state;
pub mod store;
pub mod tasks;
pub mod wire;
pub mod xmlutil;

pub use errors::{ConcordError, Result};

/// The version of the library, as `Cargo.toml` has it.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(feature = "uniffi")]
uniffi::setup_scaffolding!("ioc_concord");
