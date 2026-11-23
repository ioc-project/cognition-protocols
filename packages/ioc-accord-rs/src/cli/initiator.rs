//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The commands of the initiator: the arguments (`clap`, as the script's `argparse` has them), the state file, and what each command
//! prints. The order of the steps is the script's `main()`, so an error that has several causes is reported as the script reports it.
//!
//! * `handle` holds the exclusive lock of the state file across load, change and save, so replies that arrive together are all
//!   recorded; it saves the state when it succeeded, and when it refused a reply that has to be counted (a refusal), before the error.
//! * every other command loads the state (blank when there is no file), runs, and saves it only when it succeeded; `init` saves the state
//!   it built.

use clap::{Parser, Subcommand};
use ioc_protocol::lock::{lock_path, FileLock};
use ioc_protocol::store::{FileStore, Store};
use serde_json::Value;

use super::common::{join_hyphen_values, os_error, pathlib, Context, Output};
use super::{render_error, render_ok};
use crate::errors::AccordError;
use crate::protocol::DEFAULT_STATE;
use crate::session::{InitArgs, Session};
use crate::state::{resolve_state_path, StateCodec};

/// ACCORD merge CLI: the initiator merges the specialists' replies deterministically, and keeps the session in one JSON file, so each phase
/// can run as a separate process.
#[derive(Parser)]
#[command(
    name = "ioc-accord",
    disable_help_subcommand = true,
    args_override_self = true,
    infer_long_args = true,
    after_help = "Every command prints one JSON object {\"ok\": true, \"phase\": ..., \"data\": ...}. Each JSON argument takes a path, '-' \
                  for stdin, or the JSON value inline; only one argument per command can use '-'. Set ACCORD_RUN_KEY to isolate the \
                  state file of a run (in ACCORD_STATE_DIR, or next to --state)."
)]
struct Cli {
    /// session state file (default: $ACCORD_STATE or .accord/state.json)
    #[arg(long)]
    state: Option<String>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// start a new session (resets state)
    Init {
        /// comma-separated specialist ids
        #[arg(long)]
        specialists: String,
        /// comma-separated vocabulary terms for GROUND
        #[arg(long = "key-terms", default_value = "")]
        key_terms: String,
        /// name of the action tool/step for EXECUTION
        #[arg(long = "action-tool", default_value = "")]
        action_tool: String,
        /// task description for self-driving mode
        #[arg(long, default_value = "")]
        task: String,
    },
    /// Phase 0 CONVENE merge
    Roster {
        /// specialist replies (path, '-' for stdin, or inline JSON)
        #[arg(long)]
        responses: String,
    },
    /// Phase 1 FRAME merge (call once per round)
    Frame {
        /// specialist replies (path, '-' for stdin, or inline JSON)
        #[arg(long)]
        responses: String,
        /// atom texts already merged, [] on round 1 (path, '-' for stdin, or inline JSON)
        #[arg(long = "current-frame")]
        current_frame: Option<String>,
    },
    /// Phase 2 GROUND merge (also used after CLARIFY)
    Glossary {
        /// specialist replies (path, '-' for stdin, or inline JSON)
        #[arg(long)]
        responses: String,
        /// override key terms from init
        #[arg(long = "key-terms")]
        key_terms: Option<String>,
    },
    /// Phase 3 VERIFY merge; also returns the locked contract
    Verify {
        /// specialist replies (path, '-' for stdin, or inline JSON)
        #[arg(long)]
        responses: String,
        /// required knowledge items (path, '-' for stdin, or inline JSON)
        #[arg(long = "required-knowledge")]
        required_knowledge: Option<String>,
        /// objective clause texts (path, '-' for stdin, or inline JSON)
        #[arg(long = "objective-clauses")]
        objective_clauses: Option<String>,
    },
    /// Phase 4 LOCK: assemble contract from saved state
    Lock,
    /// render an EXECUTION_REQUEST containing the stored locked contract
    ExecutionMessage {
        /// specialist id this message is for
        #[arg(long)]
        agent: String,
        /// assignment text file, or '-' for stdin
        #[arg(long = "assignment-file")]
        assignment_file: String,
    },
    /// show session progress
    Status,
    /// record one agent reply; advance phase when all replies are in (self-driving mode)
    Handle {
        /// agent id whose reply is being recorded
        #[arg(long)]
        agent: String,
        /// raw reply text from the agent
        #[arg(long)]
        reply: String,
        /// required knowledge items for verify phase (path, '-' for stdin, or inline JSON)
        #[arg(long = "required-knowledge")]
        required_knowledge: Option<String>,
        /// objective clause texts for verify phase (path, '-' for stdin, or inline JSON)
        #[arg(long = "objective-clauses")]
        objective_clauses: Option<String>,
    },
}

