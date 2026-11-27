//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The commands both roles have (`receive`, `tick`, `wait` and `report`), and the command line: what a role adds is in its own module.

use std::time::Duration;

use crate::state::RunStatus;

use super::args::{self, Args};
use super::common::Context;
use super::out::{Fail, Out};
use super::py;
use super::session::{now, Config, Session, TaskText, DEFAULT_STATE_DIR};

/// The seconds between two looks at the state while `wait` waits, unless `--poll` says otherwise.
pub(crate) const POLL: f64 = 5.0;

/// Open a session, run `body` in it, and end it well (the sidecar is saved) only when `body` did.
pub(crate) fn with_session<T>(
    cfg: &Config,
    ctx: &Context,
    guard: bool,
    body: impl FnOnce(&mut Session<'_>) -> Result<T, Fail>,
) -> Result<T, Fail> {
    let mut s = Session::open(cfg, ctx, guard)?;
    let out = body(&mut s)?;
    s.finish()?;
    Ok(out)
}

/// A message arrived: any text, what is not CONCORD is ignored.
pub(crate) fn run_receive(cfg: &Config, ctx: &Context, text: &str) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| {
        let before = s.before();
        let step = s.agent.receive(text, None)?;
        s.compose(Some(&step), before)
    })
}

/// Time has passed: ask again for what is late, or give up on a silent agent.
pub(crate) fn run_tick(cfg: &Config, ctx: &Context) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| {
        s.need_run()?;
        let before = s.before();
        let step = s.agent.tick(None)?;
        s.compose(Some(&step), before)
    })
}

/// How the run stands or ended.
pub(crate) fn run_report(cfg: &Config, ctx: &Context) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| s.report_out())
}

/// The timer, for a host that can run a command in the background: block until the next timer is due, tick, and return what the tick
/// produced. The lock is held for each look at the state and never while sleeping, so the other commands work meanwhile.
pub(crate) fn run_wait(cfg: &Config, ctx: &Context, poll: f64) -> Result<Out, Fail> {
    loop {
        let found = with_session(cfg, ctx, true, |s| {
            let status = s.need_run()?;
            if status.status != RunStatus::Active {
                // Not the END again: it was printed when the run ended, and `report` prints it on demand. A model that starts `wait` again
                // each time it returns must be told to stop, and not be given a result to report once more.
                let mut out = Out::new("DONE");
                out.next = format!(
                    "The run is over, so there is nothing to wait for: do not start `{}` again. `{}` prints the result again. If messages of \
this run still arrive, pass each one to `{}` and send only what it prints.",
                    s.command("wait", None),
                    s.command("report", None),
                    s.command("receive", None)
                );
                return Ok(Ok(out));
            }
            let Some(wake) = s.agent.next_wakeup() else {
                let mut out = Out::new("WAIT");
                out.next = format!(
                    "No timer runs: do not start `{}` again until a call prints a WAKE. Pass every message of this run you receive, exactly \
as received, one per call, to `{}`.",
                    s.command("wait", None),
                    s.command("receive", None)
                );
                return Ok(Ok(out));
            };
            let mut delay = wake - now(); // the clock of the agent, not the time of day
            if delay <= 0.0 {
                let before = s.before();
                let step = s.agent.tick(None)?;
                // It returns for what the tick sent or ended, and not for a task that is due: a tick never makes a task due (it asks again,
                // or aborts, which removes them), so a task that is due now was due before, and the model was shown it when it became due.
                // Returning for it would return at once, again and again, for as long as the answer is owed.
                let ended = s.agent.status(None).is_some_and(|st| st.status != RunStatus::Active);
                if !step.send.is_empty() || ended {
                    return s.compose(Some(&step), before).map(Ok); // composed only now: it marks the run as briefed
                }
                delay = poll; // nothing to send (answered meanwhile): look again after a poll interval, not at once
            }
            Ok(Err(delay))
        })?;
        match found {
            Ok(out) => return Ok(out),
            Err(delay) => {
                let secs = py::max(0.05, py::min(poll, delay));
                std::thread::sleep(Duration::try_from_secs_f64(secs).unwrap_or(Duration::MAX));
            }
        }
    }
}

/// The text a command reads on its standard input: a fault when there is none, or it is empty.
fn read_stdin(ctx: &mut Context, command: &str) -> Result<String, Fail> {
    let Some(bytes) = ctx.input.read() else {
        return Err(Fail::cli(format!("{command} reads standard input and there is none"), "Pipe the text in, or use a here-document."));
    };
    let text = match String::from_utf8(bytes) {
        Ok(text) => text,
        Err(e) => {
            let detail = py::utf8_error(e.as_bytes(), &e.utf8_error());
            return Err(Fail::cli(
                format!("{command} got a standard input that is not UTF-8 text ({detail})"),
                "Pass the text exactly as you received it, in UTF-8.",
            ));
        }
    };
    if py::strip(&text).is_empty() {
        return Err(Fail::cli(format!("{command} got an empty standard input"), "Pass the text exactly as you received it."));
    }
    Ok(text)
}

/// The commands of a role besides `receive`, `tick`, `wait` and `report`: their output, or None when the command is not one of them.
pub(crate) type OwnCommands = fn(&Config, &mut Context, &Args, Option<&str>) -> Option<Result<Out, Fail>>;

/// What a role adds to the command line: its name, the commands of its own, and what the text of a task carries.
pub(crate) struct Role {
    pub name: &'static str,
    pub prog: &'static str,
    pub task_text: Option<TaskText>,
    pub own: OwnCommands,
}

/// Read the command line of `role`, run the command, and say what to print and the exit code: 0, or 2 for an ERROR.
pub(crate) fn cli_main(role: &Role, argv: &[String], ctx: &mut Context) -> (String, i32) {
    match main(role, argv, ctx) {
        Ok(out) => {
            let code = if out.status == "ERROR" { super::out::EXIT_ERROR } else { 0 };
            (out.text(), code)
        }
        Err(fail) => fail.printed(),
    }
}

fn main(role: &Role, argv: &[String], ctx: &mut Context) -> Result<Out, Fail> {
    let args = args::parse(role.name, role.prog, argv)?;
    let command = args.command.clone();
    let spec = args::commands(role.name).into_iter().find(|c| c.name == command).expect("a command of the role");
    let text = if spec.stdin { Some(read_stdin(ctx, &command)?) } else { None };
    let spokes = match args.text("relay") {
        Some(relay) => relay.split(',').map(py::strip).filter(|s| !s.is_empty()).map(str::to_string).collect(),
        None => Vec::new(),
    };
    let state_dir = args.text("state_dir").filter(|d| !d.is_empty()).unwrap_or(DEFAULT_STATE_DIR).to_string();
    let cfg = Config { prog: role.prog, me: args.text("me").unwrap_or_default().to_string(), state_dir, spokes, task_text: role.task_text };
    if let Some(result) = (role.own)(&cfg, ctx, &args, text.as_deref()) {
        return result;
    }
    let ctx = &*ctx;
    match command.as_str() {
        "receive" => run_receive(&cfg, ctx, text.as_deref().unwrap_or_default()),
        "tick" => run_tick(&cfg, ctx),
        "wait" => run_wait(&cfg, ctx, args.float("poll").unwrap_or(POLL)),
        "report" => run_report(&cfg, ctx),
        other => unreachable!("the command {other} is the role's own"),
    }
}
