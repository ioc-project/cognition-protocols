//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! One call: the lock, the agent, the sidecar, and the output of what a step produced.
//!
//! The state of an agent is kept in `concord-state-<me>.json` in the state folder (the current directory by default), the state of the
//! library. Next to it, `concord-script-<me>.json` holds what belongs to the command and not to the library: which runs have been
//! briefed, and the last refused answer. A centre of a star keeps the runs it relays in `concord-relay-<me>.json`. Every call holds an
//! exclusive lock on `concord-state-<me>.json.lock` for as long as it looks at or changes the state, so two calls on one state, from
//! either role, run one after the other.

use std::fs::{File, OpenOptions};
use std::path::PathBuf;

use serde_json::{json, Value};

use crate::agent::{Agent, Step};
use crate::errors::ConcordError;
use crate::guidance::{render_delivery, render_end, RUN_DESCRIPTION};
use crate::protocol::is_agent_id;
use crate::relay::Relay;
use crate::report::Status;
use crate::state::{Role, Run, RunStatus};
use crate::store::{FileStore, Store};
use crate::tasks::Task;
use crate::wire::peek;

use super::common::Context;
use super::out::{io_fail, wake_text, Fail, Out};
use super::py;

/// The command of the initiator, as the commands that NEXT prints write it.
pub(crate) const INITIATOR: &str = "uvx ioc-concord initiator";
/// The command of a participant: the one an initiator that takes part uses after `start`.
pub(crate) const RECEIVER: &str = "uvx ioc-concord receiver";
/// The state folder when none is given.
pub(crate) const DEFAULT_STATE_DIR: &str = ".";

/// What the text of a task gets from the command that answers it (the format of the answer), as a function of the task and of that
/// command.
pub(crate) type TaskText = fn(&Task, &str) -> String;

/// What every session of one call is opened with.
pub(crate) struct Config {
    /// `INITIATOR` or `RECEIVER`.
    pub prog: &'static str,
    pub me: String,
    /// The state folder, as `--state-dir` gave it.
    pub state_dir: String,
    /// The spokes of a star, when this agent is its centre (the initiator only).
    pub spokes: Vec<String>,
    pub task_text: Option<TaskText>,
}

/// The current time, as the clock of the agent reads it.
pub(crate) fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or_else(|before| -before.duration().as_secs_f64(), |d| d.as_secs_f64())
}

/// The state of one agent, for the length of one call. The lock is held while it lives, the agent is loaded when it opens (`s.agent`)
/// and saves itself after every call that changes it. `s.side` is saved by `finish`, when the call ends well: what a call that failed
/// was about to show (a brief) must still be shown by the next one.
pub(crate) struct Session<'a> {
    pub cfg: &'a Config,
    ctx: &'a Context,
    pub me: String,
    /// The state folder and the state file, as they are printed.
    pub state_dir: String,
    pub state_path: String,
    pub agent: Agent,
    pub side: Value,
    side_before: Value,
    side_store: FileStore,
    _lock: File,
}

/// Read a JSON file as `FileStore.load` of the scripts did, for its error: the library says what is wrong in its own words, and the
/// scripts said it in Python's. None when there is no file.
fn check_json(path: &PathBuf, shown: &str) -> Result<(), Fail> {
    let Ok(bytes) = std::fs::read(path) else { return Ok(()) };
    let text = match std::str::from_utf8(&bytes) {
        Ok(text) => text,
        Err(e) => return Err(ConcordError::state(format!("state file {shown} is not valid JSON ({})", py::utf8_error(&bytes, &e))).into()),
    };
    match py::json_loads(text) {
        Ok(_) => Ok(()),
        Err(e) => Err(ConcordError::state(format!("state file {shown} is not valid JSON ({e})")).into()),
    }
}

