//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `ioc-concord receiver`: takes part in a run opened by another agent. Besides the commands both roles have: `answer` (the answer to
//! the task it printed, as one JSON object on standard input), `task` (what is due now) and `status` (where the run stands, for
//! debugging).

use serde_json::{json, Map, Value};

use crate::errors::ConcordError;
use crate::guidance::{check_answer, lacks, refusal, refusal_text, run_view, settled, wrong_shape, RawAnchors};
use crate::protocol::{format_number, Num};
use crate::tasks::Task;
use crate::wire::ScoreIn;

use super::args::Args;
use super::commands::{self, with_session};
use super::common::Context;
use super::out::{wake_text, Fail, Out};
use super::py;
use super::session::{now, Config, Session, RECEIVER};

pub(crate) const ROLE: commands::Role = commands::Role { name: "receiver", prog: RECEIVER, task_text: Some(answer_format), own };

/// The same refusal this many times in a row: stop (spec, Appendix A.7).
const STOP_AFTER: i64 = 3;
/// Refusals of one task in a row, whatever their reasons: stop too (a model that changes its bad answer each time).
const STOP_AFTER_ANY: i64 = 6;

fn own(cfg: &Config, ctx: &mut Context, args: &Args, text: Option<&str>) -> Option<Result<Out, Fail>> {
    match args.command.as_str() {
        "answer" => Some(run_answer(cfg, ctx, text.unwrap_or_default())),
        "task" => Some(run_task(cfg, ctx)),
        "status" => Some(run_status(cfg, ctx)),
        _ => None,
    }
}

// What an answer holds, for each kind of task: (field, kind of value, what it is). The names are those of the library. The format text
// that a task carries, and the checks of an answer before the library's own, come from this table.
const RAW_SCALE: &str = "a number from 0 to 100 on YOUR raw scale (never a normalised 0-1 score)";

type Row = (&'static str, &'static str, String);

fn fields(kind: &str) -> Vec<Row> {
    let r = |name, kind, what: &str| (name, kind, what.to_string());
    match kind {
        "init-reply" => vec![
            r("raw_reject", "number", "how satisfied your role would be if x_reject happened: 0 to 100, usually low"),
            r("raw_ideal", "number", "how satisfied your role would be if x_ideal happened: 0 to 100, usually high, above raw_reject"),
            r("candidate", "text", "the one option that suits your role best, in the format the task describes"),
            r("rationale", "text", "why this option, with the facts you hold that bear on it: the others read it"),
            r("raw", "number", &format!("your own score for this option, {RAW_SCALE}. It goes in this field, not only in the reasoning")),
            r("reasoning", "text", "why that score, with the facts behind it"),
        ],
        "score" => vec![r("scores", "list", "one entry for each candidate the task lists, and no other")],
        _ => vec![
            r("candidate", "text", "a better option than the incumbent, in the format the task describes"),
            r("rationale", "text", "why this option, with the facts that bear on it"),
            r("raw", "number", &format!("your own score for this option, {RAW_SCALE}. It goes in this field, not only in the reasoning")),
            r("reasoning", "text", "why that score"),
        ],
    }
}

fn entry() -> Vec<Row> {
    vec![
        ("id", "text", "the id of the candidate, exactly as in the task".to_string()),
        ("raw", "number", format!("your rating of it, {RAW_SCALE}")),
        ("reasoning", "text", "why that rating, with the facts behind it".to_string()),
    ]
}

fn kind_words(kind: &str) -> &'static str {
    match kind {
        "number" => "a number",
        "text" => "text",
        _ => "a list",
    }
}

/// The text that tells the model what to pass to `answer` for `task` (printed after the task).
fn answer_format(task: &Task, command: &str) -> String {
    let rows = fields(task.kind());
    let mut out = vec![format!(
        "ANSWER FORMAT: write one JSON object with exactly these fields, and pass it on standard input to `{command}`. Nothing else:"
    )];
    out.extend(rows.iter().map(|(name, kind, what)| format!("  {name} ({}): {what}", kind_words(kind))));
    if task.kind() == "score" {
        out.push("  each entry of scores is an object:".to_string());
        out.extend(entry().iter().map(|(name, kind, what)| format!("    {name} ({}): {what}", kind_words(kind))));
    }
    let example_entry: Map<String, Value> =
        entry().iter().map(|(n, k, _)| ((*n).to_string(), if *k == "number" { json!(70) } else { json!("...") })).collect();
    let example: Map<String, Value> = rows
        .iter()
        .map(|(n, k, _)| {
            let v = match *k {
                "number" => json!(70),
                "text" => json!("..."),
                _ => json!([example_entry.clone()]),
            };
            ((*n).to_string(), v)
        })
        .collect();
    out.push(format!("  for example: {}", py::json_dumps(&Value::Object(example))));
    out.join("\n")
}

