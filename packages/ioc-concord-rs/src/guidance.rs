//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What an agent's model reads about the protocol: the wording of the run and of each task, in one place.
//!
//! The node (agent.rs) decides, and its model only writes content. This module is the text that tells the model what is asked of it,
//! as Appendix A of the specification describes: the run, the first answer, a score, a repair, how to weigh evidence, what the others
//! wrote, how the run ended and what to do about a refused answer. It is the reference wording: an agent may use it as it is, or write
//! its own with the same points. The wording comes from the receiver and initiator skills, moved to the current protocol.
//!
//! * `RUN_DESCRIPTION`, `INITIATOR_GUIDANCE`: once, with the standing context of the model
//! * `Agent::instructions(task)`: the text of a task of `due()`
//! * `render_init_reply`, `render_score`, `render_repair`, `render_task`: the same, from the parts
//! * `render_delivery(texts)`: the messages of the others, as they arrived, in front of the next task
//! * `render_end(me, report)`: how the run ended
//! * `check_answer`, `refusal`: what to say when an answer is refused
//!
//! It holds only text and the functions that fill it in: no I/O, no model, no clock.
//!
//! # Trust
//!
//! What the other agents wrote (candidates, rationales, reasoning, and the messages `render_delivery` shows as they arrived) goes into
//! the text the model reads, as it is. The protocol has no way to check it, and nothing here escapes or fences it: a participant can
//! write text that reads as an instruction to the model of another. The participants of a run are trusted to be cooperative, as the spec
//! says; an integration that cannot assume it must treat this text as untrusted input to the model, and delimit it itself.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value};

use crate::engine::Params;
use crate::errors::ConcordError;
use crate::protocol::{format_number, Outcome};
use crate::report::{Report, ReportStatus};
use crate::schema::render_schema;
use crate::state::Run;
use crate::tasks::{InitReplyTask, RepairTask, ScoreTask, Task};

// ---------------------------------------------------------------------------------------------------------------------------
// the points on evidence (Appendix A.6). FACTS and NOT_REASONS are the receiver script's `_FACTS`; FACTS_RULED_OUT is in the long
// form of the receiver skill and not in its script. They are part of the standing context and are repeated in the tasks that weigh
// what the others wrote.
// ---------------------------------------------------------------------------------------------------------------------------

pub const FACTS: &str =
    "Facts. What you hold is a fact. What other agents report from their own evidence (what they saw, were given or read) is \
evidence you must use: build it into your reasoning, apply it to every candidate it affects, and do not set it aside \
only because you cannot check it. Weigh it as evidence, not as certainty: a specific report counts for more than a \
vague one, and if a report contradicts your own facts or another report, say so in your reasoning and state which you \
rely on and why. An agent's conclusion or expectation is an argument, not a fact: weigh it, and set aside any claim \
that comes with no evidence, saying so. Do not speculate, and do not assume a fix, a delay or a way around something \
nobody reported. Before you rate a candidate, state what the evidence implies for it, in its own words, then give the \
number, and check that it matches that reasoning. Your own facts change only when another agent reports evidence that \
contradicts them, never because someone offers another reading of them. When two reports seem to conflict, do not \
invent a cause that reconciles them: say that they conflict and rate on what each supports.";

pub const NOT_REASONS: &str = "A candidate's name or wording, and other agents' numbers, are not reasons.";

pub const FACTS_RULED_OUT: &str =
    "A fact that rules a candidate out keeps its rating low, and a fact that supports it keeps it high, until a \
reported fact says otherwise; a risk or an effect that nobody reported is not a reason.";

// ---------------------------------------------------------------------------------------------------------------------------
// the standing context: how a run goes (concord-receiver SKILL.md: "How a run goes", and its opening)
// ---------------------------------------------------------------------------------------------------------------------------

const RUN_DESCRIPTION_HEAD: &str = "## How you decide together: CONCORD
You are one of the participants of a group that must choose a single outcome. One participant, the initiator, opens a run, and
everyone answers it.

Your part: rate options honestly against your own role's criteria, and, when it is your turn, propose a better option. Your
criteria are the ones your role already gives you; CONCORD supplies none. You may also know things the others do not, and they may
know things you do not: what you know and leave out of a rationale or a reasoning is invisible to everyone else, and what another
agent reports from its own evidence is evidence you use in your reasoning.

A run goes like this:
1. First answer. Each participant gives its anchors (how satisfied it would be with the worst and with the best outcome, on its own
   0 to 100 scale: every later rating is read against these two points), proposes the one option that suits its role best, and
   rates it.
2. Score. Each participant rates the proposals of the others, with reasons. If one option reaches the threshold for every
   participant, the run ends there: SUCCESS.
