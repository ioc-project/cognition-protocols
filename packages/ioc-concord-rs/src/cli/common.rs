//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the two commands share: what they are given (the working folder, the environment, the input), what they print, how they fail,
//! where their state is kept and how a text is cut into CONCORD messages.

use std::io::{IsTerminal, Read};
use std::path::PathBuf;
use std::sync::{mpsc, LazyLock};
use std::time::Duration;

use regex::Regex;

use crate::errors::ConcordError;

/// What a command printed, and its exit code.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Output {
    pub stdout: String,
    pub stderr: String,
    pub code: i32,
}

impl Output {
    /// Print a line on the standard output.
    pub fn out(&mut self, text: impl AsRef<str>) {
        self.stdout.push_str(text.as_ref());
        self.stdout.push('\n');
    }

    /// Print a line on the error output.
    pub fn err(&mut self, text: impl AsRef<str>) {
        self.stderr.push_str(text.as_ref());
        self.stderr.push('\n');
    }
}

/// Where a command reads the messages it is passed when they are not an argument.
pub trait Input {
    /// The input, or an empty text when nothing comes within `wait` (a terminal, or a pipe that nobody writes to).
    fn read(&mut self, wait: Duration) -> String;
}

/// The standard input of the process.
pub struct StdinInput;

impl Input for StdinInput {
    /// Waits `wait` for the input to start (a terminal, or a pipe that nobody writes to, gives nothing), then reads it to its end however
    /// slowly it comes. What is not UTF-8 is read with the replacement character.
    fn read(&mut self, wait: Duration) -> String {
        if std::io::stdin().is_terminal() {
            return String::new();
        }
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut stdin = std::io::stdin().lock();
            let mut buf = [0u8; 8192];
            loop {
                match stdin.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        if tx.send(buf[..n].to_vec()).is_err() {
                            break; // nobody waits for it any more
                        }
                    }
                }
            }
        });
        let mut bytes = match rx.recv_timeout(wait) {
            Ok(chunk) => chunk,
            Err(_) => return String::new(),
        };
        while let Ok(chunk) = rx.recv() {
            bytes.extend(chunk);
        }
        String::from_utf8_lossy(&bytes).into_owned()
    }
}

/// A fixed input, for a test: what is read, once.
pub struct StringInput(pub String);

impl Input for StringInput {
    fn read(&mut self, _wait: Duration) -> String {
        std::mem::take(&mut self.0)
    }
}

/// What a command is given besides its arguments.
pub struct Context {
    /// The folder the command runs in: the project is the folder that holds `.claude` above it, or this one.
    pub cwd: PathBuf,
    /// Where the receiver keeps its state (`CONCORD_STATE_DIR`); none: `.concord-receiver` in the project.
    pub state_dir: Option<PathBuf>,
    /// The seconds after which an initiator nudges without being forced (`CONCORD_MIN_NUDGE`).
    pub min_nudge: f64,
    pub input: Box<dyn Input>,
}

impl Context {
    /// The working folder and the environment of the process, and its standard input.
    pub fn from_env() -> Self {
        Context {
            cwd: std::env::current_dir().unwrap_or_default(),
            state_dir: std::env::var_os("CONCORD_STATE_DIR").map(PathBuf::from),
            min_nudge: std::env::var("CONCORD_MIN_NUDGE").ok().and_then(|v| v.parse().ok()).unwrap_or(30.0),
            input: Box::new(StdinInput),
        }
    }

    /// A path as the command line gives it: relative to the working folder of the context. The process's own folder needs no change, so
    /// that what is printed stays as it was typed.
    pub fn resolve(&self, path: &str) -> PathBuf {
        let path = PathBuf::from(path);
        if path.is_absolute() || std::env::current_dir().is_ok_and(|cwd| cwd == self.cwd) {
            path
        } else {
            self.cwd.join(path)
        }
    }

    /// The folder that holds `.claude` above the working folder, or the working folder.
    pub fn project_root(&self) -> PathBuf {
        let mut dir = self.cwd.as_path();
        loop {
            if dir.join(".claude").is_dir() {
                return dir.to_path_buf();
            }
            match dir.parent() {
                Some(parent) => dir = parent,
                None => return self.cwd.clone(),
            }
        }
    }

    /// The folder of the state of a command: made when it is not there.
    pub fn state_dir(&self, default_name: &str) -> Result<PathBuf, Fail> {
        let dir = self.state_dir.clone().unwrap_or_else(|| self.project_root().join(default_name));
        std::fs::create_dir_all(&dir).map_err(|e| Fail::message(format!("error: cannot make {}: {e}", dir.display())))?;
        Ok(dir)
    }
}

/// Why a command did not go through.
#[derive(Debug)]
pub enum Fail {
    /// An error of the library.
    Concord(ConcordError),
    /// What to say, with the hint when it is not the default one.
    Message(String, Option<String>),
    /// A command line the parser refused, with the hint when it is not the default one.
    Usage(String, Option<String>),
    /// What was asked for is not an error: print this, and exit with this code.
    Done(String, i32),
}

impl Fail {
    pub fn message(text: impl Into<String>) -> Fail {
        Fail::Message(text.into(), None)
    }

