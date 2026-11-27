//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The command line, read as `argparse` read the skill scripts' (and refused with its words): options before the command (`--me`,
//! `--state-dir`, and `--relay` for the initiator), then the command and its own options. A long option may be shortened to any prefix
//! that names one option only, and given as `--name=value`; an option that is given twice keeps the last value (`--option` keeps them
//! all); `-h` or `--help` prints the help; what is not understood is an error that says how to get the help.

use std::collections::HashMap;

use super::out::Fail;
use super::py;

/// What an option takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Kind {
    Text,
    Int,
    Float,
    /// A text, once for each time the option is given.
    Append,
    Help,
}

/// An option: its spellings, the name of its value, what it takes, and whether it must be given.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Opt {
    pub flags: &'static [&'static str],
    pub dest: &'static str,
    pub kind: Kind,
    pub required: bool,
}

const fn opt(flag: &'static [&'static str], dest: &'static str, kind: Kind, required: bool) -> Opt {
    Opt { flags: flag, dest, kind, required }
}

const HELP: Opt = opt(&["-h", "--help"], "help", Kind::Help, false);

/// A command of a role: its name and its options (besides `-h`).
pub(crate) struct CommandSpec {
    pub name: &'static str,
    pub opts: &'static [Opt],
    /// Whether the command reads its standard input.
    pub stdin: bool,
}

/// The options before the command, for `role`.
fn global_opts(role: &str) -> Vec<Opt> {
    let mut opts = vec![HELP, opt(&["--me"], "me", Kind::Text, true), opt(&["--state-dir"], "state_dir", Kind::Text, false)];
    if role == "initiator" {
        opts.push(opt(&["--relay"], "relay", Kind::Text, false));
    }
    opts
}

const POLL: &[Opt] = &[opt(&["--poll"], "poll", Kind::Float, false)];
const START: &[Opt] = &[
    opt(&["--participants"], "participants", Kind::Text, true),
    opt(&["--context"], "context", Kind::Text, true),
    opt(&["--x-reject"], "x_reject", Kind::Text, true),
    opt(&["--x-ideal"], "x_ideal", Kind::Text, true),
    opt(&["--option"], "option", Kind::Append, false),
    opt(&["--format"], "format_text", Kind::Text, false),
    opt(&["--schema"], "schema", Kind::Text, false),
    opt(&["--satisfaction-threshold"], "threshold", Kind::Text, false),
    opt(&["--timeout"], "timeout", Kind::Int, false),
    opt(&["--retries"], "retries", Kind::Int, false),
    opt(&["--budget"], "budget", Kind::Int, false),
];
const FROM: &[Opt] = &[opt(&["--from"], "origin", Kind::Text, false)];

/// The commands of `role`, in the order of its help.
pub(crate) fn commands(role: &str) -> Vec<CommandSpec> {
    let c = |name, opts, stdin| CommandSpec { name, opts, stdin };
    if role == "initiator" {
        vec![
            c("start", START, false),
            c("receive", &[], true),
            c("tick", &[], false),
            c("wait", POLL, false),
            c("report", &[], false),
            c("forward", FROM, true),
        ]
    } else {
        vec![
            c("receive", &[], true),
            c("answer", &[], true),
            c("tick", &[], false),
            c("wait", POLL, false),
            c("task", &[], false),
            c("status", &[], false),
            c("report", &[], false),
        ]
    }
}

/// A value of an option.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Val {
    Text(String),
    Int(i64),
    Float(f64),
    List(Vec<String>),
}

/// What the command line says.
#[derive(Debug, Default)]
pub(crate) struct Args {
    pub command: String,
    values: HashMap<&'static str, Val>,
}

impl Args {
    pub fn text(&self, dest: &str) -> Option<&str> {
        match self.values.get(dest) {
            Some(Val::Text(t)) => Some(t),
            _ => None,
        }
    }

    pub fn int(&self, dest: &str) -> Option<i64> {
        match self.values.get(dest) {
            Some(Val::Int(n)) => Some(*n),
            _ => None,
        }
    }