3. Repair. Otherwise the least satisfied participant alone proposes one option and rates it (a new one, or one already in the run
   that it now rates differently because of a fact another agent shared). The others rate it, and the cycle repeats until an
   option clears everyone or the budget is spent (BEST_EFFORT).
4. Result. The run is over once the first participant has reported the outcome and the others have compared it with their own.

What you receive: the messages of the other participants, exactly as they were sent: the init, their first answers, their scores
and their repairs. What you send: the answers that are built and sent from what you write; you write only the content.

How to weigh evidence:
";

/// How a run goes, and what the model receives and sends: the standing context of the model, said once.
pub static RUN_DESCRIPTION: LazyLock<String> =
    LazyLock::new(|| format!("{RUN_DESCRIPTION_HEAD}{FACTS}\n{NOT_REASONS}\n{FACTS_RULED_OUT}\n"));

/// The budget line, said at every task.
pub const BUDGET: &str = "Be detailed and informative, but stay within 1000 characters for each part of your answer.";

/// The opening, said at every task.
pub const OPENING: &str =
    "Decide from YOUR role's criteria and from what you know. Be honest: never inflate to seem agreeable or deflate for \
leverage, and never soften your criteria to make something look acceptable. If you cannot be satisfied, say so.";

/// Fill the `{name}` placeholders of `template`.
///
/// One pass over the template: a value that holds a placeholder (an `x_reject` that says `{x_ideal}`) is written as it is.
fn fill(template: &str, values: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let tail = &rest[open..];
        let named = tail.find('}').and_then(|close| values.iter().find(|(name, _)| tail[1..close] == **name).map(|(_, v)| (close, *v)));
        match named {
            Some((close, value)) => {
                out.push_str(value);
                rest = &tail[close + 1..];
            }
            None => {
                out.push('{');
                rest = &tail[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

// ---------------------------------------------------------------------------------------------------------------------------
// the first answer: the anchors, the proposal and its own score in one answer (spec: <concord-init-reply>)
// ---------------------------------------------------------------------------------------------------------------------------

const ANCHORS: &str = "1. ANCHORS
Rate two outcomes on your own raw 0-100 scale, as the satisfaction of your role if each happened (not how true it is today:
answering how true it is now inverts the scale and is refused). x_reject should get a LOW number, x_ideal a HIGH one strictly
above it. These are satisfaction ratings, not metrics: no error rate or latency in these fields. The anchors are rated once and stay
fixed for the whole run. Rate your raw scale, never a normalised 0-1 score.
  x_reject: {x_reject}
  x_ideal:  {x_ideal}";

const PROPOSAL: &str = "2. YOUR PROPOSAL
Propose the single candidate that best satisfies your own role, in this format. Do not hedge, do not offer alternatives, do not pre-compromise for
other roles: this round represents your viewpoint undiluted. The rationale carries the concrete facts and evidence you hold that
support it, why the other options are less suitable, but only where you have evidence against them (if you have none, say so:
never invent a risk to fill the gap), the trade-offs you considered, and why it will hold up under scrutiny: the others use it to
detect incorrect logic. Use only facts you hold: do not speculate. Your rationale is your only voice here: anything you know and
do not write in it is invisible to every other participant. Everything you write is read by every participant: do not write what
you need to keep private.";

const OWN_SCORE: &str = "3. YOUR OWN SCORE FOR IT
Rate your own proposal on the same raw scale, with the reasons. Do not give it a high number because it is yours. Before the
number, state what your facts imply for this option, in their own words; then check that the number matches that reasoning. Give
concrete reasons: the properties that drove it and the evidence for and against it (\"too expensive\" is not a reason; \"adds about
$400 a month, over the budget\" is).";

/// How the candidate must look, when the schema enforces more than a description.
pub fn options_lines(schema: &Value) -> Vec<String> {
    let options: Vec<&str> =
        schema.get("enum").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
    if !options.is_empty() {
        let mut out =
            vec!["options (the candidate must be exactly one of these, written as listed; detail about it goes in the rationale, \
             and added wording does not make a different option):"
                .to_string()];
        out.extend(options.iter().map(|o| format!("  - {o}")));
        return out;
    }
    if ["pattern", "minLength", "maxLength"].iter().any(|k| schema.get(k).is_some()) {
        return vec!["(the candidate is checked against the format above and refused if it does not satisfy it)".to_string()];
    }
    Vec::new()
}

/// The parameters of the run as one line (the threshold and eps in millionths); nothing when they are unknown.
fn parameters(params: Option<&Params>) -> Vec<String> {
    let Some(p) = params else { return Vec::new() };
    vec![format!(
        "parameters: threshold {}, repair budget {}, eps {}, timeout {} s, retries {}",
        format_number(p.satisfaction_threshold),
        p.budget,
        format_number(p.eps),
        p.timeout,
        p.retries
    )]
}

/// The text of the init-reply task `task` (see `Agent::due`) for the agent `me`; `participants` and `params` are the run's.
pub fn render_init_reply(me: &str, task: &InitReplyTask, participants: &[String], params: Option<&Params>) -> String {
    let mut out = vec![
        format!("You are {me}. The run was opened by {}; participants: {}.", task.initiator, participants.join(", ")),
        format!("CONCORD needs your first answer now. {OPENING}"),
        String::new(),
        format!("context: {}", task.context),
        format!("candidate format: {}", render_schema(&task.schema)),
    ];
    out.extend(options_lines(&task.schema));
    out.extend(parameters(params));
    out.extend([
        String::new(),
        "Your answer has three parts, written together.".to_string(),
        String::new(),
        fill(ANCHORS, &[("x_reject", &task.x_reject), ("x_ideal", &task.x_ideal)]),
        String::new(),
        PROPOSAL.to_string(),
        String::new(),
        OWN_SCORE.to_string(),
        String::new(),
        BUDGET.to_string(),
    ]);
    out.join("\n")
}

// ---------------------------------------------------------------------------------------------------------------------------
// a score: the candidates of the others, rated on one's own scale (spec: <concord-score>). It repeats the points on evidence, as the
// receiver script did at each score.
// ---------------------------------------------------------------------------------------------------------------------------

const SCORE: &str = "TASK: rate {n} candidate{s} of the others
Rate each candidate listed on your own raw 0-100 scale, the same scale as your anchors (raw_reject={raw_reject}, raw_ideal={raw_ideal}),
for the whole run: the same quality gets the same number later as at anchoring. Place each between your anchors; if one lies beyond
an anchor, use the nearest anchor value and say so in the reasoning. Move a number only for a real change (a specific fact another
agent shared, or a rationale that answers a concern you raised), and say in one sentence what changed your mind and who shared it;
refusing to budge is inertia, not honesty. Never refuse to score: a candidate you consider terrible gets a low number with honest
reasons. Give the concrete reasons for each number (the properties that drove it, the evidence from your criteria, both for and
against it: \"too expensive\" is not a reason, \"adds about $400 a month, over the budget\" is), and say if a candidate rests on a
false claim or a flawed step. Write the facts that matter, even if they seem obvious to you: the repair agent and the others see only your
reasoning, and what you leave out is invisible to the rest of the run.";

const SAME: &str =
    "  this is the same candidate as {oid}, which you rated {raw}: keep that number unless a specific fact shared since then \
changes your view. If you rate it differently, begin your reasoning with: Same as {oid}, which I rated {raw}. I now rate \
it M because <agent> reported ...";

fn same_candidate(a: &str, b: &str) -> bool {
    let norm = |s: &str| s.to_lowercase().split_whitespace().collect::<Vec<_>>().join(" ");
    norm(a) == norm(b)
}

/// The anchors of an agent, as text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ViewAnchors {
    pub raw_reject: String,
    pub raw_ideal: String,
}

/// What the score and repair tasks need from the state of a run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunView {
    /// The anchors of the agent, as text.
    pub anchors: Option<ViewAnchors>,
    /// The candidates it has rated or proposed.
    pub rated: Vec<RatedCandidate>,
    /// Every candidate of the run.
    pub pool: Vec<PoolCandidate>,
}

/// A candidate the agent has rated or proposed, with its raw score as text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RatedCandidate {
    pub id: String,
    pub candidate: String,
    pub raw: String,
}

/// A candidate of the run, and who proposed it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PoolCandidate {
    pub id: String,
    pub proposer: String,
    pub candidate: String,
}

/// What the score and repair tasks need from the state of `run` (see state.rs).
pub fn run_view(run: &Run, me: &str) -> RunView {
    let st = &run.engine;
    let anchors =
        st.anchors.get(me).map(|a| ViewAnchors { raw_reject: format_number(a.raw_reject), raw_ideal: format_number(a.raw_ideal) });
    let rated = st
        .raw
        .get(me)
        .into_iter()
        .flatten()
        .filter_map(|(cid, raw)| {
            st.pool.iter().find(|c| c.id == *cid).map(|c| RatedCandidate {
                id: cid.clone(),
                candidate: c.candidate.clone(),
                raw: format_number(*raw),
            })
        })
        .collect();
    RunView {
        anchors,
        rated,
        pool: st
            .pool
            .iter()
            .map(|c| PoolCandidate { id: c.id.clone(), proposer: c.proposer.clone(), candidate: c.candidate.clone() })
            .collect(),
    }
}

/// The opening of a task that weighs what the others wrote (a score, a repair): who, what is needed now, and the points on evidence.
fn weighing(me: &str, what: &str) -> Vec<String> {
    vec![
        format!("You are {me}. CONCORD needs your {what} now. {OPENING}"),
        String::new(),
        FACTS.to_string(),
        NOT_REASONS.to_string(),
        FACTS_RULED_OUT.to_string(),
        String::new(),
    ]
}

/// The text of the score task `task` for the agent `me`; `view` is `run_view(run, me)`.
pub fn render_score(me: &str, task: &ScoreTask, view: &RunView) -> String {
    let n = task.candidates.len();
    let (raw_reject, raw_ideal) = view.anchors.as_ref().map_or(("?", "?"), |a| (a.raw_reject.as_str(), a.raw_ideal.as_str()));
    let mut out = weighing(me, "ratings");
    out.push(fill(
        SCORE,
        &[("n", &n.to_string()), ("s", if n == 1 { "" } else { "s" }), ("raw_reject", raw_reject), ("raw_ideal", raw_ideal)],
    ));
    out.push(BUDGET.to_string());
    out.push(String::new());
    for c in &task.candidates {
        out.push(format!("candidate {} (proposed by {}): {}", c.id, c.from, c.candidate));
        out.push(format!("  its rationale: {}", c.rationale));
        out.push(format!("  its proposer's reasoning for its own score: {}", c.reasoning));
        for r in &view.rated {
            if r.id != c.id && same_candidate(&r.candidate, &c.candidate) {
                out.push(fill(SAME, &[("oid", &r.id), ("raw", &r.raw)]));
            }
        }
    }
    out.join("\n")
}

// ---------------------------------------------------------------------------------------------------------------------------
// a repair: the participant least satisfied with the incumbent proposes one better candidate, and rates it (spec: <concord-repair>).
// (3) has the case of the spec that an agent may re-argue its own earlier proposal.
// ---------------------------------------------------------------------------------------------------------------------------

const REPAIR_HEAD: &str = "TASK: repair, round {round}
REPAIR ROUND, YOU ALONE. Every rating of the incumbent {id} is in. Its weakest rating is {floor}, below the bar of {threshold}: you are
the least satisfied participant ({mine}). You have {left} repair{s} left, this one included.";

const REPAIR: &str = "Read the incumbent's ratings and their reasoning, not just the numbers. Propose ONE candidate that:
(1) clears your own bar, something you would rate close to your x_ideal and not marginally above what you just rejected;
(2) does not knowingly push any other agent below the threshold: infer from their reasoning what they are protecting and keep it;
(3) is not yet in the run, or is one already in the run that you now rate differently because of a named fact, or your own earlier
    proposal if you hold it is right and you can argue it better: then say so at the start of your rationale and give the new
    arguments, and start the reasoning of your rating with \"Same as <id>, which I rated N. I now rate it M because <agent>
    reported ...\"; a reworded copy of a scored candidate is not new;
(4) follows the candidate format;
(5) answers the strongest reasoning against your position, in your rationale: agree with part of it, refute it, or say why it does
    not apply;
(6) states any domain knowledge of your role that has not yet appeared and bears on why this candidate is the right one: it stays
    invisible to everyone if you do not write it.
{budget} Rate your own new candidate, honestly, in the same answer. If you believe none exists, give the candidate you rate
highest, even one already in the run, and state the conflict plainly in the rationale (which agent, over what property). Do not
lower the threshold, do not weaken your criteria, and do not propose something you are not satisfied by. The ratings of the
incumbent below are normalised 0-1 for you to read: never echo one back as your own rating.";

/// The text of the repair task `task` for the agent `me`; `context` and `schema` are those of the run, and `view` is
/// `run_view(run, me)`: with it the task lists the candidates already in the run and says on what scale to rate the new one.
pub fn render_repair(me: &str, task: &RepairTask, context: &str, schema: &Value, view: Option<&RunView>) -> String {
    let inc = &task.incumbent;
    let left = task.repairs_left;
    let mut out = weighing(me, "repair");
    out.push(fill(
        REPAIR_HEAD,
        &[
            ("round", &task.round.to_string()),
            ("id", &inc.id),
            ("floor", &format_number(task.floor)),
            ("threshold", &format_number(task.threshold)),
            ("mine", &task.scores.get(me).map_or_else(|| "?".to_string(), |s| format_number(*s))),
            ("left", &left.to_string()),
            ("s", if left == 1 { "" } else { "s" }),
        ],
    ));
    out.push(fill(REPAIR, &[("budget", BUDGET)]));
    out.extend([String::new(), format!("candidate format: {}", render_schema(schema)), format!("context: {context}")]);
    out.extend(options_lines(schema));
    out.extend([
        String::new(),
        format!("incumbent {}: {}", inc.id, inc.candidate),
        format!("its rationale: {}", inc.rationale),
        format!("ratings of the incumbent (the bar is {}):", format_number(task.threshold)),
    ]);
    for (agent, score) in &task.scores {
        let below = match task.shortfall.get(agent) {
            Some(short) if *short > 0 => format!(", below the bar by {}", format_number(*short)),
            _ => String::new(),
        };
        out.push(format!("  - {agent}: {}{below} ({})", format_number(*score), task.reasoning.get(agent).map_or("", String::as_str)));
    }
    if let Some(view) = view {
        out.push(String::new());
        out.push("candidates in the run:".to_string());
        out.extend(view.pool.iter().map(|c| format!("  - {} (proposed by {}): {}", c.id, c.proposer, c.candidate)));
        if let Some(a) = &view.anchors {
            out.push(format!("your own scale, for rating your new candidate: raw_reject={}, raw_ideal={}", a.raw_reject, a.raw_ideal));
        }
    }
    out.join("\n")
}

/// The text of any task of `Agent::due()` for the agent `me`, from the state of `run`.
pub fn render_task(run: &Run, me: &str, task: &Task) -> String {
    match task {
        Task::InitReply(t) => render_init_reply(me, t, &run.participants, Some(&run.params)),
        Task::Score(t) => render_score(me, t, &run_view(run, me)),
        Task::Repair(t) => render_repair(me, t, &run.context, &run.schema, Some(&run_view(run, me))),
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// the initiator: how to fill the init (initiator skill: `init --help`, "How to fill the arguments", and its long form, "Starting a
// run"). The order of the participants is from the spec. The timeout, the budget and eps are not for the model to choose.
// ---------------------------------------------------------------------------------------------------------------------------

pub const START_PARAMS: [(&str, &str); 6] = [
    (
        "participants",
        "The ids of the agents whose satisfaction counts, exactly as written in the task. The order matters: the \
first one reports the outcome and wins ties, so list yourself first.",
    ),
    (
        "satisfaction_threshold",
        "The standard the outcome genuinely has to meet, a number above 0 and at most 1, not a level you are \
sure is reachable (0.6 is a starting point). If it sits below what the agents already reach on \
their own proposals, the run ends with no negotiation at all. It never changes during the run.",
    ),
    ("options", "The options of the task, each copied exactly as given. Never write the list yourself."),
    (
        "x_reject",
        "The objective, never the means: the state the context is trying to produce, as a plain before/after. This is \
the worst outcome: never a proposal, and no word about how it is reached. If an anchor could pass as something \
an agent might propose, rewrite it one level up.",
    ),
    (
        "x_ideal",
        "The objective, never the means: the best outcome, as a plain before/after in the same terms. Never a proposal, and \
no word about how it is reached.",
    ),
    ("context", "The problem to decide, as every participant must understand it."),
];

/// How to fill the init, for the agent that opens the run.
pub static INITIATOR_GUIDANCE: LazyLock<String> = LazyLock::new(|| {
    let lines: Vec<String> = START_PARAMS.iter().map(|(k, v)| format!("- {k}: {v}")).collect();
    format!("## Starting the run: you are the initiator\nTo open the run you give:\n{}", lines.join("\n"))
});

// ---------------------------------------------------------------------------------------------------------------------------
// checks that go beyond the specification: a reasoning too short to be one, a number of 0-1 sent as a raw score (the receiver script
// refused both; the library does not). A rating beyond an anchor is not refused: the spec clamps it.
// ---------------------------------------------------------------------------------------------------------------------------

pub const MIN_REASON: usize = 20;

/// The number in a JSON value that a model may give as a number, a text that reads as one, or `true` or `false`; None for anything else.
fn as_float(x: Option<&Value>) -> Option<f64> {
    match x? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    }
}

/// The text of a JSON value, or the empty text when it is unset (`null`, `false`, `0`) or missing; any other value as its JSON.
fn text_or_empty(x: Option<&Value>) -> String {
    match x {
        None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) if n.as_f64() == Some(0.0) => String::new(),
        Some(other) => other.to_string(),
    }
}

/// The problem of a reasoning or a rationale too short to be one, as a list (empty when it is fine).
fn short(label: &str, text: Option<&Value>) -> Vec<String> {
    if text_or_empty(text).trim().chars().count() >= MIN_REASON {
        return Vec::new();
    }
    vec![format!(
        "{label} is too short: give the concrete reasons (the properties that drove it, the evidence from your criteria). \
         \"too expensive\" is not a reason."
    )]
}

/// What the raw anchors of an agent are, as numbers.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RawAnchors {
    pub raw_reject: f64,
    pub raw_ideal: f64,
}

fn show_value(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// The problems of an answer of the model to a task of `kind` (`init-reply`, `score` or `repair`), as a list of sentences (empty when
/// it is fine). `answer` holds what the matching `submit_*` takes, by name: for an init-reply `raw_reject`, `raw_ideal`, `candidate`,
/// `rationale`, `raw`, `reasoning`; for a score `scores`, a list of `{"id", "raw", "reasoning"}`; for a repair `candidate`,
/// `rationale`, `raw`, `reasoning`. `anchors` are the raw anchors of the agent as numbers, or None before its init reply.
pub fn check_answer(kind: &str, answer: &Value, anchors: Option<RawAnchors>) -> Vec<String> {
    let mut out = Vec::new();
    let ideal = anchors.map(|a| a.raw_ideal);
    let raw_ok = |out: &mut Vec<String>, label: String, value: Option<&Value>, ideal: Option<f64>| {
        if let Some(v) = as_float(value) {
            if 0.0 < v && v < 1.0 && 1.0 < ideal.unwrap_or(0.0) {
                out.push(format!(
                    "{label} is {}: it looks like a normalised 0-1 score. Use your raw scale, never a normalised one.",
                    show_value(value)
                ));
            }
        }
    };
    let get = |key: &str| answer.get(key);
    if kind == "init-reply" || kind == "repair" {
        out.extend(short("the rationale", get("rationale")));
        out.extend(short("the reasoning", get("reasoning")));
        let raw_ideal = as_float(get("raw_ideal"));
        if kind == "init-reply" && raw_ideal.is_some_and(|v| v <= 1.0) {
            out.push(format!(
                "your anchors are {} and {}: they look like normalised 0-1 scores. Use your raw 0-100 scale, never a normalised one.",
                show_value(get("raw_reject")),
                show_value(get("raw_ideal"))
            ));
        }
        raw_ok(&mut out, "your score".to_string(), get("raw"), if kind == "init-reply" { raw_ideal } else { ideal });
    } else if kind == "score" {
        for s in get("scores").and_then(Value::as_array).into_iter().flatten() {
            if let Value::Object(map) = s {
                let id = show_value(map.get("id"));
                out.extend(short(&format!("the reasoning for {id}"), map.get("reasoning")));
                raw_ok(&mut out, format!("your rating of {id}"), map.get("raw"), ideal);
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------------------------------------------------------
// a refused answer: what the model reads. Every refusal says what went wrong and what to do about it, as the receiver script did
// (`STATUS: ERROR`, the fault, `NEXT:`): never the complaint alone (Appendix A.7). The hints are those of its `_hint_for`.
// ---------------------------------------------------------------------------------------------------------------------------

pub const HINT_ALREADY: &str =
    "That is already answered, or not yours to answer: nothing is lost. Do not answer it again; wait for your next \
task, and if none comes, send nothing.";
pub const HINT_MISSING: &str = "Answer again with every required field.";
pub const HINT_ANCHORS: &str =
    "Rate the two outcomes again, as satisfaction of your role if each happened (reject LOW, ideal HIGH), and answer again.";
pub const HINT_IDS: &str = "Rate exactly the candidates the task lists, with their ids exactly as printed, and answer again.";
pub const HINT_FORMAT: &str = "The candidate must be exactly as the format says: put any extra detail in the rationale, and answer again.";
pub const HINT_NUMBER: &str = "Give every score as a number from 0 to 100 on your raw scale, and answer again.";
pub const HINT_DEFAULT: &str =
    "Fix what the message says and answer again. Never work around it: you write only the content, and the message \
itself is written for you.";
pub const HINT_SHAPE: &str =
    "Send it in the shape asked for: a list is a list of separate entries, not one piece of text, and an entry is an \
object with the fields asked for. Then answer again.";
pub const HINT_UNEXPECTED: &str = "Answer once more, with the same values. If it fails again, stop: do not try to work around it.";

/// The text of a failure nobody foresaw: `kind` is what failed, `detail` what it said.
pub fn unexpected(kind: &str, detail: &str) -> String {
    format!("processing the answer failed unexpectedly: {kind}: {detail}")
}

/// What the model is told after the same refusal twice in a row.
pub fn repeated(n: u32) -> String {
    format!(
        "You have been refused the same way {n} times in a row: re-read the task and the fields asked for, and change the \
         answer, not only its wording."
    )
}

/// The sentence for the fields an answer lacks; `parts` are `name` (what it is) texts. With what the answer sent and what is taken, the
/// model sees the difference: one that copies its own earlier answer otherwise repeats the slip.
pub fn lacks(parts: &[&str], sent: &[String], takes: &[String]) -> String {
    let mut out = format!("your answer lacks {}.", parts.join("; "));
    if !sent.is_empty() && !takes.is_empty() {
        out.push_str(&format!(" You sent: {}. Expected: {}.", sent.join(", "), takes.join(", ")));
    }
    out
}

/// What a value is, in words the model reads.
fn kind_of(value: &Value) -> &'static str {
    match value {
        Value::String(_) => "text",
        Value::Bool(_) => "true or false",
        Value::Number(_) => "a number",
        Value::Array(_) => "a list",
        Value::Object(_) => "an object",
        Value::Null => "NoneType",
    }
}

/// The sentence for a field that has the wrong shape, such as a list sent as one piece of text.
pub fn wrong_shape(field: &str, expected: &str, got: &Value) -> String {
    format!("`{field}` must be {expected}, not {}.", kind_of(got))
}

/// True when `error`, an error of the library, says the answer is already given or not the agent's: the turn is over, it is not to be
/// retried. The text of a refusal on its own is never settled: only the type of an error says so.
pub fn settled(error: &ConcordError) -> bool {
    error.is_state()
}

// a refusal and what to do about it: a phrase of the refusal (the texts of the library's own errors, and of `lacks` and `wrong_shape`),
// and the hint. The first row that matches is used.
const HINTS: [(&[&str], &str); 7] = [
    (&["your answer lacks"], HINT_MISSING),
    (&["strictly greater than raw_reject", "must exceed raw_reject"], HINT_ANCHORS),
    (&["covers exactly the candidates", "is not a candidate id", "rated twice", "which it proposed"], HINT_IDS),
    (&["the candidate must", "the candidate is required"], HINT_FORMAT),
    (&["from 0 to 100", "must be a number"], HINT_NUMBER),
    (&["must be a list", "must be an object"], HINT_SHAPE),
    (&["failed unexpectedly"], HINT_UNEXPECTED),
];

/// What to do about the text of a refusal: always a way back onto the path, never just the complaint.
pub fn hint_for_text(text: &str) -> &'static str {
    let m = text.to_lowercase();
    HINTS.iter().find(|(phrases, _)| phrases.iter().any(|p| m.contains(p))).map_or(HINT_DEFAULT, |(_, hint)| hint)
}

/// What to do about a refusal that is an error of the library: always a way back onto the path, never just the complaint.
pub fn hint_for(error: &ConcordError) -> &'static str {
    if settled(error) {
        HINT_ALREADY
    } else {
        hint_for_text(&error.to_string())
    }
}

fn refused(text: &str, hint: &str, repeats: u32, settled: bool) -> String {
    let mut out = format!("refused: {}. Nothing was sent.\nNEXT: {hint}", text.trim().trim_end_matches('.'));
    if repeats >= 2 && !settled {
        out.push(' ');
        out.push_str(&repeated(repeats));
    }
    out
}

/// What the model reads for a refused answer: the fault, that nothing was sent, and what to do. After the same refusal twice in a row
/// the model is also told to change the answer.
pub fn refusal(error: &ConcordError, repeats: u32) -> String {
    refused(&error.to_string(), hint_for(error), repeats, settled(error))
}

/// `refusal` for the text of a refusal that is not an error of the library.
pub fn refusal_text(text: &str, repeats: u32) -> String {
    refused(text, hint_for_text(text), repeats, false)
}

// ---------------------------------------------------------------------------------------------------------------------------
// what the agent receives: the messages of the others, as they arrived (Appendix A.4). In the skills the agent passed each delivery (the
// replies of the other agents, as XML) to its script, so they stayed in its context; here the node takes them (`Step::accepted`) and the
// model reads the same text in front of its next task. A resend is shown as the message it carries.
// ---------------------------------------------------------------------------------------------------------------------------

pub const DELIVERY_HEADER: &str =
    "CONCORD messages you received since your last answer, exactly as they arrived (every message goes to all the participants):";

static RESEND_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<concord-resend\b").unwrap());
static OPEN_TAG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<concord-([a-z-]+)\b").unwrap());
static CLOSE_TAG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"</concord-([a-z-]+)\s*>").unwrap());

/// Whether the name after `concord-` is the resend's.
fn is_resend(name: &str) -> bool {
    name == "resend"
}

/// The message a `concord-resend` carries (the resend itself says nothing new), or `text` if it is not one.
pub fn unwrap(text: &str) -> &str {
    if !RESEND_RE.is_match(text) {
        return text;
    }
    let start = OPEN_TAG_RE.captures_iter(text).find(|c| !is_resend(&c[1])).map(|c| c.get(0).unwrap().start());
    let end = CLOSE_TAG_RE.captures_iter(text).filter(|c| !is_resend(&c[1])).last().map(|c| c.get(0).unwrap().end());
    match (start, end) {
        (Some(s), Some(e)) if s <= e => &text[s..e],
        _ => text,
    }
}

/// The messages received since the last answer, as the agent reads them (empty when there are none).
pub fn render_delivery<S: AsRef<str>>(texts: &[S]) -> String {
    if texts.is_empty() {
        return String::new();
    }
    let body: Vec<&str> = texts.iter().map(|t| unwrap(t.as_ref()).trim()).collect();
    format!("{DELIVERY_HEADER}\n\n{}", body.join("\n\n"))
}

// ---------------------------------------------------------------------------------------------------------------------------
// the end: in the spec the reporter (the first participant) announces the outcome to all the others in a `concord-result`, and each
// participant compares it with its own. The receiver skill ended with: "Nothing is due from you then: do not re-open the decision,
// do not re-propose, and do not send an acknowledgement. If SUCCESS, proceed with the winning candidate. If BEST_EFFORT, say so
// when you report, naming the agents below their floor."
// ---------------------------------------------------------------------------------------------------------------------------

const END_DISPUTED: &str =
    "Nothing is due from you: do not re-open the decision and do not re-propose. But no result of a disputed run is to be relied \
on: do not act on this outcome, and say so when you report.";

const END_RULES: &str =
    "Nothing is due from you: do not re-open the decision, do not re-propose, and do not send an acknowledgement. If SUCCESS, \
proceed with the winning candidate. If BEST_EFFORT, say so when you report, naming the agents below their floor.";

/// What the agent knows when its run is over, from its report (see `Agent::report`). An initiator that is not a participant has no
/// outcome of its own: it learns it from the result of the reporter.
pub fn render_end(me: &str, report: &Report) -> String {
    let cmp = &report.results_compared;
    let outcome = report.outcome.or(cmp.result.as_ref().map(|r| r.outcome));
    if outcome == Some(Outcome::Aborted) {
        let why = report.reason.as_ref();
        let detail = match why {
            Some(w) => {
                let agent = w.agent.as_ref().map(|a| format!(", agent {a}")).unwrap_or_default();
                format!(" ({}{agent})", w.reason)
            }
            None => String::new(),
        };
        return format!("The run was aborted{detail}. There is no outcome to act on: nothing is certified.");
    }
    let (Some(outcome), Some(win)) = (outcome, report.winner.as_ref()) else {
        return "The run is not over: the result of the reporter has not arrived.".to_string();
    };
    let who = if cmp.reporter == me { "You reported".to_string() } else { format!("{} reported", cmp.reporter) };
    let text = win.candidate.clone().unwrap_or_else(|| "None".to_string());
    let mut lines =
        vec![format!("The run is over. {who} the result to all the participants: {outcome}, winning candidate {} ({text}).", win.id)];
    if !report.scores.is_empty() {
        let scores: Vec<String> = report.scores.iter().map(|(a, s)| format!("{a} {}", format_number(*s))).collect();
        let rounds = report.rounds.map(|r| format!(". Repair rounds used: {r}.")).unwrap_or_else(|| ".".to_string());
        lines.push(format!("Final scores of the winning candidate, normalized 0-1: {}{rounds}", scores.join(", ")));
    }
    if !report.shortfall.is_empty() {
        let short: Vec<String> = report.shortfall.iter().map(|(a, s)| format!("{a} by {}", format_number(*s))).collect();
        lines.push(format!("Below the bar: {}.", short.join(", ")));
    }
    if cmp.reporter != me {
        if let Some(matches) = cmp.matches {
            lines.push(format!("Your own outcome {} the reporter's result.", if matches { "matches" } else { "DIFFERS from" }));
        }
    }
    lines.push(format!("Status of the run for you: {}.", report.status.as_str()));
    match report.status {
        ReportStatus::Disputed => lines.push(format!(
            "This run is DISPUTED (mismatch errors sent: {}, received: {}): the participants did not all reach the same outcome, or a \
             cancel reached an agent whose run had ended.",
            report.mismatches.sent,
            report.mismatches.received.len()
        )),
        ReportStatus::Unverified => {
            lines.push("You have no outcome of your own to compare it with: this is the result of the reporter.".to_string())
        }
        _ => {}
    }
    let rules = if report.status == ReportStatus::Disputed { END_DISPUTED } else { END_RULES };
    format!("{}\n{rules}", lines.join("\n"))
}

/// A JSON object as the model's answer to a task, for `check_answer`: a convenience for callers that hold the fields one by one.
pub fn answer_of(fields: impl IntoIterator<Item = (&'static str, Value)>) -> Value {
    Value::Object(fields.into_iter().map(|(k, v)| (k.to_string(), v)).collect::<Map<String, Value>>())
}
