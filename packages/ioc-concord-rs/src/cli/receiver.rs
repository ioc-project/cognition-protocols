//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `ioc-concord receiver`: pass what you got to `handle`; give your judgement to `answer`; `due` says what is due.

use clap::{Parser, Subcommand};
use indexmap::IndexMap;
use serde_json::{json, Value};

use super::common::{documents, expand, has_concord, safe, Context, Fail, Output, Res};
use super::{COMMAND, WAIT};
use crate::agent::Agent;
use crate::protocol::Num;
use crate::report::ReportStatus;
use crate::store::FileStore;
use crate::tasks::Task;
use crate::wire::ScoreIn;

const STATE_DIR: &str = ".concord-receiver";

/// CONCORD receiver: pass what you got to `handle`; give your judgement to `answer`.
#[derive(Parser)]
#[command(name = "ioc-concord receiver", disable_help_subcommand = true)]
struct Cli {
    /// your agent id, as in the initiator's --agents list
    #[arg(long, global = true)]
    me: Option<String>,
    /// the run's id (only to name it in the commands printed)
    #[arg(long = "state-id", global = true)]
    state_id: Option<String>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Pass a CONCORD message or reply you got; prints what is due, or NONE.
    #[command(name = "observe", alias = "handle")]
    Observe {
        /// who sent it (a message names its author: not needed)
        #[arg(long = "from")]
        sender: Option<String>,
        /// message text (stdin if omitted)
        text: Option<String>,
    },
    /// Give your judgement with the flags the task shows; prints STATUS: SEND and the reply to send.
    #[command(allow_negative_numbers = true)]
    Answer {
        /// anchor: your rating of x_reject
        #[arg(long)]
        reject: Option<String>,
        /// anchor: your rating of x_ideal
        #[arg(long)]
        ideal: Option<String>,
        /// seed or repair: the candidate
        #[arg(long)]
        candidate: Option<String>,
        /// seed or repair: why
        #[arg(long)]
        rationale: Option<String>,
        /// a rating: the candidate id, your raw number, your reasons (repeat for each candidate; your own candidate too)
        #[arg(long, num_args = 3, action = clap::ArgAction::Append, value_names = ["ID", "N", "REASONS"])]
        score: Vec<String>,
        /// (older form) the answer as JSON, if no flag is given
        text: Option<String>,
    },
    /// Print the current status, change nothing but the timers.
    Due,
}

fn parse_from(args: &[String]) -> Result<Cli, clap::Error> {
    Cli::try_parse_from(std::iter::once("ioc-concord receiver".to_string()).chain(args.iter().cloned()))
}

/// The command line, with `handle` put in when the command was left out (it is the usual one): after `--me` and `--state-id`.
fn parse(args: &[String]) -> Res<Cli> {
    use clap::error::ErrorKind;
    match parse_from(args) {
        Ok(cli) => Ok(cli),
        Err(first) if matches!(first.kind(), ErrorKind::MissingSubcommand | ErrorKind::InvalidSubcommand | ErrorKind::UnknownArgument) => {
            let mut at = 0;
            while at < args.len() {
                match args[at].as_str() {
                    "--me" | "--state-id" => at += 2,
                    a if a.starts_with("--me=") || a.starts_with("--state-id=") => at += 1,
                    _ => break,
                }
            }
            let mut argv = args.to_vec();
            argv.insert(at.min(args.len()), "handle".to_string());
            parse_from(&argv).map_err(|_| super::common::from_clap(first))
        }
        Err(e) => Err(super::common::from_clap(e)),
    }
}

pub fn run(args: &[String], ctx: &mut Context, out: &mut Output) -> Res<()> {
    let cli = parse(args)?;
    let me = cli.me.clone().ok_or_else(|| Fail::message(RECEIVER_ME_REQUIRED))?;
    let path = ctx.state_dir(STATE_DIR)?.join(format!("state-{}.json", safe(&me)));
    let mut agent = Agent::builder(&me).store(FileStore::new(path)).build()?;
    let state_id = cli.state_id.as_deref();
    match cli.cmd {
        Cmd::Observe { text, .. } => handle(&mut agent, state_id, text, ctx, out),
        Cmd::Answer { reject, ideal, candidate, rationale, score, text } => {
            let flags = Flags { reject, ideal, candidate, rationale, score };
            answer(&mut agent, state_id, flags, text, ctx, out)
        }
        Cmd::Due => {
            let step = agent.tick(None)?;
            let (status, text) = status(&agent, state_id, &step.send, &[]);
            print(out, status, &text);
            Ok(())
        }
    }
}

fn over(agent: &Agent) -> bool {
    agent.report(None).is_some_and(|r| r.status != ReportStatus::Pending)
}

