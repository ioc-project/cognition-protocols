//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The CONCORD messages: build every one, and read every one.
//!
//! Everything here works on text and plain values: it holds no process state, no clock and prints nothing.
//!
//! A builder is the author's side: it validates what the spec makes the author responsible for (see Invalid messages), then returns
//! the message as text, with its `check` where it carries one. The text is the stored original: it is sent as it is and never
//! written again.
//!
//! A reader is the receiver's side, in the layers of the Order of checks, so a caller can stop at the first that rejects:
//!
//! * `locate(text)`: the one document in `text`, taken unchanged
//! * `peek(doc)`: the root attributes, without validating anything: enough to know the run, the sender, the `uuid`, the `version`,
//!   and whom to ask when the document is damaged
//! * `check_ok(doc)`: the integrity check
//! * `parse_message(doc, spec)`: the whole message, validated; fails with `InvalidMessage` with the reason that goes into an
//!   `<concord-error reason="invalid">`
//!
//! The run-dependent rules are the caller's: whether the repair round `N` is the next one (a receiver with an incomplete copy of the
//! run state first asks for what it lacks), and who the participants are. `parse_message` takes the candidate schema of the run, when
//! the caller has it, and then also checks the candidates against it.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;
use roxmltree::{Document, Node};
use serde_json::Value;

use crate::engine::{check_params, parse_candidate_id, Params};
use crate::errors::{ConcordError, Result};
use crate::protocol::{
    format_number, is_agent_id, is_uuid, parse_number, quote, squeeze, trim_ws, Num, Outcome, DEFAULT_BUDGET, DEFAULT_EPS, DEFAULT_RETRIES,
    DEFAULT_THRESHOLD, DEFAULT_TIMEOUT, MAX_INTEGER, MILLION, PROTOCOL_VERSION, RESULT_OUTCOMES,
};
use crate::schema::{check_candidate, schema_text, validate_schema};
use crate::xmlutil::{check_of, esc_attr, esc_text, start_tag, verify_check, with_check};
use ioc_protocol::message::{Decode, MessageError};
use ioc_protocol::xml::Locator;

// --------------------------------------------------------------------------
// vocabulary
// --------------------------------------------------------------------------

pub const INIT: &str = "concord-init";
pub const INIT_REPLY: &str = "concord-init-reply";
pub const REPAIR: &str = "concord-repair";
pub const SCORE: &str = "concord-score";
pub const RESULT: &str = "concord-result";
pub const ERROR: &str = "concord-error";
pub const RESEND: &str = "concord-resend";
pub const CANCEL: &str = "concord-cancel";

/// The messages a run is made of, and the ones that carry a `check` (all of them): the others are control messages.
pub const DATA_TAGS: [&str; 5] = [INIT, INIT_REPLY, REPAIR, SCORE, RESULT];
pub const CONTROL_TAGS: [&str; 3] = [ERROR, RESEND, CANCEL];

pub const ERROR_REASONS: [&str; 6] = ["missing", "check", "invalid", "mismatch", "busy", "version"];
pub const ERROR_WHAT: [&str; 5] = ["init", "init-reply", "score", "repair", "result"];
pub const CANCEL_REASONS: [&str; 3] = ["superseded", "version", "missing-agent"];

/// What kind of message a root element names.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Kind {
    Init,
    InitReply,
    Repair,
    Score,
    Result,
    Error,
    Resend,
    Cancel,
}

impl Kind {
    pub fn from_tag(tag: &str) -> Option<Kind> {
        Some(match tag {
            INIT => Kind::Init,
            INIT_REPLY => Kind::InitReply,
            REPAIR => Kind::Repair,
            SCORE => Kind::Score,
            RESULT => Kind::Result,
            ERROR => Kind::Error,
            RESEND => Kind::Resend,
            CANCEL => Kind::Cancel,
            _ => return None,
        })
    }

    /// The name of the kind as the library writes it: `init`, `init-reply`, `repair`, `score`, `result`, `error`, `resend`, `cancel`.
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Init => "init",
            Kind::InitReply => "init-reply",
            Kind::Repair => "repair",
            Kind::Score => "score",
            Kind::Result => "result",
            Kind::Error => "error",
            Kind::Resend => "resend",
            Kind::Cancel => "cancel",
        }
    }

    pub fn tag(self) -> &'static str {
        match self {
            Kind::Init => INIT,
            Kind::InitReply => INIT_REPLY,
            Kind::Repair => REPAIR,
            Kind::Score => SCORE,
            Kind::Result => RESULT,
            Kind::Error => ERROR,
            Kind::Resend => RESEND,
            Kind::Cancel => CANCEL,
        }
    }
}

/// The name of the kind that the tag `tag` stands for, as `Kind::as_str` writes it.
pub fn kind_of_tag(tag: &str) -> Option<&'static str> {
    Kind::from_tag(tag).map(Kind::as_str)
}

/// Where a CONCORD message is in a text: a root element `concord-…`, in a fence marked `xml` or not, or with prose around it.
static LOCATOR: LazyLock<Locator> = LazyLock::new(|| Locator::new("concord-", "CONCORD", "xml"));
static TAG_NAME_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^concord-[a-z-]+$").unwrap());
static ANY_TAG_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]*>").unwrap());

/// A new UUID, as text: the id of a run or of one transmission.
pub fn new_uuid() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn unescape(s: &str) -> String {
    s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&amp;", "&")
}

fn invalid<T>(message: impl Into<String>) -> Result<T> {
    Err(ConcordError::invalid_message(message))
}

/// How deep the elements of a message may nest. A limit of this library, not of the spec: a message of the protocol nests 5 levels at
/// most (a resend around an init reply, its proposal, its score and its `raw`), and the XML parser recurses once per level, so a text
/// of any depth would overflow the stack of whoever receives it.
const MAX_DEPTH: usize = 16;