    pub fn float(&self, dest: &str) -> Option<f64> {
        match self.values.get(dest) {
            Some(Val::Float(n)) => Some(*n),
            _ => None,
        }
    }

    pub fn list(&self, dest: &str) -> Vec<String> {
        match self.values.get(dest) {
            Some(Val::List(l)) => l.clone(),
            _ => Vec::new(),
        }
    }
}

/// How an argument reads to a parser with the options `opts`.
enum Class {
    /// A value, or a command.
    Positional,
    /// An option of the parser, with the value written after `=` (or after a short option).
    Option(Opt, Option<String>),
    /// What looks like an option the parser does not have.
    Unknown,
}

fn negative_number(arg: &str) -> bool {
    let rest = &arg[1..];
    let (int, frac) = rest.split_once('.').map_or((rest, None), |(a, b)| (a, Some(b)));
    match frac {
        None => !int.is_empty() && int.chars().all(|c| c.is_ascii_digit()),
        Some(f) => int.chars().all(|c| c.is_ascii_digit()) && !f.is_empty() && f.chars().all(|c| c.is_ascii_digit()),
    }
}

fn names(o: &Opt) -> String {
    o.flags.join("/")
}

fn classify(arg: &str, opts: &[Opt]) -> Result<Class, String> {
    if !arg.starts_with('-') {
        return Ok(Class::Positional);
    }
    let find = |s: &str| opts.iter().find(|o| o.flags.contains(&s)).copied();
    if let Some(o) = find(arg) {
        return Ok(Class::Option(o, None));
    }
    if arg.len() == 1 {
        return Ok(Class::Positional);
    }
    if let Some((name, value)) = arg.split_once('=') {
        if let Some(o) = find(name) {
            return Ok(Class::Option(o, Some(value.to_string())));
        }
    }
    // a prefix of a long option, or a short option with its value attached
    let mut found: Vec<(Opt, String, Option<String>)> = Vec::new();
    if arg.starts_with("--") {
        let (prefix, explicit) = arg.split_once('=').map_or((arg, None), |(p, v)| (p, Some(v.to_string())));
        for o in opts {
            for f in o.flags {
                if f.starts_with(prefix) {
                    found.push((*o, (*f).to_string(), explicit.clone()));
                }
            }
        }
    } else {
        let short = &arg[..arg.char_indices().nth(2).map_or(arg.len(), |(i, _)| i)];
        for o in opts {
            for f in o.flags {
                if *f == short {
                    found.push((*o, (*f).to_string(), Some(arg[short.len()..].to_string())));
                } else if f.starts_with(arg) {
                    found.push((*o, (*f).to_string(), None));
                }
            }
        }
    }
    if found.len() > 1 {
        let all: Vec<&str> = found.iter().map(|(_, f, _)| f.as_str()).collect();
        return Err(format!("ambiguous option: {arg} could match {}", all.join(", ")));
    }
    if let Some((o, _, explicit)) = found.pop() {
        return Ok(Class::Option(o, explicit));
    }
    if negative_number(arg) || arg.contains(' ') {
        return Ok(Class::Positional);
    }
    Ok(Class::Unknown)
}

/// What the parser of one level ends with.
enum Parsed {
    Done(Vec<String>),
    Help,
}