/// SEND (messages to send), ASK (a task for the model) or NONE (nothing to send), and the text that goes with it.
fn status(agent: &Agent, state_id: Option<&str>, sends: &[String], accepted: &[String]) -> (&'static str, String) {
    if !sends.is_empty() {
        return ("SEND", sends.join("\n\n"));
    }
    if let Some(ask) = ask_text(agent, state_id, accepted) {
        return ("ASK", ask);
    }
    if over(agent) {
        return ("NONE", agent.ending(None).unwrap_or_default());
    }
    ("NONE", receiver_nothing_due())
}

fn print(out: &mut Output, status: &str, text: &str) {
    out.out(super::render(status, text));
    if status == "SEND" {
        out.err(receiver_send_reminder());
    }
}

fn handle(agent: &mut Agent, state_id: Option<&str>, text: Option<String>, ctx: &mut Context, out: &mut Output) -> Res<()> {
    let text = match text.as_deref() {
        None => ctx.input.read(WAIT),
        Some("-") => ctx.input.read(std::time::Duration::from_secs(30)),
        Some(given) => expand(ctx, given)?, // `@path` is a file only when it is given as an argument
    };
    let docs = documents(&text);
    let mut sends = agent.tick(None)?.send;
    let mut accepted = Vec::new();
    for doc in &docs {
        let step = agent.receive(doc, None)?;
        sends.extend(step.send);
        accepted.extend(step.accepted);
    }
    let used = !accepted.is_empty();
    let (status, payload) = status(agent, state_id, &sends, &accepted);
    print(out, status, &payload);
    if !has_concord(&text) && status != "SEND" {
        out.out(RECEIVER_NO_MESSAGE_NOTE);
    } else if has_concord(&text) && !used && status != "SEND" {
        out.out(RECEIVER_NOT_USED_NOTE);
    }
    Ok(())
}

/// What the flags of `answer` say.
struct Flags {
    reject: Option<String>,
    ideal: Option<String>,
    candidate: Option<String>,
    rationale: Option<String>,
    score: Vec<String>,
}

/// An answer, by name: the anchors, the candidate, and the ratings by candidate id: `(raw, reasoning)`.
struct Data {
    reject: Option<String>,
    ideal: Option<String>,
    candidate: Option<String>,
    rationale: Option<String>,
    scores: IndexMap<String, (String, String)>,
}

fn text_of(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        other => Some(other.to_string()),
    }
}

/// The answer the flags give, or the older form (JSON, on the command line or on the input); none when nothing was given.
fn answer_data(flags: Flags, text: Option<String>, ctx: &mut Context) -> Result<Option<Data>, String> {
    let given = flags.reject.is_some()
        || flags.ideal.is_some()
        || flags.candidate.is_some()
        || flags.rationale.is_some()
        || !flags.score.is_empty();
    if given {
        let mut scores = IndexMap::new();
        for c in flags.score.as_chunks::<3>().0 {
            if scores.insert(c[0].clone(), (c[1].clone(), c[2].clone())).is_some() {
                return Err(format!("--score was given twice for {}: give one rating for each candidate.", c[0]));
            }
        }
        return Ok(Some(Data { reject: flags.reject, ideal: flags.ideal, candidate: flags.candidate, rationale: flags.rationale, scores }));
    }
    let text = text.unwrap_or_else(|| ctx.input.read(std::time::Duration::from_secs(2)));
    if text.trim().is_empty() {
        return Ok(None);
    }
    let data: Value = serde_json::from_str(&text).map_err(|e| answer_unreadable(&e.to_string()))?;
    let own = data.get("seed").or_else(|| data.get("repair")).cloned().unwrap_or(Value::Null);
    let anchor = data.get("anchor").cloned().unwrap_or(Value::Null);
    let mut scores = IndexMap::new();
    for s in data.get("scores").and_then(Value::as_array).into_iter().flatten() {
        let (Some(id), Some(raw), Some(why)) = (text_of(s.get("id")), text_of(s.get("raw")), text_of(s.get("reasoning"))) else {
            return Err(answer_unreadable("a rating needs id, raw and reasoning"));
        };
        scores.insert(id, (raw, why));
    }
    Ok(Some(Data {
        reject: text_of(anchor.get("raw_reject")),
        ideal: text_of(anchor.get("raw_ideal")),
        candidate: text_of(own.get("candidate")),
        rationale: text_of(own.get("rationale")),
        scores,
    }))
}

