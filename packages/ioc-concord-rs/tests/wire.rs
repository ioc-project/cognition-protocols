//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The messages: building each one (the author's checks), reading each one (the receiver's checks), the integrity check, locating and
//! peeking.
//!
//! An outcome that `Outcome` cannot hold (a string such as `"WON"`) cannot be written: `Outcome::Aborted` stands for the one that the type
//! allows, and a winning candidate of `None` is the empty text.

mod common;

use common::RUN;
use ioc_concord::engine::Params;
use ioc_concord::errors::{ConcordError, Result};
use ioc_concord::protocol::Num;
use ioc_concord::protocol::{
    is_uuid, Outcome, DEFAULT_BUDGET, DEFAULT_EPS, DEFAULT_RETRIES, DEFAULT_THRESHOLD, DEFAULT_TIMEOUT, MILLION as M,
};
use ioc_concord::wire::{
    build_cancel, build_error, build_init, build_init_reply, build_repair, build_resend, build_result, build_score, check_ok, locate,
    parse_message, peek, Body, InitBody, InitParams, InitReplyBody, Message, Proposal, ScoreBody, ScoreEntry, ScoreIn,
};
use ioc_concord::xmlutil::{check_of, with_check};
use regex::Regex;
use serde_json::{json, Value};

const OTHER: &str = "c3d4e5f6-0718-4a9b-8c2d-1e3f5a7b9d02";

fn spec() -> Value {
    json!({"type": "string", "description": "a plan"})
}

fn enum_spec() -> Value {
    json!({"enum": ["A", "B"]})
}

// --- the messages as the tests write them ------------------------------------

struct Init {
    from: &'static str,
    participants: Vec<&'static str>,
    context: &'static str,
    x_reject: &'static str,
    x_ideal: &'static str,
    spec: Value,
    params: Option<InitParams>,
    run_id: Option<&'static str>,
}

impl Default for Init {
    fn default() -> Self {
        Init {
            from: "alice",
            participants: vec!["cost", "security"],
            context: "pick one",
            x_reject: "nothing",
            x_ideal: "all agree",
            spec: spec(),
            params: None,
            run_id: Some(RUN),
        }
    }
}

impl Init {
    fn build(&self) -> Result<String> {
        build_init(
            self.from,
            &self.participants,
            self.context,
            self.x_reject,
            self.x_ideal,
            &self.spec,
            self.params.as_ref(),
            self.run_id,
            None,
        )
    }
}

fn init() -> String {
    Init::default().build().unwrap()
}

struct InitReply {
    raw_reject: Num,
    raw_ideal: Num,
    candidate: &'static str,
    rationale: &'static str,
    raw: Num,
    reasoning: &'static str,
    spec: Option<Value>,
}

impl Default for InitReply {
    fn default() -> Self {
        InitReply {
            raw_reject: 8.into(),
            raw_ideal: 95.into(),
            candidate: "local cache",
            rationale: "because",
            raw: 82.into(),
            reasoning: "good",
            spec: None,
        }
    }
}

impl InitReply {
    fn build(&self) -> Result<String> {
        build_init_reply(
            "cost",
            RUN,
            &self.raw_reject,
            &self.raw_ideal,
            self.candidate,
            self.rationale,
            &self.raw,
            self.reasoning,
            self.spec.as_ref(),
            None,
        )
    }
}

fn init_reply() -> String {
    InitReply::default().build().unwrap()
}

struct Repair {
    n: u32,
    candidate: &'static str,
    raw: Num,
    spec: Option<Value>,
}

impl Default for Repair {
    fn default() -> Self {
        Repair { n: 1, candidate: "bigger cache", raw: 70.into(), spec: None }
    }
}

impl Repair {
    fn build(&self) -> Result<String> {
        build_repair("cost", RUN, self.n, self.candidate, "because", &self.raw, "ok", self.spec.as_ref(), None)
    }
}

fn repair() -> String {
    Repair::default().build().unwrap()
}

fn entries() -> Vec<ScoreIn> {
    vec![ScoreIn::new("seed-security", 41, "risky"), ScoreIn::new("r1-security", 60.5, "better")]
}

fn score() -> String {
    build_score("cost", RUN, &entries(), None).unwrap()
}

fn score_of(scores: Vec<ScoreIn>) -> Result<String> {
    build_score("cost", RUN, &scores, None)
}

fn error_of(from: &str, to: &str, what: &str, reason: &str) -> String {
    build_error(from, RUN, to, what, reason, None, None, "", None).unwrap()
}

/// The arguments of `build_error`, with the defaults of the tests: a `score` of `b` that is missing.
struct Err_ {
    to: &'static str,
    what: &'static str,
    reason: &'static str,
    candidate: Option<&'static str>,
    reference: Option<&'static str>,
    text: &'static str,
}

impl Default for Err_ {
    fn default() -> Self {
        Err_ { to: "b", what: "score", reason: "missing", candidate: Some("seed-b"), reference: None, text: "" }
    }
}

impl Err_ {
    fn build(&self) -> Result<String> {
        build_error("a", RUN, self.to, self.what, self.reason, self.candidate, self.reference, self.text, None)
    }
}

// --- reading what was built ---------------------------------------------------

fn parse(doc: &str) -> Message {
    parse_message(doc, None).unwrap_or_else(|e| panic!("{e}: {doc}"))
}

fn init_body(m: Message) -> InitBody {
    match m.body {
        Body::Init(b) => b,
        other => panic!("not an init: {other:?}"),
    }
}

fn reply_body(m: Message) -> InitReplyBody {
    match m.body {
        Body::InitReply(b) => b,
        other => panic!("not an init reply: {other:?}"),
    }
}

fn proposal_of(m: Message) -> Proposal {
    match m.body {
        Body::Repair(p) => p,
        Body::InitReply(b) => b.proposal,
        other => panic!("no proposal: {other:?}"),
    }
}

fn score_body(m: Message) -> ScoreBody {
    match m.body {
        Body::Score(b) => b,
        other => panic!("not a score: {other:?}"),
    }
}