/// Read `args` with the options `opts`; with `commands`, the first positional argument is the command, and what follows it is read by
/// the parser of that command. `hint` is what the error says to run.
fn parse_level(
    args: &[String],
    opts: &[Opt],
    commands: Option<&[CommandSpec]>,
    out: &mut Args,
    hint: &dyn Fn(&str) -> String,
    level: &str,
) -> Result<Parsed, Fail> {
    let usage = |message: String, at: &str| Fail::Usage(message, hint(at));
    let mut extras = Vec::new();
    let mut seen: Vec<&str> = Vec::new();
    let mut i = 0;
    let mut only_positionals = false;
    while i < args.len() {
        let arg = &args[i];
        let class = if only_positionals {
            Class::Positional
        } else if arg == "--" {
            only_positionals = true;
            i += 1;
            continue;
        } else {
            classify(arg, opts).map_err(|m| usage(m, level))?
        };
        match class {
            Class::Option(o, explicit) => {
                if o.kind == Kind::Help {
                    if let Some(v) = explicit.filter(|_| arg.starts_with("--")) {
                        return Err(usage(format!("argument {}: ignored explicit argument {}", names(&o), py::repr(&v)), level));
                    }
                    return Ok(Parsed::Help);
                }
                let value = match explicit {
                    Some(v) => v,
                    None => {
                        let next = args.get(i + 1).filter(|n| !only_positionals && matches!(classify(n, opts), Ok(Class::Positional)));
                        match next {
                            Some(v) if v != "--" => {
                                i += 1;
                                v.clone()
                            }
                            _ => return Err(usage(format!("argument {}: expected one argument", names(&o)), level)),
                        }
                    }
                };
                let val = match o.kind {
                    Kind::Int => Val::Int(
                        py::parse_int(&value)
                            .ok_or_else(|| usage(format!("argument {}: invalid int value: {}", names(&o), py::repr(&value)), level))?,
                    ),
                    Kind::Float => Val::Float(
                        py::parse_float(&value)
                            .ok_or_else(|| usage(format!("argument {}: invalid float value: {}", names(&o), py::repr(&value)), level))?,
                    ),
                    Kind::Append => {
                        let mut list = match out.values.remove(o.dest) {
                            Some(Val::List(l)) => l,
                            _ => Vec::new(),
                        };
                        list.push(value);
                        Val::List(list)
                    }
                    Kind::Text | Kind::Help => Val::Text(value),
                };
                out.values.insert(o.dest, val);
                seen.push(o.dest);
            }
            Class::Unknown => extras.push(arg.clone()),
            Class::Positional => match commands {
                Some(cmds) => {
                    let Some(c) = cmds.iter().find(|c| c.name == arg) else {
                        let choices: Vec<String> = cmds.iter().map(|c| py::repr(c.name)).collect();
                        let names: Vec<&str> = cmds.iter().map(|c| c.name).collect();
                        let maybe = py::close_match(arg, &names).map(|c| format!(", maybe you meant {}?", py::repr(c))).unwrap_or_default();
                        return Err(usage(
                            format!("argument command: invalid choice: {}{maybe} (choose from {})", py::repr(arg), choices.join(", ")),
                            level,
                        ));
                    };
                    out.command = c.name.to_string();
                    let mut sub_opts = vec![HELP];
                    sub_opts.extend_from_slice(c.opts);
                    match parse_level(&args[i + 1..], &sub_opts, None, out, hint, c.name)? {
                        Parsed::Help => return Ok(Parsed::Help),
                        Parsed::Done(more) => extras.extend(more),
                    }
                    break;
                }
                None => extras.push(arg.clone()),
            },
        }
        i += 1;
    }
    let mut missing: Vec<String> = opts.iter().filter(|o| o.required && !seen.contains(&o.dest)).map(names).collect();
    if commands.is_some() && out.command.is_empty() {
        missing.push("command".to_string());
    }
    if !missing.is_empty() {
        return Err(usage(format!("the following arguments are required: {}", missing.join(", ")), level));
    }
    Ok(Parsed::Done(extras))
}