/// The answer to `task` as the node takes it (by name), and what is wrong with it. Nothing is built from a wrong answer.
fn problems(agent: &Agent, me: &str, task: &Task, data: &Data) -> (Option<Value>, Vec<String>) {
    if let Task::Score(t) = task {
        let owed: Vec<String> = t.candidates.iter().map(|c| c.id.clone()).collect();
        let missing: Vec<String> = owed.iter().filter(|c| !data.scores.contains_key(*c)).cloned().collect();
        let unknown: Vec<String> = data.scores.keys().filter(|c| !owed.contains(c)).cloned().collect();
        let mut out = Vec::new();
        if !missing.is_empty() {
            out.push(answer_scores_missing(&missing));
        }
        if !unknown.is_empty() {
            out.push(answer_scores_unknown(&unknown));
        }
        let scores: Vec<Value> =
            owed.iter().filter_map(|c| data.scores.get(c).map(|(raw, why)| json!({"id": c, "raw": raw, "reasoning": why}))).collect();
        let answer = json!({ "scores": scores });
        out.extend(agent.check_answer("score", &answer));
        return (Some(answer), out);
    }
    let (kind, own) = match task {
        Task::InitReply(_) => ("init-reply", format!("seed-{me}")),
        Task::Repair(t) => ("repair", format!("r{}-{me}", t.round)),
        Task::Score(_) => unreachable!("handled above"),
    };
    let mut out = Vec::new();
    let anchors = [("reject", &data.reject), ("ideal", &data.ideal)];
    for (name, value) in anchors.iter().filter(|_| kind == "init-reply") {
        if value.is_none() {
            out.push(answer_missing(&format!("--{name}")));
        }
    }
    for (name, value) in [("candidate", &data.candidate), ("rationale", &data.rationale)] {
        if value.is_none() {
            out.push(answer_missing(&format!("--{name}")));
        }
    }
    let Some((raw, why)) = data.scores.get(&own) else {
        out.push(answer_needs_own_score(&own));
        return (None, out);
    };
    if !out.is_empty() {
        return (None, out);
    }
    let mut answer = json!({"candidate": data.candidate, "rationale": data.rationale, "raw": raw, "reasoning": why});
    if kind == "init-reply" {
        answer["raw_reject"] = json!(data.reject);
        answer["raw_ideal"] = json!(data.ideal);
    }
    let found = agent.check_answer(kind, &answer);
    (Some(answer), found)
}

fn field(answer: &Value, key: &str) -> String {
    text_of(answer.get(key)).unwrap_or_default()
}

fn submit(agent: &mut Agent, task: &Task, answer: &Value) -> crate::Result<crate::agent::Step> {
    let num = |key: &str| Num::Text(field(answer, key));
    match task {
        Task::InitReply(t) => agent.submit_init_reply(
            &t.run_id,
            num("raw_reject"),
            num("raw_ideal"),
            &field(answer, "candidate"),
            &field(answer, "rationale"),
            num("raw"),
            &field(answer, "reasoning"),
            None,
        ),
        Task::Repair(t) => agent.submit_repair(
            &t.run_id,
            &field(answer, "candidate"),
            &field(answer, "rationale"),
            num("raw"),
            &field(answer, "reasoning"),
            None,
        ),
        Task::Score(t) => {
            let scores: Vec<ScoreIn> = answer["scores"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|s| ScoreIn::new(field(s, "id"), Num::Text(field(s, "raw")), field(s, "reasoning")))
                .collect();
            agent.submit_score(&t.run_id, &scores, None)
        }
    }
}

/// An answer that was not used: the fault, that nothing was sent, what to do, and the task again.
fn refuse(agent: &mut Agent, state_id: Option<&str>, text: &str, out: &mut Output) -> Res<()> {
    let refusal = agent.refuse(text)?;
    let ask = ask_text(agent, state_id, &[]).unwrap_or_default();
    out.out(super::render("ASK", &format!("{refusal}\n\n{ask}")));
    Ok(())
}

