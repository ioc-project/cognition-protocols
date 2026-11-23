//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Where the state of a protocol is kept: the `Store` trait is the backend interface, and each backend is a module of its own.
//!
//! * `file`: `FileStore`, a state in one file, written atomically and durably
//! * `memory`: `MemoryStore`, a state in memory, shared by the clones of a store (for tests, and for a state that need not outlive the
//!   process)
//!
//! A protocol crate only ever asks a store for `load` and `save`. Its state is its own type: a backend that writes text (the file)
//! takes a `Codec`, which turns the state into text and back (the format, the defaults a loaded state is merged over); a backend that
//! keeps the value (memory) takes the state type alone.
//!
//! # Adding a backend
//!
//! A new backend (a database, a key-value service, ...) is a new module here, or a type in any crate, that implements `Store`: the
//! protocol crates take any `Store` and need no change. It must:
//!
//! * fail `load` with `StoreErrorKind::Missing` when nothing has been saved, and only then: that is what tells a run to start empty, and
//!   every other failure is a fault;
//! * make one `save` all-or-nothing: a `save` that fails leaves the state that was saved before it;
//! * give, on every handle on the same storage, what the last `save` saved.
//!
//! `conformance::run` checks these on any backend; each backend of this crate runs it in its tests. Locking and path policy are not a
//! store's: see `lock` and `path`.

pub mod conformance;
pub mod file;
pub mod memory;

use std::fmt;

use serde_json::Value;

pub use file::FileStore;
pub use memory::MemoryStore;

/// A backend of the state: where one state is kept.
pub trait Store {
    /// The state it keeps.
    type State;

    /// The saved state.
    ///
    /// # Errors
    ///
    /// `StoreErrorKind::Missing` when nothing has been saved, another kind when what is saved cannot be read.
    fn load(&self) -> Result<Self::State, StoreError>;

    /// Replace the saved state with `state`, all or nothing.
    ///
    /// # Errors
    ///
    /// A `StoreError` when the state cannot be saved; the state saved before is then still the saved state.
    fn save(&mut self, state: &Self::State) -> Result<(), StoreError>;
}

/// How a state is written as text and read back. A protocol writes its own: the format (`serde_json`, or Python's `json.dumps`), and
/// what `decode` adds to what it reads (the defaults of the keys an older state lacks).
pub trait Codec {
    /// The state.
    type State;

    /// The name of the format, for the errors: `JSON`.
    const FORMAT: &'static str;

    /// The text of `state`.
    ///
    /// # Errors
    ///
    /// Why the state cannot be written, in words.
    fn encode(&self, state: &Self::State) -> Result<String, String>;

    /// The state written in `text`.
    ///
    /// # Errors
    ///
    /// Why the text is not a state, in words.
    fn decode(&self, text: &str) -> Result<Self::State, String>;
}

/// A JSON value written by `serde_json` with an indent of two spaces and a final line break.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct JsonCodec;

impl Codec for JsonCodec {
    type State = Value;
    const FORMAT: &'static str = "JSON";

    fn encode(&self, state: &Value) -> Result<String, String> {
        let mut text = serde_json::to_string_pretty(state).map_err(|exc| exc.to_string())?;
        text.push('\n');
        Ok(text)
    }

    fn decode(&self, text: &str) -> Result<Value, String> {
        serde_json::from_str(text).map_err(|exc| exc.to_string())
    }
}

/// Why a store could not load or save.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum StoreErrorKind {
    /// Nothing has been saved. It is not a fault, unlike a saved state that cannot be read: only this one means "start empty".
    Missing,
    /// What is saved cannot be read (an I/O error).
    Unreadable,
    /// What is saved is not a state (the codec refused it).
    Invalid,
    /// The state cannot be written as text (the codec refused it).
    Unencodable,
    /// The state cannot be saved (an I/O error, or a backend that refuses it).
    Unwritable,
}

/// A store that could not load or save: its kind, and what happened, in words.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreError {
    kind: StoreErrorKind,
    message: String,
}

impl StoreError {
    pub fn new(kind: StoreErrorKind, message: impl Into<String>) -> Self {
        StoreError { kind, message: message.into() }
    }

    /// The kind of the error.
    pub fn kind(&self) -> StoreErrorKind {
        self.kind
    }

    /// Whether nothing has been saved (`StoreErrorKind::Missing`).
    pub fn is_missing(&self) -> bool {
        self.kind == StoreErrorKind::Missing
    }

    /// What happened.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for StoreError {}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_json_codec_is_pretty_with_a_final_line_break() {
        assert_eq!(JsonCodec.encode(&json!({"k": [1], "é": null})).unwrap(), "{\n  \"k\": [\n    1\n  ],\n  \"é\": null\n}\n");
        assert_eq!(JsonCodec.decode("{\"b\": 1, \"a\": 2}").unwrap().as_object().unwrap().keys().collect::<Vec<_>>(), ["b", "a"]);
        assert!(JsonCodec.decode("{oops").is_err());
    }

    #[test]
    fn an_error_says_what_happened_and_whether_nothing_was_saved() {
        let e = StoreError::new(StoreErrorKind::Missing, "nothing has been saved");
        assert!(e.is_missing());
        assert_eq!(e.to_string(), "nothing has been saved");
        assert!(!StoreError::new(StoreErrorKind::Invalid, "x").is_missing());
    }
}
