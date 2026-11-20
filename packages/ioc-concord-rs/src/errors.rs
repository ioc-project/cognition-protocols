//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the library returns on purpose. A caller matches on `ConcordError` and decides how to report it: the library never prints
//! or exits.

use std::fmt;

/// Every error the library raises on purpose. `code()` is a stable, machine-readable name for the kind of error; `to_string()`
/// says what is wrong, in words an agent can act on.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ConcordError {
    /// A candidate schema (or one of its keywords) is not valid or not supported.
    Schema(String),
    /// The parameters of a run are not valid (too few agents, a threshold out of range, ...).
    InvalidRun(String),
    /// A message breaks a rule of the protocol: it is not well-formed, lacks a field, or a value is out of range. The text is what
    /// goes into a `<concord-error reason="invalid">`.
    InvalidMessage(String),
    /// A saved state cannot be loaded, or a call does not fit the state of the run.
    State(String),
    /// Nothing has been saved yet. It is not a fault, unlike a saved state that cannot be read: only this one means "start empty".
    MissingState(String),
}

/// The result type of the library.
pub type Result<T> = std::result::Result<T, ConcordError>;

impl ConcordError {
    /// A stable, machine-readable name for the kind of error.
    pub fn code(&self) -> &'static str {
        match self {
            ConcordError::Schema(_) => "invalid_schema",
            ConcordError::InvalidRun(_) => "invalid_run",
            ConcordError::InvalidMessage(_) => "invalid_message",
            ConcordError::State(_) => "state_error",
            ConcordError::MissingState(_) => "missing_state",
        }
    }

    /// What is wrong, without the kind.
    pub fn message(&self) -> &str {
        match self {
            ConcordError::Schema(m)
            | ConcordError::InvalidRun(m)
            | ConcordError::InvalidMessage(m)
            | ConcordError::State(m)
            | ConcordError::MissingState(m) => m,
        }
    }

    pub fn schema(message: impl Into<String>) -> Self {
        ConcordError::Schema(message.into())
    }

    pub fn invalid_run(message: impl Into<String>) -> Self {
        ConcordError::InvalidRun(message.into())
    }

    pub fn invalid_message(message: impl Into<String>) -> Self {
        ConcordError::InvalidMessage(message.into())
    }

    pub fn state(message: impl Into<String>) -> Self {
        ConcordError::State(message.into())
    }

    pub fn missing_state(message: impl Into<String>) -> Self {
        ConcordError::MissingState(message.into())
    }

    /// Whether this is a `State` or `MissingState` error: the answer is already given or not the agent's, and asking again does nothing.
    pub fn is_state(&self) -> bool {
        matches!(self, ConcordError::State(_) | ConcordError::MissingState(_))
    }
}

impl fmt::Display for ConcordError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for ConcordError {}
