//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The centre of a star: which messages it passes on to the spokes (see Transport in the spec).
//!
//! On a broadcast channel and on a full mesh nothing relays. In a star every agent, a spoke, has a link to one central agent, the
//! centre. A spoke sends each message to the centre only; the centre sends the messages of the runs it knows on to every spoke except
//! the one a message came from, and its own messages go to every spoke. Passing on is mechanical: the centre never changes a message,
//! and it does not depend on what the centre does with it as an agent. It passes a message on even when it drops it as a duplicate,
//! ignores it, or has ended or aborted the run, since the spokes behind it still need it.
//!
//! A `Relay` makes that decision, and keeps what it needs for it: the ids of the runs it knows, and the spokes. A centre that does not
//! take part in any run is just a `Relay`; one that does gives it to its `Agent` (`Agent::with_relay`), which then says in
//! `step.forward` what to pass on. The relay never sends anything: the caller does, to the spokes that `targets` names, which are
//! every spoke except the one a message came from, and every spoke for an own message of the agent.
//!
//! The spokes are the links of the centre, which only the transport knows (the init lists the agents that negotiate, not the links,
//! and a spoke need not be listed in it), so the caller registers them: `add_spoke` when one connects, `remove_spoke` when it goes. A
//! spoke is any name that the caller can send through: a connection id, a socket id. They are not saved, since they are live links
//! that connect again after a restart, unlike the run ids.
//!
//! The centre knows a run once it has passed on that run's `<concord-init>`, bare or inside a `<concord-resend>`, since the spokes
//! learn the run only from that message. It passes on a message of any other run only if it knows it. To decide it reads only the name
//! and the `run_id` of the outermost element and, for a `<concord-resend>`, the name of the element inside: not the version, the check,
//! the sender or what the message says. So it also passes on a message of a version it does not speak, and a name it has never heard
//! of. It keeps the run ids for as long as it keeps the state of the run, ended runs included, so that errors and re-sends after the
//! end still reach the spokes: `forget` is how the caller says a run's state is dropped.

use indexmap::{IndexMap, IndexSet};
use serde::{Deserialize, Serialize};

use crate::errors::{ConcordError, Result};
use crate::protocol::is_uuid;
use crate::store::Store;
use crate::wire::{locate, peek, INIT, RESEND};

/// What the relay reads of a message: the name of the outermost element, the name of the element inside a resend, the run id.
struct Read {
    tag: String,
    inner: Option<String>,
    run_id: String,
}

/// The state of a relay, saved when a run is learned or forgotten: `{"runs": {run id: true}}`. The ids are a set, kept as a map so
/// that the state is JSON.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RelayState {
    pub runs: IndexMap<String, bool>,
}

/// The relay of a centre. `store` keeps the run ids between calls (None keeps them in memory only).
pub struct Relay {
    store: Option<Box<dyn Store + Send>>,
    pub state: RelayState,
    spokes: IndexSet<String>, // in the order they were added; not part of the state
}

impl Default for Relay {
    fn default() -> Self {
        Relay { store: None, state: RelayState::default(), spokes: IndexSet::new() }
    }
}

impl Relay {
    /// A relay that keeps its run ids in memory only.
    pub fn new() -> Self {
        Self::default()
    }

    /// A relay that keeps its run ids in `store`, and starts from what the store holds. A state that cannot be read is an error: the
    /// runs would be forgotten.
    ///
    /// # Errors
    ///
    /// `ConcordError::State` when the saved state cannot be read, or holds no runs.
    pub fn with_store(store: impl Store + Send + 'static) -> Result<Self> {
        let state = match store.load() {
            Ok(value) => serde_json::from_value(value).map_err(|_| ConcordError::state("the saved relay state holds no runs"))?,
            Err(ConcordError::MissingState(_)) => RelayState::default(), // nothing saved yet
            Err(other) => return Err(other),
        };
        Ok(Relay { store: Some(Box::new(store)), state, spokes: IndexSet::new() })
    }

    fn save(&mut self) -> Result<()> {
        match &mut self.store {
            Some(store) => {
                store.save(&serde_json::to_value(&self.state).map_err(|exc| ConcordError::state(format!("the state is not JSON ({exc})")))?)
            }
            None => Ok(()),
        }
    }

    /// What the relay reads of `text`, or None when it is not a CONCORD message with a run id of the right form.
    fn read(text: &str) -> Option<Read> {
        let info = peek(locate(text).ok()?)?;
        let run_id = info.attr("run_id").filter(|r| is_uuid(r))?.to_string();
        Some(Read { tag: info.tag.clone(), inner: info.inner.as_ref().map(|i| i.tag.clone()), run_id })
    }

    /// Remember the run of a message that has been read, if it is that run's init. Returns whether it is.
    fn learn_read(&mut self, read: Option<&Read>) -> Result<bool> {
        let Some(read) = read else { return Ok(false) };
        let opens = read.tag == INIT || (read.tag == RESEND && read.inner.as_deref() == Some(INIT));
        if opens && !self.state.runs.contains_key(&read.run_id) {
            self.state.runs.insert(read.run_id.clone(), true);
            self.save()?;
        }
        Ok(opens)
    }

    /// Remember the run of `text` if it is that run's init, bare or inside a resend. Returns whether `text` is such an init: the centre
    /// passes on, or sends, the message that makes it know the run.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save the run.
    pub fn learn(&mut self, text: &str) -> Result<bool> {
        self.learn_read(Self::read(text).as_ref())
    }

    /// Whether the centre passes `text` on to the spokes: it is the init of a run, which it then knows, or a message of a run it knows.
    /// Whatever the centre does with the message as an agent changes nothing.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save a run it learns.
    pub fn should_pass_on(&mut self, text: &str) -> Result<bool> {
        let read = Self::read(text);
        Ok(self.learn_read(read.as_ref())? || read.is_some_and(|r| self.state.runs.contains_key(&r.run_id)))
    }

    /// What to pass on for `text`, as a list: the text itself, unchanged, or nothing.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save a run it learns.
    pub fn receive(&mut self, text: &str) -> Result<Vec<String>> {
        Ok(if self.should_pass_on(text)? { vec![text.to_string()] } else { Vec::new() })
    }

    /// Whether the centre knows the run `run_id`.
    pub fn knows(&self, run_id: &str) -> bool {
        self.state.runs.contains_key(run_id)
    }

    /// Drop a run: the centre no longer passes on its messages. Call it when the state of the run is dropped, not when it ends.
    ///
    /// # Errors
    ///
    /// The error of the store when it cannot save.
    pub fn forget(&mut self, run_id: &str) -> Result<()> {
        if self.state.runs.shift_remove(run_id).is_some() {
            self.save()?;
        }
        Ok(())
    }

    /// The spokes, in the order they were added.
    pub fn spokes(&self) -> Vec<String> {
        self.spokes.iter().cloned().collect()
    }

    /// Register a spoke: a link of the centre, as a name that the caller can send through. Adding one twice changes nothing.
    pub fn add_spoke(&mut self, spoke: impl Into<String>) {
        self.spokes.insert(spoke.into());
    }

    /// Drop a spoke that is gone. A spoke that was not registered changes nothing.
    pub fn remove_spoke(&mut self, spoke: &str) {
        self.spokes.shift_remove(spoke);
    }

    /// The spokes to send a message to: every spoke except `origin`, the one the message came from. With no origin, which is the case
    /// for an own message of the agent, every spoke. An origin that is not registered leaves them all.
    pub fn targets(&self, origin: Option<&str>) -> Vec<String> {
        self.spokes.iter().filter(|s| Some(s.as_str()) != origin).cloned().collect()
    }
}
