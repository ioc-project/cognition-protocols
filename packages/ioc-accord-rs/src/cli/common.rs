//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the command is given (the working folder, the environment, the input), what it prints, how it fails, and how it reads its
//! arguments: a JSON value from a file, from the input or inline, as the script's `read_json` does, with Python's errors.

use std::io::Read;
use std::path::PathBuf;

use serde_json::Value;

use crate::errors::AccordError;
use crate::py;
use crate::pyjson::loads;

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

/// Where an argument given as `-` is read from.
pub trait Input {
    /// The bytes of the input, to its end. A second read gives what is left: nothing.
    fn read(&mut self) -> std::io::Result<Vec<u8>>;
}

/// The standard input of the process: read to its end, waiting for it as the script does.
pub struct StdinInput;

impl Input for StdinInput {
    fn read(&mut self) -> std::io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        std::io::stdin().lock().read_to_end(&mut bytes)?;
        Ok(bytes)
    }
}

/// A fixed input, for a test: what is read, once.
pub struct StringInput(pub String);

impl Input for StringInput {
    fn read(&mut self) -> std::io::Result<Vec<u8>> {
        Ok(std::mem::take(&mut self.0).into_bytes())
    }
}

/// What a command is given besides its arguments.
pub struct Context {
    /// The folder the command runs in: the relative paths are relative to it.
    pub cwd: PathBuf,
    /// The state file when `--state` does not name one (`ACCORD_STATE`); none: `.accord/state.json`.
    pub state: Option<String>,
    /// The run key that isolates the state file (`ACCORD_RUN_KEY`; empty is none).
    pub run_key: Option<String>,
    /// The folder of the isolated state file (`ACCORD_STATE_DIR`; empty is none: next to the state file).
    pub state_dir: Option<String>,
    pub input: Box<dyn Input>,
}

