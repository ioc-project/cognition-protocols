//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Where a run's state is kept. The library asks only for `load` and `save`: how and where is the caller's choice (a file, a
//! database, memory). Locking and path policy belong to the caller too: a store makes one `save` all-or-nothing, and nothing more.
//!
//! The backends are `ioc_protocol::store`'s, shared with the other protocol libraries: `FileStore` (atomic writes, the state as JSON
//! with an indent of two spaces and a final line break) and `MemoryStore`. Any backend of `ioc_protocol::store::Store` whose state is a
//! JSON value is a `Store` here, its errors made `ConcordError`s; a type can also implement `Store` directly.

use serde_json::Value;

use crate::errors::Result;

/// A state kept in one JSON file. A save writes a temporary file next to it and renames it over the old one, so a reader (or a crash)
/// never sees half a state. The rename gives the file a new identity: serialise writers with a lock on a separate file
/// (`ioc_protocol::lock`), not on this one.
pub type FileStore = ioc_protocol::store::FileStore<ioc_protocol::store::JsonCodec>;

/// A state kept in memory. A clone shares the state it holds, so a test can give one to an agent and read it, or give it to the
/// agent that replaces it after a restart.
pub type MemoryStore = ioc_protocol::store::MemoryStore<Value>;

/// What a state store must offer.
pub trait Store {
    /// The saved state. Fails with `MissingState` if nothing has been saved, and `State` if what is saved cannot be read.
    fn load(&self) -> Result<Value>;

    /// Replace the saved state with `state`.
    fn save(&mut self, state: &Value) -> Result<()>;
}

/// Every backend of `ioc_protocol` that keeps a JSON value: `MissingState` when nothing is saved, `State` for any other failure.
impl<T: ioc_protocol::store::Store<State = Value>> Store for T {
    fn load(&self) -> Result<Value> {
        Ok(ioc_protocol::store::Store::load(self)?)
    }

    fn save(&mut self, state: &Value) -> Result<()> {
        Ok(ioc_protocol::store::Store::save(self, state)?)
    }
}
