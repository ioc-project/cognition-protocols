//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The `ioc-concord` command, as a library function (feature `cli`): `ioc-concord initiator ...` opens a run and follows it,
//! `ioc-concord receiver ...` takes part in one. It is the command line of the CONCORD skill scripts (`concord_initiator.py` and
//! `concord_receiver.py`), the same commands, options, output, exit codes and state files, run by this crate: what the scripts printed
//! for `concord_initiator.py --me a receive` this prints for `uvx ioc-concord initiator --me a receive`. The programs that call `run` (a
//! binary, a Python entry point) only pass the arguments in and put what comes out on the screen.
//!
//! Every call prints `STATUS:` (`SEND`, `ANSWER`, `WAIT`, `DONE` or `ERROR`), sections that each start with a `--- TITLE` line, and a
//! `NEXT:` line that says what to do. The exit code is 0, or 2 for an `ERROR`. The state of an agent is kept in
//! `concord-state-<me>.json` in `--state-dir` (the current directory by default); see `session`.
//!
//! * `run(args, context)`: the command, with the working folder and the input it is given
//! * `Output`: what it printed, on the standard output and the error output, and its exit code
//! * `Input`: where a command reads the text it is passed (`receive`, `answer`, `forward`)

mod args;
mod commands;
mod common;
mod help;
mod initiator;
mod out;
mod py;
mod receiver;
mod session;

pub use common::{Context, Input, Output, StdinInput, StringInput};

/// The usage of `ioc-concord` without a role.
const USAGE: &str = "usage: ioc-concord {initiator,receiver} ...

  initiator  open a CONCORD run and follow it to its end (`ioc-concord initiator --help`)
  receiver   take part in a run (`ioc-concord receiver --help`)
";

/// Run `ioc-concord <args>`: `args[0]` is the role (`initiator` or `receiver`), the rest is the command line of that role. The command does
/// its own printing into the `Output`; it never exits, and never fails with anything but an exit code in it.
pub fn run(args: &[String], context: &mut Context) -> Output {
    let (stdout, code) = match args.first().map(String::as_str) {
        Some("initiator") => commands::cli_main(&initiator::ROLE, &args[1..], context),
        Some("receiver") => commands::cli_main(&receiver::ROLE, &args[1..], context),
        None | Some("-h" | "--help") => (USAGE.to_string(), 0),
        Some(other) => (
            out::error_text(
                &format!("argument role: invalid choice: {} (choose from 'initiator', 'receiver')", py::repr(other)),
                "Run `uvx ioc-concord initiator --help` or `uvx ioc-concord receiver --help`.",
            ),
            out::EXIT_ERROR,
        ),
    };
    Output { stdout, stderr: String::new(), code }
}
