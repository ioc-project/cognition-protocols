//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The in-memory backend: a state kept as the value itself, never written as text.
//!
//! A clone shares the state it holds, so a test can give one to an agent and read it, or give it to the agent that replaces it after a
//! restart. A store made with `refusing` fails the saves it is told to, to test what a protocol does when its state cannot be saved.

use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard};

use super::{Store, StoreError, StoreErrorKind};

type Refuse<S> = Arc<dyn Fn(&S) -> bool + Send + Sync>;

/// A state of type `S` kept in memory.
pub struct MemoryStore<S> {
    state: Arc<Mutex<Option<S>>>,
    refuse: Option<Refuse<S>>,
}

impl<S> MemoryStore<S> {
    /// An empty store.
    pub fn new() -> Self {
        MemoryStore { state: Arc::new(Mutex::new(None)), refuse: None }
    }

    /// An empty store whose `save` fails, and keeps what it held, for every state `refuse` says yes to.
    pub fn refusing(refuse: impl Fn(&S) -> bool + Send + Sync + 'static) -> Self {
        MemoryStore { state: Arc::new(Mutex::new(None)), refuse: Some(Arc::new(refuse)) }
    }

    /// The state behind its lock. A thread that panicked while it held the lock left the state whole (it is replaced in one assignment),
    /// so a poisoned lock is no reason to fail.
    fn guard(&self) -> MutexGuard<'_, Option<S>> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl<S: Clone> MemoryStore<S> {
    /// The saved state, if any.
    pub fn get(&self) -> Option<S> {
        self.guard().clone()
    }
}

impl<S> Default for MemoryStore<S> {
    fn default() -> Self {
        MemoryStore::new()
    }
}

impl<S> Clone for MemoryStore<S> {
    fn clone(&self) -> Self {
        MemoryStore { state: Arc::clone(&self.state), refuse: self.refuse.clone() }
    }
}

impl<S: fmt::Debug> fmt::Debug for MemoryStore<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MemoryStore").field("state", &self.state).field("refusing", &self.refuse.is_some()).finish()
    }
}

impl<S: Clone> Store for MemoryStore<S> {
    type State = S;

    fn load(&self) -> Result<S, StoreError> {
        self.get().ok_or_else(|| StoreError::new(StoreErrorKind::Missing, "nothing has been saved"))
    }

    fn save(&mut self, state: &S) -> Result<(), StoreError> {
        if self.refuse.as_ref().is_some_and(|refuse| refuse(state)) {
            return Err(StoreError::new(StoreErrorKind::Unwritable, "the store refused the state"));
        }
        *self.guard() = Some(state.clone());
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::conformance;
    use serde_json::{json, Value};

    #[test]
    fn the_memory_backend_conforms() {
        let store = MemoryStore::<Value>::refusing(|v| v.get("poison").is_some());
        conformance::run(|| store.clone(), json!({"v": 1}), json!({"v": [2, "é"]}), json!({"poison": 1}));
        let typed = MemoryStore::<(u8, String)>::refusing(|v| v.0 == 0);
        conformance::run(|| typed.clone(), (1, "a".into()), (2, "b".into()), (0, "z".into()));
    }

    #[test]
    fn a_clone_shares_what_it_holds() {
        let mut s = MemoryStore::new();
        assert_eq!(s.load().unwrap_err().message(), "nothing has been saved");
        let twin = s.clone();
        s.save(&json!({"v": 1})).unwrap();
        assert_eq!(twin.load().unwrap(), json!({"v": 1}));
        assert_eq!(twin.get(), Some(json!({"v": 1})));
        assert!(format!("{twin:?}").contains("refusing: false"));
    }

    #[test]
    fn a_poisoned_lock_still_gives_the_state() {
        let s = MemoryStore::<u8>::default();
        let mut inner = s.clone();
        inner.save(&3).unwrap();
        let held = s.clone();
        let _ = std::thread::spawn(move || {
            let _guard = held.state.lock().unwrap();
            panic!("poison the lock");
        })
        .join();
        assert_eq!(s.load().unwrap(), 3);
    }
}
