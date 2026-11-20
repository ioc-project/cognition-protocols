//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `ioc-concord initiator`: open a run (`init`), then pass every reply to `handle` and send what it prints.
//!
//! Everything the agents send is a broadcast, so a block goes to every agent (the one that wrote a reply ignores it).

use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use clap::{Parser, Subcommand};
use serde_json::{json, Value};

use super::common::{documents, expand, has_concord, Context, Fail, Output, Res};
use super::{COMMAND, WAIT};
use crate::agent::Agent;
use crate::errors::ConcordError;
use crate::protocol::Num;
use crate::relay::Relay;
use crate::report::ReportStatus;
use crate::store::FileStore;
use crate::wire::InitParams;

const DEFAULT_STATE: &str = "concord-state.json";
const ME: &str = "initiator";

/// CONCORD initiator: open a run (`init`), then pass every reply to `handle` and send what it prints.
#[derive(Parser)]
#[command(name = "ioc-concord initiator", disable_help_subcommand = true)]
struct Cli {
    #[arg(long, global = true, default_value = DEFAULT_STATE)]
    state: String,
    /// output format (default: text)
    #[arg(long, global = true, default_value = "text", value_parser = ["text", "json"])]
    format: String,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// start a new run
    #[command(after_help = INIT_GUIDE)]
    Init {
        /// comma-separated agent names, e.g. security,cost,latency
        #[arg(long)]
        agents: String,
        #[arg(long = "satisfaction-threshold", default_value_t = 0.6)]
        satisfaction_threshold: f64,
        #[arg(long, default_value_t = 0.05)]
        eps: f64,
        #[arg(short = 'B', long = "B", alias = "budget", default_value_t = 8)]
        budget: i64,
        /// candidate format, a JSON Schema (enforced): '{"type": "string", "description": "..."}' for an open format, '{"enum": ["A", "B"]}' for a closed list (or @file)
        #[arg(long)]
        schema: Option<String>,
        /// problem statement (or @file)
        #[arg(long)]
        context: String,
        /// reject anchor — outcome that fails all criteria (or @file)
        #[arg(long = "x-reject")]
        x_reject: String,
        /// ideal anchor — outcome that fully satisfies all criteria (or @file)
        #[arg(long = "x-ideal")]
        x_ideal: String,
        /// do not pass each reply on to the other agents (when they already see each other's replies)
        #[arg(long = "no-relay")]
        no_relay: bool,
        /// print only the state path and what to run next
        #[arg(long)]
        quiet: bool,
    },
    /// (not in this protocol version) mark an agent non-responsive on a candidate
    MarkMissing {
        /// agent name
        agent: String,
        /// candidate id the agent failed to score
        candidate: String,
    },
    /// pass what arrived (replies, or nothing); prints the next message to send, or nothing
    Handle {
        /// who sent it (a reply names its author: not needed)
        #[arg(long = "from")]
        sender: Option<String>,
        /// print the pending message again
        #[arg(long)]
        resend: bool,
        /// nudge even if the last message went out only seconds ago (see CONCORD_MIN_NUDGE)
        #[arg(long)]
        force: bool,
    },
    /// wait for a stall; then print the nudge, or STATUS: ASK if nudges do not help
    #[command(allow_negative_numbers = true)]
    Watchdog {
        /// seconds without progress that count as a stall
        #[arg(long, default_value_t = 60.0)]
        timeout: f64,
        /// nudges in a row without progress before it asks you to decide
        #[arg(long = "max-nudges", default_value_t = 3)]
        max_nudges: u32,
    },
    /// show the phase and whether the run is over (changes nothing)
    Status,
}