fn attr(doc: &str, name: &str) -> String {
    peek(doc).unwrap().attrs.get(name).cloned().unwrap_or_default()
}

fn entry(id: &str, raw: i64, reasoning: &str) -> ScoreEntry {
    ScoreEntry { id: id.into(), raw, reasoning: reasoning.into() }
}

/// That the call is refused with an `InvalidMessage` whose text matches the regular expression `pattern` (a search, as `re.search`).
fn refuses<T: std::fmt::Debug>(result: Result<T>, pattern: &str) {
    match result {
        Err(ConcordError::InvalidMessage(m)) => {
            assert!(Regex::new(pattern).unwrap().is_match(&m), "the refusal {m:?} does not match {pattern:?}")
        }
        other => panic!("expected an InvalidMessage matching {pattern:?}, got {other:?}"),
    }
}

fn refuses_parse(doc: &str, pattern: &str) {
    refuses(parse_message(doc, None), pattern);
}

fn default_params() -> Params {
    Params {
        satisfaction_threshold: DEFAULT_THRESHOLD,
        eps: DEFAULT_EPS,
        budget: DEFAULT_BUDGET,
        timeout: DEFAULT_TIMEOUT,
        retries: DEFAULT_RETRIES,
    }
}

fn with_params(params: InitParams) -> Init {
    Init { params: Some(params), ..Init::default() }
}

// --- every message round-trips ----------------------------------------------

#[test]
fn every_data_message_carries_a_correct_check_and_reads_back() {
    let result = build_result("cost", RUN, Outcome::Success, "seed-cost", None).unwrap();
    let docs = [("init", init()), ("init-reply", init_reply()), ("repair", repair()), ("score", score()), ("result", result)];
    for (kind, doc) in docs {
        assert!(check_ok(&doc), "{kind}");
        let m = parse(&doc);
        assert_eq!((m.kind().as_str(), m.version.as_str(), m.run_id.as_str()), (kind, "0.3.0", RUN));
        assert_eq!(m.check.as_deref(), Some(attr(&doc, "check").as_str()));
        assert!(is_uuid(&m.uuid));
    }
}

#[test]
fn init_reads_back_with_every_parameter() {
    let params = InitParams::default().satisfaction_threshold(0.7).eps(0.1).budget(4).timeout(60).retries(1);
    let m = parse(&with_params(params).build().unwrap());
    assert_eq!(m.from, "alice");
    let b = init_body(m);
    assert_eq!(b.participants, ["cost", "security"]);
    assert_eq!(b.params, Params { satisfaction_threshold: 700_000, eps: 100_000, budget: 4, timeout: 60, retries: 1 });
    assert_eq!((b.context.as_str(), b.x_reject.as_str(), b.x_ideal.as_str(), &b.schema), ("pick one", "nothing", "all agree", &spec()));
}

#[test]
fn init_defaults_fill_what_is_left_out_and_a_zero_timeout_is_not_valid() {
    assert_eq!(init_body(parse(&init())).params, default_params());
    refuses(with_params(InitParams::default().timeout(0)).build(), "timeout");
    refuses_parse(&init().replace("<timeout>300</timeout>", "<timeout>0</timeout>"), "timeout must be an integer, from 1 to");
    let bare = init().replace("<params>", "<x>").replace("</params>", "</x>");
    assert_eq!(init_body(parse(&bare)).params, default_params());
}

#[test]
fn init_makes_a_run_id_when_none_is_given() {
    let fresh = || attr(&Init { run_id: None, ..Init::default() }.build().unwrap(), "run_id");
    let (a, b) = (fresh(), fresh());
    assert!(is_uuid(&a) && a != b);
}

#[test]
fn init_reply_and_repair_carry_the_proposal_and_the_proposers_own_score() {
    let b = reply_body(parse(&init_reply()));
    assert_eq!((b.raw_reject, b.raw_ideal), (8 * M, 95 * M));
    assert_eq!(
        b.proposal,
        Proposal {
            id: "seed-cost".into(),
            round: 0,
            candidate: "local cache".into(),
            rationale: "because".into(),
            raw: 82 * M,
            reasoning: "good".into()
        }
    );
    let p = proposal_of(parse(&Repair { n: 3, ..Repair::default() }.build().unwrap()));
    assert_eq!((p.id.as_str(), p.round, p.raw), ("r3-cost", 3, 70 * M));
}

#[test]
fn score_reads_back_every_rating_in_order() {
    let b = score_body(parse(&score()));
    assert_eq!(b.scores, [entry("seed-security", 41 * M, "risky"), entry("r1-security", 60_500_000, "better")]);
    assert!(b.ignored.is_empty());
}

#[test]
fn a_result_carries_its_outcome_and_winner_and_an_aborted_run_has_none() {
    for outcome in [Outcome::Success, Outcome::BestEffort] {
        let m = parse(&build_result("cost", RUN, outcome, "r2-security", None).unwrap());
        assert!(matches!(&m.body, Body::Result(r) if r.outcome == outcome && r.winning_candidate_id == "r2-security"));
    }
    refuses(build_result("cost", RUN, Outcome::Aborted, "seed-cost", None), "outcome must be");
    let doc = build_result("cost", RUN, Outcome::Success, "seed-cost", None).unwrap();
    for bad in ["<outcome>ABORTED</outcome>", "<outcome>success</outcome>", "<outcome></outcome>"] {
        refuses_parse(&doc.replace("<outcome>SUCCESS</outcome>", bad), "outcome");
    }
    refuses_parse(&doc.replace("<winning_candidate_id>seed-cost</winning_candidate_id>", ""), "winning_candidate_id");
}

