//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The short fingerprint that binds a message to its text: the first 8 lowercase hex characters of a SHA-256.
//!
//! What is hashed is the protocol's choice, and is what makes the fingerprint blind to whitespace: CONCORD hashes the canonical text of
//! a document (`xml::canonical_text`), ACCORD the text without any whitespace (`without`, with Python's whitespace).

use sha2::{Digest, Sha256};

/// The number of hex characters of a fingerprint.
pub const CHECK_LEN: usize = 8;

/// The fingerprint of `text`: the first `CHECK_LEN` lowercase hex characters of the SHA-256 of its UTF-8 bytes.
#[must_use]
pub fn short_digest(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    digest.iter().take(CHECK_LEN / 2).map(|b| format!("{b:02x}")).collect()
}

/// `text` without the characters `is_space` says are whitespace: the text a whitespace-blind fingerprint is computed over.
#[must_use]
pub fn without(text: &str, is_space: impl Fn(char) -> bool) -> String {
    text.chars().filter(|c| !is_space(*c)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_digest_is_the_prefix_of_the_sha256() {
        assert_eq!(short_digest(""), "e3b0c442");
        assert_eq!(short_digest("abc"), "ba7816bf");
        assert_eq!(short_digest("é").len(), CHECK_LEN);
    }

    #[test]
    fn whitespace_is_what_the_caller_says() {
        assert_eq!(without(" a\tb\u{a0}c\n", char::is_whitespace), "abc");
        assert_eq!(without(" a\u{a0}", |c| c == ' '), "a\u{a0}");
        assert_eq!(short_digest(&without("a b", char::is_whitespace)), short_digest("ab"));
    }
}