fn answer(agent: &mut Agent, state_id: Option<&str>, flags: Flags, text: Option<String>, ctx: &mut Context, out: &mut Output) -> Res<()> {
    let tasks = agent.due();
    let Some(task) = tasks.first() else {
        let (status, payload) = status(agent, state_id, &[], &[]);
        print(out, status, &payload);
        return Ok(());
    };
    let me = agent.me().to_string();
    let data = match answer_data(flags, text, ctx) {
        Ok(Some(data)) => data,
        Ok(None) => return refuse(agent, state_id, ANSWER_NONE, out),
        Err(text) => return refuse(agent, state_id, &text, out),
    };
    let (answer, found) = problems(agent, &me, task, &data);
    let (true, Some(answer)) = (found.is_empty(), answer) else {
        return refuse(agent, state_id, &answer_not_usable(&found), out);
    };
    match submit(agent, task, &answer) {
        Ok(step) => {
            print(out, "SEND", &step.send.join("\n\n"));
            Ok(())
        }
        Err(e) => refuse(agent, state_id, &e.to_string(), out),
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// what the receiver prints
// ---------------------------------------------------------------------------------------------------------------------------

/// What the receiver prints for a task: the messages received since the last answer (`accepted`), the text of each task that is due, and the
/// `answer` command to run; none when nothing is due. `state_id` names the run in that command.
fn ask_text(agent: &Agent, state_id: Option<&str>, accepted: &[String]) -> Option<String> {
    let tasks = agent.due();
    if tasks.is_empty() {
        return None;
    }
    let mut parts = Vec::new();
    if !accepted.is_empty() {
        parts.push(crate::guidance::render_delivery(accepted));
    }
    parts.extend(tasks.iter().filter_map(|task| agent.instructions(task).ok()));
    parts.push(receiver_answer_command(agent.me(), state_id, &tasks));
    Some(parts.join("\n\n"))
}

const NOTHING_MORE: &str = "When you are done, write nothing more: not even a confirmation of what you sent or decided. Your environment may pass anything you write to the other participants.";

/// Under `STATUS: SEND`, on the error output: what to do with the text.
fn receiver_send_reminder() -> String {
    format!(
        "NEXT: Send the text printed under STATUS: SEND exactly as it is, and nothing else: no words before or after it, no summary of your own. Then pass the next CONCORD message you get to `handle`. {NOTHING_MORE}"
    )
}

/// `STATUS: NONE` when nothing is due and the run is not over.
fn receiver_nothing_due() -> String {
    format!(
        "nothing is due from you. Send no message.\nNEXT: Send nothing: never write a message yourself (when one is due, `handle` prints it as a SEND). Pass the next CONCORD message or reply you get to `handle`. {NOTHING_MORE}"
    )
}

/// After `STATUS: NONE`, when what was passed holds no CONCORD message.
const RECEIVER_NOT_USED_NOTE: &str = "[note: a CONCORD message was found in what you passed, but nothing was recorded from it: it is cut off or damaged, of another run or version, or already known. If it was cut, pass the complete text again]";
const RECEIVER_NO_MESSAGE_NOTE: &str = "[note: no CONCORD message was found in what you passed: nothing was recorded]";

/// The error of a command that needs `--me`.
const RECEIVER_ME_REQUIRED: &str = "error: --me <your agent id> is required before handle/due/answer";

/// The command-line flags that answer these tasks: the node knows what is due, so the answer needs only the values.
fn answer_flags(me: &str, tasks: &[Task]) -> String {
    let mut flags: Vec<String> = Vec::new();
    for task in tasks {
        match task {
            Task::InitReply(_) => {
                flags.push("--reject N --ideal N".to_string());
                flags.push("--candidate \"TEXT\" --rationale \"TEXT with the evidence for it\"".to_string());
                flags.push(format!("--score seed-{me} N \"reasons\""));
            }
            Task::Repair(t) => {
                flags.push("--candidate \"TEXT\" --rationale \"TEXT\"".to_string());
                flags.push(format!("--score r{}-{me} N \"reasons\"", t.round));
            }
            Task::Score(t) => flags.extend(t.candidates.iter().map(|c| format!("--score {} N \"reasons\"", c.id))),
        }
    }
    flags.join(" ")
}

/// The paragraph that ends a task for the receiver: the exact `answer` command to run.
fn receiver_answer_command(me: &str, state_id: Option<&str>, tasks: &[Task]) -> String {
    let who = format!("--me {me}{}", state_id.map(|s| format!(" --state-id {s}")).unwrap_or_default());
    format!(
        "Answer by running: {COMMAND} receiver {who} answer {}\n(N is a number; each text is one argument, and may have several lines.)\nIt prints STATUS: SEND followed by your reply: send exactly that, nothing else. Your judgement goes in these values only: never write the reply yourself, and never add text before or after what the command prints. {NOTHING_MORE}",
        answer_flags(me, tasks)
    )
}

/// The refusal of an answer whose flags are not usable: what is wrong with them.
fn answer_not_usable(problems: &[String]) -> String {
    format!("Your answer was not usable: {}", problems.join("; "))
}

/// A flag the task needs and the answer lacks.
fn answer_missing(flag: &str) -> String {
    format!("{flag} is missing")
}

/// Candidates to rate that the answer lacks.
fn answer_scores_missing(ids: &[String]) -> String {
    format!("scores is missing: {}", ids.join(", "))
}

/// Ratings of candidates that are not to be rated now.
fn answer_scores_unknown(ids: &[String]) -> String {
    format!("these are not candidates to rate now: {}", ids.join(", "))
}

/// The rating of the agent's own candidate, which an answer with a candidate needs.
fn answer_needs_own_score(id: &str) -> String {
    format!("scores must include a rating of your own candidate {id}")
}

/// The text when `answer` is given nothing.
const ANSWER_NONE: &str = "You gave no answer. Give it with the flags the task shows.";

/// The text when the older form of `answer` (JSON) cannot be read.
fn answer_unreadable(detail: &str) -> String {
    format!("your answer could not be read ({detail}). Give it with the flags the task shows.")
}