#[test]
fn error_resend_and_cancel_read_back() {
    let err = build_error("cost", RUN, "security", "score", "missing", Some("seed-cost"), None, "I miss <it> & more", None).unwrap();
    let m = parse(&err);
    let Body::Error(e) = &m.body else { panic!("not an error") };
    assert_eq!(
        (e.to.as_str(), e.what.as_str(), e.candidate.as_deref(), e.reason.as_str(), e.text.as_str()),
        ("security", "score", Some("seed-cost"), "missing", "I miss <it> & more")
    );
    assert!(!peek(&err).unwrap().attrs.contains_key("check"));
    let bare = error_of("cost", "security", "init", "busy");
    assert!(bare.ends_with("/>"));
    assert!(matches!(&parse(&bare).body, Body::Error(e) if e.text.is_empty()));
    let chk = build_error("cost", RUN, "security", "score", "check", None, Some(RUN), "", None).unwrap();
    assert!(matches!(&parse(&chk).body, Body::Error(e) if e.reference.as_deref() == Some(RUN) && e.candidate.is_none()));
    let cancel = |doc: String| match parse(&doc).body {
        Body::Cancel(c) => (c.reason, c.superseded_by, c.agent),
        other => panic!("not a cancel: {other:?}"),
    };
    let s = |x: &str| Some(x.to_string());
    assert_eq!(cancel(build_cancel("alice", RUN, "superseded", Some(OTHER), None, None).unwrap()), ("superseded".into(), s(OTHER), None));
    assert_eq!(
        cancel(build_cancel("cost", RUN, "missing-agent", None, Some("security"), None).unwrap()),
        ("missing-agent".into(), None, s("security"))
    );
    assert_eq!(cancel(build_cancel("alice", RUN, "version", None, None, None).unwrap()), ("version".into(), None, None));
    assert!(!peek(&build_cancel("alice", RUN, "version", None, None, None).unwrap()).unwrap().attrs.contains_key("check"));
}

#[test]
fn every_send_gets_a_new_uuid_and_a_given_one_is_kept() {
    assert_ne!(attr(&score(), "uuid"), attr(&score(), "uuid"));
    assert_eq!(attr(&build_score("cost", RUN, &entries(), Some(RUN)).unwrap(), "uuid"), RUN);
}

#[test]
fn the_text_of_a_message_survives_escaping_and_quotes() {
    let cand = "a < b & c > d, \"quoted\", 'single'";
    let m = InitReply { candidate: cand, rationale: "x &amp; <y>", ..InitReply::default() }.build().unwrap();
    let p = proposal_of(parse(&m));
    assert_eq!((p.candidate.as_str(), p.rationale.as_str()), (cand, "x &amp; <y>"));
    let doc = InitReply { candidate: cand, ..InitReply::default() }.build().unwrap();
    assert!(doc.contains("\"quoted\"") && doc.contains("&lt;"));
    let odd = build_score("security", RUN, &[ScoreIn::new("seed-cost", 5, "why \"not\" <this> & that")], None).unwrap();
    assert_eq!(score_body(parse(&odd)).scores[0].reasoning, "why \"not\" <this> & that");
    assert!(check_ok(&odd));
}

#[test]
fn only_space_tab_and_line_breaks_are_trimmed_from_the_text_of_a_message() {
    let cand = "\u{a0}\u{85}local cache\u{2003}";
    let m = InitReply { candidate: " \t\u{a0}\u{85}local cache\u{2003}\n ", ..InitReply::default() }.build().unwrap();
    assert_eq!(proposal_of(parse(&m)).candidate, cand);
    let err = build_error("alice", RUN, "cost", "init", "invalid", None, None, "\u{a0}too   many\n\tspaces\u{a0}", None).unwrap();
    match parse(&err).body {
        Body::Error(e) => assert_eq!(e.text, "\u{a0}too many spaces\u{a0}"),
        other => panic!("not an error: {other:?}"),
    }
}

#[test]
fn a_message_that_does_not_start_with_an_angle_bracket_is_refused_and_never_panics() {
    let wrapped = build_resend("cost", RUN, &score(), None, None).unwrap();
    let bom_resend =
        format!("<concord-resend version=\"0.3.0\" from=\"bob\" run_id=\"{RUN}\" uuid=\"{RUN}\"><concord-result/></concord-resend>");
    for doc in [score(), init_reply(), wrapped, bom_resend] {
        for prefix in ["\u{feff}", "\u{a0}", "\u{2028}", "\u{85}", "x"] {
            refuses_parse(&format!("{prefix}{doc}"), "does not start with <");
        }
        parse(&format!(" \t\r\n{doc}\n\n")); // the four blanks are trimmed, as before
    }
}

// --- the author's responsibility --------------------------------------------

#[test]
fn init_is_refused_when_it_breaks_a_rule_of_the_configuration() {
    refuses(Init { participants: vec!["a"], ..Init::default() }.build(), "at least 2");
    refuses(Init { participants: vec!["a", "a"], ..Init::default() }.build(), "duplicate");
    refuses(with_params(InitParams::default().satisfaction_threshold(0)).build(), "satisfaction_threshold");
    refuses(with_params(InitParams::default().budget(0)).build(), "budget");
    refuses(with_params(InitParams::default().retries(-1)).build(), "retries");
    refuses(with_params(InitParams::default().timeout(-5)).build(), "timeout");
    // `InitParams` has no field for an unknown parameter, so one cannot be written.
    refuses(Init { context: " ", ..Init::default() }.build(), "context is required");
    refuses(Init { x_ideal: "", ..Init::default() }.build(), "x_ideal is required");
    refuses(Init { spec: json!({"description": "d", "color": 1}), ..Init::default() }.build(), "keywords that are not enforced");
    refuses(Init { spec: json!({}), ..Init::default() }.build(), "needs a");
    refuses(Init { run_id: Some("nope"), ..Init::default() }.build(), "run_id must be a UUID");
}