/// Refuses a `doc` whose elements nest deeper than `MAX_DEPTH`, before the parser sees it. One pass, in a fixed amount of memory: the end
/// of a tag is the first `>` that is not inside a quoted value. A tag that never ends is left to the parser to report.
fn check_depth(doc: &str) -> Result<()> {
    let bytes = doc.as_bytes();
    let (mut depth, mut at) = (0usize, 0usize);
    while let Some(offset) = bytes[at..].iter().position(|&b| b == b'<') {
        let start = at + offset;
        let (mut quote, mut end) = (0u8, None);
        for (i, &b) in bytes.iter().enumerate().skip(start + 1) {
            if quote != 0 {
                if b == quote {
                    quote = 0;
                }
            } else if b == b'"' || b == b'\'' {
                quote = b;
            } else if b == b'>' {
                end = Some(i);
                break;
            }
        }
        let Some(end) = end else { return Ok(()) };
        if bytes[start + 1..].first() == Some(&b'/') {
            depth = depth.saturating_sub(1);
        } else if bytes[end - 1] != b'/' {
            depth += 1;
            if depth > MAX_DEPTH {
                return invalid(format!("the message nests its elements deeper than {MAX_DEPTH} levels"));
            }
        }
        at = end + 1;
    }
    Ok(())
}

// --------------------------------------------------------------------------
// building
// --------------------------------------------------------------------------

/// `value` in millionths, as a raw score or an anchor: 0 to 100 in the form of the spec. Fails with `InvalidMessage`.
fn score_value(value: &Num, what: &str) -> Result<i64> {
    match value.to_millionths() {
        Some(m) if m <= 100 * MILLION => Ok(m),
        _ => invalid(format!("{what} must be a number from 0 to 100 with at most 6 decimal places, not {}", show(value))),
    }
}

fn show(value: &Num) -> String {
    match value {
        Num::Text(t) => quote(t),
        other => other.to_string(),
    }
}

fn need_agent(value: &str, what: &str) -> Result<()> {
    if is_agent_id(value) {
        Ok(())
    } else {
        invalid(format!("{what} must be an agent id (1 to 64 letters, digits, _ or -), not {}", quote(value)))
    }
}

fn need_uuid(value: &str, what: &str) -> Result<()> {
    if is_uuid(value) {
        Ok(())
    } else {
        invalid(format!("{what} must be a UUID, not {}", quote(value)))
    }
}

/// `value` without the whitespace around it, which is required to hold something.
fn need(value: &str, what: &str) -> Result<String> {
    let v = trim_ws(value);
    if v.is_empty() {
        return invalid(format!("{what} is required and cannot be empty"));
    }
    Ok(v.to_string())
}

/// The start tag of a message. `uid` is the `uuid` of the transmission: a caller that must be able to replay a run passes the one its own
/// source of UUIDs gives (the agent always does); without it a random one is made here, which no replay can reproduce.
fn head(tag: &str, from: &str, run_id: &str, uid: Option<&str>, more: &[(&str, Option<&str>)]) -> Result<String> {
    need_agent(from, "from")?;
    if let Some(u) = uid {
        need_uuid(u, "uuid")?;
    }
    let fresh = new_uuid();
    let mut out = format!("<{tag}");
    let fixed =
        [("version", Some(PROTOCOL_VERSION)), ("from", Some(from)), ("run_id", Some(run_id)), ("uuid", Some(uid.unwrap_or(&fresh)))];
    for (k, v) in fixed.iter().chain(more) {
        if let Some(v) = v {
            out.push_str(&format!(" {k}=\"{}\"", esc_attr(v)));
        }
    }
    Ok(out)
}

/// Make the refusal of a validator of another module an `InvalidMessage`.
fn problem<T>(result: Result<T>) -> Result<T> {
    result.map_err(|exc| match exc {
        ConcordError::InvalidMessage(_) => exc,
        other => ConcordError::invalid_message(other.to_string()),
    })
}

/// The parameters of an init that the initiator sets: anything left out takes its default. `satisfaction_threshold` and `eps` are
/// numbers as text, int or float; `budget` is the `B` of the spec.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct InitParams {
    pub satisfaction_threshold: Option<Num>,
    pub eps: Option<Num>,
    pub budget: Option<i64>,
    pub timeout: Option<i64>,
    pub retries: Option<i64>,
}

impl InitParams {
    pub fn satisfaction_threshold(mut self, v: impl Into<Num>) -> Self {
        self.satisfaction_threshold = Some(v.into());
        self
    }

    pub fn eps(mut self, v: impl Into<Num>) -> Self {
        self.eps = Some(v.into());
        self
    }

    pub fn budget(mut self, v: i64) -> Self {
        self.budget = Some(v);
        self
    }

    pub fn timeout(mut self, v: i64) -> Self {
        self.timeout = Some(v);
        self
    }

    pub fn retries(mut self, v: i64) -> Self {
        self.retries = Some(v);
        self
    }

    fn millionths(v: &Option<Num>, key: &str, default: i64) -> Result<i64> {
        match v {
            None => Ok(default),
            Some(n) => n.to_millionths().ok_or_else(|| {
                ConcordError::invalid_message(format!("{key} must be a number with at most 6 decimal places, not {}", show(n)))
            }),
        }
    }

    /// The parameters of the run, with the defaults filled in.
    pub fn resolve(&self) -> Result<Params> {
        Ok(Params {
            satisfaction_threshold: Self::millionths(&self.satisfaction_threshold, "satisfaction_threshold", DEFAULT_THRESHOLD)?,
            eps: Self::millionths(&self.eps, "eps", DEFAULT_EPS)?,
            budget: self.budget.unwrap_or(DEFAULT_BUDGET),
            timeout: self.timeout.unwrap_or(DEFAULT_TIMEOUT),
            retries: self.retries.unwrap_or(DEFAULT_RETRIES),
        })
    }
}

