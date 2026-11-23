//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the library returns when a command cannot be carried out: `AccordError`, and `hint_for`, what to do about it.
//!
//! The script has two exception classes, `AccordError` and its subclass `AccordRefusal`; here they are one type with a `refusal` flag. A
//! refusal is the one error whose bookkeeping must outlive it: `Session::handle` keeps the state as it stood when the refusal was raised
//! (the count of the attempts that carried no check), where every other error leaves the session as it was before the call.
//!
//! Where the script would fail with a Python exception (a reply whose `holds` is a number, a hand-edited state), the error carries the
//! text the script prints for it, `TypeError: 'int' object is not iterable`, as Python 3.15 words it.

use std::fmt;

/// A command refused or failed. `message()` is the text the script prints in its `error` field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccordError {
    message: String,
    refusal: bool,
}

/// The result type of the library.
pub type Result<T> = std::result::Result<T, AccordError>;

impl AccordError {
    /// An error the orchestrator can act on; nothing is recorded.
    pub fn new(message: impl Into<String>) -> Self {
        AccordError { message: message.into(), refusal: false }
    }

    /// An error whose bookkeeping is kept: the state is saved before it is reported.
    pub fn refusal(message: impl Into<String>) -> Self {
        AccordError { message: message.into(), refusal: true }
    }

    /// What a Python exception of the script prints: `<kind>: <text>`.
    pub fn python(kind: &str, text: impl fmt::Display) -> Self {
        AccordError::new(format!("{kind}: {text}"))
    }

    /// A `TypeError` of the script.
    pub fn type_error(text: impl fmt::Display) -> Self {
        AccordError::python("TypeError", text)
    }

    /// The text of the error.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Whether the state must be saved before the error is reported.
    pub fn is_refusal(&self) -> bool {
        self.refusal
    }

    /// What to do about the error: `hint_for` its message.
    pub fn hint(&self) -> &'static str {
        hint_for(&self.message)
    }
}

impl fmt::Display for AccordError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for AccordError {}

/// A message that cannot be read or written: an error with its text.
impl From<ioc_protocol::MessageError> for AccordError {
    fn from(err: ioc_protocol::MessageError) -> Self {
        AccordError::new(err.message())
    }
}

/// A state that cannot be loaded or saved: an error with its text.
impl From<ioc_protocol::StoreError> for AccordError {
    fn from(err: ioc_protocol::StoreError) -> Self {
        AccordError::new(err.message())
    }
}

/// What to do about an error: always a way back onto the path, never just the complaint.
pub fn hint_for(msg: &str) -> &'static str {
    let m = msg.to_lowercase();
    if m.contains("no state file") || m.contains("no task in state") {
        return "Run `init` first, with --task to drive the session from `handle`. Use the same --state (or the same \
                $ACCORD_RUN_KEY) on every command in the session.";
    }
    if m.contains("not a specialist") || m.contains("unknown agent") {
        return "Use the specialist ids exactly as you passed them to `init --specialists`.";
    }
    if m.contains("does not match its check") {
        return "That reply is not the one the specialist returned for the message it was sent. Do not summarize, shorten or rewrite \
                a reply: pass it to `handle` exactly as received, including the `<accord-delivery>` line it starts with.";
    }
    if m.contains("carried no") && m.contains("accord-delivery") {
        return "The reply is missing the `<accord-delivery>` line it was asked to echo. Send that specialist its message again and \
                pass the whole reply back unedited.";
    }
    if m.contains("invalid json") {
        return "That argument is not JSON. Pass a file path, '-' for stdin, or the JSON value inline — and only one argument per \
                command can use '-'.";
    }
    if m.contains("none of the responses") {
        return "Those replies are prose or the wrong shape, and nothing was recorded. Ask each specialist to answer again from the \
                message it was sent, and pass the replies through unedited — never write the JSON yourself.";
    }
    if m.contains("no locked contract") || m.contains("not intent-locked") {
        return "Finish VERIFY first. `execution-message` only works once the contract is INTENT-LOCKED.";
    }
    if m.contains("no such file") {
        return "Check the path. Each JSON argument takes a file path, '-' for stdin, or the JSON value inline.";
    }
    if m.contains("invalid choice") || m.contains("unrecognized arguments") || m.contains("required") || m.contains("usage") {
        return "Run `--help` for the command. The usual sequence is `init --task ...` once, then `handle --agent <id> --reply <text>` \
                for each reply.";
    }
    "Fix what the message says and run the same command again. Never work around it by writing a reply or a phase artifact yourself."
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_refusal_is_flagged_and_its_text_is_the_message() {
        let e = AccordError::refusal("handle: x");
        assert!(e.is_refusal());
        assert_eq!(e.to_string(), "handle: x");
        assert!(!AccordError::new("y").is_refusal());
        assert_eq!(AccordError::type_error("'int' object is not iterable").message(), "TypeError: 'int' object is not iterable");
    }

    #[test]
    fn every_hint_is_reachable_and_the_default_comes_last() {
        assert!(hint_for("No state file at x").starts_with("Run `init` first"));
        assert!(hint_for("handle: no task in state").starts_with("Run `init` first"));
        assert!(hint_for("unknown agent z").starts_with("Use the specialist ids"));
        assert!(hint_for("the reply from a does not match its check").starts_with("That reply is not"));
        assert!(hint_for("carried no `<accord-delivery>` check line").starts_with("The reply is missing"));
        assert!(hint_for("invalid JSON in x").starts_with("That argument is not JSON"));
        assert!(hint_for("frame: none of the responses carried").starts_with("Those replies"));
        assert!(hint_for("execution-message: no locked contract exists").starts_with("Finish VERIFY"));
        assert!(hint_for("contract is not INTENT-LOCKED").starts_with("Finish VERIFY"));
        assert!(hint_for("No such file or directory").starts_with("Check the path"));
        assert!(hint_for("usage error: the following arguments are required").starts_with("Run `--help`"));
        assert!(hint_for("something else").starts_with("Fix what the message says"));
    }
}
