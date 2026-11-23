//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The conformance suite of a backend: what every `Store` must do, as one function that a test of the backend calls.
//!
//! It panics on the first rule the backend breaks, like an `assert!`: call it from a test, never from code that runs in production.

use std::fmt::Debug;

use super::{Store, StoreErrorKind};

/// Check the rules of a backend (see `store`) on fresh, empty storage.
///
/// `open` gives a handle on that storage, the same storage on every call (a file store of the same path, a clone of a memory store).
/// `first` and `second` are two different states; `refused` is a state that the backend under test fails to save (a codec or a store
/// made to refuse it), so that a failed save can be checked.
///
/// The rules: loading before any save fails with `Missing`; a saved state loads back, from this handle and from another one; a save
/// replaces the state; a save that fails leaves the state saved before it.
///
/// # Panics
///
/// When the backend breaks a rule.
pub fn run<S, F>(open: F, first: S::State, second: S::State, refused: S::State)
where
    S: Store,
    S::State: PartialEq + Debug,
    F: Fn() -> S,
{
    assert_ne!(first, second, "the conformance suite needs two different states");
    let mut store = open();

    // load missing
    let err = store.load().expect_err("a load before any save must fail");
    assert_eq!(err.kind(), StoreErrorKind::Missing, "a load before any save must fail with Missing, not: {err}");

    // save, then load back, from this handle and another
    store.save(&first).unwrap_or_else(|e| panic!("a save must succeed: {e}"));
    assert_eq!(store.load().unwrap_or_else(|e| panic!("a saved state must load: {e}")), first);
    assert_eq!(open().load().unwrap_or_else(|e| panic!("another handle must load the saved state: {e}")), first);

    // overwrite
    store.save(&second).unwrap_or_else(|e| panic!("a second save must succeed: {e}"));
    assert_eq!(store.load().unwrap_or_else(|e| panic!("the new state must load: {e}")), second);
    assert_eq!(open().load().unwrap_or_else(|e| panic!("another handle must load the new state: {e}")), second);

    // a failed save leaves the old state
    let err = store.save(&refused).expect_err("the refused state must not be saved");
    assert_ne!(err.kind(), StoreErrorKind::Missing, "a failed save is not Missing: {err}");
    assert_eq!(store.load().unwrap_or_else(|e| panic!("the state must still load after a failed save: {e}")), second);
    assert_eq!(open().load().unwrap_or_else(|e| panic!("another handle must still load the state: {e}")), second);
}