/// `<concord-init>`: the initiator `from` opens a run. `spec` is the candidate schema as a JSON object. `params` holds any of the
/// parameters (the defaults fill the rest). A fresh run id is made when none is given. Fails with `InvalidMessage` when a rule of
/// the configuration is broken.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when a rule of the configuration is broken: an agent id that is not valid, fewer than 2 or repeated
/// participants, a value out of its range, an empty text, a schema that is not supported, or a run id or `uid` that is not a UUID.
#[allow(clippy::too_many_arguments)]
pub fn build_init<S: AsRef<str>>(
    from: &str,
    participants: &[S],
    context: &str,
    x_reject: &str,
    x_ideal: &str,
    spec: &Value,
    params: Option<&InitParams>,
    run_id: Option<&str>,
    uid: Option<&str>,
) -> Result<String> {
    need_agent(from, "the initiator id")?;
    let full = params.cloned().unwrap_or_default().resolve()?;
    let agents: Vec<String> = participants.iter().map(|a| a.as_ref().to_string()).collect();
    problem(check_params(&agents, &full))?;
    need(context, "context")?;
    need(x_reject, "x_reject")?;
    need(x_ideal, "x_ideal")?;
    problem(validate_schema(spec, "schema"))?;
    let fresh = new_uuid();
    let run_id = run_id.unwrap_or(&fresh);
    need_uuid(run_id, "run_id")?;
    let mut lines = vec![format!("{}>", head(INIT, from, run_id, uid, &[])?), "  <participants>".to_string()];
    lines.extend(agents.iter().map(|a| format!("    <agent>{}</agent>", esc_text(a))));
    lines.extend(["  </participants>".to_string(), "  <params>".to_string()]);
    let numbers = [
        ("satisfaction_threshold", "satisfaction_threshold", format_number(full.satisfaction_threshold)),
        ("eps", "eps", format_number(full.eps)),
        ("B", "budget", full.budget.to_string()),
        ("timeout", "timeout", full.timeout.to_string()),
        ("retries", "retries", full.retries.to_string()),
    ];
    lines.extend(numbers.iter().map(|(_, tag, text)| format!("    <{tag}>{}</{tag}>", esc_text(text))));
    lines.extend([
        "  </params>".to_string(),
        format!("  <context>{}</context>", esc_text(context)),
        "  <anchors>".to_string(),
        format!("    <x_reject>{}</x_reject>", esc_text(x_reject)),
        format!("    <x_ideal>{}</x_ideal>", esc_text(x_ideal)),
        "  </anchors>".to_string(),
        "  <proposal>".to_string(),
        format!("    <candidate_schema>{}</candidate_schema>", esc_text(&schema_text(spec))),
        "  </proposal>".to_string(),
        format!("</{INIT}>"),
    ]);
    with_check(&lines.join("\n"))
}

fn proposal_lines(
    pid: &str,
    from: &str,
    candidate: &str,
    rationale: &str,
    raw: &Num,
    reasoning: &str,
    spec: Option<&Value>,
) -> Result<Vec<String>> {
    match parse_candidate_id(pid) {
        Some((_, who)) if who == from => {}
        _ => return invalid(format!("the proposal id {} is not seed-{from} or r<N>-{from}", quote(pid))),
    }
    let candidate = need(candidate, "the candidate")?;
    if let Some(spec) = spec {
        if let Some(why) = problem(check_candidate(spec, &candidate))? {
            return invalid(format!("the candidate {why}"));
        }
    }
    let raw = score_value(raw, "raw")?;
    Ok(vec![
        format!("  <proposal id=\"{}\">", esc_attr(pid)),
        format!("    <candidate>{}</candidate>", esc_text(&candidate)),
        format!("    <rationale>{}</rationale>", esc_text(&need(rationale, "the rationale")?)),
        "    <score>".to_string(),
        format!("      <raw>{}</raw>", format_number(raw)),
        format!("      <reasoning>{}</reasoning>", esc_text(&need(reasoning, "the reasoning")?)),
        "    </score>".to_string(),
        "  </proposal>".to_string(),
    ])
}

/// `<concord-init-reply>`: the anchors, the seed `seed-<from>` and the agent's own score for it. With the candidate schema `spec` of
/// the run, the candidate is checked against it. Fails with `InvalidMessage` for anything the spec makes invalid: a value outside 0
/// to 100 or in another form, `raw_ideal` not above `raw_reject`, a candidate that breaks the schema.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when a value is outside 0 to 100 or in another form, `raw_ideal` does not exceed `raw_reject`, a
/// text is empty, the candidate breaks the schema, or an id is not valid.
#[allow(clippy::too_many_arguments)]
pub fn build_init_reply(
    from: &str,
    run_id: &str,
    raw_reject: &Num,
    raw_ideal: &Num,
    candidate: &str,
    rationale: &str,
    raw: &Num,
    reasoning: &str,
    spec: Option<&Value>,
    uid: Option<&str>,
) -> Result<String> {
    let (reject, ideal) = (score_value(raw_reject, "raw_reject")?, score_value(raw_ideal, "raw_ideal")?);
    if ideal <= reject {
        return invalid(format!(
            "raw_ideal ({}) must be strictly greater than raw_reject ({}). These are satisfaction ratings of the two outcomes \
             (higher = better for your role), not measurements of the current state: re-rate the two outcomes, then answer again.",
            format_number(ideal),
            format_number(reject)
        ));
    }
    need_uuid(run_id, "run_id")?;
    let mut lines = vec![
        format!("{}>", head(INIT_REPLY, from, run_id, uid, &[])?),
        "  <anchors>".to_string(),
        format!("    <raw_reject>{}</raw_reject>", format_number(reject)),
        format!("    <raw_ideal>{}</raw_ideal>", format_number(ideal)),
        "  </anchors>".to_string(),
    ];
    lines.extend(proposal_lines(&format!("seed-{from}"), from, candidate, rationale, raw, reasoning, spec)?);
    lines.push(format!("</{INIT_REPLY}>"));
    with_check(&lines.join("\n"))
}