/// Read the command line of `role` (`args` without the role): the arguments, or the help that was asked for (`Fail::Help`), or what is
/// wrong with them (`Fail::Usage`). `prog` is the name of the command, for the hint.
pub(crate) fn parse(role: &str, prog: &str, args: &[String]) -> Result<Args, Fail> {
    let mut out = Args::default();
    let hint = |level: &str| {
        let command = if level.is_empty() { String::new() } else { format!("{level} ") };
        format!("Run `{prog} {command}--help`.")
    };
    let cmds = commands(role);
    let globals = global_opts(role);
    match parse_level(args, &globals, Some(&cmds), &mut out, &hint, "")? {
        Parsed::Help => {
            let command = (!out.command.is_empty()).then_some(out.command.as_str());
            Err(Fail::Help(super::help::help(role, command).to_string()))
        }
        Parsed::Done(extras) if !extras.is_empty() => Err(Fail::Usage(format!("unrecognized arguments: {}", extras.join(" ")), hint(""))),
        Parsed::Done(_) => Ok(out),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn err(role: &str, line: &[&str]) -> String {
        let args: Vec<String> = line.iter().map(|s| (*s).to_string()).collect();
        match parse(role, "p", &args) {
            Err(Fail::Usage(m, h)) => format!("{m} | {h}"),
            Err(Fail::Help(h)) => format!("HELP {}", h.lines().next().unwrap_or("")),
            other => format!("{other:?}"),
        }
    }

    #[test]
    fn a_command_line_is_refused_with_the_words_of_argparse() {
        assert_eq!(
            err("receiver", &["nonsense"]),
            "argument command: invalid choice: 'nonsense' (choose from 'receive', 'answer', 'tick', 'wait', 'task', 'status', 'report') | Run `p --help`."
        );
        assert_eq!(err("receiver", &["report"]), "the following arguments are required: --me | Run `p --help`.");
        assert_eq!(err("receiver", &[]), "the following arguments are required: --me, command | Run `p --help`.");
        assert_eq!(err("receiver", &["--me"]), "argument --me: expected one argument | Run `p --help`.");
        assert_eq!(
            err("initiator", &["--me", "a", "start"]),
            "the following arguments are required: --participants, --context, --x-reject, --x-ideal | Run `p start --help`."
        );
        assert_eq!(
            err("initiator", &["--me", "a", "start", "--x", "f"]),
            "ambiguous option: --x could match --x-reject, --x-ideal | Run `p start --help`."
        );
        assert_eq!(
            err("initiator", &["--me", "a", "wait", "--poll", "x"]),
            "argument --poll: invalid float value: 'x' | Run `p wait --help`."
        );
        assert_eq!(err("initiator", &["--me", "a", "report", "extra", "--foo"]), "unrecognized arguments: extra --foo | Run `p --help`.");
        assert_eq!(err("initiator", &["--me", "a", "report", "--me", "b"]), "unrecognized arguments: --me b | Run `p --help`.");
        assert_eq!(err("initiator", &["--help=3"]), "argument -h/--help: ignored explicit argument '3' | Run `p --help`.");
        assert_eq!(err("initiator", &["-hx"]), "HELP usage: uvx ioc-concord initiator [-h] --me ME [--state-dir STATE_DIR]");
        assert_eq!(
            err("initiator", &["--me", "a", "forward", "--from"]),
            "argument --from: expected one argument | Run `p forward --help`."
        );
    }

    #[test]
    fn options_are_read_as_argparse_reads_them() {
        let args: Vec<String> = [
            "--m=a",
            "--st",
            "d",
            "start",
            "--part",
            "a,b",
            "--co",
            "c",
            "--x-r",
            "r",
            "--x-i=i",
            "--option",
            "X",
            "--option=Y",
            "--timeout",
            "5",
        ]
        .iter()
        .map(|s| (*s).to_string())
        .collect();
        let a = parse("initiator", "p", &args).unwrap();
        assert_eq!((a.text("me"), a.text("state_dir"), a.command.as_str()), (Some("a"), Some("d"), "start"));
        assert_eq!(
            (a.text("participants"), a.list("option"), a.int("timeout")),
            (Some("a,b"), vec!["X".to_string(), "Y".to_string()], Some(5))
        );
        let args: Vec<String> = ["--me", "-5", "report"].iter().map(|s| (*s).to_string()).collect();
        assert_eq!(parse("receiver", "p", &args).unwrap().text("me"), Some("-5"));
    }
}
