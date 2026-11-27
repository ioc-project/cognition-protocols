//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What a call prints: a `STATUS:` line, sections that each start with a `--- TITLE` line, then a `NEXT:` line that says what to do; and
//! the fault that is printed instead.

use crate::errors::ConcordError;

use super::py;

/// The exit code of a call that prints `STATUS: ERROR`.
pub(crate) const EXIT_ERROR: i32 = 2;

/// What the hint of a fault of the library says: it is not a refused answer (those say what to change in the answer).
pub(crate) const FIX_HINT: &str =
    "Nothing was changed. Fix what the message says (the file or the argument it names) and run the same command \
again. If it keeps failing, stop and say why.";

/// Why a call printed something else than its output.
#[derive(Debug)]
pub(crate) enum Fail {
    /// A fault the caller can fix: what is wrong, and what to do about it. It never changes the state.
    Cli(String, String),
    /// A command line that cannot be read, and what to do.
    Usage(String, String),
    /// The help that was asked for.
    Help(String),
    /// An error of the library: the state or the input is wrong.
    Concord(ConcordError),
}

impl Fail {
    pub fn cli(message: impl Into<String>, hint: impl Into<String>) -> Fail {
        Fail::Cli(message.into(), hint.into())
    }

    /// What is printed, and the exit code.
    pub fn printed(self) -> (String, i32) {
        match self {
            Fail::Cli(message, hint) | Fail::Usage(message, hint) => (error_text(&message, &hint), EXIT_ERROR),
            Fail::Help(text) => (text, 0),
            Fail::Concord(e) => (error_text(&e.to_string(), FIX_HINT), EXIT_ERROR),
        }
    }
}

impl From<ConcordError> for Fail {
    fn from(e: ConcordError) -> Fail {
        Fail::Concord(e)
    }
}

/// A fault of the system (a folder that cannot be made, a file that cannot be read), which the scripts did not catch.
pub(crate) fn io_fail(what: &str, e: &std::io::Error) -> Fail {
    Fail::Concord(ConcordError::state(format!("{what}: {}", strerror(e))))
}

/// What the system says of an error, without the number Rust adds to it: `strerror`.
pub(crate) fn strerror(e: &std::io::Error) -> String {
    let text = e.to_string();
    match text.rfind(" (os error ") {
        Some(at) => text[..at].to_string(),
        None => text,
    }
}

/// What one call prints: a status, sections, and what to do next.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Out {
    pub status: &'static str,
    pub sections: Vec<(String, String)>,
    pub next: String,
}

impl Out {
    pub fn new(status: &'static str) -> Out {
        Out { status, sections: Vec::new(), next: String::new() }
    }

    pub fn add(&mut self, title: impl Into<String>, body: &str) {
        self.sections.push((title.into(), body.trim_matches('\n').to_string()));
    }

    pub fn text(&self) -> String {
        let mut parts = vec![format!("STATUS: {}", self.status)];
        for (title, body) in &self.sections {
            parts.push(if body.is_empty() { format!("--- {title}") } else { format!("--- {title}\n{body}") });
        }
        parts.push(format!("NEXT: {}", self.next));
        parts.join("\n") + "\n"
    }
}

/// The text of a fault: never the fault alone.
pub(crate) fn error_text(message: &str, hint: &str) -> String {
    format!("STATUS: ERROR\n{message}\nNEXT: {hint}\n")
}

/// A time to call `tick` (or to expect `wait` to return), as ISO 8601 with the zone, and how long from now.
pub(crate) fn wake_text(when: f64, now: f64) -> String {
    format!("{} (in {} s)", py::local_time(when), py::max(0.0, py::round(when - now)) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_output_is_a_status_sections_and_a_next_line() {
        let mut out = Out::new("SEND");
        out.add("SEND to: b", "<x/>\n");
        out.add("WAKE", "later");
        out.next = "do it".to_string();
        assert_eq!(out.text(), "STATUS: SEND\n--- SEND to: b\n<x/>\n--- WAKE\nlater\nNEXT: do it\n");
    }

    #[test]
    fn a_fault_always_says_what_to_do() {
        assert_eq!(error_text("bad", "fix it"), "STATUS: ERROR\nbad\nNEXT: fix it\n");
        assert!(wake_text(1000.0, 940.0).ends_with("(in 60 s)"));
        assert!(wake_text(1000.0, 1940.0).ends_with("(in 0 s)"));
    }
}