/// `<concord-repair>`: the proposal `r<n>-<from>` and its proposer's own score. That `n` is the next round is the caller's to see
/// (`EngineState::next_round`).
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the round is 0, a score is outside 0 to 100 or in another form, a text is empty, the candidate
/// breaks the schema, or an id is not valid.
#[allow(clippy::too_many_arguments)]
pub fn build_repair(
    from: &str,
    run_id: &str,
    n: u32,
    candidate: &str,
    rationale: &str,
    raw: &Num,
    reasoning: &str,
    spec: Option<&Value>,
    uid: Option<&str>,
) -> Result<String> {
    if n < 1 {
        return invalid(format!("the repair round must be an integer, at least 1, not {n}"));
    }
    need_uuid(run_id, "run_id")?;
    let mut lines = vec![format!("{}>", head(REPAIR, from, run_id, uid, &[])?)];
    lines.extend(proposal_lines(&format!("r{n}-{from}"), from, candidate, rationale, raw, reasoning, spec)?);
    lines.push(format!("</{REPAIR}>"));
    with_check(&lines.join("\n"))
}

/// One entry of a `<concord-score>` as its author gives it: a candidate id, a raw score and the reasoning.
#[derive(Debug, Clone, PartialEq)]
pub struct ScoreIn {
    pub id: String,
    pub raw: Num,
    pub reasoning: String,
}

impl ScoreIn {
    pub fn new(id: impl Into<String>, raw: impl Into<Num>, reasoning: impl Into<String>) -> Self {
        ScoreIn { id: id.into(), raw: raw.into(), reasoning: reasoning.into() }
    }
}

/// `<concord-score>`: `scores` are the candidates of the others that the sender has not rated. Invalid when empty, when a candidate is
/// rated twice or is one the sender proposed.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when there is no entry, a candidate is rated twice or is one the sender proposed, a score is
/// outside 0 to 100 or in another form, a reasoning is empty, or an id is not valid.
pub fn build_score(from: &str, run_id: &str, scores: &[ScoreIn], uid: Option<&str>) -> Result<String> {
    if scores.is_empty() {
        return invalid("a score message must rate at least one candidate");
    }
    need_uuid(run_id, "run_id")?;
    let mut lines = vec![format!("{}>", head(SCORE, from, run_id, uid, &[])?)];
    let mut seen = std::collections::HashSet::new();
    for s in scores {
        let Some((_, who)) = parse_candidate_id(&s.id) else {
            return invalid(format!("{} is not a candidate id", quote(&s.id)));
        };
        if who == from {
            return invalid(format!("{from} rates {}, which it proposed: its score is in the proposal", s.id));
        }
        if !seen.insert(s.id.as_str()) {
            return invalid(format!("{} is rated twice", s.id));
        }
        let raw = score_value(&s.raw, &format!("the score for {}", s.id))?;
        lines.extend([
            format!("  <score candidate_id=\"{}\">", esc_attr(&s.id)),
            format!("    <raw>{}</raw>", format_number(raw)),
            format!("    <reasoning>{}</reasoning>", esc_text(&need(&s.reasoning, &format!("the reasoning for {}", s.id))?)),
            "  </score>".to_string(),
        ]);
    }
    lines.push(format!("</{SCORE}>"));
    with_check(&lines.join("\n"))
}

/// `<concord-result>`: the outcome of the reporter, SUCCESS or BEST_EFFORT, with the winning candidate. An aborted run sends no
/// result. That the sender is the reporter and the winner a candidate of the run is the caller's to see.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the outcome is `ABORTED`, the winning candidate id is not a candidate id, or an id is not
/// valid.
pub fn build_result(from: &str, run_id: &str, outcome: Outcome, winning_candidate_id: &str, uid: Option<&str>) -> Result<String> {
    if !RESULT_OUTCOMES.contains(&outcome) {
        return invalid(format!("outcome must be one of SUCCESS, BEST_EFFORT, not {}", quote(outcome.as_str())));
    }
    if parse_candidate_id(winning_candidate_id).is_none() {
        return invalid(format!("a {outcome} result needs the id of the winning candidate, not {}", quote(winning_candidate_id)));
    }
    need_uuid(run_id, "run_id")?;
    let lines = [
        format!("{}>", head(RESULT, from, run_id, uid, &[])?),
        format!("  <outcome>{outcome}</outcome>"),
        format!("  <winning_candidate_id>{}</winning_candidate_id>", esc_text(winning_candidate_id)),
        format!("</{RESULT}>"),
    ];
    with_check(&lines.join("\n"))
}

/// `v` quoted, or `(missing)` when there is none.
fn quote_or_missing(v: Option<&str>) -> String {
    v.map_or_else(|| "(missing)".to_string(), quote)
}

fn check_error_fields(what: Option<&str>, reason: Option<&str>, candidate: Option<&str>, reference: Option<&str>) -> Result<()> {
    let reason_ok = reason.filter(|r| ERROR_REASONS.contains(r));
    let Some(reason) = reason_ok else {
        return invalid(format!("reason must be one of {}, not {}", ERROR_REASONS.join(", "), quote_or_missing(reason)));
    };
    let Some(what) = what.filter(|w| ERROR_WHAT.contains(w)) else {
        return invalid(format!("what must be one of {}, not {}", ERROR_WHAT.join(", "), quote_or_missing(what)));
    };
    if what == "init" && !["busy", "invalid", "version"].contains(&reason) {
        return invalid("what=\"init\" goes only with reason=\"busy\", \"invalid\" or \"version\"");
    }
    if reason == "mismatch" && what != "result" {
        return invalid("reason=\"mismatch\" is about a result: what must be \"result\"");
    }
    if ["busy", "invalid", "version"].contains(&reason) && what != "init" {
        return invalid(format!("reason=\"{reason}\" is about the init: what must be \"init\""));
    }
    let reference = reference.filter(|r| !r.is_empty());
    let candidate = candidate.filter(|c| !c.is_empty());
    if reason == "check" && reference.is_none() {
        return invalid("reason=\"check\" needs ref, the uuid of the damaged document");
    }
    if let Some(r) = reference {
        if !is_uuid(r) {
            return invalid(format!("ref must be a UUID, not {}", quote(r)));
        }
        if reason != "check" {
            return invalid("ref goes only with reason=\"check\"");
        }
    }
    if (what == "score" || what == "repair") && reason != "check" && candidate.is_none() {
        return invalid(format!("what=\"{what}\" needs candidate, the id of the candidate concerned"));
    }
    if what != "score" && what != "repair" && candidate.is_some() {
        return invalid(format!("candidate goes only with what=\"score\" or what=\"repair\", not \"{what}\""));
    }
    Ok(())
}

