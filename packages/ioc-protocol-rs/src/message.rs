//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! A message of a protocol, read and written: the error a reader returns, and the traits a protocol implements for its own types.
//!
//! The library knows no schema. A protocol decodes the text of a message into its own type (`Decode`) and encodes its own type into
//! the text it sends (`Encode`); `read` puts the tolerant reading of a text (finding the message in it, see `xml::Locator` and the
//! functions of `text`) in front of the decoding. A protocol keeps its own error type and converts from `MessageError`.

use std::fmt;

/// Why a message could not be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum ErrorKind {
    /// The text holds no message of the protocol.
    NotFound,
    /// The message is there but cut off: its first tag, or its end, is missing.
    CutOff,
    /// The message is not well-formed: it cannot be parsed.
    Malformed,
    /// The message is well-formed but breaks a rule of the protocol (a field is missing, a value is out of range).
    Invalid,
    /// The fingerprint the message carries is not the one of its text.
    CheckMismatch,
}

impl ErrorKind {
    /// A stable, machine-readable name of the kind.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::NotFound => "not_found",
            ErrorKind::CutOff => "cut_off",
            ErrorKind::Malformed => "malformed",
            ErrorKind::Invalid => "invalid",
            ErrorKind::CheckMismatch => "check_mismatch",
        }
    }
}

impl fmt::Display for ErrorKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A message that could not be read or written: its kind, and what is wrong, in words an agent can act on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MessageError {
    kind: ErrorKind,
    message: String,
}

impl MessageError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        MessageError { kind, message: message.into() }
    }

    pub fn not_found(message: impl Into<String>) -> Self {
        MessageError::new(ErrorKind::NotFound, message)
    }

    pub fn cut_off(message: impl Into<String>) -> Self {
        MessageError::new(ErrorKind::CutOff, message)
    }

    pub fn malformed(message: impl Into<String>) -> Self {
        MessageError::new(ErrorKind::Malformed, message)
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        MessageError::new(ErrorKind::Invalid, message)
    }

    pub fn check_mismatch(message: impl Into<String>) -> Self {
        MessageError::new(ErrorKind::CheckMismatch, message)
    }

    /// The kind of the error.
    pub fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// What is wrong, without the kind.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for MessageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for MessageError {}

/// A type of a protocol read from the text of one message.
pub trait Decode: Sized {
    /// What decoding needs besides the text (the schema of a run, ...): `()` when nothing.
    type Context<'a>;

    /// The message `doc`, which is the message alone (already located), decoded.
    ///
    /// # Errors
    ///
    /// A `MessageError` of the kind that says why `doc` is not a message of this type.
    fn decode(doc: &str, context: Self::Context<'_>) -> Result<Self, MessageError>;
}

/// A type of a protocol written as the text of one message.
pub trait Encode {
    /// The text of the message, as it is sent.
    ///
    /// # Errors
    ///
    /// A `MessageError` when the value cannot be written as a message of the protocol (a field out of its range, ...).
    fn encode(&self) -> Result<String, MessageError>;
}

/// The message of type `T` in `text`: `locate` finds it (a fence or prose may be around it) and `T::decode` reads it.
///
/// # Errors
///
/// The `MessageError` of `locate` (no message, a message cut off) or of `T::decode`.
pub fn read<'t, T: Decode>(
    text: &'t str,
    locate: impl FnOnce(&'t str) -> Result<&'t str, MessageError>,
    context: T::Context<'_>,
) -> Result<T, MessageError> {
    T::decode(locate(text)?, context)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text::{outermost, trim_ws};

    /// A message of a toy protocol: `[name:value]`.
    #[derive(Debug, PartialEq)]
    struct Pair {
        name: String,
        value: u32,
    }

    impl Decode for Pair {
        type Context<'a> = &'a [&'a str];

        fn decode(doc: &str, names: &[&str]) -> Result<Self, MessageError> {
            let inner = doc.strip_prefix('[').and_then(|d| d.strip_suffix(']')).ok_or_else(|| MessageError::malformed("no brackets"))?;
            let (name, value) = inner.split_once(':').ok_or_else(|| MessageError::malformed("no colon"))?;
            if !names.contains(&name) {
                return Err(MessageError::invalid(format!("unknown name {name}")));
            }
            let value = value.parse().map_err(|_| MessageError::invalid("not a number"))?;
            Ok(Pair { name: name.to_string(), value })
        }
    }

    impl Encode for Pair {
        fn encode(&self) -> Result<String, MessageError> {
            Ok(format!("[{}:{}]", self.name, self.value))
        }
    }

    fn locate(text: &str) -> Result<&str, MessageError> {
        outermost(trim_ws(text), '[', ']').ok_or_else(|| MessageError::not_found("no message in the text"))
    }

    #[test]
    fn a_message_is_located_then_decoded_into_the_protocol_type() {
        let names: &[&str] = &["a", "b"];
        let got: Pair = read("Here: [a:7] done", locate, names).unwrap();
        assert_eq!(got, Pair { name: "a".into(), value: 7 });
        assert_eq!(read::<Pair>(&got.encode().unwrap(), locate, names).unwrap(), got);
        assert_eq!(read::<Pair>("nothing", locate, names).unwrap_err().kind(), ErrorKind::NotFound);
        let err = read::<Pair>("[c:1]", locate, names).unwrap_err();
        assert_eq!((err.kind(), err.message()), (ErrorKind::Invalid, "unknown name c"));
        assert_eq!(read::<Pair>("[a7]", locate, names).unwrap_err().kind(), ErrorKind::Malformed);
    }

    #[test]
    fn an_error_shows_its_message_and_names_its_kind() {
        let e = MessageError::cut_off("the message is not closed");
        assert_eq!(e.to_string(), "the message is not closed");
        assert_eq!(e.kind().as_str(), "cut_off");
        assert_eq!(MessageError::check_mismatch("x").kind().to_string(), "check_mismatch");
    }
}
