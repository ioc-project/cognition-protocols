//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `ioc-concord initiator`: opens a run (`start`), and follows it to its end; the centre of a star passes the messages of the spokes on
//! (`forward`). An initiator that takes part in its run is handed over to the receiver after `start`.

use serde_json::{json, Value};

use crate::errors::ConcordError;
use crate::protocol::Num;
use crate::state::Role;
use crate::wire::InitParams;

use super::args::Args;
use super::commands::{self, with_session};
use super::common::Context;
use super::out::{strerror, wake_text, Fail, Out};
use super::py;
use super::session::{now, Config, INITIATOR, RECEIVER};

pub(crate) const ROLE: commands::Role = commands::Role { name: "initiator", prog: INITIATOR, task_text: None, own };

fn own(cfg: &Config, ctx: &mut Context, args: &Args, text: Option<&str>) -> Option<Result<Out, Fail>> {
    match args.command.as_str() {
        "start" => Some(run_start(cfg, ctx, args)),
        "forward" => Some(run_forward(cfg, ctx, args.text("origin"), text.unwrap_or_default())),
        _ => None,
    }
}

/// A text argument: itself, or the contents of the file when it is written @path (UTF-8). A text that starts with @ is written @@...
fn text_arg(ctx: &Context, value: &str) -> Result<String, Fail> {
    if let Some(rest) = value.strip_prefix("@@") {
        return Ok(format!("@{rest}"));
    }
    let Some(path) = value.strip_prefix('@').filter(|p| !p.is_empty()) else { return Ok(value.to_string()) };
    let bytes = std::fs::read(ctx.path(path)).map_err(|e| {
        Fail::cli(
            format!("cannot read {path}: {}", strerror(&e)),
            "Give the text itself, or the path of a file that exists. A text that starts with @ is written with @@.",
        )
    })?;
    let text = String::from_utf8(bytes)
        .map_err(|_| Fail::cli(format!("{path} is not UTF-8 text"), "Save the file as UTF-8, or give the text itself."))?;
    // read as text, as Python reads a file: a line ends in \n whatever it ended in
    Ok(py::strip(&text.replace("\r\n", "\n").replace('\r', "\n")).to_string())
}

/// The candidate schema the arguments say: exactly one of the three ways to say it.
fn schema_of(ctx: &Context, args: &Args) -> Result<Value, Fail> {
    let options = args.list("option");
    let format = args.text("format_text").filter(|t| !t.is_empty());
    let schema = args.text("schema").filter(|t| !t.is_empty());
    let given: Vec<&str> = [("--option", !options.is_empty()), ("--format", format.is_some()), ("--schema", schema.is_some())]
        .iter()
        .filter(|(_, g)| *g)
        .map(|(n, _)| *n)
        .collect();
    if given.len() != 1 {
        return Err(Fail::cli(
            format!(
                "give exactly one of --option (a closed list of possible answers), --format (the open format, in words) and --schema (a \
JSON Schema); you gave {}",
                if given.is_empty() { "none".to_string() } else { given.join(", ") }
            ),
            "If the task gave you a closed list of possible answers, use --option once for each; otherwise describe in words what an \
answer looks like with --format. Run `start --help` for what each means.",
        ));
    }
    if !options.is_empty() {
        return Ok(json!({ "enum": options })); // each option as it is written: an option that starts with @ is not a file
    }
    if let Some(format) = format {
        return Ok(json!({"type": "string", "description": text_arg(ctx, format)?}));
    }
    py::json_loads(schema.unwrap_or_default()).map_err(|e| {
        Fail::cli(format!("--schema is not valid JSON ({e})"), "Fix the JSON, or use --option or --format. Nothing was started.")
    })
}