/// `<concord-error>`: ask `to` for a message again (`missing`, `check`), or tell it that its init is refused (`busy`, `version`), its
/// init is invalid (`invalid`), or its result differs (`mismatch`). It carries no `check`. `text` is for readers only.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when `what`, `reason`, `candidate` and `reference` do not go together as the spec says, or an id is
/// not valid.
#[allow(clippy::too_many_arguments)]
pub fn build_error(
    from: &str,
    run_id: &str,
    to: &str,
    what: &str,
    reason: &str,
    candidate: Option<&str>,
    reference: Option<&str>,
    text: &str,
    uid: Option<&str>,
) -> Result<String> {
    check_error_fields(Some(what), Some(reason), candidate, reference)?;
    need_uuid(run_id, "run_id")?;
    need_agent(to, "to")?;
    let more = [("to", Some(to)), ("what", Some(what)), ("candidate", candidate), ("ref", reference), ("reason", Some(reason))];
    let head = head(ERROR, from, run_id, uid, &more)?;
    let text = trim_ws(text);
    Ok(if text.is_empty() { format!("{head}/>") } else { format!("{head}>\n  {}\n</{ERROR}>", esc_text(text)) })
}

/// `<concord-resend>`: carry the stored message `original` again, word for word. `from` is the author of the message inside.
/// `reply_to` is the `uuid` of the error it answers, when it answers one.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when `original` is not a message of the run, was not written by `from`, or an id is not valid.
pub fn build_resend(from: &str, run_id: &str, original: &str, reply_to: Option<&str>, uid: Option<&str>) -> Result<String> {
    let inner = trim_ws(original);
    let info = peek(inner).filter(|i| DATA_TAGS.contains(&i.tag.as_str()));
    let Some(info) = info else {
        return invalid("a resend carries a message of the run: an init, init reply, repair, score or result");
    };
    if info.attrs.get("from").map(String::as_str) != Some(from) {
        return invalid(format!(
            "{from} can only send again its own messages, not one from {}",
            quote_or_missing(info.attrs.get("from").map(String::as_str))
        ));
    }
    if let Some(r) = reply_to {
        need_uuid(r, "reply_to")?;
    }
    need_uuid(run_id, "run_id")?;
    let head = head(RESEND, from, run_id, uid, &[("reply_to", reply_to)])?;
    Ok(format!("{head}>\n{inner}\n</{RESEND}>"))
}

fn check_cancel_fields(reason: &str, superseded_by: Option<&str>, agent: Option<&str>) -> Result<()> {
    if !CANCEL_REASONS.contains(&reason) {
        return invalid(format!("reason must be one of {}, not {}", CANCEL_REASONS.join(", "), quote(reason)));
    }
    // an attribute that is there counts, whatever its value: an empty one is not a UUID or an agent id, and is refused below
    if (reason == "superseded") != superseded_by.is_some() {
        return invalid("superseded_by goes with reason=\"superseded\", and only with it");
    }
    if (reason == "missing-agent") != agent.is_some() {
        return invalid("agent goes with reason=\"missing-agent\", and only with it");
    }
    if let Some(s) = superseded_by {
        if !is_uuid(s) {
            return invalid(format!("superseded_by must be a UUID, not {}", quote(s)));
        }
    }
    if let Some(a) = agent {
        if !is_agent_id(a) {
            return invalid(format!("agent must be an agent id, not {}", quote(a)));
        }
    }
    Ok(())
}

/// `<concord-cancel>`: ends the run for every agent. `superseded_by` is the run id that continues, for `superseded`; `agent` is the
/// agent that was found silent, for `missing-agent`. It carries no `check`.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the reason is not one of the spec's, `superseded_by` or `agent` is missing for the reason that
/// needs it (or given for one that does not), or an id is not valid.
pub fn build_cancel(
    from: &str,
    run_id: &str,
    reason: &str,
    superseded_by: Option<&str>,
    agent: Option<&str>,
    uid: Option<&str>,
) -> Result<String> {
    check_cancel_fields(reason, superseded_by, agent)?;
    need_uuid(run_id, "run_id")?;
    let more = [("reason", Some(reason)), ("superseded_by", superseded_by), ("agent", agent)];
    Ok(format!("{}/>", head(CANCEL, from, run_id, uid, &more)?))
}

// --------------------------------------------------------------------------
// reading, layer by layer
// --------------------------------------------------------------------------

/// The CONCORD document in `text`, taken unchanged, from its first `<` to its last `>`. The message may be handed over inside a
/// markdown fence or prose; what is returned is the sender's own text, byte for byte. Fails with `InvalidMessage` when there is none,
/// or it is not complete.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when `text` holds no CONCORD message, or the message is cut off.
pub fn locate(text: &str) -> Result<&str> {
    Ok(LOCATOR.locate(text)?)
}