impl Cmd {
    /// The name of the command: the `phase` of what it prints.
    fn name(&self) -> &'static str {
        match self {
            Cmd::Init { .. } => "init",
            Cmd::Roster { .. } => "roster",
            Cmd::Frame { .. } => "frame",
            Cmd::Glossary { .. } => "glossary",
            Cmd::Verify { .. } => "verify",
            Cmd::Lock => "lock",
            Cmd::ExecutionMessage { .. } => "execution-message",
            Cmd::Status => "status",
            Cmd::Handle { .. } => "handle",
        }
    }
}

/// Why a command did not go through.
enum Fail {
    /// An error of the command: printed as an error of its phase, exit code 1.
    Accord(AccordError),
    /// What the script does not catch (a state file that cannot be read or written outside `handle`): it prints nothing on the standard
    /// output, only this on the error output, and exits with 1.
    Uncaught(String),
}

impl From<AccordError> for Fail {
    fn from(e: AccordError) -> Fail {
        Fail::Accord(e)
    }
}

pub fn run(args: &[String], ctx: &mut Context, out: &mut Output) {
    let argv = std::iter::once("ioc-accord".to_string()).chain(join_hyphen_values(args));
    let cli = match Cli::try_parse_from(argv) {
        Ok(cli) => cli,
        Err(e) => {
            use clap::error::ErrorKind;
            if matches!(e.kind(), ErrorKind::DisplayHelp | ErrorKind::DisplayVersion) {
                out.out(e.to_string().trim_end());
                return;
            }
            out.out(render_error("usage", &format!("usage error: {}", clap_message(&e))));
            out.code = 2;
            return;
        }
    };
    let phase = cli.cmd.name();
    let given = cli.state.or_else(|| ctx.state.clone()).unwrap_or_else(|| DEFAULT_STATE.to_string());
    let result = state_path(&given, ctx).and_then(|path| match cli.cmd {
        Cmd::Handle { agent, reply, required_knowledge, objective_clauses } => {
            handle(ctx, &path, &agent, &reply, required_knowledge.as_deref(), objective_clauses.as_deref()).map_err(Fail::Accord)
        }
        cmd => command(ctx, &path, cmd),
    });
    match result {
        Ok(data) => out.out(render_ok(phase, data)),
        Err(Fail::Accord(e)) => {
            out.out(render_error(phase, e.message()));
            out.code = 1;
        }
        Err(Fail::Uncaught(text)) => {
            out.err(format!("error: {text}"));
            out.code = 1;
        }
    }
}

/// The message of a command line the parser refused, on one line, without the usage that `clap` adds: `--help` prints that.
fn clap_message(e: &clap::Error) -> String {
    let text = e.to_string();
    let text = text.split("\n\nUsage:").next().unwrap_or(&text);
    let text = text.strip_prefix("error: ").unwrap_or(text);
    text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with("tip:")).collect::<Vec<_>>().join(" ")
}

/// The state file of this invocation, as the script names it (`resolve_state_path`, as `pathlib` writes it). Under a run key its folder
/// is made, as the script makes it.
fn state_path(given: &str, ctx: &Context) -> Result<String, Fail> {
    let run_key = ctx.run_key.as_deref().filter(|k| !k.is_empty());
    let path = pathlib(&resolve_state_path(given, run_key, ctx.state_dir.as_deref()));
    if run_key.is_some() {
        let dir = parent(&path);
        std::fs::create_dir_all(ctx.resolve(&dir)).map_err(|e| Fail::Uncaught(os_error(&e, Some(&dir)).to_string()))?;
    }
    Ok(path)
}

/// The folder of a path, as `Path(path).parent` writes it.
fn parent(path: &str) -> String {
    let (root, parts) = ioc_protocol::path::split_posix(path);
    ioc_protocol::path::join_posix(root, &parts[..parts.len().saturating_sub(1)])
}

/// Make the folder of the state file.
fn make_parent(ctx: &Context, path: &str) -> Result<(), AccordError> {
    let dir = parent(path);
    std::fs::create_dir_all(ctx.resolve(&dir)).map_err(|e| os_error(&e, Some(&dir)))
}