/// Open the run: the init, and what to do next.
fn run_start(cfg: &Config, ctx: &Context, args: &Args) -> Result<Out, Fail> {
    let participants: Vec<String> =
        args.text("participants").unwrap_or_default().split(',').map(py::strip).filter(|n| !n.is_empty()).map(str::to_string).collect();
    let params = InitParams {
        satisfaction_threshold: args.text("threshold").map(|t| Num::Text(t.to_string())),
        eps: None,
        budget: args.int("budget"),
        timeout: args.int("timeout"),
        retries: args.int("retries"),
    };
    let spec = schema_of(ctx, args)?;
    with_session(cfg, ctx, false, |s| {
        let spokes = &s.cfg.spokes;
        let unreachable: Vec<&String> = participants.iter().filter(|n| **n != s.me && !spokes.is_empty() && !spokes.contains(n)).collect();
        if !unreachable.is_empty() {
            // the init goes to the spokes: a participant that is not one never gets it
            let one = unreachable.len() == 1;
            let names: Vec<&str> = unreachable.iter().map(|n| n.as_str()).collect();
            return Err(Fail::cli(
                format!(
                    "{} {} but not {} (--relay {}): the init could never reach them",
                    names.join(", "),
                    if one { "is a participant" } else { "are participants" },
                    if one { "one of the spokes" } else { "spokes" },
                    spokes.join(",")
                ),
                "Add them to --relay, or leave them out of --participants. Nothing was started.",
            ));
        }
        let context = text_arg(ctx, args.text("context").unwrap_or_default())?;
        let x_reject = text_arg(ctx, args.text("x_reject").unwrap_or_default())?;
        let x_ideal = text_arg(ctx, args.text("x_ideal").unwrap_or_default())?;
        let step = match s.agent.start(&participants, &context, &x_reject, &x_ideal, &spec, Some(&params), None, None) {
            Ok(step) => step,
            Err(ConcordError::State(_)) => {
                let report = s.command("report", None);
                return Err(Fail::cli(
                    format!("{} is in a run already", s.me),
                    format!(
                        "`{report}` says how it stands. Start another run only when it is over; to run two at once use another --me or \
--state-dir."
                    ),
                ));
            }
            Err(ConcordError::InvalidMessage(e)) => {
                return Err(Fail::cli(
                    format!("the run is not valid: {e}"),
                    "Fix the argument it names and run `start` again. Nothing was started.",
                ))
            }
            Err(e) => return Err(e.into()),
        };
        if !s.cfg.spokes.is_empty() {
            let mut relay = s.relay()?;
            for sent in &step.send {
                relay.learn(sent)?; // the spokes learn the run from this message, and so does the centre
            }
        }
        if s.run().is_none_or(|run| run.role != Role::Participant) {
            return s.compose(Some(&step), None);
        }
        let mut out = Out::new("SEND");
        s.add_sends(&mut out, &step.send)?;
        if let Some(wake) = s.agent.next_wakeup() {
            out.add("WAKE", &wake_text(wake, now()));
        }
        out.next = format!(
            "Broadcast each SEND block to the agents on its `to:` line, exactly as printed and without that line. Never write or change \
a message. You take part in this run, so from now on use only the receiver script, never this one: run `{}` to see what you owe \
first, and follow its NEXT line.",
            s.command("task", Some(RECEIVER))
        );
        if !s.cfg.spokes.is_empty() {
            out.next.push_str(&format!(
                " You are also the centre of a star: for every message that comes from a spoke, first run `{}` and deliver what it prints, \
then pass the same message to `{}`.",
                s.command("forward --from <the spoke>", None),
                s.command("receive", Some(RECEIVER))
            ));
        }
        Ok(out)
    })
}

/// The messages to pass on to the spokes, for the centre of a star: the text as it came, to every spoke but the one it came from.
fn run_forward(cfg: &Config, ctx: &Context, origin: Option<&str>, text: &str) -> Result<Out, Fail> {
    with_session(cfg, ctx, false, |s| {
        let spokes = &s.cfg.spokes;
        if spokes.is_empty() {
            return Err(Fail::cli(
                "forward is for the centre of a star and needs --relay (the names of the spokes)",
                "Give --relay before the command, on this call and on every call of the run.",
            ));
        }
        if let Some(origin) = origin.filter(|o| !spokes.iter().any(|s| s == o)) {
            return Err(Fail::cli(
                format!("{origin} is not one of the spokes (--relay {})", spokes.join(",")),
                "Give --from the name of the spoke that sent the message, as written in --relay, or leave it out.",
            ));
        }
        let mut relay = s.relay()?;
        let passed = relay.receive(text)?;
        let targets = relay.targets(origin);
        let mut out = Out::new(if !passed.is_empty() && !targets.is_empty() { "SEND" } else { "WAIT" });
        if !targets.is_empty() {
            for sent in &passed {
                out.add(format!("FORWARD to: {}", targets.join(", ")), py::strip(sent));
            }
        }
        if out.sections.is_empty() {
            out.add(
                "NOTE",
                if passed.is_empty() {
                    "not passed on: it is not a message of a run this centre knows"
                } else {
                    "no other spoke to pass it to"
                },
            );
        }
        let script = if s.run().is_some_and(|run| run.role == Role::Participant) { RECEIVER } else { INITIATOR };
        out.next = format!(
            "{}Then pass the same message to `{}`.",
            if out.status == "SEND" {
                "Deliver each FORWARD block to the spokes named on its `to:` line, exactly as printed and without that line. "
            } else {
                ""
            },
            s.command("receive", Some(script))
        );
        Ok(out)
    })
}