/// The root of a document, read from its text without validating anything.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Peek {
    /// The name of the root element.
    pub tag: String,
    /// Every attribute of the start tag (`version`, `from`, `run_id`, `uuid`, `check`, `reply_to`, ...), unescaped.
    pub attrs: HashMap<String, String>,
    /// The peek of the document inside a `<concord-resend>`, one level only: its own `inner` is None.
    pub inner: Option<Box<Peek>>,
    /// The text of an `<concord-error>`: an error of another version is read by its root attributes and this text alone.
    pub text: Option<String>,
}

impl Peek {
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs.get(name).map(String::as_str)
    }
}

/// The root of `doc`, read from its text without validating anything, or None when it is not a CONCORD element. The document inside a
/// `<concord-resend>` is read one level deep: a resend inside a resend is not a message of the protocol, so its `inner` is None, and a
/// text of any depth is read in a fixed number of steps.
pub fn peek(doc: &str) -> Option<Peek> {
    peek_level(doc, true)
}

fn peek_level(doc: &str, nested: bool) -> Option<Peek> {
    let doc = trim_ws(doc);
    let tag = start_tag(doc).filter(|t| TAG_NAME_RE.is_match(&t.name))?;
    let name = tag.name.as_str();
    let body = if tag.empty {
        ""
    } else {
        // Like a slice up to a `rfind` that found nothing: everything but the last character.
        let end = doc.rfind(&format!("</{name}")).unwrap_or_else(|| doc.char_indices().last().map_or(0, |(i, _)| i));
        doc.get(tag.end..end).unwrap_or("")
    };
    let mut out = Peek { tag: name.to_string(), attrs: tag.attrs.clone(), inner: None, text: None };
    match name {
        RESEND if nested => out.inner = peek_level(body, false).map(Box::new),
        ERROR => {
            let plain = ANY_TAG_RE.replace_all(body, " ");
            out.text = Some(squeeze(&unescape(&plain)));
        }
        _ => {}
    }
    Some(out)
}

/// Whether `doc` carries a `check` and it is correct (the integrity check, on the text).
pub fn check_ok(doc: &str) -> bool {
    verify_check(doc)
}

// --------------------------------------------------------------------------
// the messages as read
// --------------------------------------------------------------------------