/// The saved state, or None when there is no file. The error is worded as Python words it when it can be: the file is read again for it.
fn load(ctx: &Context, store: &FileStore<StateCodec>, path: &str) -> Result<Option<Value>, AccordError> {
    match store.load() {
        Ok(st) => Ok(Some(st)),
        Err(e) if e.is_missing() => Ok(None),
        Err(e) => {
            let text = ctx.read_file(path)?;
            Err(match crate::state::load_state(&text) {
                Err(python) => python,
                Ok(_) => AccordError::new(e.message()),
            })
        }
    }
}

/// Every command but `handle`: load the state (blank when there is none), run, save when it went through.
fn command(ctx: &mut Context, path: &str, cmd: Cmd) -> Result<Value, Fail> {
    let mut store = FileStore::with_codec(ctx.resolve(path), StateCodec);
    let st = load(ctx, &store, path).map_err(|e| Fail::Uncaught(e.to_string()))?;
    let mut session = match st {
        Some(st) => Session::from_state(st).map_err(|e| Fail::Uncaught(e.to_string()))?,
        None => Session::new(),
    };
    let data = match cmd {
        Cmd::Init { specialists, key_terms, action_tool, task } => {
            session.init(&InitArgs { specialists, key_terms, action_tool, task, state_id: None })?
        }
        Cmd::Roster { responses } => {
            let responses = ctx.read_json(Some(&responses))?.unwrap_or(Value::Null);
            session.roster(&responses)?
        }
        Cmd::Frame { responses, current_frame } => {
            // read only to refuse a current frame that is not JSON: the atoms come from the state
            ctx.read_json(current_frame.as_deref())?;
            let responses = ctx.read_json(Some(&responses))?.unwrap_or(Value::Null);
            session.frame(&responses)?
        }
        Cmd::Glossary { responses, key_terms } => {
            let responses = ctx.read_json(Some(&responses))?.unwrap_or(Value::Null);
            session.glossary(&responses, key_terms.as_deref())?
        }
        Cmd::Verify { responses, required_knowledge, objective_clauses } => {
            let required = ctx.read_json(required_knowledge.as_deref())?;
            let clauses = ctx.read_json(objective_clauses.as_deref())?;
            let responses = ctx.read_json(Some(&responses))?.unwrap_or(Value::Null);
            session.verify(&responses, required.as_ref(), clauses.as_ref())?
        }
        Cmd::Lock => session.lock()?,
        Cmd::ExecutionMessage { agent, assignment_file } => {
            // the contract is checked before the assignment is read, as the script does
            session.execution_message(&agent, "")?;
            let assignment = ctx.read_text(&assignment_file)?;
            session.execution_message(&agent, &assignment)?
        }
        Cmd::Status => session.status()?,
        Cmd::Handle { .. } => unreachable!("handle has its own path"),
    };
    make_parent(ctx, path).map_err(|e| Fail::Uncaught(e.to_string()))?;
    store.save(session.state()).map_err(|e| Fail::Uncaught(e.message().to_string()))?;
    Ok(data)
}

/// `handle`: under the lock of the state file, load it, record the reply, and save it.
fn handle(
    ctx: &mut Context,
    path: &str,
    agent: &str,
    reply: &str,
    required_knowledge: Option<&str>,
    objective_clauses: Option<&str>,
) -> Result<Value, AccordError> {
    make_parent(ctx, path)?;
    let file = ctx.resolve(path);
    if !file.exists() {
        return Err(no_state(path));
    }
    let _lock = FileLock::exclusive(lock_path(&file)).map_err(|e| os_error(&e, Some(path)))?;
    let mut store = FileStore::with_codec(&file, StateCodec);
    let st = load(ctx, &store, path)?.ok_or_else(|| no_state(path))?;
    let mut session = Session::from_state(st)?;
    let required = ctx.read_json(required_knowledge)?;
    let clauses = ctx.read_json(objective_clauses)?;
    let result = session.handle(agent, reply, required, clauses);
    if result.as_ref().is_ok() || result.as_ref().is_err_and(AccordError::is_refusal) {
        // a refusal counted this attempt: the count has to survive it
        store.save(session.state())?;
    }
    result
}

fn no_state(path: &str) -> AccordError {
    AccordError::new(format!("no state file at {path} — run init first"))
}