#[test]
fn init_reply_is_refused_when_invalid() {
    let r = |f: fn() -> InitReply| f().build();
    refuses(r(|| InitReply { raw_reject: 50.into(), raw_ideal: 50.into(), ..InitReply::default() }), "strictly greater");
    refuses(r(|| InitReply { raw_reject: (-1).into(), ..InitReply::default() }), "raw_reject must be a number from 0 to 100");
    refuses(r(|| InitReply { raw_ideal: 101.into(), ..InitReply::default() }), "raw_ideal must be a number from 0 to 100");
    refuses(r(|| InitReply { raw: f64::NAN.into(), ..InitReply::default() }), "raw must be a number from 0 to 100");
    refuses(r(|| InitReply { candidate: "  ", ..InitReply::default() }), "the candidate is required");
    refuses(r(|| InitReply { rationale: "", ..InitReply::default() }), "the rationale is required");
    refuses(r(|| InitReply { reasoning: "", ..InitReply::default() }), "the reasoning is required");
    refuses(r(|| InitReply { spec: Some(enum_spec()), candidate: "C", ..InitReply::default() }), "must be exactly one of");
    let ok = InitReply { spec: Some(enum_spec()), candidate: "A", ..InitReply::default() }.build().unwrap();
    assert_eq!(proposal_of(parse(&ok)).candidate, "A");
}

#[test]
fn repair_is_refused_when_invalid() {
    refuses(Repair { n: 0, ..Repair::default() }.build(), "at least 1");
    refuses(Repair { raw: 500.into(), ..Repair::default() }.build(), "must be a number from 0");
    refuses(Repair { spec: Some(enum_spec()), candidate: "zzz", ..Repair::default() }.build(), "must be exactly one of");
}

#[test]
fn score_is_refused_when_empty_duplicated_or_for_its_own_candidate() {
    let one = |id: &str, raw: i32, why: &str| vec![ScoreIn::new(id, raw, why)];
    refuses(score_of(vec![]), "at least one");
    refuses(score_of(one("seed-cost", 5, "r")), "which it proposed");
    refuses(score_of(one("r2-cost", 5, "r")), "which it proposed");
    refuses(score_of(vec![ScoreIn::new("seed-b", 5, "r"), ScoreIn::new("seed-b", 6, "r")]), "rated twice");
    refuses(score_of(one("nope", 5, "r")), "not a candidate id");
    refuses(score_of(one("seed-b", 101, "r")), "0 to 100");
    refuses(score_of(one("seed-b", 5, " ")), "reasoning for seed-b is required");
}

#[test]
fn result_error_resend_cancel_are_refused_when_inconsistent() {
    refuses(build_result("a", RUN, Outcome::Aborted, "seed-a", None), "outcome must be");
    refuses(build_result("a", RUN, Outcome::Success, "", None), "winning candidate");
    refuses(build_result("a", RUN, Outcome::BestEffort, "junk", None), "winning candidate");
    let d = Err_::default;
    refuses(Err_ { reason: "lost", ..d() }.build(), "reason must be");
    refuses(Err_ { what: "thing", ..d() }.build(), "what must be");
    refuses(Err_ { candidate: None, ..d() }.build(), "needs candidate");
    refuses(Err_ { what: "result", ..d() }.build(), "candidate goes only with");
    refuses(Err_ { reason: "check", candidate: None, ..d() }.build(), "needs ref");
    refuses(Err_ { reference: Some(RUN), ..d() }.build(), "ref goes only");
    refuses(Err_ { what: "init", reason: "missing", candidate: None, ..d() }.build(), "goes only with reason");
    refuses(Err_ { reason: "busy", ..d() }.build(), "what must be \"init\"");
    refuses(Err_ { what: "init", reason: "check", reference: Some(RUN), candidate: None, ..d() }.build(), "goes only with reason");
    refuses(Err_ { reason: "invalid", ..d() }.build(), "what must be \"init\"");
    refuses(Err_ { what: "result", reason: "version", candidate: None, ..d() }.build(), "what must be \"init\"");
    let ok = Err_ { what: "init", reason: "invalid", candidate: None, text: "why", ..d() }.build().unwrap();
    assert!(matches!(&parse(&ok).body, Body::Error(e) if e.text == "why"));
    refuses(Err_ { reason: "check", reference: Some("nope"), candidate: None, ..d() }.build(), "ref must be a UUID");
    refuses(Err_ { to: "b c", ..d() }.build(), "to must be an agent id");
    let cancel = |reason, by, agent| build_cancel("a", RUN, reason, by, agent, None);
    refuses(cancel("because", None, None), "reason must be");
    refuses(cancel("invalid", None, Some("b")), "reason must be");
    refuses(cancel("superseded", None, None), "superseded_by");
    refuses(cancel("version", Some(RUN), None), "superseded_by");
    refuses(cancel("missing-agent", Some(RUN), Some("b")), "superseded_by");
    refuses(cancel("missing-agent", None, None), "agent goes with");
    refuses(cancel("version", None, Some("b")), "agent goes with");
    refuses(cancel("superseded", Some(OTHER), Some("b")), "agent goes with");
    refuses(cancel("superseded", Some("OTHER-RUNID"), None), "superseded_by must be a UUID");
    refuses(cancel("missing-agent", None, Some("b c")), "agent must be an agent id");
}

#[test]
fn a_cancel_with_an_empty_agent_or_superseded_by_is_refused_whatever_the_reason() {
    let version = build_cancel("alice", RUN, "version", None, None, None).unwrap();
    let superseded = build_cancel("alice", RUN, "superseded", Some(OTHER), None, None).unwrap();
    let missing = build_cancel("cost", RUN, "missing-agent", None, Some("security"), None).unwrap();
    let with = |doc: &str, extra: &str| doc.replace("/>", &format!(" {extra}/>"));
    let docs = [
        (with(&version, "agent=\"\""), "agent goes with"),
        (with(&version, "superseded_by=\"\""), "superseded_by goes with"),
        (with(&superseded, "agent=\"\""), "agent goes with"),
        (superseded.replace(&format!("superseded_by=\"{OTHER}\""), "superseded_by=\"\""), "superseded_by must be a UUID"),
        (missing.replace("agent=\"security\"", "agent=\"\""), "agent must be an agent id"),
        (with(&missing, "superseded_by=\"\""), "superseded_by goes with"),
    ];
    for (doc, why) in docs {
        refuses_parse(&doc, why);
    }
    for doc in [version, superseded, missing] {
        parse(&doc); // and the ones without the empty attribute are read as before
    }
}

