//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! XML text helpers: escaping, and the integrity check of a document.
//!
//! The check works on the text of a document, exactly as it was sent: a document is never parsed and written again before its
//! check is computed or verified.

use regex::{Captures, Regex};
use sha2::{Digest, Sha256};

use crate::errors::{ConcordError, Result};
use std::sync::LazyLock;

const WS: [char; 4] = [' ', '\t', '\r', '\n'];

static START_TAG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^<[^>]*>").unwrap());
static CHECK_ATTR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#" check="[^"]*""#).unwrap());
static CHECK_VALUE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"\scheck="([^"]*)""#).unwrap());
static TEXT_RUN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r">([^<]*)<").unwrap());
static BLANKS_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t\r\n]+").unwrap());

fn strip_ws(s: &str) -> &str {
    s.trim_matches(&WS[..])
}

/// `v` as XML text: `<`, `>` and `&` are escaped, a `"` is not.
pub fn esc_text(v: &str) -> String {
    v.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// `v` as the value of an attribute: `<`, `>`, `&` and `"` are escaped.
pub fn esc_attr(v: &str) -> String {
    esc_text(v).replace('"', "&quot;")
}

fn squeeze(m: &Captures) -> String {
    let text = &m[1];
    let inner = if strip_ws(text).is_empty() { String::new() } else { strip_ws(&BLANKS_RE.replace_all(text, " ")).to_string() };
    format!(">{inner}<")
}

/// The text the check is computed over: the document without its `check` attribute (and the space before it), with each stretch of
/// text between a `>` and the next `<` made of whitespace only removed, and each other one with its runs of whitespace made one
/// space and none left at either end. Nothing else changes.
pub fn canonical_text(doc: &str) -> String {
    let doc = strip_ws(doc);
    let doc = match START_TAG_RE.find(doc) {
        Some(start) => format!("{}{}", CHECK_ATTR_RE.replace(start.as_str(), ""), &doc[start.end()..]),
        None => doc.to_string(),
    };
    TEXT_RUN_RE.replace_all(&doc, |m: &Captures| squeeze(m)).into_owned()
}

/// The `check` of `doc`: the first 8 lowercase hex characters of the SHA-256 of its canonical text, as UTF-8.
pub fn compute_check(doc: &str) -> String {
    let digest = Sha256::digest(canonical_text(doc).as_bytes());
    digest.iter().take(4).map(|b| format!("{b:02x}")).collect()
}

/// The value of the `check` attribute on the root element of `doc`, or None when it has none.
pub fn check_of(doc: &str) -> Option<String> {
    let start = START_TAG_RE.find(strip_ws(doc))?;
    CHECK_VALUE_RE.captures(start.as_str()).map(|m| m[1].to_string())
}

/// True when `doc` carries a `check` and it is the one `compute_check` gives.
pub fn verify_check(doc: &str) -> bool {
    check_of(doc).is_some_and(|claimed| claimed == compute_check(doc))
}

/// `doc_without_check`, whose root start tag has no `check`, with the attribute added as its last attribute.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the document does not start with a tag.
pub fn with_check(doc_without_check: &str) -> Result<String> {
    let doc = strip_ws(doc_without_check);
    let start = START_TAG_RE.find(doc).ok_or_else(|| ConcordError::invalid_message("a document starts with a tag"))?;
    let tag = start.as_str();
    let closing = if tag.ends_with("/>") { "/>" } else { ">" };
    Ok(format!("{} check=\"{}\"{}{}", &tag[..tag.len() - closing.len()], compute_check(doc), closing, &doc[start.end()..]))
}