/// A proposal: the candidate, the rationale and the proposer's own score for it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Proposal {
    pub id: String,
    pub round: u32,
    pub candidate: String,
    pub rationale: String,
    /// In millionths.
    pub raw: i64,
    pub reasoning: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct InitBody {
    pub participants: Vec<String>,
    pub params: Params,
    pub context: String,
    pub x_reject: String,
    pub x_ideal: String,
    pub schema: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InitReplyBody {
    /// In millionths.
    pub raw_reject: i64,
    /// In millionths.
    pub raw_ideal: i64,
    pub proposal: Proposal,
}

/// One entry of a score as it was read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScoreEntry {
    pub id: String,
    /// In millionths.
    pub raw: i64,
    pub reasoning: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScoreBody {
    pub scores: Vec<ScoreEntry>,
    /// The ids of the entries that were ignored.
    pub ignored: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResultBody {
    pub outcome: Outcome,
    pub winning_candidate_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ErrorBody {
    pub to: String,
    pub what: String,
    pub candidate: Option<String>,
    /// The `ref` attribute.
    pub reference: Option<String>,
    pub reason: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResendBody {
    pub reply_to: Option<String>,
    /// The stored message, as text.
    pub inner: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelBody {
    pub reason: String,
    pub superseded_by: Option<String>,
    pub agent: Option<String>,
}

/// What a message says, by kind.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum Body {
    Init(InitBody),
    InitReply(InitReplyBody),
    Repair(Proposal),
    Score(ScoreBody),
    Result(ResultBody),
    Error(ErrorBody),
    Resend(ResendBody),
    Cancel(CancelBody),
}

/// A message read and validated: the common attributes, and the fields of its kind.
#[derive(Debug, Clone, PartialEq)]
pub struct Message {
    pub version: String,
    pub from: String,
    pub run_id: String,
    pub uuid: String,
    pub check: Option<String>,
    pub body: Body,
}

impl Message {
    pub fn kind(&self) -> Kind {
        match self.body {
            Body::Init(_) => Kind::Init,
            Body::InitReply(_) => Kind::InitReply,
            Body::Repair(_) => Kind::Repair,
            Body::Score(_) => Kind::Score,
            Body::Result(_) => Kind::Result,
            Body::Error(_) => Kind::Error,
            Body::Resend(_) => Kind::Resend,
            Body::Cancel(_) => Kind::Cancel,
        }
    }
}

// --------------------------------------------------------------------------
// reading the content
// --------------------------------------------------------------------------

type Element<'a, 'b> = Node<'a, 'b>;

fn name_of<'a>(el: Element<'a, '_>) -> &'a str {
    el.tag_name().name()
}

/// The text of an element before its first child element, without the whitespace around it.
fn txt(el: Element) -> String {
    trim_ws(own_text(el)).to_string()
}

fn own_text<'a>(el: Element<'a, '_>) -> &'a str {
    el.first_child().filter(|c| c.is_text()).and_then(|c| c.text()).unwrap_or("")
}

fn children<'a, 'b>(parent: Element<'a, 'b>, tag: &str) -> impl Iterator<Item = Element<'a, 'b>> {
    let tag = tag.to_string();
    parent.children().filter(move |c| c.is_element() && name_of(*c) == tag && c.tag_name().namespace().is_none())
}

fn child<'a, 'b>(parent: Element<'a, 'b>, tag: &str) -> Result<Element<'a, 'b>> {
    match children(parent, tag).next() {
        Some(el) => Ok(el),
        None => invalid(format!("<{tag}> is missing in <{}>", name_of(parent))),
    }
}

fn required(parent: Element, tag: &str) -> Result<String> {
    let t = txt(child(parent, tag)?);
    if t.is_empty() {
        return invalid(format!("<{tag}> in <{}> is empty", name_of(parent)));
    }
    Ok(t)
}

/// The number in `text`, in millionths. Fails with `InvalidMessage` unless it is written in the form of the spec.
fn number(text: &str, what: &str) -> Result<i64> {
    parse_number(text).ok_or_else(|| {
        ConcordError::invalid_message(format!(
            "{what} is not a number in the form of the spec (digits, an optional point, at most 6 decimal places): {}",
            quote(text)
        ))
    })
}

fn raw_number(text: &str, what: &str) -> Result<i64> {
    let m = number(text, what)?;
    if m > 100 * MILLION {
        return invalid(format!("{what} must be a number from 0 to 100, not {}", quote(text)));
    }
    Ok(m)
}

fn integer(text: &str, what: &str) -> Result<i64> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return invalid(format!("{what} is not an integer: {}", quote(text)));
    }
    let digits = text.trim_start_matches('0'); // nothing left is a zero
    if digits.len() > MAX_INTEGER.to_string().len() {
        return invalid(format!("{what} is above {MAX_INTEGER}"));
    }
    Ok(digits.parse().unwrap_or(0))
}

fn attr<'a>(el: Element<'a, '_>, name: &str) -> Option<&'a str> {
    el.attribute(name).filter(|v| !v.is_empty())
}

fn parse_proposal(root: Element, from: &str, spec: Option<&Value>, seed: bool) -> Result<Proposal> {
    let p = child(root, "proposal")?;
    let pid = p.attribute("id").unwrap_or("");
    let parsed = parse_candidate_id(pid);
    let round = match parsed {
        Some((round, who)) if who == from && (round == 0) == seed => round,
        _ => {
            return invalid(format!(
                "the proposal id {} is not {}",
                quote(pid),
                if seed { format!("seed-{from}") } else { format!("r<N>-{from}") }
            ))
        }
    };
    let candidate = required(p, "candidate")?;
    if let Some(spec) = spec {
        if let Some(why) = problem(check_candidate(spec, &candidate))? {
            return invalid(format!("the candidate {why}"));
        }
    }
    let s = child(p, "score")?;
    let raw = raw_number(&required(s, "raw")?, "<raw>")?;
    Ok(Proposal { id: pid.to_string(), round, candidate, rationale: required(p, "rationale")?, raw, reasoning: required(s, "reasoning")? })
}

fn parse_init(root: Element) -> Result<InitBody> {
    let participants: Vec<String> = children(child(root, "participants")?, "agent").map(txt).collect();
    let mut params = Params::default();
    let boxed = children(root, "params").next();
    // an element that is there is read: an empty one is not a number, and is refused below
    let given = |tag: &str| boxed.and_then(|b| children(b, tag).next()).map(txt);
    if let Some(t) = given("satisfaction_threshold") {
        params.satisfaction_threshold = number(&t, "<satisfaction_threshold>")?;
    }
    if let Some(t) = given("eps") {
        params.eps = number(&t, "<eps>")?;
    }
    if let Some(t) = given("budget") {
        params.budget = integer(&t, "<budget>")?;
    }
    if let Some(t) = given("timeout") {
        params.timeout = integer(&t, "<timeout>")?;
    }
    if let Some(t) = given("retries") {
        params.retries = integer(&t, "<retries>")?;
    }
    problem(check_params(&participants, &params))?;
    let anchors = child(root, "anchors")?;
    let text = required(child(root, "proposal")?, "candidate_schema")?;
    let loaded: Value = serde_json::from_str(&text)
        .map_err(|exc| ConcordError::invalid_message(format!("<candidate_schema> is not valid JSON ({exc})")))?;
    let (context, x_reject, x_ideal) = (required(root, "context")?, required(anchors, "x_reject")?, required(anchors, "x_ideal")?);
    problem(validate_schema(&loaded, "schema"))?;
    let schema = loaded;
    Ok(InitBody { participants, params, context, x_reject, x_ideal, schema })
}

fn parse_init_reply(root: Element, from: &str, spec: Option<&Value>) -> Result<InitReplyBody> {
    let anchors = child(root, "anchors")?;
    let raw_reject = raw_number(&required(anchors, "raw_reject")?, "<raw_reject>")?;
    let raw_ideal = raw_number(&required(anchors, "raw_ideal")?, "<raw_ideal>")?;
    if raw_ideal <= raw_reject {
        return invalid(format!("raw_ideal ({}) must exceed raw_reject ({})", format_number(raw_ideal), format_number(raw_reject)));
    }
    Ok(InitReplyBody { raw_reject, raw_ideal, proposal: parse_proposal(root, from, spec, true)? })
}

/// The entries of a score message. An entry that rates a candidate the sender proposed, or one that an earlier entry already rated,
/// is ignored and the others are kept: `ignored` lists their candidate ids.
fn parse_score(root: Element, from: &str) -> Result<ScoreBody> {
    let (mut scores, mut ignored, mut seen, mut found) = (Vec::new(), Vec::new(), std::collections::HashSet::new(), false);
    for sc in children(root, "score") {
        found = true;
        let cid = sc.attribute("candidate_id").unwrap_or("");
        let Some((_, who)) = parse_candidate_id(cid) else {
            return invalid(format!("{} is not a candidate id", quote(cid)));
        };
        let raw = raw_number(&required(sc, "raw")?, &format!("the <raw> of {cid}"))?;
        let reasoning = required(sc, "reasoning")?;
        if who == from || seen.contains(cid) {
            ignored.push(cid.to_string());
            continue;
        }
        seen.insert(cid);
        scores.push(ScoreEntry { id: cid.to_string(), raw, reasoning });
    }
    if !found {
        return invalid("a score message must rate at least one candidate");
    }
    Ok(ScoreBody { scores, ignored })
}

fn parse_result(root: Element) -> Result<ResultBody> {
    let text = required(root, "outcome")?;
    let Some(outcome) = Outcome::parse(&text).filter(|o| RESULT_OUTCOMES.contains(o)) else {
        return invalid(format!("outcome must be one of SUCCESS, BEST_EFFORT, not {}", quote(&text)));
    };
    let winner = required(root, "winning_candidate_id")?;
    if parse_candidate_id(&winner).is_none() {
        return invalid(format!("<winning_candidate_id> is not a candidate id: {}", quote(&winner)));
    }
    Ok(ResultBody { outcome, winning_candidate_id: winner })
}

fn parse_error(root: Element) -> Result<ErrorBody> {
    let Some(to) = attr(root, "to") else {
        return invalid("<concord-error> needs the attribute to");
    };
    need_agent(to, "to")?;
    let (what, candidate, reference, reason) = (attr(root, "what"), attr(root, "candidate"), attr(root, "ref"), attr(root, "reason"));
    check_error_fields(what, reason, candidate, reference)?;
    Ok(ErrorBody {
        to: to.to_string(),
        what: what.unwrap_or_default().to_string(),
        candidate: candidate.map(str::to_string),
        reference: reference.map(str::to_string),
        reason: reason.unwrap_or_default().to_string(),
        text: squeeze(own_text(root)),
    })
}

fn parse_resend(doc: &str, root: Element) -> Result<ResendBody> {
    let kids: Vec<Element> = root.children().filter(|c| c.is_element()).collect();
    // the message that it carries is a message of the protocol: an element of a namespace with the same local name is not one
    if kids.len() != 1 || !DATA_TAGS.contains(&name_of(kids[0])) || kids[0].tag_name().namespace().is_some() {
        return invalid("a <concord-resend> carries exactly one message of the run");
    }
    let Some(start) = start_tag(doc) else {
        return invalid("the start tag of a <concord-resend> is not well-formed");
    };
    let end = doc.rfind(&format!("</{RESEND}")).unwrap_or(doc.len());
    let inner = trim_ws(doc.get(start.end..end).unwrap_or("")).to_string();
    let reply_to = root.attribute("reply_to");
    if let Some(r) = reply_to {
        if !is_uuid(r) {
            return invalid(format!("reply_to must be a UUID, not {}", quote(r)));
        }
    }
    Ok(ResendBody { reply_to: reply_to.map(str::to_string), inner })
}

fn parse_cancel(root: Element) -> Result<CancelBody> {
    let (reason, by, agent) = (root.attribute("reason"), root.attribute("superseded_by"), root.attribute("agent"));
    check_cancel_fields(reason.unwrap_or(""), by, agent)?;
    Ok(CancelBody {
        reason: reason.unwrap_or_default().to_string(),
        superseded_by: by.map(str::to_string),
        agent: agent.map(str::to_string),
    })
}

/// The message in `doc`, validated. The `version` is read, not compared, and the `check` is not verified: those are the earlier
/// steps of the Order of checks. With the candidate schema `spec` of the run, every candidate is checked against it. Fails with
/// `InvalidMessage` with the reason a receiver puts in its error.
///
/// Every number is an integer count of millionths (see `protocol::MILLION`): the anchors, the raw scores, and the
/// `satisfaction_threshold` and `eps` of the init.
///
/// # Errors
///
/// `ConcordError::InvalidMessage`, with the reason a receiver puts in its error, when the message is not well-formed, lacks a field
/// or an attribute, holds a value out of its range, or breaks the schema `spec`.
pub fn parse_message(doc: &str, spec: Option<&Value>) -> Result<Message> {
    let doc = trim_ws(doc);
    if !doc.starts_with('<') {
        return invalid("the message is not well-formed XML: it does not start with <");
    }
    if doc.contains("<!") || doc.contains("<?") {
        return invalid("a message holds no comments, CDATA sections, processing instructions or declarations");
    }
    check_depth(doc)?;
    let tree = Document::parse(doc).map_err(|exc| ConcordError::invalid_message(format!("the message is not well-formed XML: {exc}")))?;
    let root = tree.root_element();
    let tag = name_of(root);
    let kind = match Kind::from_tag(tag).filter(|_| root.tag_name().namespace().is_none()) {
        Some(kind) => kind,
        None => return invalid(format!("unknown message <{tag}>")),
    };
    let mut common = Vec::new();
    for k in ["version", "from", "run_id", "uuid"] {
        match attr(root, k) {
            Some(v) => common.push(v.to_string()),
            None => return invalid(format!("<{tag}> needs the attribute {k}")),
        }
    }
    let [version, from, run_id, uuid]: [String; 4] = common.try_into().expect("four attributes");
    for (k, v) in [("run_id", &run_id), ("uuid", &uuid)] {
        need_uuid(v, k)?;
    }
    need_agent(&from, "from")?;
    let body = match kind {
        Kind::Init => Body::Init(parse_init(root)?),
        Kind::InitReply => Body::InitReply(parse_init_reply(root, &from, spec)?),
        Kind::Repair => Body::Repair(parse_proposal(root, &from, spec, false)?),
        Kind::Score => Body::Score(parse_score(root, &from)?),
        Kind::Result => Body::Result(parse_result(root)?),
        Kind::Error => Body::Error(parse_error(root)?),
        Kind::Resend => Body::Resend(parse_resend(doc, root)?),
        Kind::Cancel => Body::Cancel(parse_cancel(root)?),
    };
    Ok(Message { version, from, run_id, uuid, check: check_of(doc), body })
}

/// A CONCORD message is decoded by `parse_message`, with the candidate schema of the run when the caller has it:
/// `ioc_protocol::message::read::<Message>(text, locate, spec)` locates it in a text, then reads it. Every refusal is `Invalid`, with
/// the reason a receiver puts in its error.
impl Decode for Message {
    type Context<'a> = Option<&'a Value>;

    fn decode(doc: &str, spec: Option<&Value>) -> std::result::Result<Self, MessageError> {
        parse_message(doc, spec).map_err(|exc| MessageError::invalid(exc.message()))
    }
}
