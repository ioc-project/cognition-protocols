//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The `ioc-concord` command, as a library function (feature `cli`): `ioc-concord initiator ...` opens and drives a run, `ioc-concord
//! receiver ...` takes part in one. Every output starts with a status (`SEND`, `ASK`, `NONE` or `ERROR`) and says what to do next; the
//! wording is in the module that prints it (the shared lines here) and in `guidance`, and the state is kept by the agent (see `state::Session`). The programs that call `run` (a
//! binary, a Python entry point) only pass the arguments in and put what comes out on the screen.
//!
//! * `run(args, context)`: the command, with the working folder, the environment and the input it is given
//! * `Output`: what it printed, on the standard output and the error output, and its exit code
//! * `Input`: where a command reads the messages it is passed when no argument holds them

mod common;
mod initiator;
mod receiver;

use std::time::Duration;

pub use common::{Context, Input, Output, StdinInput, StringInput};

use common::Fail;

/// Run `ioc-concord <args>`: `args[0]` is the role (`initiator` or `receiver`). The command does its own printing into the `Output`; it
/// never exits, and never fails with anything but an exit code in it.
pub fn run(args: &[String], context: &mut Context) -> Output {
    let mut out = Output::default();
    let result = match args.first().map(String::as_str) {
        Some("initiator") => {
            initiator::run(&args[1..], context, &mut out).map_err(|f| f.with_hints(INITIATOR_USAGE_HINT, INITIATOR_FIX_HINT))
        }
        Some("receiver") => receiver::run(&args[1..], context, &mut out).map_err(|f| f.with_hints(RECEIVER_USAGE_HINT, RECEIVER_FIX_HINT)),
        None | Some("-h" | "--help") => {
            out.out(USAGE);
            Ok(())
        }
        Some(other) => Err(Fail::Usage(format!("unknown command {other:?}"), Some(USAGE_HINT.to_string()))),
    };
    if let Err(fail) = result {
        out.code = fail.print(&mut out);
    }
    out
}

/// How long a command waits for input that nobody has started to write, when it is not given an argument.
pub(crate) const WAIT: Duration = Duration::from_secs(1);

// ---------------------------------------------------------------------------------------------------------------------------
// what every output looks like
// ---------------------------------------------------------------------------------------------------------------------------

/// The name of the command.
pub(crate) const COMMAND: &str = "uvx ioc-concord";

/// An output: the status on the first line, then the text.
pub(crate) fn render(status: &str, text: &str) -> String {
    format!("STATUS: {status}\n{text}")
}

/// An error in the same shape as every other output: a status, what happened, what to do. `hint` is what to do when the caller knows
/// better than `guidance::hint_for_text` does.
pub(crate) fn render_error(message: &str, hint: Option<&str>) -> String {
    let hint = hint.unwrap_or_else(|| crate::guidance::hint_for_text(message));
    format!("STATUS: ERROR\n{}\nNEXT: {hint}", message.trim())
}

/// The message of a command line the parser refused (an unknown command, a missing argument).
pub(crate) fn usage_error(detail: &str) -> String {
    format!("usage error: {}", detail.trim_start_matches("error: "))
}

/// What to do about an unknown command of `ioc-concord`.
pub(crate) const USAGE_HINT: &str = "Run `uvx ioc-concord initiator --help` or `uvx ioc-concord receiver --help`.";

/// What to do about a command line of the initiator that the parser refused.
pub(crate) const INITIATOR_USAGE_HINT: &str = "You need two commands only: `init` once (see `uvx ioc-concord initiator init --help`), then `handle`: with no input for the first message, then with each reply on stdin. Use the state path `init` printed. `--help` lists the rest.";

/// What to do about any other error of the initiator.
pub(crate) const INITIATOR_FIX_HINT: &str = "Fix what the message says and run the same command again. Use the exact state path `init` printed (run `init` first if there is none). Never work around it by writing a message, a rating or a reply yourself.";

/// What to do about a command line of the receiver that the parser refused.
pub(crate) const RECEIVER_USAGE_HINT: &str = "You need two commands only: `--me <you> handle` with each CONCORD message or reply (on stdin), and `--me <you> answer` with the flags the task shows, when `handle` printed STATUS: ASK (`due` prints the task and its exact command again). Copy the command as the task printed it.";

/// What to do about any other error of the receiver that has no hint of its own: the one of `guidance`.
pub(crate) const RECEIVER_FIX_HINT: &str =
    "Fix what the message says and run the same command again. Never work around it by writing a reply, a rating or XML yourself.";

/// The usage of `ioc-concord`.
pub(crate) const USAGE: &str = "usage: ioc-concord {initiator,receiver} ...\n\n  initiator  open a CONCORD run and drive it: init, then handle with each reply\n  receiver   take part in a run: handle each message you get, answer when it asks you";
