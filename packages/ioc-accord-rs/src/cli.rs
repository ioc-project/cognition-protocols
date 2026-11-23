//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The `ioc-accord` command, as a library function (feature `cli`): the command line of `accord_initiator.py`, ported faithfully. The same
//! subcommands (`init`, `roster`, `frame`, `glossary`, `verify`, `lock`, `execution-message`, `status`, `handle`) with the same flags, the
//! same JSON on the standard output, the same exit codes and the same state file. The programs that call `run` (a binary, a Python entry
//! point) only pass the arguments in and put what comes out on the screen.
//!
//! * `run(args, context)`: the command, with the working folder, the environment and the input it is given
//! * `Output`: what it printed, on the standard output and the error output, and its exit code
//! * `Input`: where `-` reads from (`--responses -`, `--assignment-file -`)
//!
//! Every command prints one JSON object, as the script prints it (`json.dumps(..., indent=2)` and a line break): `{"ok": true, "phase":
//! <command>, "data": ...}` when it went through, `{"ok": false, "phase", "status": "ERROR", "error", "next"}` when it did not (exit code
//! 1), with the phase `usage` and an error `usage error: ...` for a command line the parser refused (exit code 2).

mod common;
mod initiator;

pub use common::{Context, Input, Output, StdinInput, StringInput};

use serde_json::json;

use crate::pyjson::dumps_pretty;

/// Run `ioc-accord <args>`: the arguments as the script takes them (`args[0]` is the global `--state` or the command). The command does
/// its own printing into the `Output`; it never exits, and never fails with anything but an exit code in it.
pub fn run(args: &[String], context: &mut Context) -> Output {
    let mut out = Output::default();
    initiator::run(args, context, &mut out);
    out
}

/// What a command prints when it went through.
pub(crate) fn render_ok(phase: &str, data: serde_json::Value) -> String {
    dumps_pretty(&json!({"ok": true, "phase": phase, "data": data}))
}

/// An error in the same shape as every other result: a status, what happened, what to do (`hint_for` the message).
pub(crate) fn render_error(phase: &str, message: &str) -> String {
    let text = crate::py::strip(message);
    dumps_pretty(&json!({"ok": false, "phase": phase, "status": "ERROR", "error": text, "next": crate::hint_for(text)}))
}