#[test]
fn a_value_that_is_wrong_is_quoted_in_the_refusal_and_a_missing_one_is_said_to_be_missing() {
    let d = Err_::default;
    refuses(Err_ { reason: "lost", ..d() }.build(), "not 'lost'");
    refuses(Err_ { reason: "it's", ..d() }.build(), "not \"it's\"");
    refuses(Err_ { reason: "a\nb\\c", ..d() }.build(), r"not 'a\\nb\\\\c'");
    let busy = build_error("alice", RUN, "cost", "init", "busy", None, None, "", None).unwrap();
    refuses_parse(&busy.replace(" reason=\"busy\"", ""), r"reason must be one of .*, not \(missing\)");
    refuses_parse(&busy.replace(" what=\"init\"", ""), r"what must be one of .*, not \(missing\)");
    let no_sender = init_reply().replace(" from=\"cost\"", "");
    refuses(build_resend("security", RUN, &no_sender, None, None), r"not one from \(missing\)");
}

#[test]
fn peek_and_the_parser_read_the_same_attributes_whatever_the_quotes_and_the_references() {
    let doc = score();
    let who = peek(&doc).unwrap().attrs["from"].clone();
    let forged = |extra: &str| {
        with_check(&doc.replace(&format!(" check=\"{}\"", check_of(&doc).unwrap()), "").replace(" from=", &format!(" {extra} from=")))
            .unwrap()
    };
    for extra in ["x=' from=\"security\"'", "x=\">\"", "x='/>'", "x = 'a>b'", "x=\"a&gt;b\"", "x='from=\"security\"'"] {
        let d = forged(extra);
        assert_eq!(peek(&d).unwrap().attrs["from"], who, "{extra}");
        assert_eq!(parse(&d).from, who, "{extra}");
        assert!(check_ok(&d), "{extra}");
        assert_eq!(locate(&format!("see {d} ok")).unwrap(), d, "{extra}");
    }
    let by_reference = doc.replace(" from=\"cost\"", " from=\"&#99;ost\"");
    assert_eq!(peek(&by_reference).unwrap().attrs["from"], "cost");
    assert_eq!(peek(&doc.replace(" from=\"cost\"", " from=\"cost&#10;\"")).unwrap().attrs["from"], "cost\n");
    for bad in [" from=\"cost\" from=\"security\"", " from=\"cost\" x=1", " from=\"cost\" x=\"a<b\"", " from=\"cost\" x=\"&nope;\""] {
        assert!(peek(&doc.replace(" from=\"cost\"", bad)).is_none(), "{bad}");
    }
}

#[test]
fn a_participant_can_send_again_only_its_own_data_messages() {
    let original = init_reply();
    refuses(build_resend("security", RUN, &original, None, None), "only send again its own");
    refuses(
        build_resend("cost", RUN, &build_cancel("cost", RUN, "version", None, None, None).unwrap(), None, None),
        "a message of the run",
    );
    refuses(build_resend("cost", RUN, "just text", None, None), "a message of the run");
}

// --- resend ------------------------------------------------------------------

#[test]
fn resend_carries_the_stored_original_untouched() {
    let original = build_score("security", RUN, &[ScoreIn::new("seed-cost", 44, "no")], None).unwrap();
    let doc = build_resend("security", RUN, &original, Some(RUN), None).unwrap();
    let m = parse(&doc);
    let Body::Resend(r) = &m.body else { panic!("not a resend") };
    assert_eq!((r.reply_to.as_deref(), &r.inner), (Some(RUN), &original));
    assert!(check_ok(&r.inner));
    let p = peek(&doc).unwrap();
    assert_eq!(p.inner.as_ref().unwrap().attrs["from"], "security");
    let (inner_uuid, outer_uuid) = (&p.inner.as_ref().unwrap().attrs["uuid"], &p.attrs["uuid"]);
    assert_eq!(*inner_uuid, attr(&original, "uuid"));
    assert_ne!(inner_uuid, outer_uuid);
    let none = parse(&build_resend("security", RUN, &original, None, None).unwrap());
    assert!(matches!(&none.body, Body::Resend(r) if r.reply_to.is_none()));
}

#[test]
fn the_initiator_sends_init_again_inside_a_resend() {
    let original = init();
    let m = parse(&build_resend("alice", RUN, &original, None, None).unwrap());
    let Body::Resend(r) = &m.body else { panic!("not a resend") };
    assert_eq!(r.inner, original);
    assert_eq!(parse(&r.inner).kind().as_str(), "init");
}

#[test]
fn a_resend_must_hold_exactly_one_message_of_the_run() {
    let uid = "22222222-2222-4222-8222-222222222222";
    let head = format!("<concord-resend version=\"0.3.0\" from=\"a\" run_id=\"{RUN}\" uuid=\"{uid}\">");
    refuses_parse(&format!("{head}</concord-resend>"), "exactly one");
    refuses_parse(&format!("{head}<x/></concord-resend>"), "exactly one");
    refuses_parse(&format!("{head}{}{}</concord-resend>", score(), score()), "exactly one");
}

// --- the integrity check -----------------------------------------------------

#[test]
fn a_reindented_or_rewrapped_message_still_verifies_and_a_changed_word_does_not() {
    let doc = score();
    assert!(check_ok(&doc.replace("\n  ", "\n\n        ").replace("</raw>", "</raw>   ")));
    assert!(!check_ok(&doc.replace("risky", "risk")));
    assert!(!check_ok(&doc.replace("41.0", "14.0")));
    assert!(!check_ok(&doc.replace("seed-security", "seed-secure")));
    assert!(!check_ok(&doc.replace(" check=\"", " check=\"0")));
    assert!(!check_ok(&error_of("a", "b", "init", "busy")));
}

