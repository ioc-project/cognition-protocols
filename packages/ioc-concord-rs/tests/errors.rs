//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The errors: each one has a code, and a missing state is a state error of its own kind.
// `ConcordError` is an enum with no generic variant, so there is no base error with a custom code to test.

use ioc_concord::ConcordError;

#[test]
fn every_error_is_a_concord_error_with_a_code() {
    let err = ConcordError::schema("bad schema");
    assert!(matches!(err, ConcordError::Schema(_)));
    assert_eq!(err.to_string(), "bad schema");
    assert_eq!(err.code(), "invalid_schema");
    assert_eq!(ConcordError::invalid_run("x").code(), "invalid_run");
    assert_eq!(ConcordError::invalid_message("x").code(), "invalid_message");
    let boxed: Box<dyn std::error::Error> = Box::new(ConcordError::state("x"));
    assert_eq!(boxed.to_string(), "x");
}

#[test]
fn a_missing_state_is_a_state_error_of_its_own_kind() {
    let err = ConcordError::missing_state("nothing saved");
    assert!(err.is_state());
    assert_eq!(err.code(), "missing_state");
    assert_eq!(err.to_string(), "nothing saved");
    assert_eq!(ConcordError::state("x").code(), "state_error");
    assert!(ConcordError::state("x").is_state());
    assert!(!matches!(ConcordError::state("x"), ConcordError::MissingState(_)));
    assert!(!ConcordError::schema("x").is_state());
}