impl Context {
    /// The working folder and the environment of the process, and its standard input. `ACCORD_EMBEDDING` is not read: the similarity is
    /// always the string one.
    pub fn from_env() -> Self {
        Context {
            cwd: std::env::current_dir().unwrap_or_default(),
            state: std::env::var("ACCORD_STATE").ok(),
            run_key: std::env::var("ACCORD_RUN_KEY").ok(),
            state_dir: std::env::var("ACCORD_STATE_DIR").ok(),
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

    /// The input, as text: what is not UTF-8 is refused as Python refuses it. The line breaks are kept (the script's standard input does
    /// not translate them).
    pub fn read_input(&mut self) -> Result<String, AccordError> {
        let bytes = self.input.read().map_err(|e| os_error(&e, None))?;
        utf8(bytes)
    }

    /// The text of a file, as `Path(path).read_text()` gives it: `\r\n` and `\r` read as `\n`, and Python's error when it cannot be read.
    pub fn read_file(&self, path: &str) -> Result<String, AccordError> {
        let shown = pathlib(path);
        let bytes = std::fs::read(self.resolve(&shown)).map_err(|e| os_error(&e, Some(&shown)))?;
        Ok(utf8(bytes)?.replace("\r\n", "\n").replace('\r', "\n"))
    }

    /// `read_json`: None without an argument; else the JSON value given inline (an argument that starts with `[` or `{`), on the input
    /// (`-`) or in the file the argument names.
    pub fn read_json(&mut self, arg: Option<&str>) -> Result<Option<Value>, AccordError> {
        let Some(arg) = arg else { return Ok(None) };
        let stripped = py::strip(arg);
        let (source, text) = if stripped.starts_with(['[', '{']) {
            // inline: a caller driving the command through a tool has one input per call, so the other arguments come inline
            ("inline JSON", stripped.to_string())
        } else if arg == "-" {
            (arg, self.read_input()?)
        } else {
            (arg, self.read_file(arg)?)
        };
        loads(&text).map(Some).map_err(|e| {
            let detail = e.message().strip_prefix("JSONDecodeError: ").unwrap_or(e.message());
            AccordError::new(format!("invalid JSON in {source}: {detail}"))
        })
    }

    /// `read_text`: an assignment from a file, or from the input when the argument is `-`, stripped.
    pub fn read_text(&mut self, arg: &str) -> Result<String, AccordError> {
        let text = if arg == "-" { self.read_input()? } else { self.read_file(arg)? };
        Ok(py::strip(&text).to_string())
    }
}

/// A path as `pathlib.Path` writes it (`a//b/./c` is `a/b/c`, an empty path is `.`): the script names its files so.
pub fn pathlib(path: &str) -> String {
    let (root, parts) = ioc_protocol::path::split_posix(path);
    ioc_protocol::path::join_posix(root, &parts)
}

/// An I/O error as Python prints its `OSError`: `FileNotFoundError: [Errno 2] No such file or directory: 'x.json'`.
pub fn os_error(e: &std::io::Error, path: Option<&str>) -> AccordError {
    let Some(errno) = e.raw_os_error() else { return AccordError::python("OSError", e) };
    let kind = match errno {
        1 | 13 => "PermissionError",
        2 => "FileNotFoundError",
        17 => "FileExistsError",
        20 => "NotADirectoryError",
        21 => "IsADirectoryError",
        _ => "OSError",
    };
    let text = e.to_string();
    let text = text.strip_suffix(&format!(" (os error {errno})")).unwrap_or(&text);
    match path {
        Some(path) => AccordError::python(kind, format!("[Errno {errno}] {text}: {}", py::repr_str(path))),
        None => AccordError::python(kind, format!("[Errno {errno}] {text}")),
    }
}

/// Bytes as UTF-8 text, refused as Python's decoder refuses them: `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position
/// 0: invalid start byte`.
fn utf8(bytes: Vec<u8>) -> Result<String, AccordError> {
    String::from_utf8(bytes).map_err(|e| {
        let bytes = e.as_bytes();
        let err = e.utf8_error();
        let start = err.valid_up_to();
        let (len, reason) = match err.error_len() {
            None => (bytes.len() - start, "unexpected end of data"),
            Some(1) if matches!(bytes[start], 0x80..=0xc1 | 0xf5..=0xff) => (1, "invalid start byte"),
            Some(n) => (n, "invalid continuation byte"),
        };
        let at = if len == 1 {
            format!("byte 0x{:02x} in position {start}", bytes[start])
        } else {
            format!("bytes in position {start}-{}", start + len - 1)
        };
        AccordError::python("UnicodeDecodeError", format!("'utf-8' codec can't decode {at}: {reason}"))
    })
}

/// The options of the script that take a value, by their long name.
const VALUE_OPTIONS: [&str; 12] = [
    "state",
    "specialists",
    "key-terms",
    "action-tool",
    "task",
    "responses",
    "current-frame",
    "required-knowledge",
    "objective-clauses",
    "agent",
    "assignment-file",
    "reply",
];

/// The arguments with the value of an option joined to it (`--reply`, `- item` becomes `--reply=- item`) when the value starts with `-`
/// but `argparse` takes it as a value all the same: `-` alone, a negative number, or a text with a space. `clap` would take it for an
/// option; what `argparse` takes for an option (`--reply -x`) stays apart, and is refused by both.
pub fn join_hyphen_values(args: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(args.len());
    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        let takes_value = arg
            .strip_prefix("--")
            .is_some_and(|name| !name.is_empty() && !name.contains('=') && VALUE_OPTIONS.iter().any(|o| o.starts_with(name)));
        match args.get(i + 1) {
            Some(value) if takes_value && value.starts_with('-') && argparse_value(value) => {
                out.push(format!("{arg}={value}"));
                i += 2;
            }
            _ => {
                out.push(arg.clone());
                i += 1;
            }
        }
    }
    out
}

/// Whether `argparse` reads an argument that starts with `-` as a value rather than an option.
fn argparse_value(arg: &str) -> bool {
    if arg == "-" || arg.contains(' ') {
        return true;
    }
    // `^-\d+$|^-\d*\.\d+$`
    let rest = &arg[1..];
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    digits(rest) || rest.split_once('.').is_some_and(|(int, frac)| int.bytes().all(|b| b.is_ascii_digit()) && digits(frac))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|a| a.to_string()).collect()
    }

    #[test]
    fn a_value_argparse_reads_as_a_value_is_joined_to_its_option() {
        assert_eq!(join_hyphen_values(&args(&["--reply", "- a b", "--agent", "-1.5"])), ["--reply=- a b", "--agent=-1.5"]);
        assert_eq!(join_hyphen_values(&args(&["--resp", "-"])), ["--resp=-"]);
        assert_eq!(join_hyphen_values(&args(&["--reply", "-x", "--help", "- a"])), ["--reply", "-x", "--help", "- a"]);
        assert_eq!(join_hyphen_values(&args(&["--reply=-x", "--", "-"])), ["--reply=-x", "--", "-"]);
    }

    #[test]
    fn errors_are_worded_as_python_words_them() {
        assert_eq!(pathlib("a//b/./c.json"), "a/b/c.json");
        assert_eq!(pathlib(""), ".");
        let e = std::io::Error::from_raw_os_error(2);
        assert_eq!(os_error(&e, Some("x.json")).message(), "FileNotFoundError: [Errno 2] No such file or directory: 'x.json'");
        assert_eq!(
            utf8(vec![b'a', 0xff]).unwrap_err().message(),
            "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 1: invalid start byte"
        );
        assert_eq!(
            utf8(vec![0xe2, 0x82]).unwrap_err().message(),
            "UnicodeDecodeError: 'utf-8' codec can't decode bytes in position 0-1: unexpected end of data"
        );
        assert_eq!(
            utf8(vec![0xe2, 0x28]).unwrap_err().message(),
            "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe2 in position 0: invalid continuation byte"
        );
    }
}
