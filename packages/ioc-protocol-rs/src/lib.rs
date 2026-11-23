//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! ioc-protocol: what the cognition protocol libraries (`ioc-concord`, `ioc-accord`) share.
//!
//! Two things, and only the mechanics of each: a protocol crate keeps its own messages, its own state and its own wording, and plugs them
//! in through the traits and functions here.
//!
//! * the message format: reading a message out of what a model wrote (a markdown fence, prose around it, a cut-off text), escaping,
//!   whitespace, the root start tag of an XML document, the short fingerprints that bind a message to its text, and the error a reader
//!   returns (`message::MessageError`, with its `ErrorKind`). A protocol decodes and encodes its own types (`message::Decode`,
//!   `message::Encode`).
//! * the state: where it is kept (`store::Store`, the backend interface, with a file and an in-memory backend), in which format
//!   (`store::Codec`), under which path (`path`), and how writers are serialised (`lock`).
//!
//! Modules:
//!
//! * `text`: the whitespace of the protocols, trimming and squeezing, fences and prose around a message
//! * `check`: the short SHA-256 fingerprint
//! * `xml`: escaping, the root start tag, locating an XML message in a text, and the integrity check of a document
//! * `message`: `MessageError` and its kinds, `Decode`, `Encode`, `read`
//! * `store`: `Store`, `Codec`, `StoreError`, and the backends `store::file` and `store::memory`
//! * `path`: the path of a state file: a free one, or one isolated under a run key
//! * `lock`: an exclusive lock on a file of its own

pub mod check;
pub mod lock;
pub mod message;
pub mod path;
pub mod store;
pub mod text;
pub mod xml;

pub use message::{ErrorKind, MessageError};
pub use store::{Codec, Store, StoreError, StoreErrorKind};

/// The version of the library, as `Cargo.toml` has it.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