    /// Print the failure as an output of its own, and say the exit code: 2 for what the caller got wrong.
    pub fn print(self, out: &mut Output) -> i32 {
        match self {
            Fail::Message(text, hint) => out.out(super::render_error(&text, hint.as_deref())),
            Fail::Usage(detail, hint) => {
                out.out(super::render_error(&super::usage_error(&detail), Some(hint.as_deref().unwrap_or(super::USAGE_HINT))))
            }
            Fail::Concord(e @ ConcordError::State(_)) => {
                out.out(super::render_error(&e.to_string(), None));
                return 1; // what the caller got wrong is 2; a state that cannot be read or saved is not that
            }
            Fail::Concord(e) => out.out(super::render_error(&e.to_string(), None)),
            Fail::Done(text, code) => {
                out.out(text);
                return code;
            }
        }
        2
    }
}

impl Fail {
    /// The hints of a role for the failures that have none of their own: `usage` for a command line the parser refused, `other` for the rest.
    pub fn with_hints(self, usage: &str, other: &str) -> Fail {
        match self {
            Fail::Concord(e @ ConcordError::State(_)) => Fail::Concord(e),
            Fail::Concord(e) => Fail::Message(e.to_string(), Some(other.to_string())),
            Fail::Message(text, None) => Fail::Message(text, Some(other.to_string())),
            Fail::Usage(detail, None) => Fail::Usage(detail, Some(usage.to_string())),
            fail => fail,
        }
    }
}

impl From<ConcordError> for Fail {
    fn from(e: ConcordError) -> Fail {
        Fail::Concord(e)
    }
}

impl From<std::io::Error> for Fail {
    fn from(e: std::io::Error) -> Fail {
        Fail::message(format!("error: {e}"))
    }
}

pub type Res<T> = Result<T, Fail>;

/// A command line the parser refused, or the help that was asked for.
pub fn from_clap(error: clap::Error) -> Fail {
    use clap::error::ErrorKind;
    match error.kind() {
        ErrorKind::DisplayHelp | ErrorKind::DisplayVersion => Fail::Done(error.to_string().trim_end().to_string(), 0),
        _ => Fail::Usage(error.to_string().trim().to_string(), None),
    }
}

/// Resolve `@path` by reading the file.
pub fn expand(ctx: &Context, value: &str) -> Res<String> {
    match value.strip_prefix('@') {
        Some(path) => std::fs::read_to_string(ctx.resolve(path)).map_err(|e| Fail::message(format!("error: cannot read {path}: {e}"))),
        None => Ok(value.to_string()),
    }
}

/// A name that is safe in a file name.
pub fn safe(name: &str) -> String {
    name.chars().map(|c| if c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-') { c } else { '_' }).collect()
}

static OPEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<(concord-[a-z-]+)\b").expect("a valid pattern"));
static SEPARATOR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?m)^--- (?:from|to): .*$").expect("a valid pattern"));

/// Whether `text` holds the start of a CONCORD message.
pub fn has_concord(text: &str) -> bool {
    OPEN.is_match(text)
}

/// The CONCORD messages in `text`, one by one: each message names its author, so a `--- from: NAME` line is not needed to tell them apart
/// (and is not looked at inside a message). A message that is cut is passed on up to the next one, for the node to refuse. A text that holds
/// no message is passed as it is, without the separator lines (the node ignores what is not CONCORD).
pub fn documents(text: &str) -> Vec<String> {
    let opens: Vec<regex::Captures> = OPEN.captures_iter(text).collect();
    let mut out = Vec::new();
    let mut from = 0;
    for (i, m) in opens.iter().enumerate() {
        let whole = m.get(0).expect("the whole match");
        if whole.start() < from {
            continue; // inside the message before it
        }
        let close = Regex::new(&format!(r"</{}\s*>", regex::escape(&m[1]))).expect("a valid pattern");
        let end = match close.find_at(text, whole.start()) {
            Some(end) => end.end(),
            None => {
                opens[i + 1..].iter().find_map(|n| n.get(0).filter(|n| n.start() > whole.start()).map(|n| n.start())).unwrap_or(text.len())
            }
        };
        out.push(text[whole.start()..end].trim().to_string());
        from = end;
    }
    if out.is_empty() {
        out.extend(SEPARATOR.split(text).map(str::trim).filter(|part| !part.is_empty()).map(str::to_string));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_are_cut_one_by_one_and_a_separator_inside_one_does_not_split_it() {
        let text = "--- from: a\n<concord-score from=\"a\">x\n--- from: b\ny</concord-score>\n--- from: c\n<concord-init-reply from=\"c\">";
        let docs = documents(text);
        assert_eq!(docs.len(), 2, "{docs:?}");
        assert!(docs[0].starts_with("<concord-score") && docs[0].ends_with("</concord-score>") && docs[0].contains("--- from: b"));
        assert_eq!(docs[1], "<concord-init-reply from=\"c\">", "a cut message is passed on, to be refused");
    }

    #[test]
    fn a_text_without_a_message_is_passed_without_its_separators() {
        assert_eq!(documents("--- from: a\nhello\n--- to: b\nworld"), vec!["hello", "world"]);
        assert!(documents("  \n").is_empty());
        assert!(!has_concord("hello") && has_concord("x <concord-score a"));
    }
}
