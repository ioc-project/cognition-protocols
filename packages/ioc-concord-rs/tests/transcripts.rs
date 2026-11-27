//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The recorded transcripts of the skill scripts (`protocols/concord/tests/transcripts`), replayed through `cli::run`: every call of a
//! transcript is made again, with its arguments and its standard input, in a folder of its own, and what it prints and its exit code must
//! be what the scripts printed, byte for byte.
//!
//! A transcript is a list of blocks (see `protocols/concord/tests/driver.py`): `=== <role> <me> <command> (exit N)`, then the arguments
//! (`$ `), the standard input (` in| `), what was printed (`out| `) and on the error stream (`err| `), a line per line, wrapped at 120
//! columns with a `⏎` at the end of a line that goes on. What changes from one run to the next was normalised when it was recorded, and
//! is normalised here in the same way and in the same order (the arguments, then the input, then the output of each call): every UUID
//! becomes `<UUID-n>` in the order it first appears, every `check` becomes `<CHECK>`, a time to come back becomes `<WAKE>`. And the name
//! of the command is written as the name of the script it replaces: `uvx ioc-concord initiator` as `concord_initiator.py`, `uvx
//! ioc-concord receiver` as `concord_receiver.py`. That is all that is normalised.
//!
//! The standard input of a call was normalised too: it is made real again from what the replay printed before it. A message is the text
//! of a `SEND` or `FORWARD` section printed earlier whose normalised form is the one recorded (the `check` of a message can only be found
//! that way); any other input has its `<UUID-n>` put back.
#![cfg(feature = "cli")]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use ioc_concord::cli::{run, Context, StringInput};
use regex::Regex;

static UUID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}").unwrap());
static CHECK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"\bcheck="[0-9a-f]{8}""#).unwrap());
static WAKE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d \(in \d+ s\)").unwrap());
static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<UUID-(\d+)>").unwrap());
static SECTION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?ms)^--- (?:SEND|FORWARD) to: [^\n]*\n(.*?)(?:^--- |^NEXT:)").unwrap());

const WRAP: char = '\u{23ce}';

fn transcripts() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../protocols/concord/tests/transcripts")
}

/// One call of a transcript.
#[derive(Debug, Default)]
struct Call {
    title: String,
    code: i32,
    argv: String,
    stdin: String,
    stdout: String,
    stderr: String,
}

/// The calls of a transcript, with the wrapped lines put together again.
fn parse(text: &str) -> Vec<Call> {
    let mut calls: Vec<Call> = Vec::new();
    let mut lines: Vec<(String, String)> = Vec::new(); // (tag, line), unwrapped
    let flush = |calls: &mut Vec<Call>, lines: &mut Vec<(String, String)>| {
        if let Some(call) = calls.last_mut() {
            let join = |tag: &str| lines.iter().filter(|(t, _)| t == tag).map(|(_, l)| l.as_str()).collect::<Vec<_>>().join("\n");
            call.argv = join("$");
            call.stdin = join("in");
            call.stdout = join("out");
            call.stderr = join("err");
        }
        lines.clear();
    };
    let mut pending: Option<(String, String)> = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("=== ") {
            flush(&mut calls, &mut lines);
            let (title, code) = rest.rsplit_once(" (exit ").expect("a call header");
            calls.push(Call { title: title.to_string(), code: code.trim_end_matches(')').parse().unwrap(), ..Call::default() });
            continue;
        }
        if line.is_empty() && pending.is_none() {
            continue; // the blank line after a call
        }
        let (tag, body) = if let Some(b) = line.strip_prefix("$ ") {
            ("$", b)
        } else {
            let (tag, body) = line.split_once("| ").unwrap_or_else(|| (line.trim_end_matches('|'), ""));
            (tag.trim(), body)
        };
        let (body, cont) = match body.strip_suffix(WRAP) {
            Some(b) => (b, true),
            None => (body, false),
        };
        let mut whole = match pending.take() {
            Some((t, before)) => {
                assert_eq!(t, tag, "a wrapped line goes on in the same stream");
                before + body
            }
            None => body.to_string(),
        };
        if cont {
            pending = Some((tag.to_string(), std::mem::take(&mut whole)));
        } else {
            lines.push((tag.to_string(), whole));
        }
    }
    flush(&mut calls, &mut lines);
    calls
}

/// The options that take a value, and those of them that are before the command (they take one word).
const GLOBAL: [&str; 3] = ["--me", "--state-dir", "--relay"];
const FLAGS: [&str; 15] = [
    "--participants",
    "--context",
    "--x-reject",
    "--x-ideal",
    "--option",
    "--format",
    "--schema",
    "--satisfaction-threshold",
    "--timeout",
    "--retries",
    "--budget",
    "--poll",
    "--from",
    "--me",
    "--relay",
];

/// The arguments of a call, from the line that has them joined by spaces: a value runs to the next option.
fn arguments(line: &str) -> Vec<String> {
    let words: Vec<&str> = line.split(' ').collect();
    let mut out: Vec<String> = Vec::new();
    let mut i = 0;
    let mut command = false;
    while i < words.len() {
        let w = words[i];
        if !command && GLOBAL.contains(&w) {
            out.push(w.to_string());
            out.push(words[i + 1].to_string());
            i += 2;
        } else if command && FLAGS.contains(&w) {
            out.push(w.to_string());
            let mut j = i + 1;
            while j < words.len() && !FLAGS.contains(&words[j]) {
                j += 1;
            }
            out.push(words[i + 1..j].join(" "));
            i = j;
        } else {
            command = true;
            out.push(w.to_string());
            i += 1;
        }
    }
    out
}