pub fn run(args: &[String], ctx: &mut Context, out: &mut Output) -> Res<()> {
    let cli = Cli::try_parse_from(std::iter::once("ioc-concord initiator".to_string()).chain(args.iter().cloned()))
        .map_err(super::common::from_clap)?;
    let json = cli.format == "json";
    let state = ctx.resolve(&cli.state).to_string_lossy().into_owned();
    match cli.cmd {
        Cmd::Init { agents, satisfaction_threshold, eps, budget, schema, context, x_reject, x_ideal, no_relay, quiet } => {
            let init = Init { agents, satisfaction_threshold, eps, budget, schema, context, x_reject, x_ideal, relay: !no_relay };
            let _ = quiet; // `init` prints only the state path and what to run next, with or without it
            init_run(&state, init, json, ctx, out)
        }
        Cmd::MarkMissing { .. } => Err(Fail::message(MARK_MISSING_GONE)),
        Cmd::Handle { resend, force, .. } => {
            let text = ctx.input.read(WAIT);
            let mut agent = open(&state)?;
            handle(&mut agent, &documents(&text), resend, force, ctx.min_nudge, out)
        }
        Cmd::Watchdog { timeout, .. } if !timeout.is_finite() || timeout < 0.0 => {
            Err(Fail::Usage("--timeout must be a number of seconds, 0 or more".to_string(), None))
        }
        Cmd::Watchdog { timeout, max_nudges } => watchdog(&state, timeout, max_nudges, ctx.min_nudge, out),
        Cmd::Status => status(&open(&state)?, &state, json, out),
    }
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// The agent as the initiator keeps it: with its relay when it was made the centre of a star.
fn build(state: &str, spokes: Option<&[String]>) -> Res<Agent> {
    let mut builder = Agent::builder(ME).store(FileStore::new(state));
    if let Some(spokes) = spokes {
        let mut relay = Relay::with_store(FileStore::new(format!("{state}.relay")))?;
        for spoke in spokes {
            relay.add_spoke(spoke.clone());
        }
        builder = builder.relay(relay);
    }
    Ok(builder.build()?)
}

fn open(state: &str) -> Res<Agent> {
    if !Path::new(state).exists() {
        return Err(Fail::message(no_state(state)));
    }
    let spokes = build(state, None)?.kept_spokes().map(<[String]>::to_vec);
    build(state, spokes.as_deref())
}

/// The state path of a new run: the one given when it is free, else with a random suffix (a run's id is not shared with another).
fn unique_state_path(path: &str) -> String {
    if !Path::new(path).exists() {
        return path.to_string();
    }
    let suffix = &crate::wire::new_uuid()[..8];
    let path = Path::new(path);
    let stem = path.file_stem().map_or_else(String::new, |s| s.to_string_lossy().into_owned());
    let name = match path.extension() {
        Some(ext) => format!("{stem}-{suffix}.{}", ext.to_string_lossy()),
        None => format!("{stem}-{suffix}"),
    };
    path.with_file_name(name).to_string_lossy().into_owned()
}

fn over(agent: &Agent) -> bool {
    agent.report(None).is_some_and(|r| r.status != ReportStatus::Pending)
}

fn phase(agent: &Agent) -> String {
    agent
        .status(None)
        .and_then(|s| serde_json::to_value(&s).ok())
        .and_then(|v| v["state"].as_str().map(str::to_string))
        .unwrap_or_else(|| "-".into())
}

struct Init {
    agents: String,
    satisfaction_threshold: f64,
    eps: f64,
    budget: i64,
    schema: Option<String>,
    context: String,
    x_reject: String,
    x_ideal: String,
    relay: bool,
}

fn init_run(state: &str, init: Init, json_out: bool, ctx: &Context, out: &mut Output) -> Res<()> {
    let agents: Vec<String> = init.agents.split(',').map(str::trim).filter(|a| !a.is_empty()).map(str::to_string).collect();
    let (context, x_reject, x_ideal) = (expand(ctx, &init.context)?, expand(ctx, &init.x_reject)?, expand(ctx, &init.x_ideal)?);
    let schema = init.schema.ok_or_else(|| Fail::message(SCHEMA_REQUIRED))?;
    let spec: Value = serde_json::from_str(&expand(ctx, &schema)?)
        .map_err(|e| Fail::Concord(ConcordError::schema(format!("--schema is not JSON ({e})"))))?;
    let state = unique_state_path(state);
    let params =
        InitParams::default().satisfaction_threshold(Num::Float(init.satisfaction_threshold)).eps(Num::Float(init.eps)).budget(init.budget);
    let started = (|| -> Res<(Agent, crate::agent::Step)> {
        let mut agent = build(&state, init.relay.then_some(agents.as_slice()))?;
        let step = agent.start(&agents, &context, &x_reject, &x_ideal, &spec, Some(&params), None, None)?;
        agent.keep_spokes()?;
        agent.set_outbox(&step.send, false, None)?;
        Ok((agent, step))
    })();
    let (agent, step) = match started {
        Ok(started) => started,
        Err(fail) => {
            for path in [state.clone(), format!("{state}.relay")] {
                let _ = std::fs::remove_file(path);
            }
            return Err(fail);
        }
    };
    if json_out {
        out.out(
            serde_json::to_string_pretty(&json!({"state_file": state, "agents": agents, "phase": phase(&agent), "messages": step.send}))
                .unwrap_or_default(),
        );
    } else {
        out.out(init_done(&state));
    }
    Ok(())
}

/// Print what to send, or that nothing is due, or the result.
fn emit(agent: &mut Agent, messages: &[String], notes: &[String], out: &mut Output) -> Res<()> {
    let over = over(agent);
    if !messages.is_empty() {
        agent.set_outbox(messages, true, None)?;
        if over {
            out.err(agent.ending(None).unwrap_or_default());
        }
        let agents = agent.participants(None).unwrap_or_default();
        out.out(super::render("SEND", &format!("{}\n{}", initiator_next_send(over), send_block(&agents, messages))));
        return Ok(());
    }
    out.out(super::render("NONE", &initiator_next_none(over)));
    out.out(if over { agent.ending(None).unwrap_or_default() } else { initiator_waiting(&phase(agent)) });
    for note in notes {
        out.out(note);
    }
    Ok(())
}

/// Pass whatever arrived (replies, one or several, from any agent; or nothing): prints the next block to send.
fn handle(agent: &mut Agent, docs: &[String], resend: bool, force: bool, min_nudge: f64, out: &mut Output) -> Res<()> {
    let mut messages: Vec<String> = Vec::new();
    if resend {
        messages = agent.outbox().map(|o| o.messages.clone()).unwrap_or_default();
    } else if docs.is_empty() {
        messages = agent.hand_outbox(None)?; // the first call: the message that opens the run
    }
    let mut accepted = false;
    for doc in docs {
        match agent.receive(doc, None) {
            Ok(step) => {
                accepted |= !step.accepted.is_empty();
                messages.extend(step.send);
                messages.extend(step.forward);
            }
            Err(e) => {
                // what the messages before this one made is in the state: keep it for `handle --resend`, it would be lost otherwise
                if !messages.is_empty() {
                    let _ = agent.set_outbox(&messages, true, None);
                }
                return Err(e.into());
            }
        }
    }
    if docs.is_empty() && messages.is_empty() && over(agent) {
        return emit(agent, &messages, &[], out); // nothing is awaited, so no nudge is too recent
    }
    if docs.is_empty() && messages.is_empty() {
        let since = now() - agent.outbox().map_or(0.0, |o| o.at);
        if since < min_nudge && !force {
            out.out(super::render("NONE", &initiator_too_recent(since)));
            return Ok(());
        }
        let at = match agent.next_wakeup() {
            Some(wake) if force => now().max(wake),
            _ => now(),
        };
        messages = agent.tick(Some(at))?.send;
    }
    let mut notes = Vec::new();
    if messages.is_empty() && !over(agent) && docs.iter().any(|d| has_concord(d)) {
        notes.push(if accepted { INITIATOR_RECORDED_NOTE } else { INITIATOR_NOT_RECORDED_NOTE }.to_string());
    }
    emit(agent, &messages, &notes, out)
}

/// What the run holds, without its timers: a nudge moves the deadlines and counts a retry, and that is not progress.
fn fingerprint(agent: &Agent) -> String {
    let mut status = serde_json::to_value(agent.status(None)).unwrap_or_default();
    if let Some(fields) = status.as_object_mut() {
        fields.remove("timers");
    }
    format!("{status}{}", serde_json::to_string(&agent.report(None)).unwrap_or_default())
}

/// Block until the run has made no progress for `timeout` seconds, then print what to do about it, and return.
fn watchdog(state: &str, timeout: f64, max_nudges: u32, min_nudge: f64, out: &mut Output) -> Res<()> {
    let mut last: Option<String> = None;
    let mut since = Instant::now();
    loop {
        let mut agent = open(state)?;
        if over(&agent) {
            out.out(super::render("NONE", &initiator_next_none(true)));
            out.out(agent.ending(None).unwrap_or_default());
            return Ok(());
        }
        let fp = fingerprint(&agent);
        if last.as_deref() != Some(&fp) {
            last = Some(fp.clone());
            since = Instant::now();
            agent.watch_progress(&fp)?;
        } else if since.elapsed().as_secs_f64() >= timeout {
            let nudges = agent.watch_nudge(&fp)?;
            if nudges > max_nudges {
                out.out(super::render("ASK", &watchdog_stuck(max_nudges, &phase(&agent))));
                return Ok(());
            }
            out.err(watchdog_note(timeout, &phase(&agent), nudges, max_nudges));
            return handle(&mut agent, &[], false, true, min_nudge, out);
        }
        std::thread::sleep(Duration::from_secs_f64((timeout / 10.0).clamp(0.1, 1.0)));
    }
}

/// Look at a run without changing it.
fn status(agent: &Agent, state: &str, json_out: bool, out: &mut Output) -> Res<()> {
    let over = over(agent);
    if json_out {
        let value = json!({"state_file": state, "status": agent.status(None), "report": agent.report(None), "finished": over});
        out.out(serde_json::to_string_pretty(&value).unwrap_or_default());
        return Ok(());
    }
    let outcome = agent.report(None).and_then(|r| r.outcome).map(|o| o.as_str().to_string());
    out.out(status_phase(&phase(agent), outcome.as_deref().filter(|_| over)));
    out.out(if over { agent.ending(None).unwrap_or_default() } else { STATUS_NOT_FINISHED.to_string() });
    Ok(())
}

// ---------------------------------------------------------------------------------------------------------------------------
// what the initiator prints
// ---------------------------------------------------------------------------------------------------------------------------

const RULES: &str = "Never write or change a message or a reply yourself.";

/// How to fill the arguments of `init`: the epilog of its help.
const INIT_GUIDE: &str = "\
How to fill the arguments:

  --agents                 the ids of the agents whose satisfaction counts, exactly as your tool for reaching each of
                           them names it. Use these same ids everywhere in this run.
  --satisfaction-threshold the standard the outcome has to meet, not a level you are sure is reachable (0.6 is
                           a starting point). If it sits below the best seed, the run ends with no negotiation at all.
  --schema                 the JSON Schema for a candidate (plain text is refused), enforced on every
                           proposal, seeds and repairs alike. The default is an open format: '{\"type\": \"string\",
                           \"description\": \"<the proposal, in words>\"}' (nothing is enforced, and agents may propose
                           combinations). A schema needs a \"description\" or an \"enum\". Use a JSON enum,
                           '{\"enum\": [\"<option A>\", \"<option B>\", \"<option C>\"]}', only when the options were handed to
                           you: the task or a tool output lists them, and you copy each exactly as given (a repair may
                           re-propose one already in the run). Never write the list yourself: if you would have to
                           decide what the candidates are, use the open format. Other supported keywords: \"pattern\" (a
                           regular expression), \"minLength\", \"maxLength\", \"title\". Invalid JSON or any other keyword is refused.
  --x-reject / --x-ideal   the objective, never the means: the state the context is trying to produce, as a plain
                           before/after in its own numbers (for example error rate above 40%: ideal \"error rate returns
                           to 0\", reject \"stays above 40% or rises\"). Never a proposal, and no word about how it is
                           reached. If an anchor could pass as something an agent might propose, rewrite it one level up.
  --quiet                  has no effect: init always prints only the state path and what to run next.

Pass every value as a plain argument (multi-line is fine). `init` mints the state path and prints it: use that exact
path on every later call, never one you made up.
";

/// The error of an `init` without `--schema`.
const SCHEMA_REQUIRED: &str = "error: --schema is required: a JSON Schema such as '{\"type\": \"string\", \"description\": \"what one option is\"}' for an open format, or '{\"enum\": [\"A\", \"B\", \"C\"]}' for a closed list";

/// What `init` prints: the state path, and what to run next.
fn init_done(state_path: &str) -> String {
    format!(
        "State: {state_path}\nNEXT: Run `{COMMAND} initiator --state {state_path} handle` with no input to get the first message to send. Use exactly this state path on every call of this run."
    )
}

/// The error of a command whose state file is not there.
fn no_state(path: &str) -> String {
    format!("error: no state file at {path}: run `init` first")
}

/// A block of messages for the agents: the line that names who gets it, then the messages.
fn send_block(agents: &[String], messages: &[String]) -> String {
    format!("--- to: {}\n{}", agents.join(", "), messages.join("\n\n"))
}

/// Under `STATUS: SEND`: what to do with the block; `complete` when the run is over.
fn initiator_next_send(complete: bool) -> String {
    let tail = if complete {
        "The run is over: after sending, report the result printed on the error output, exactly as printed and with nothing added. Ignore any later message from the agents about this run: the run is over and nothing is owed to them."
    } else {
        "Pass every reply you receive to `handle`, exactly as received (several replies in one call is fine). Do not stop before the run is over. If sending to an agent fails (an error, or the message did not go out), try once more with the full block as the message; if it fails again, stop and report which agent could not be reached and quote the error: do not keep retrying."
    };
    format!("NEXT: Send the block below to the agent or agents named on its `--- to:` line, exactly as printed and without that line. {RULES} {tail}")
}

/// Under `STATUS: NONE`: what to do; `over` when the run is over.
fn initiator_next_none(over: bool) -> String {
    if over {
        return "NEXT: The run is over. Report exactly the result below (the outcome and the winning candidate, and the scores when it gives them) to whoever asked for it, and add nothing: no steps, recommendations or commentary of your own. Run no more commands. Ignore any later message from the agents about this run: the run is over.".to_string();
    }
    "NEXT: Send nothing. Wait for a reply. Pass each reply to `handle` as it arrives, exactly as received. The run is not over, so do not end your turn just because nothing is due now. If no reply is still on its way (you have already collected what the agents answered), run `watchdog`: it waits for the stall, then prints the nudge to send, as it would after a lost message. Do not ask the agents for anything yourself: each agent works out what it owes.".to_string()
}

/// `STATUS: NONE` when the run is not over: where it stands.
fn initiator_waiting(phase: &str) -> String {
    format!("Phase: {phase}. Nothing new to send: wait for the replies still awaited.")
}

/// After a reply that was recorded and needs nothing sent yet.
const INITIATOR_RECORDED_NOTE: &str = "[recorded; nothing to send for it yet]";

/// After a CONCORD message that was not used.
const INITIATOR_NOT_RECORDED_NOTE: &str = "[not recorded: what you passed holds a CONCORD message that could not be used: it is cut off or damaged, of another run or version, or already known. If it is a reply of this run that was cut, pass it again as it arrived; else wait for the agent's next message]";

/// `STATUS: NONE` for a nudge asked for too soon after the last message.
fn initiator_too_recent(seconds: f64) -> String {
    format!(
        "NEXT: Send nothing. The last message went out {seconds:.0}s ago, too recently to be lost: the agents are still working. Pass each reply to `handle` as it arrives, or run `watchdog`."
    )
}

/// The error of `mark-missing`, which this protocol version does not have.
const MARK_MISSING_GONE: &str = "error: mark-missing does not exist in this protocol version: a silent agent is asked again by the timers, and the run is aborted when it stays silent. Wait with `watchdog`, or stop and report that the run could not be completed.";

/// The note of `watchdog` before a nudge, on the error output.
fn watchdog_note(timeout: f64, phase: &str, nudges: u32, max_nudges: u32) -> String {
    format!("[watchdog: no progress for {timeout}s (phase {phase}); nudge {nudges} of {max_nudges}]")
}

/// `STATUS: ASK` of `watchdog`: nudges did not help.
fn watchdog_stuck(max_nudges: u32, phase: &str) -> String {
    format!(
        "NEXT: Decide as described below and act on it. Do not invent a step, and do not write anything on an agent's behalf.\nThe run is stuck: no progress after {max_nudges} nudges (phase {phase}). Decide: wait longer (run `watchdog` again), or stop and report that the run could not be completed."
    )
}

/// The first line of `status`: the phase, and the outcome when the run is over.
fn status_phase(phase: &str, outcome: Option<&str>) -> String {
    match outcome {
        Some(outcome) => format!("Phase: {phase} — {outcome}"),
        None => format!("Phase: {phase}"),
    }
}

/// `status` of a run that is not over.
const STATUS_NOT_FINISHED: &str =
    "NOT FINISHED: there is no outcome yet. Do not report a result until this tool prints a phase with an outcome.";