#[test]
fn a_damaged_message_can_still_be_peeked_to_know_whom_to_ask() {
    let doc = score().replace("risky", "risk");
    assert_eq!((attr(&doc, "from").as_str(), attr(&doc, "run_id").as_str(), attr(&doc, "version").as_str()), ("cost", RUN, "0.3.0"));
    assert!(is_uuid(&attr(&doc, "uuid")));
}

// --- locating and peeking ----------------------------------------------------

#[test]
fn locate_takes_the_document_unchanged_out_of_prose_or_a_fence() {
    let doc = score();
    assert_eq!(locate(&doc).unwrap(), doc);
    assert_eq!(locate(&format!("\n\n  {doc}\n")).unwrap(), doc);
    assert_eq!(locate(&format!("Here you go:\n```xml\n{doc}\n```\nbye")).unwrap(), doc);
    assert_eq!(locate(&format!("I will send {doc} now, ok?")).unwrap(), doc);
    let err = error_of("a", "b", "init", "busy");
    assert_eq!(locate(&format!("x {err} y")).unwrap(), err);
    let res = build_resend("cost", RUN, &init_reply(), None, None).unwrap();
    assert_eq!(locate(&format!("> {res} <")).unwrap(), res);
}

#[test]
fn locate_refuses_what_is_not_a_complete_message() {
    for text in ["just words", "<other>x</other>", "<concord-score from='a'>never closed", "<concord-score from='a'"] {
        refuses(locate(text), "CONCORD|closed|cut off");
    }
}

#[test]
fn peek_reads_the_root_of_anything_and_nothing_of_the_rest() {
    assert!(peek("<other/>").is_none() && peek("words").is_none());
    let err =
        "<concord-error version=\"9.9.9\" run_id=\"R\" from=\"x\" to=\"y\" what=\"init\" reason=\"version\" uuid=\"u\">I speak 9.9.9, \
               got 0.3.0 &amp; more</concord-error>";
    let p = peek(err).unwrap();
    assert_eq!(
        (p.tag.as_str(), p.attrs["reason"].as_str(), p.attrs["version"].as_str(), p.text.as_deref()),
        ("concord-error", "version", "9.9.9", Some("I speak 9.9.9, got 0.3.0 & more"))
    );
}

#[test]
fn peek_reads_one_level_into_a_resend_and_none_into_a_resend_of_a_resend() {
    let once = build_resend("cost", RUN, &score(), None, None).unwrap();
    let p = peek(&once).unwrap();
    let inner = p.inner.as_ref().unwrap();
    assert_eq!(inner.tag, "concord-score");
    assert!(inner.inner.is_none());
    let wrap = format!("<concord-resend version=\"0.3.0\" from=\"cost\" run_id=\"{RUN}\" uuid=\"{OTHER}\">");
    let twice = format!("{wrap}{once}</concord-resend>");
    let p = peek(&twice).unwrap();
    assert_eq!(p.inner.as_ref().unwrap().tag, "concord-resend");
    assert!(p.inner.as_ref().unwrap().inner.is_none());
}

#[test]
fn a_resend_nested_to_any_depth_is_peeked_in_a_fixed_number_of_steps() {
    let wrap = format!("<concord-resend version=\"0.3.0\" from=\"cost\" run_id=\"{RUN}\" uuid=\"{OTHER}\">");
    for depth in [2, 2_000, 200_000] {
        let text = format!("{}{}{}", wrap.repeat(depth), score(), "</concord-resend>".repeat(depth));
        let p = peek(&text).unwrap();
        assert_eq!(p.tag, "concord-resend");
        assert_eq!(p.inner.as_ref().unwrap().tag, "concord-resend");
    }
}

/// The score message with `n` elements nested one in the other inside its root, so that it nests `n + 1` levels. `open` is the start tag.
fn score_nested(n: usize, open: &str) -> String {
    score().replace("</concord-score>", &format!("{}{}</concord-score>", open.repeat(n), "</a>".repeat(n)))
}

#[test]
fn a_message_that_nests_deeper_than_the_limit_is_refused_before_the_parser_sees_it() {
    parse_message(&score_nested(15, "<a>"), None).unwrap(); // 16 levels: the limit
    for n in [16, 20_000, 1_000_000] {
        refuses_parse(&score_nested(n, "<a>"), "deeper than 16 levels");
    }
}

#[test]
fn a_gt_or_a_slash_inside_a_quoted_value_does_not_end_a_tag_for_the_depth() {
    parse_message(&score_nested(15, "<a x=\"/>\">"), None).unwrap();
    parse_message(&score_nested(15, "<a x='>'>"), None).unwrap();
    refuses_parse(&score_nested(16, "<a x=\"/>\">"), "deeper than 16 levels");
    refuses_parse(&score_nested(16, "<a x='>'>"), "deeper than 16 levels");
}

#[test]
fn an_element_that_closes_itself_and_the_ends_of_elements_do_not_count_as_depth() {
    let flat = score().replace("</concord-score>", &"<a/><b></b>".repeat(100_000).to_string());
    parse_message(&format!("{flat}</concord-score>"), None).unwrap();
}

#[test]
fn a_parameter_element_that_is_empty_makes_the_init_invalid_and_one_that_is_left_out_takes_its_default() {
    let doc = init();
    for tag in ["satisfaction_threshold", "eps", "budget", "timeout", "retries"] {
        let re = Regex::new(&format!("<{tag}>[^<]*</{tag}>")).unwrap();
        assert!(re.is_match(&doc), "{tag} is in the init");
        for empty in [format!("<{tag}></{tag}>"), format!("<{tag}/>"), format!("<{tag}> \n</{tag}>")] {
            refuses_parse(&re.replace(&doc, empty.as_str()), tag);
        }
        let left_out = re.replace(&doc, "").into_owned();
        parse_message(&left_out, None).unwrap_or_else(|e| panic!("{tag} left out: {e}"));
    }
}