fn fits(kind: &str, value: &Value) -> bool {
    match kind {
        "number" => matches!(value, Value::Number(_) | Value::String(_)),
        "text" => value.is_string(),
        _ => value.is_array(),
    }
}

/// The fields of `obj` that are there but of the wrong kind of value, as a sentence (empty when all are right).
fn wrong_shapes(rows: &[Row], obj: &Map<String, Value>, at: &str) -> String {
    rows.iter()
        .filter_map(|(name, kind, _)| {
            let value = obj.get(*name).filter(|v| !v.is_null())?;
            (!fits(kind, value)).then(|| wrong_shape(&format!("{at}{name}"), kind_words(kind), value))
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn absent(value: Option<&Value>) -> bool {
    matches!(value, None | Some(Value::Null)) || value.and_then(Value::as_str) == Some("")
}

/// The fields the answer lacks, as a sentence that shows what was sent next to what is expected (empty when none is lacking).
fn missing_fields(kind: &str, answer: &Map<String, Value>) -> String {
    let rows = fields(kind);
    let top: Vec<String> = rows.iter().filter(|(n, _, _)| absent(answer.get(*n))).map(|(n, _, what)| format!("`{n}` ({what})")).collect();
    let mut nested = Vec::new();
    if kind == "score" {
        if let Some(Value::Array(entries)) = answer.get("scores") {
            let rows = entry();
            let takes: Vec<&str> = rows.iter().map(|(n, _, _)| *n).collect();
            for (n, e) in entries.iter().enumerate() {
                let Value::Object(e) = e else { continue };
                let have = e.keys().cloned().collect::<Vec<_>>().join(", ");
                let suffix = format!(" [the entry has: {have}; an entry takes: {}]", takes.join(", "));
                for (name, _, what) in &rows {
                    if absent(e.get(*name)) {
                        nested.push(format!("entry {} of `scores`: `{name}` ({what}){suffix}", n + 1));
                    }
                }
            }
        }
    }
    if !nested.is_empty() {
        nested.extend(top);
        let parts: Vec<&str> = nested.iter().map(String::as_str).collect();
        return lacks(&parts, &[], &[]);
    }
    if top.is_empty() {
        return String::new();
    }
    let parts: Vec<&str> = top.iter().map(String::as_str).collect();
    let sent: Vec<String> = answer.keys().cloned().collect();
    let takes: Vec<String> = rows.iter().map(|(n, _, _)| (*n).to_string()).collect();
    lacks(&parts, &sent, &takes)
}

/// The answer as an object, or what is wrong with `text`: its shape, what it lacks, and the checks beyond the specification, in that
/// order. The library's own checks come when it is submitted.
fn problems(task: &Task, s: &Session<'_>, text: &str) -> Result<Map<String, Value>, String> {
    let answer = match py::json_loads(text) {
        Ok(Value::Object(map)) => map,
        Ok(other) => return Err(wrong_shape("answer", "an object", &other)),
        Err(e) => return Err(format!("the answer is not valid JSON ({e}): pass one JSON object")),
    };
    let kind = task.kind();
    let mut shape = wrong_shapes(&fields(kind), &answer, "");
    if kind == "score" {
        if let Some(Value::Array(entries)) = answer.get("scores") {
            let names: Vec<&str> = entry().iter().map(|(n, _, _)| *n).collect();
            let mut parts = vec![shape];
            for (n, e) in entries.iter().enumerate() {
                if !e.is_object() {
                    parts.push(wrong_shape(&format!("scores` entry `{}", n + 1), &format!("an object with {}", names.join(", ")), e));
                }
            }
            for (n, e) in entries.iter().enumerate() {
                if let Value::Object(e) = e {
                    parts.push(wrong_shapes(&entry(), e, &format!("scores` entry `{}` field `", n + 1)));
                }
            }
            shape = py::strip(&parts.join(" ")).to_string();
        }
    }
    if !shape.is_empty() {
        return Err(shape);
    }
    let lack = missing_fields(kind, &answer);
    if !lack.is_empty() {
        return Err(lack);
    }
    let run = s.agent.state.runs.get(task.run_id()).expect("the run of a task that is due");
    let anchors = run_view(run, &s.me)
        .anchors
        .map(|a| RawAnchors { raw_reject: a.raw_reject.parse().unwrap_or(f64::NAN), raw_ideal: a.raw_ideal.parse().unwrap_or(f64::NAN) });
    let found = check_answer(kind, &Value::Object(answer.clone()), anchors);
    if found.is_empty() {
        Ok(answer)
    } else {
        Err(found.join(" "))
    }
}

/// What identifies a task: the same task is asked again until it is answered, and a refusal of it is counted with the earlier ones.
fn task_key(task: &Task) -> Value {
    let mut ids: Vec<String> = match task {
        Task::Score(t) => t.candidates.iter().map(|c| c.id.clone()).collect(),
        _ => Vec::new(),
    };
    ids.sort();
    json!([task.run_id(), task.kind(), task.round(), ids])
}

/// Why an answer was refused: an error of the library, or the text of a refusal of the command.
enum Refused {
    Error(ConcordError),
    Text(String),
}

/// The output for an answer that was refused: what is wrong and what to do, and, when it goes on, the stop that the specification asks
/// for (A.7: limit the retries). Two counts, both of consecutive refusals of the same task: the same refusal again and again (the stop
/// is at STOP_AFTER, and the model is told to change its answer from the second), and refusals for any reason (the stop is at
/// STOP_AFTER_ANY): a model that changes its bad answer each time never repeats a refusal. A refusal that says the answer is already
/// given is not counted.
fn refuse(s: &mut Session<'_>, task: Option<&Task>, error: Refused) -> Out {
    let (mut same, mut tries) = (1i64, 1i64);
    let settles = matches!(&error, Refused::Error(e) if settled(e));
    if !settles {
        let why = match &error {
            Refused::Error(e) => e.to_string(),
            Refused::Text(t) => t.clone(),
        };
        let key = task.map_or(Value::Null, task_key);
        let last = s.side["refusal"].clone();
        if last.get("task") == Some(&key) {
            tries = last["tries"].as_i64().unwrap_or(0) + 1;
            same = if last["text"].as_str() == Some(why.as_str()) { last["same"].as_i64().unwrap_or(0) + 1 } else { 1 };
        }
        s.side["refusal"] = json!({"task": key, "tries": tries, "text": why, "same": same});
    }
    let repeats = u32::try_from(same).unwrap_or(u32::MAX);
    let text = match &error {
        Refused::Error(e) => refusal(e, repeats),
        Refused::Text(t) => refusal_text(t, repeats),
    };
    let (said, hint) = text.split_once("\nNEXT: ").unwrap_or((text.as_str(), ""));
    let mut out = Out::new("ERROR");
    out.add("REFUSED", said);
    out.next = hint.to_string();
    if same >= STOP_AFTER || tries >= STOP_AFTER_ANY {
        let how = if same >= STOP_AFTER {
            format!("The same refusal came {same} times in a row")
        } else {
            format!("This task has been refused {tries} times in a row")
        };
        out.next = format!(
            "Stop. {how}, so the answer cannot be obtained. Do not try again and do not work around it: report that you could not answer \
this task, and why (quote the refusal above). Silence ends the run for everybody after a while, which is the right outcome here."
        );
    }
    out
}

fn num(value: &Value) -> Num {
    match value {
        Value::String(t) => Num::Text(t.clone()),
        Value::Number(n) => n.as_i64().map_or_else(|| Num::Float(n.as_f64().unwrap_or(f64::NAN)), Num::Int),
        other => Num::Text(other.to_string()),
    }
}

fn text_of<'v>(answer: &'v Map<String, Value>, name: &str) -> &'v str {
    answer.get(name).and_then(Value::as_str).unwrap_or_default()
}

/// Answer the task that is due: check the answer, submit it to the library, and print what follows.
fn run_answer(cfg: &Config, ctx: &Context, text: &str) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| {
        let tasks = s.agent.due();
        let Some(task) = tasks.first() else {
            let error = ConcordError::state("nothing is due from you now: that is answered already, or not yours to answer");
            return Ok(refuse(s, None, Refused::Error(error)));
        };
        let answer = match problems(task, s, text) {
            Ok(answer) => answer,
            Err(wrong) => return Ok(refuse(s, Some(task), Refused::Text(wrong))),
        };
        let (before, run_id) = (s.before(), task.run_id().to_string());
        let a = &answer;
        let submitted = match task {
            Task::InitReply(_) => s.agent.submit_init_reply(
                &run_id,
                num(&a["raw_reject"]),
                num(&a["raw_ideal"]),
                text_of(a, "candidate"),
                text_of(a, "rationale"),
                num(&a["raw"]),
                text_of(a, "reasoning"),
                None,
            ),
            Task::Score(_) => {
                let scores: Vec<ScoreIn> = a["scores"]
                    .as_array()
                    .map(|entries| {
                        entries
                            .iter()
                            .map(|e| {
                                ScoreIn::new(
                                    e["id"].as_str().unwrap_or_default(),
                                    num(&e["raw"]),
                                    e["reasoning"].as_str().unwrap_or_default(),
                                )
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                s.agent.submit_score(&run_id, &scores, None)
            }
            _ => s.agent.submit_repair(
                &run_id,
                text_of(a, "candidate"),
                text_of(a, "rationale"),
                num(&a["raw"]),
                text_of(a, "reasoning"),
                None,
            ),
        };
        let step = match submitted {
            Ok(step) => step,
            Err(e) => return Ok(refuse(s, Some(task), Refused::Error(e))),
        };
        s.side["refusal"] = Value::Null;
        s.compose(Some(&step), before)
    })
}

/// What is due now, as after any call: the task if there is one, and the timer. It changes nothing.
fn run_task(cfg: &Config, ctx: &Context) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| {
        s.need_run()?;
        let before = s.before();
        s.compose(None, before)
    })
}

/// Where the run stands, for a person who debugs: the view of the engine, and the timers.
fn run_status(cfg: &Config, ctx: &Context) -> Result<Out, Fail> {
    with_session(cfg, ctx, true, |s| {
        let st = s.need_run()?;
        let mut out = Out::new(if st.status == crate::state::RunStatus::Active { "WAIT" } else { "DONE" });
        let scores: Vec<String> = st.scores.iter().map(|(a, v)| format!("{}: {}", py::repr(a), py::repr(&format_number(*v)))).collect();
        let lacking: Vec<String> = st.lacking.iter().map(|(a, c)| format!("({}, {})", py::repr(a), py::repr(c))).collect();
        let mut lines = vec![
            format!("run_id: {}", st.run_id),
            format!("role: {}", name(&st.role)),
            format!("status: {}", st.status.as_str()),
            format!("outcome: {}", st.outcome.map_or("None", |o| o.as_str())),
            format!(
                "state: {} (round {}, reporter {})",
                name(&st.state),
                st.round.map_or_else(|| "None".to_string(), |r| r.to_string()),
                st.reporter
            ),
            format!("incumbent: {}", st.x_star.as_deref().unwrap_or("None")),
            format!("scores: {{{}}}", scores.join(", ")),
            format!("bottleneck: {}", st.bottleneck.as_deref().unwrap_or("None")),
            format!("anchors in: {}", if st.anchors_in.is_empty() { "none".to_string() } else { st.anchors_in.join(", ") }),
            format!("lacking: [{}]", lacking.join(", ")),
        ];
        let clock = now();
        lines.extend(st.timers.iter().map(|(key, due)| format!("timer {key}: {}", wake_text(*due, clock))));
        out.add("STATE", &lines.join("\n"));
        out.next = format!(
            "This is a view for debugging: it changes nothing. To go on, see the NEXT line of your last call, or run `{}`.",
            s.command("report", None)
        );
        Ok(out)
    })
}

/// The name of a value of the library, as its state writes it (`participant`, `wait`, ...).
fn name<T: serde::Serialize>(value: &T) -> String {
    match serde_json::to_value(value) {
        Ok(Value::String(s)) => s,
        Ok(other) => other.to_string(),
        Err(_) => String::new(),
    }
}
