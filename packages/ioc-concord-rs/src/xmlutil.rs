//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! XML text helpers: escaping, the start tag of a document, and its integrity check.
//!
//! They are `ioc_protocol::xml`'s, shared with the other protocol libraries: the check works on the text of a document, exactly as it
//! was sent, and the root start tag is read once, for everything that looks at a document before it is parsed (the `check`, and the
//! `from` and `uuid` that decide whether a message is dropped). Only `with_check` is wrapped here, to fail with a `ConcordError`.

use crate::errors::Result;

pub use ioc_protocol::xml::{canonical_text, check_of, compute_check, esc_attr, esc_text, start_tag, verify_check, StartTag};

/// `doc_without_check`, whose root start tag has no `check`, with the attribute added as its last attribute.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the document does not start with a tag.
pub fn with_check(doc_without_check: &str) -> Result<String> {
    Ok(ioc_protocol::xml::with_check(doc_without_check)?)
}