#[test]
fn an_integer_parameter_above_999999999_is_refused_and_leading_zeros_do_not_count() {
    let doc = init();
    for tag in ["budget", "timeout", "retries"] {
        let re = Regex::new(&format!("<{tag}>[0-9]+</{tag}>")).unwrap();
        assert!(re.is_match(&doc), "{tag} is in the init");
        let at = |v: &str| re.replace(&doc, format!("<{tag}>{v}</{tag}>")).into_owned();
        assert!(parse_message(&at("999999999"), None).is_ok(), "{tag}");
        assert!(parse_message(&at("0000000000000000000000008"), None).is_ok(), "{tag}");
        for bad in ["1000000000", "99999999999999999999", &"9".repeat(5000)] {
            refuses_parse(&at(bad), &format!("<{tag}> is above 999999999"));
        }
    }
}

// --- the receiver's side -----------------------------------------------------

#[test]
fn a_receiver_refuses_what_is_not_well_formed_or_holds_forbidden_markup() {
    let ok = score();
    refuses_parse(&ok.replace("risky", "a < b"), "not well-formed");
    refuses_parse(&ok.replace("risky", "a & b"), "not well-formed");
    refuses_parse(&ok.replace("<raw>41.0", "<!-- hi --><raw>41.0"), "no comments");
    refuses_parse(&ok.replace("risky", "<![CDATA[risky]]>"), "no comments");
    refuses_parse(&format!("<?xml version='1.0'?>{ok}"), "no comments");
    refuses_parse("<concord-nope/>", "unknown message");
}

#[test]
fn a_receiver_refuses_missing_attributes_and_bad_uuids() {
    let ok = score();
    for name in ["version", "from", "run_id", "uuid"] {
        let attr = Regex::new(&format!(" {name}=\"[^\"]*\"")).unwrap();
        refuses_parse(&attr.replace(&ok, ""), &format!("needs the attribute {name}"));
    }
    refuses_parse(&ok.replace(RUN, "not-a-uuid"), "run_id must be a UUID");
}

#[test]
fn a_receiver_refuses_a_proposal_with_the_wrong_id() {
    let reply = init_reply();
    refuses_parse(&reply.replace("id=\"seed-cost\"", "id=\"seed-security\""), "is not seed-cost");
    refuses_parse(&reply.replace("id=\"seed-cost\"", "id=\"r1-cost\""), "is not seed-cost");
    refuses_parse(&repair().replace("id=\"r1-cost\"", "id=\"seed-cost\""), "is not r<N>-cost");
    refuses_parse(&repair().replace("id=\"r1-cost\"", "id=\"r1-security\""), "is not r<N>-cost");
}

#[test]
fn a_receiver_refuses_invalid_scores_and_anchors_it_reads() {
    refuses_parse(&score().replace("41.0", "141.0"), "0 to 100");
    refuses_parse(&score().replace("41.0", "lots"), "is not a number");
    refuses_parse(&score().replace("seed-security", "nope"), "not a candidate id");
    refuses_parse(&score().replace("seed-security", "seed-security&#10;"), "not a candidate id");
    refuses_parse(&score().replace("r1-security", "r99999999999-alice"), "not a candidate id");
    refuses_parse(&init_reply().replace("id=\"seed-cost\"", "id=\"seed-cost&#10;\""), "the proposal id");
    refuses_parse(&init_reply().replace("<raw_ideal>95.0", "<raw_ideal>5.0"), "must exceed");
    refuses_parse(&init_reply().replace("<raw_reject>8.0", "<raw_reject>-8.0"), "is not a number");
    refuses_parse(&init_reply().replace("<reasoning>good</reasoning>", "<reasoning> </reasoning>"), "<reasoning> in <score> is empty");
    refuses_parse(&init_reply().replace("anchors", "ancho"), "<anchors> is missing");
    refuses_parse(&score().replace("score", "x").replace("concord-x", "concord-score"), "at least one");
}

#[test]
fn a_score_entry_for_the_senders_own_candidate_or_a_repeated_one_is_ignored_and_the_rest_kept() {
    let b = score_body(parse(&score().replace("seed-security", "seed-cost")));
    assert_eq!(b.scores.iter().map(|e| e.id.as_str()).collect::<Vec<_>>(), ["r1-security"]);
    assert_eq!(b.ignored, ["seed-cost"]);
    let b = score_body(parse(&score().replace("r1-security", "seed-security").replace("60.5", "99")));
    assert_eq!(b.scores, [entry("seed-security", 41 * M, "risky")]);
    assert_eq!(b.ignored, ["seed-security"]);
    let only = score_body(parse(&score_of(vec![ScoreIn::new("seed-b", 5, "r")]).unwrap().replace("seed-b", "r2-cost")));
    assert!(only.scores.is_empty());
    assert_eq!(only.ignored, ["r2-cost"]);
}

#[test]
fn a_number_in_another_form_or_out_of_range_makes_the_message_invalid() {
    let texts = [
        ".5",
        "82.",
        "82.1234567",
        "+5",
        "-0",
        "1e2",
        "NaN",
        "inf",
        "1_0",
        "8 2",
        "1,5",
        "\u{0663}\u{0662}",
        "0x10",
        "1000",
        "100.000001",
        "101",
    ];
    for text in texts {
        for (doc, prefix, number) in [(score(), "", "41.0"), (init_reply(), "<raw>", "82.0"), (init_reply(), "<raw_reject>", "8.0")] {
            let new = doc.replace(&format!("{prefix}{number}"), &format!("{prefix}{text}"));
            assert_ne!(new, doc);
            refuses(parse_message(&new, None), "number|0 to 100");
        }
    }
}

#[test]
fn a_number_in_the_form_of_the_spec_is_read_exactly() {
    let cases =
        [("0", 0), ("100", 100 * M), ("100.0", 100 * M), ("007", 7 * M), ("82.123456", 82_123_456), ("0.000001", 1), ("00.5", 500_000)];
    for (text, millionths) in cases {
        assert_eq!(score_body(parse(&score().replace("41.0", text))).scores[0].raw, millionths, "{text}");
    }
}

