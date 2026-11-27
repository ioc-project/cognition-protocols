//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What a call is given besides its arguments (the working folder and the standard input), and what it gives back (what it printed, and
//! its exit code).

use std::io::{IsTerminal, Read};
use std::path::{Path, PathBuf};

/// What a command printed, and its exit code.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Output {
    pub stdout: String,
    pub stderr: String,
    pub code: i32,
}

/// Where a command reads the text it is passed (`receive`, `answer`, `forward`).
pub trait Input {
    /// The bytes of the input, read to its end; None when there is none to read: the input is closed, or it is a terminal.
    fn read(&mut self) -> Option<Vec<u8>>;
}

/// The standard input of the process.
pub struct StdinInput;

impl Input for StdinInput {
    fn read(&mut self) -> Option<Vec<u8>> {
        let stdin = std::io::stdin();
        if stdin.is_terminal() || closed() {
            return None;
        }
        let mut bytes = Vec::new();
        stdin.lock().read_to_end(&mut bytes).ok()?;
        Some(bytes)
    }
}

/// Whether the process was started with its standard input closed (`<&-`).
#[cfg(unix)]
fn closed() -> bool {
    use std::os::fd::BorrowedFd;
    // SAFETY: the descriptor is only duplicated, to see whether it is open; the duplicate is closed at once.
    unsafe { BorrowedFd::borrow_raw(0) }.try_clone_to_owned().is_err()
}

#[cfg(not(unix))]
fn closed() -> bool {
    false
}

/// A fixed input, for a test: the text that is read, or None for an input that is closed.
pub struct StringInput(pub Option<String>);

impl StringInput {
    pub fn new(text: impl Into<String>) -> Self {
        StringInput(Some(text.into()))
    }

    /// An input that is closed.
    pub fn closed() -> Self {
        StringInput(None)
    }
}

impl Input for StringInput {
    fn read(&mut self) -> Option<Vec<u8>> {
        self.0.take().map(String::into_bytes)
    }
}

/// What a command is given besides its arguments.
pub struct Context {
    /// The folder the command runs in: a relative `--state-dir` or `@path` is read from there.
    pub cwd: PathBuf,
    pub input: Box<dyn Input>,
}

impl Context {
    /// The working folder of the process, and its standard input.
    pub fn from_env() -> Self {
        Context { cwd: std::env::current_dir().unwrap_or_default(), input: Box::new(StdinInput) }
    }

    /// A context with this working folder and this input.
    pub fn new(cwd: impl Into<PathBuf>, input: impl Input + 'static) -> Self {
        Context { cwd: cwd.into(), input: Box::new(input) }
    }

    /// A path as the command line gives it, relative to the working folder of the context. In the process's own folder it is left as it
    /// is, so that what the library says of a file names it as it was typed.
    pub fn path(&self, path: &str) -> PathBuf {
        let p = Path::new(path);
        if p.is_absolute() || std::env::current_dir().is_ok_and(|cwd| cwd == self.cwd) {
            p.to_path_buf()
        } else {
            self.cwd.join(p)
        }
    }
}