/// The normalisation of a replay, kept across its calls.
#[derive(Default)]
struct Norm {
    uuids: HashMap<String, usize>,
    real: HashMap<usize, String>,
    tmp: String,
}

impl Norm {
    fn norm(&mut self, text: &str) -> String {
        let text = text
            .replace(&self.tmp, "<TMP>")
            .replace("uvx ioc-concord initiator", "concord_initiator.py")
            .replace("uvx ioc-concord receiver", "concord_receiver.py");
        let text = UUID
            .replace_all(&text, |c: &regex::Captures| {
                let n = self.uuids.len() + 1;
                let n = *self.uuids.entry(c[0].to_string()).or_insert(n);
                self.real.insert(n, c[0].to_string());
                format!("<UUID-{n}>")
            })
            .into_owned();
        WAKE.replace_all(&CHECK.replace_all(&text, r#"check="<CHECK>""#), "<WAKE>").into_owned()
    }

    /// The normal form of `text` with what is known so far, without learning anything from it.
    fn peek(&self, text: &str) -> String {
        let text = UUID.replace_all(text, |c: &regex::Captures| match self.uuids.get(&c[0]) {
            Some(n) => format!("<UUID-{n}>"),
            None => "<UUID-?>".to_string(),
        });
        CHECK.replace_all(&text, r#"check="<CHECK>""#).into_owned()
    }
}

fn replay(name: &str) {
    let text = std::fs::read_to_string(transcripts().join(format!("{name}.txt"))).expect("the transcript");
    let calls = parse(&text);
    assert!(!calls.is_empty(), "{name}: no call");
    let dir = std::env::temp_dir().join(format!("concord-replay-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let mut norm = Norm { tmp: dir.display().to_string(), ..Norm::default() };
    let mut sent: Vec<String> = Vec::new();
    for (n, call) in calls.iter().enumerate() {
        let what = format!("{name}, call {} ({})", n + 1, call.title);
        let role = call.title.split(' ').next().unwrap();
        let args: Vec<String> = std::iter::once(role.to_string()).chain(arguments(&call.argv)).collect();
        let stdin = match sent.iter().find(|s| norm.peek(s) == call.stdin) {
            Some(real) => real.clone(),
            None => {
                let real = PLACEHOLDER.replace_all(&call.stdin, |c: &regex::Captures| norm.real[&c[1].parse::<usize>().unwrap()].clone());
                assert!(!real.contains("<CHECK>"), "{what}: the message on standard input was never printed");
                real.into_owned()
            }
        };
        let mut ctx = Context::new(&dir, StringInput::new(stdin.clone()));
        let out = run(&args, &mut ctx);
        assert_eq!(args[1..].iter().map(|a| norm.norm(a)).collect::<Vec<_>>().join(" "), call.argv, "{what}: the arguments");
        assert_eq!(norm.norm(&stdin), call.stdin, "{what}: the standard input");
        let got = norm.norm(&out.stdout);
        assert_eq!(got, call.stdout, "{what}: what it printed");
        assert_eq!(norm.norm(&out.stderr), call.stderr, "{what}: the error stream");
        assert_eq!(out.code, call.code, "{what}: the exit code");
        sent.extend(SECTION.captures_iter(&out.stdout).map(|c| c[1].trim_end_matches('\n').to_string()));
    }
    std::fs::remove_dir_all(&dir).ok();
}

macro_rules! transcripts {
    ($($name:ident),* $(,)?) => {
        $(
            #[test]
            fn $name() {
                replay(stringify!($name));
            }
        )*

        #[test]
        fn every_transcript_is_replayed() {
            let mut names: Vec<String> = std::fs::read_dir(transcripts())
                .unwrap()
                .filter_map(|e| e.ok()?.file_name().to_str()?.strip_suffix(".txt").map(str::to_string))
                .collect();
            names.sort();
            let mut replayed = vec![$(stringify!($name).to_string()),*];
            replayed.sort();
            assert_eq!(names, replayed);
        }
    };
}

transcripts!(
    best_effort_budget_spent,
    initiator_errors,
    initiator_takes_part,
    open_schema_with_pattern,
    receiver_errors,
    receiver_schema_enforcement,
    repair_then_success,
    silent_agent_aborts,
    star_centre_takes_part,
    star_with_relay,
    success_at_score,
    three_agents_success,
);

#[test]
fn a_transcript_is_read_back_as_it_was_written() {
    let text = "=== initiator ini start (exit 0)\n$ --me ini start --participants a,b --context Pick one option --format a one-line \u{23ce}\n$ proposal\n in| x\nout| STATUS: SEND\nout| a long \u{23ce}\nout| line\nout| \n\n";
    let calls = parse(text);
    assert_eq!(calls.len(), 1);
    assert_eq!((calls[0].title.as_str(), calls[0].code, calls[0].stdin.as_str()), ("initiator ini start", 0, "x"));
    assert_eq!(calls[0].stdout, "STATUS: SEND\na long line\n");
    assert_eq!(
        arguments(&calls[0].argv),
        ["--me", "ini", "start", "--participants", "a,b", "--context", "Pick one option", "--format", "a one-line proposal"]
    );
}