#[test]
fn a_receiver_refuses_an_agent_id_or_uuid_in_the_wrong_form() {
    let long = format!("from=\"{}\"", "x".repeat(65));
    let cases = [
        ("from=\"cost\"", "from=\"co st\""),
        ("from=\"cost\"", long.as_str()),
        ("from=\"cost\"", "from=\"c\u{e9}\""),
        ("uuid=\"", "uuid=\"X"),
    ];
    for (old, new) in cases {
        refuses_parse(&score().replacen(old, new, 1), "agent id|UUID");
    }
}

#[test]
fn a_receiver_refuses_an_init_that_breaks_the_configuration() {
    let ok = init();
    refuses_parse(&ok.replace("<agent>security</agent>", ""), "at least 2");
    refuses_parse(&ok.replace("<agent>security</agent>", "<agent>cost</agent>"), "duplicate");
    refuses_parse(&ok.replace("<budget>8", "<budget>8.5"), "budget is not an integer|<budget> is not an integer");
    refuses_parse(&ok.replace("<eps>0.05", "<eps>0"), "eps");
    refuses_parse(&ok.replace("<eps>0.05", "<eps>.05"), "is not a number");
    refuses_parse(&ok.replace("<satisfaction_threshold>0.6", "<satisfaction_threshold>6e-1"), "is not a number");
    refuses_parse(&ok.replace("<satisfaction_threshold>0.6", "<satisfaction_threshold>1.000001"), "above 0 and at most 1");
    refuses_parse(&ok.replace("<retries>3", "<retries>\u{0663}"), "is not an integer");
    refuses_parse(&ok.replace("<agent>security</agent>", "<agent>sec urity</agent>"), "agent id");
    refuses_parse(&ok.replace("<timeout>300", "<timeout>-1"), "timeout");
    refuses_parse(&ok.replace("&quot;", "").replace("\"type\"", "type").replace('{', "{{"), "not valid JSON");
    refuses_parse(&ok.replace("&lt;", "<").replace("\"type\"", "\"color\": 1, \"type\""), "not enforced");
    refuses_parse(&ok.replace("pick one", " "), "<context> in <concord-init> is empty");
}

#[test]
fn the_schema_of_the_run_is_enforced_when_the_caller_has_it() {
    let doc = InitReply { candidate: "C", ..InitReply::default() }.build().unwrap();
    assert_eq!(proposal_of(parse(&doc)).candidate, "C");
    refuses(parse_message(&doc, Some(&enum_spec())), "must be exactly one of");
    refuses(parse_message(&Repair { candidate: "C", ..Repair::default() }.build().unwrap(), Some(&enum_spec())), "must be exactly one of");
}

#[test]
fn error_and_cancel_are_checked_on_the_way_in_too() {
    let err = build_error("a", RUN, "b", "score", "missing", Some("seed-b"), None, "", None).unwrap();
    refuses_parse(&err.replace(" candidate=\"seed-b\"", ""), "needs candidate");
    refuses_parse(&err.replace(" to=\"b\"", ""), "needs the attribute to");
    let cancel = build_cancel("a", RUN, "version", None, None, None).unwrap();
    refuses_parse(&cancel.replace("\"version\"", "\"why\""), "reason must be");
    refuses_parse(&cancel.replace("\"version\"", "\"invalid\" agent=\"b\""), "reason must be");
    refuses_parse(&cancel.replace("\"version\"", "\"missing-agent\""), "agent goes with");
    let missing = build_cancel("a", RUN, "missing-agent", None, Some("b"), None).unwrap();
    refuses_parse(&missing.replace("agent=\"b\"", "agent=\"b c\""), "agent must be an agent id");
    let check = build_error("a", RUN, "b", "score", "check", None, Some(RUN), "", None).unwrap();
    refuses_parse(&check.replace(&format!("{RUN}\"/>"), "x\"/>").replace(&format!("ref=\"{RUN}\""), "ref=\"x\""), "ref must be a UUID");
    refuses_parse(&err.replace(" to=\"b\"", " to=\"b c\""), "to must be an agent id");
}

// --- the examples of the spec ------------------------------------------------

#[test]
fn the_examples_of_the_spec_read() {
    let spec_reply = format!(
        r#"<concord-init-reply version="0.3.0" from="cost" run_id="{RUN}" uuid="6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51" check="e5f6a7b8">
  <anchors>
    <raw_reject>8.0</raw_reject>
    <raw_ideal>95.0</raw_ideal>
  </anchors>
  <proposal id="seed-cost">
    <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
    <rationale>evidence and reasoning</rationale>
    <score>
      <raw>82.0</raw>
      <reasoning>full reasoning text</reasoning>
    </score>
  </proposal>
</concord-init-reply>"#
    );
    assert_eq!(proposal_of(parse(&spec_reply)).raw, 82 * M);
    let result = format!(
        r#"<concord-result version="0.3.0" from="cost" run_id="{RUN}" uuid="9d2f4c60-18a7-4e35-b3c9-5a0e7d1f6b82" check="0f1e2d3c">
  <outcome>BEST_EFFORT</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
</concord-result>"#
    );
    assert!(matches!(&parse(&result).body, Body::Result(r) if r.winning_candidate_id == "seed-cost"));
    let cancel = format!(
        "<concord-cancel version=\"0.3.0\" from=\"alice\" run_id=\"{RUN}\" uuid=\"8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36\" \
         reason=\"superseded\" superseded_by=\"c3d4e5f6-0718-4a9b-8c2d-1e3f5a7b9d02\"/>"
    );
    assert!(matches!(&parse(&cancel).body, Body::Cancel(c) if c.superseded_by.as_deref() == Some(OTHER)));
    let missing = format!(
        "<concord-cancel version=\"0.3.0\" from=\"cost\" run_id=\"{RUN}\" uuid=\"e1c4a7d2-3b58-4f09-8a6e-9d2b5c7f0a13\" \
         reason=\"missing-agent\" agent=\"security\"/>"
    );
    assert!(matches!(&parse(&missing).body, Body::Cancel(c) if c.agent.as_deref() == Some("security")));
}