impl<'a> Session<'a> {
    /// Open the session of `cfg.me`. `guard` says whether the guard of the role applies to this call (see `check_role`).
    pub fn open(cfg: &'a Config, ctx: &'a Context, guard: bool) -> Result<Session<'a>, Fail> {
        let me = cfg.me.clone();
        if !is_agent_id(&me) {
            return Err(Fail::cli(
                format!("--me {} is not an agent id: 1 to 64 ASCII letters, digits, _ or -", py::repr(&me)),
                "Run again with the id you were given for this run.",
            ));
        }
        let state_dir = py::path_name(&cfg.state_dir);
        let state_path = py::path_join(&state_dir, &format!("concord-state-{me}.json"));
        let side_path = py::path_join(&state_dir, &format!("concord-script-{me}.json"));
        std::fs::create_dir_all(ctx.path(&state_dir)).map_err(|e| io_fail(&format!("cannot make the folder {state_dir}"), &e))?;
        let lock_path = format!("{state_path}.lock");
        // read and write access: on Windows the lock of a file needs one of them, and an append-only handle has neither
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(ctx.path(&lock_path))
            .map_err(|e| io_fail(&format!("cannot open {lock_path}"), &e))?;
        lock.lock().map_err(|e| io_fail(&format!("cannot lock {lock_path}"), &e))?;
        check_json(&ctx.path(&state_path), &state_path)?;
        let agent = Agent::builder(&me).store(FileStore::new(ctx.path(&state_path))).build()?;
        check_json(&ctx.path(&side_path), &side_path)?;
        let side_store = FileStore::new(ctx.path(&side_path));
        let mut side = match side_store.load() {
            Ok(Value::Object(map)) => Value::Object(map),
            Ok(_) => return Err(ConcordError::state(format!("state file {side_path} does not hold an object")).into()),
            Err(ConcordError::MissingState(_)) => json!({}),
            Err(e) => return Err(e.into()),
        };
        let map = side.as_object_mut().expect("an object");
        map.entry("briefed").or_insert(json!([]));
        map.entry("refusal").or_insert(Value::Null);
        let session = Session { cfg, ctx, me, state_dir, state_path, agent, side_before: side.clone(), side, side_store, _lock: lock };
        if guard {
            session.check_role()?;
        }
        Ok(session)
    }

    /// End the call well: save the sidecar if it changed. The lock is let go when the session is dropped.
    pub fn finish(mut self) -> Result<(), Fail> {
        if self.side != self.side_before {
            self.side_store.save(&self.side)?;
        }
        Ok(())
    }

    fn initiator(&self) -> bool {
        self.cfg.prog == INITIATOR
    }

    /// A fault when this command is not the one for the run the agent is in. An initiator that takes part in the run is a participant:
    /// its tasks and its answers are the receiver's.
    pub fn check_role(&self) -> Result<(), Fail> {
        if !self.initiator() {
            return Ok(());
        }
        if self.run().is_some_and(|run| run.role == Role::Participant) {
            return Err(Fail::cli(
                format!("you take part in this run ({} is one of its participants), so this script does not handle it", self.me),
                format!("Run the same command with the receiver script: `{}`.", self.command("<the same command>", Some(RECEIVER))),
            ));
        }
        Ok(())
    }

    /// The options that go before the command, on every call of this agent with `prog`, as a string that starts with a space. The centre
    /// of a star gives its spokes on every call of the initiator, so the commands that NEXT prints carry them; the receiver has no such
    /// option.
    pub fn global_args(&self, prog: &str) -> String {
        let mut out =
            if self.state_dir == DEFAULT_STATE_DIR { String::new() } else { format!(" --state-dir {}", py::shell_quote(&self.state_dir)) };
        if self.initiator() && !self.cfg.spokes.is_empty() && prog == self.cfg.prog {
            out.push_str(&format!(" --relay {}", self.cfg.spokes.join(",")));
        }
        out
    }

    /// The command line that runs `name` for this agent, as the NEXT lines print it (with the other role, when `prog` is given).
    pub fn command(&self, name: &str, prog: Option<&str>) -> String {
        let prog = prog.unwrap_or(self.cfg.prog);
        format!("{prog} --me {}{} {name}", self.me, self.global_args(prog))
    }

    /// The run the agent is in, or the last one it was in; None before the first.
    pub fn run(&self) -> Option<&Run> {
        let state = &self.agent.state;
        state.active.as_deref().and_then(|id| state.runs.get(id))
    }

    /// The status of the run now, to be given to `compose` after the call that may change it.
    pub fn before(&self) -> Option<RunStatus> {
        self.agent.status(None).map(|s| s.status)
    }

    /// The status of the run, or a fault when the agent has none.
    pub fn need_run(&self) -> Result<Status, Fail> {
        self.agent.status(None).ok_or_else(|| {
            Fail::cli(
                format!("there is no run for {} in {}", self.me, self.state_path),
                format!("A run starts with the message that opens it: pass that message to `{}`.", self.command("receive", None)),
            )
        })
    }

    /// The relay of the centre of a star: the runs it passes on are kept in `concord-relay-<me>.json`.
    pub fn relay(&self) -> Result<Relay, Fail> {
        let path = py::path_join(&self.state_dir, &format!("concord-relay-{}.json", self.me));
        check_json(&self.ctx.path(&path), &path)?;
        let mut relay = Relay::with_store(FileStore::new(self.ctx.path(&path)))?;
        for spoke in &self.cfg.spokes {
            relay.add_spoke(spoke.clone());
        }
        Ok(relay)
    }

    /// The agents a message of this agent is for. The centre of a star sends its own messages to every spoke. Otherwise: every other
    /// agent of its run, the initiator included; for a message about a run the agent does not know (a refusal of an init) the one it is
    /// addressed to; for one that names neither, the agents of the run the agent is in. A fault when there is no run either: a message
    /// must never be printed for an audience that is not known, since a reader takes "no one" for "everyone".
    pub fn recipients(&self, text: &str) -> Result<Vec<String>, Fail> {
        if self.initiator() && !self.cfg.spokes.is_empty() {
            return Ok(self.cfg.spokes.clone());
        }
        let info = peek(text);
        let attr = |name: &str| info.as_ref().and_then(|p| p.attr(name)).filter(|v| !v.is_empty());
        let mut run = attr("run_id").and_then(|id| self.agent.state.runs.get(id));
        if run.is_none() {
            if let Some(to) = attr("to") {
                return Ok(vec![to.to_string()]);
            }
        }
        if run.is_none() {
            run = self.run();
        }
        let Some(run) = run else {
            return Err(Fail::cli(
                "a message of the agent has no recipient that can be worked out (it names no run and no addressee, and the agent is in \
no run): this is a fault of the library",
                "Do not send it. Report this output as a bug.",
            ));
        };
        let mut names: Vec<String> = Vec::new();
        for n in run.participants.iter().chain(std::iter::once(&run.initiator)) {
            if *n != self.me && !names.contains(n) {
                names.push(n.clone());
            }
        }
        Ok(names)
    }

    /// One `SEND to:` section for each message.
    pub fn add_sends(&self, out: &mut Out, send: &[String]) -> Result<(), Fail> {
        for text in send {
            let to = self.recipients(text)?.join(", ");
            out.add(format!("SEND to: {to}"), py::strip(text));
        }
        Ok(())
    }

    /// The first time a run is met, its description for the model: once per run.
    fn brief(&mut self, out: &mut Out, run_id: &str) {
        let briefed = self.side["briefed"].as_array().is_some_and(|b| b.iter().any(|r| r == run_id));
        if !briefed {
            if let Some(list) = self.side["briefed"].as_array_mut() {
                list.push(Value::String(run_id.to_string()));
            } else {
                self.side["briefed"] = json!([run_id]);
            }
            out.add("BRIEF", &RUN_DESCRIPTION);
        }
    }

    /// The output of a call that produced `step` (None: nothing happened), given the status of the run before it (see `before`): what to
    /// send, what was read, what is due, how the run ended, when to come back, and what to do next.
    pub fn compose(&mut self, step: Option<&Step>, before: Option<RunStatus>) -> Result<Out, Fail> {
        let empty: (&[String], &[String]) = (&[], &[]);
        let (send, accepted) = step.map_or(empty, |s| (&s.send[..], &s.accepted[..]));
        let status = self.agent.status(None);
        let over = status.as_ref().is_some_and(|s| s.status != RunStatus::Active);
        let ended_now = over && !matches!(before, Some(RunStatus::Ended | RunStatus::Aborted));
        let tasks = self.agent.due();
        let mut out = Out::new(if !send.is_empty() {
            "SEND"
        } else if !tasks.is_empty() {
            "ANSWER"
        } else if over {
            "DONE"
        } else {
            "WAIT"
        });
        let participant = self.run().filter(|run| run.role == Role::Participant).map(|run| run.run_id.clone());
        if let Some(run_id) = participant {
            self.brief(&mut out, &run_id);
        }
        self.add_sends(&mut out, send)?;
        if !accepted.is_empty() {
            out.add("READ", &render_delivery(accepted));
        }
        if let Some(task) = tasks.first() {
            let mut text = self.agent.instructions(task)?;
            if let Some(task_text) = self.cfg.task_text {
                let extra = task_text(task, &self.command("answer", None));
                if !extra.is_empty() {
                    text = format!("{text}\n\n{extra}");
                }
            }
            out.add("TASK", &text);
        }
        if ended_now {
            if let Some(report) = self.agent.report(None) {
                out.add("END", &render_end(&self.me, &report));
            }
        }
        let wake = if over { None } else { self.agent.next_wakeup() };
        if let Some(wake) = wake {
            out.add("WAKE", &wake_text(wake, now()));
        }
        if let Some(step) = step.filter(|s| s.accepted.is_empty() && s.send.is_empty() && !s.events.is_empty()) {
            let reasons: std::collections::BTreeSet<String> = step
                .events
                .iter()
                .map(|e| e.str("reason").filter(|r| !r.is_empty()).map_or_else(|| e.event.clone(), str::to_string))
                .collect();
            out.add("NOTE", &format!("not used: {}", reasons.into_iter().collect::<Vec<_>>().join("; ")));
        }
        out.next = self.next_line(!send.is_empty(), !tasks.is_empty(), over, ended_now, wake.is_some());
        Ok(out)
    }

    /// The report of the run: how it ended, or that it is not over. Only a run that is over has an END: the end text says that the run
    /// is over as soon as an outcome is computed, and an agent that waits for the result of the reporter is not there yet.
    pub fn report_out(&self) -> Result<Out, Fail> {
        let status = self.need_run()?;
        let over = status.status != RunStatus::Active;
        let mut out = Out::new(if over { "DONE" } else { "WAIT" });
        if over {
            if let Some(report) = self.agent.report(None) {
                out.add("END", &render_end(&self.me, &report));
            }
        } else {
            let mut text = "The run is not over: nothing is final until a call prints STATUS: DONE.".to_string();
            if let Some(outcome) = status.outcome {
                text.push_str(&format!(
                    " You have computed an outcome ({}), but the result of the reporter, which confirms it, has not arrived: do not \
report it as the result.",
                    outcome.as_str()
                ));
            }
            out.add("NOT OVER", &text);
        }
        let wake = if over { None } else { self.agent.next_wakeup() };
        if let Some(wake) = wake {
            out.add("WAKE", &wake_text(wake, now()));
        }
        out.next = self.next_line(false, false, over, false, wake.is_some());
        Ok(out)
    }

    /// What to do now: the one place the command says it, in words that do not depend on the command.
    pub fn next_line(&self, sent: bool, task: bool, over: bool, ended_now: bool, timer: bool) -> String {
        let mut parts: Vec<String> = Vec::new();
        if sent {
            parts.push(
                "Broadcast each SEND block to the agents on its `to:` line, exactly as printed and without that line. Never write or \
change a message."
                    .to_string(),
            );
        }
        if task {
            parts.push(format!(
                "{} the TASK: write the answer it asks for and pass it to `{}`. Never write a CONCORD message yourself.",
                if sent { "Then answer" } else { "Answer" },
                self.command("answer", None)
            ));
        }
        if ended_now {
            parts.push("The run is over: report what END says, and nothing more about the run.".to_string());
        }
        if over {
            parts.push(format!(
                "If messages of this run still arrive, pass each one to `{}` and send only what it prints.",
                self.command("receive", None)
            ));
        } else {
            parts.push(format!(
                "Pass every message of this run you receive, exactly as received, one per call, to `{}`.",
                self.command("receive", None)
            ));
        }
        if timer {
            parts.push(format!(
                "A timer runs: start `{}` in the background (it returns when something is to be sent) and start it again each time it \
returns, or call `{}` at the time under WAKE.",
                self.command("wait", None),
                self.command("tick", None)
            ));
        }
        let mut text = parts.join(" ");
        if self.initiator() && !self.cfg.spokes.is_empty() && !over {
            text.push_str(&format!(
                " You are the centre of a star: for every message that comes from a spoke, first run `{}` and deliver what it prints, \
then pass the same message to `{}`.",
                self.command("forward --from <the spoke>", None),
                self.command("receive", None)
            ));
        }
        text
    }
}
