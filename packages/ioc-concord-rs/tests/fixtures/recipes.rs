//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! How the fixtures of `protocols/concord/spec/fixtures` are made. `generate` builds every file in memory, with fixed ids, uuids and
//! clock, so that two runs give the same bytes.
//!
//! What the spec decides is written here by hand and asserted, never read back from the library: who sends each tricky message, that
//! each message of the refused list is refused, what each pattern matches, the canonical text of each document and the `check` computed
//! from it by a hash taken here and not by the library. What follows from the builders (the values of a message read back, the saved
//! state) is taken from the library. `generate` returns the files and the hand-written facts that did not hold.

use std::collections::BTreeMap;

use ioc_concord::agent::Agent;
use ioc_concord::protocol::Outcome;
use ioc_concord::schema::compile_pattern;
use ioc_concord::store::{MemoryStore, Store};
use ioc_concord::wire::{
    build_cancel, build_error, build_init, build_init_reply, build_repair, build_resend, build_result, build_score, parse_message, peek,
    Body, InitParams, Message, Proposal, ScoreIn,
};
use ioc_concord::xmlutil::{check_of, with_check};
use regex::Regex;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::uuids;

pub const RUN: &str = "11111111-1111-4111-8111-111111111111";
/// Another run, the one that continues when a cancel says `superseded`.
const OTHER_RUN: &str = "22222222-2222-4222-8222-222222222222";

/// What is generated, by the name of the file or folder under the fixtures directory. The README is not.
pub const GENERATED: [&str; 4] = ["wire", "state", "check.json", "patterns.json"];

/// The schema of the runs of the fixtures, for the messages that carry a candidate.
pub fn spec() -> Value {
    json!({"description": "a short plan, in lower case", "pattern": "^[a-z ]+$", "maxLength": 80})
}

/// The uuid of the message number `n`.
fn uid(n: u64) -> String {
    format!("a0000000-0000-4000-8000-{n:012x}")
}

fn proposal_json(p: &Proposal) -> Value {
    json!({"id": p.id, "round": p.round, "candidate": p.candidate, "rationale": p.rationale, "raw": p.raw, "reasoning": p.reasoning})
}

/// A message as the fixtures write what a reader must get from it: the names are those of `wire/valid.json`.
pub fn parsed_json(m: &Message) -> Value {
    let kind = m.kind().as_str();
    let mut out = json!({
        "kind": kind, "tag": format!("concord-{kind}"), "check": m.check, "version": m.version, "from": m.from,
        "run_id": m.run_id, "uuid": m.uuid,
    });
    let more = match &m.body {
        Body::Init(b) => json!({
            "participants": b.participants, "params": b.params, "context": b.context, "x_reject": b.x_reject, "x_ideal": b.x_ideal,
            "schema": b.schema,
        }),
        Body::InitReply(b) => json!({"raw_reject": b.raw_reject, "raw_ideal": b.raw_ideal, "proposal": proposal_json(&b.proposal)}),
        Body::Repair(p) => json!({"proposal": proposal_json(p)}),
        Body::Score(b) => json!({
            "scores": b.scores.iter().map(|s| json!({"id": s.id, "raw": s.raw, "reasoning": s.reasoning})).collect::<Vec<_>>(),
            "ignored": b.ignored,
        }),
        Body::Result(b) => json!({"outcome": b.outcome.as_str(), "winning_candidate_id": b.winning_candidate_id}),
        Body::Error(b) => json!({
            "to": b.to, "what": b.what, "candidate": b.candidate, "ref": b.reference, "reason": b.reason, "text": b.text,
        }),
        Body::Resend(b) => json!({"reply_to": b.reply_to, "inner": b.inner}),
        Body::Cancel(b) => json!({"reason": b.reason, "superseded_by": b.superseded_by, "agent": b.agent}),
        other => panic!("a message of a kind that the fixtures do not know: {other:?}"),
    };
    out.as_object_mut().unwrap().extend(more.as_object().unwrap().clone());
    out
}

/// `doc` with `old` replaced by `new` and its check computed again: intact, but not what an honest author sends.
fn forge(doc: &str, old: &str, new: &str) -> String {
    assert!(doc.contains(old), "{old:?} is not in {doc}");
    let bare = match check_of(doc) {
        Some(own) => doc.replace(&format!(" check=\"{own}\""), ""),
        None => doc.to_string(),
    };
    with_check(&bare.replacen(old, new, 1)).unwrap()
}

/// `doc` with `old` replaced by `new`, for a message that carries no `check` (an error, a resend or a cancel): nothing is computed.
fn edit(doc: &str, old: &str, new: &str) -> String {
    assert!(doc.contains(old), "{old:?} is not in {doc}");
    doc.replacen(old, new, 1)
}

/// `init` with its candidate schema replaced by the JSON `schema_json`, the check computed again.
fn with_schema(init: &str, schema_json: &str) -> String {
    let element = Regex::new("(?s)<candidate_schema>.*?</candidate_schema>").unwrap().find(init).unwrap().as_str().to_string();
    let escaped = schema_json.replace('&', "&amp;").replace('<', "&lt;");
    forge(init, &element, &format!("<candidate_schema>{escaped}</candidate_schema>"))
}

// --------------------------------------------------------------------------
// the messages
// --------------------------------------------------------------------------

struct Messages {
    init: String,
    reply_cost: String,
    reply_security: String,
    score: String,
    repair: String,
    result: String,
    missing: String,
    version: String,
    resend: String,
    cancel: String,
    init_defaults: String,
    init_partial: String,
    cancel_version: String,
    cancel_superseded: String,
}

fn messages() -> Messages {
    let schema = spec();
    let params = InitParams::default().satisfaction_threshold(0.7).eps(0.1).budget(3).timeout(60).retries(2);
    let init = build_init(
        "alice",
        &["cost", "security"],
        "Cut errors to < 1% & keep the cost down",
        "errors stay as they are",
        "no error and a low cost",
        &schema,
        Some(&params),
        Some(RUN),
        Some(&uid(1)),
    )
    .unwrap();
    let reply_cost = build_init_reply(
        "cost",
        RUN,
        &10.into(),
        &90.into(),
        "cache the answers",
        "cheap & fast",
        &82.5.into(),
        "good for cost",
        Some(&schema),
        Some(&uid(2)),
    )
    .unwrap();
    let reply_security = build_init_reply(
        "security",
        RUN,
        &0.into(),
        &100.into(),
        "check every input",
        "the \"safe\" way",
        &40.into(),
        "slow",
        Some(&schema),
        Some(&uid(3)),
    )
    .unwrap();
    let score = build_score(
        "cost",
        RUN,
        &[ScoreIn::new("seed-security", 41.5, "risky & slow"), ScoreIn::new("r1-security", 60, "better")],
        Some(&uid(4)),
    )
    .unwrap();
    let repair = build_repair(
        "security",
        RUN,
        1,
        "check only what is new",
        "less work < before",
        &70.25.into(),
        "fair",
        Some(&schema),
        Some(&uid(5)),
    )
    .unwrap();
    let result = build_result("cost", RUN, Outcome::Success, "r1-security", Some(&uid(6))).unwrap();
    let missing =
        build_error("cost", RUN, "security", "score", "missing", Some("seed-cost"), None, "never got it & asked", Some(&uid(7))).unwrap();
    let version =
        build_error("security", RUN, "alice", "init", "version", None, None, "I speak 0.2.0; this init is version 0.3.0.", Some(&uid(8)))
            .unwrap();
    let resend = build_resend("security", RUN, &reply_security, Some(&uid(7)), Some(&uid(9))).unwrap();
    let cancel = build_cancel("cost", RUN, "missing-agent", None, Some("security"), Some(&uid(10))).unwrap();
    // an init that says nothing about the parameters, and one that leaves a single parameter out: what is left out takes its default
    let init_defaults = build_init(
        "alice",
        &["cost", "security"],
        "Cut errors to < 1% & keep the cost down",
        "errors stay as they are",
        "no error and a low cost",
        &schema,
        None,
        Some(RUN),
        Some(&uid(11)),
    )
    .unwrap();
    let init_partial = forge(&forge(&init, "<eps>0.1</eps>", ""), &format!("uuid=\"{}\"", uid(1)), &format!("uuid=\"{}\"", uid(12)));
    let cancel_version = build_cancel("alice", RUN, "version", None, None, Some(&uid(13))).unwrap();
    let cancel_superseded = build_cancel("alice", RUN, "superseded", Some(OTHER_RUN), None, Some(&uid(14))).unwrap();
    Messages {
        init,
        reply_cost,
        reply_security,
        score,
        repair,
        result,
        missing,
        version,
        resend,
        cancel,
        init_defaults,
        init_partial,
        cancel_version,
        cancel_superseded,
    }
}

/// The messages that are valid but that a careless reader gets wrong. Each is written with the sender, the run and the uuid that it
/// must be read with, which are the facts of the spec: the `from` that is not an attribute is not the sender.
fn tricky(m: &Messages) -> Vec<(&'static str, String, &'static str, String)> {
    let score_uid = uid(4);
    vec![
        (
            "hidden-from-in-a-single-quoted-value",
            forge(&m.score, " from=\"cost\"", " from=\"cost\" x=' from=\"security\"'"),
            "cost",
            score_uid.clone(),
        ),
        (
            "hidden-check-in-a-single-quoted-value",
            forge(&m.score, " from=\"cost\"", " x='a check=\"00000000\"' from=\"cost\""),
            "cost",
            score_uid.clone(),
        ),
        ("single-quotes-and-a-gt-in-a-value", forge(&m.score, " from=\"cost\"", " from='cost' x='a>b' y=\"/>\""), "cost", score_uid),
        (
            "blanks-and-unicode-spaces-in-the-text",
            forge(&m.reply_cost, "<rationale>cheap &amp; fast</rationale>", "<rationale>\n   cheap \u{a0} &amp;\t\tfast\n</rationale>"),
            "cost",
            uid(2),
        ),
        // a character reference is the character: the sender is `alice`, and the check is that of the text as it was sent
        ("a-character-reference-in-from", forge(&m.init, " from=\"alice\"", " from=\"&#97;lice\""), "alice", uid(1)),
    ]
}

/// The messages that break a rule of the spec although their check is right: name, text, and the rule.
fn refused(m: &Messages) -> Vec<(&'static str, String, &'static str)> {
    let (init, reply, score, repair, result) = (&m.init, &m.reply_cost, &m.score, &m.repair, &m.result);
    let long_agent = "a".repeat(65);
    let number_form = "a number is [0-9]{1,3}(.[0-9]{1,6})?";
    vec![
        (
            "score-id-with-a-line-feed",
            forge(score, "candidate_id=\"seed-security\"", "candidate_id=\"seed-security&#10;\""),
            "a candidate id is exactly seed-<agent> or r<N>-<agent>",
        ),
        ("proposal-id-with-a-line-feed", forge(reply, "id=\"seed-cost\"", "id=\"seed-cost&#10;\""), "the id of a proposal"),
        ("proposal-id-of-another-author", forge(reply, "id=\"seed-cost\"", "id=\"seed-security\""), "an init reply proposes seed-<from>"),
        ("repair-round-zero", forge(repair, "id=\"r1-security\"", "id=\"r0-security\""), "a repair round starts at 1"),
        ("winner-that-is-not-an-id", forge(result, "r1-security", "nope"), "the winner is a candidate id"),
        ("result-outcome-aborted", forge(result, "SUCCESS", "ABORTED"), "a result is SUCCESS or BEST_EFFORT"),
        ("timeout-zero", forge(init, "<timeout>60</timeout>", "<timeout>0</timeout>"), "timeout is above 0"),
        ("budget-zero", forge(init, "<budget>3</budget>", "<budget>0</budget>"), "B is at least 1"),
        ("retries-negative", forge(init, "<retries>2</retries>", "<retries>-1</retries>"), "retries is at least 0"),
        ("empty-timeout", forge(init, "<timeout>60</timeout>", "<timeout></timeout>"), "an empty number is not in the form"),
        ("empty-eps", forge(init, "<eps>0.1</eps>", "<eps/>"), "an empty number is not in the form"),
        ("threshold-above-one", forge(init, "<satisfaction_threshold>0.7<", "<satisfaction_threshold>1.5<"), "tau is at most 1"),
        ("eps-zero", forge(init, "<eps>0.1</eps>", "<eps>0</eps>"), "eps is above 0"),
        ("number-without-leading-digit", forge(score, "<raw>60.0</raw>", "<raw>.5</raw>"), number_form),
        ("number-with-an-exponent", forge(score, "<raw>60.0</raw>", "<raw>1e2</raw>"), number_form),
        ("number-with-a-plus-sign", forge(score, "<raw>60.0</raw>", "<raw>+5</raw>"), number_form),
        ("number-with-seven-decimals", forge(score, "<raw>60.0</raw>", "<raw>5.1234567</raw>"), "at most 6 decimals"),
        ("score-above-100", forge(score, "<raw>60.0</raw>", "<raw>101</raw>"), "a score is 0 to 100"),
        ("anchors-not-ordered", forge(reply, "<raw_ideal>90.0</raw_ideal>", "<raw_ideal>5.0</raw_ideal>"), "raw_ideal exceeds raw_reject"),
        ("agent-id-too-long", forge(score, "from=\"cost\"", &format!("from=\"{long_agent}\"")), "an agent id is 1 to 64 of A-Za-z0-9_-"),
        ("agent-id-with-a-space", forge(score, "from=\"cost\"", "from=\"co st\""), "an agent id is 1 to 64 of A-Za-z0-9_-"),
        (
            "upper-case-uuid",
            forge(score, &format!("uuid=\"{}\"", uid(4)), &format!("uuid=\"{}\"", uid(4).to_uppercase())),
            "a uuid is lower case",
        ),
        ("run-id-not-a-uuid", forge(score, &format!("run_id=\"{RUN}\""), "run_id=\"run-1\""), "a run id is a uuid"),
        (
            "repeated-attribute",
            score.replacen(" from=\"cost\"", " from=\"cost\" from=\"security\"", 1),
            "an element holds an attribute once",
        ),
        ("a-comment", forge(score, "<score ", "<!-- no --><score "), "no comments"),
        ("a-cdata-section", forge(score, "risky &amp; slow", "<![CDATA[risky]]>"), "no CDATA"),
        ("a-processing-instruction", format!("<?xml version=\"1.0\"?>{score}"), "no processing instructions"),
        ("enum-option-with-outer-blanks", with_schema(init, r#"{"enum": [" a ", "b"]}"#), "an enum option is trimmed like a candidate"),
        ("pattern-with-a-lookahead", with_schema(init, r#"{"description": "d", "pattern": "(?=a)b"}"#), "a pattern is the subset"),
        ("pattern-with-a-backslash-b", with_schema(init, r#"{"description": "d", "pattern": "\\bab"}"#), "a pattern is the subset"),
        ("schema-keyword-not-supported", with_schema(init, r#"{"description": "d", "format": "uri"}"#), "an unsupported schema keyword"),
        ("candidate-breaks-the-schema", forge(reply, "cache the answers", "Cache The Answers"), "a candidate satisfies the schema"),
        ("cancel-missing-agent-without-agent", edit(&m.cancel, " agent=\"security\"", ""), "missing-agent needs agent"),
        (
            "cancel-with-an-unknown-reason",
            edit(&m.cancel_version, "reason=\"version\"", "reason=\"because\""),
            "a cancel reason is superseded, version or missing-agent",
        ),
        (
            "cancel-superseded-without-superseded-by",
            edit(&m.cancel_superseded, &format!(" superseded_by=\"{OTHER_RUN}\""), ""),
            "superseded needs superseded_by",
        ),
        ("cancel-version-with-an-agent", edit(&m.cancel_version, "/>", " agent=\"security\"/>"), "agent goes only with missing-agent"),
        (
            "cancel-superseded-with-a-superseded-by-that-is-not-a-uuid",
            edit(&m.cancel_superseded, OTHER_RUN, "run-2"),
            "superseded_by is a run id",
        ),
        ("cancel-version-with-an-empty-agent", edit(&m.cancel_version, "/>", " agent=\"\"/>"), "an empty agent is not an agent id"),
        (
            "cancel-version-with-an-empty-superseded-by",
            edit(&m.cancel_version, "/>", " superseded_by=\"\"/>"),
            "an empty superseded_by is not a run id",
        ),
        ("cancel-superseded-with-an-empty-agent", edit(&m.cancel_superseded, "/>", " agent=\"\"/>"), "an empty agent is not an agent id"),
        (
            "cancel-superseded-with-an-empty-superseded-by",
            edit(&m.cancel_superseded, &format!("superseded_by=\"{OTHER_RUN}\""), "superseded_by=\"\""),
            "an empty superseded_by is not a run id",
        ),
        (
            "cancel-missing-agent-with-an-empty-agent",
            edit(&m.cancel, "agent=\"security\"", "agent=\"\""),
            "an empty agent is not an agent id",
        ),
        (
            "cancel-missing-agent-with-an-empty-superseded-by",
            edit(&m.cancel, "/>", " superseded_by=\"\"/>"),
            "an empty superseded_by is not a run id",
        ),
        (
            "error-with-an-unknown-reason",
            edit(&m.missing, "reason=\"missing\"", "reason=\"lost\""),
            "an error reason is missing, check, invalid, mismatch, busy or version",
        ),
        (
            "error-with-an-unknown-what",
            edit(&m.missing, "what=\"score\"", "what=\"thing\""),
            "an error is about an init, init-reply, score, repair or result",
        ),
        (
            "error-missing-without-a-candidate",
            edit(&m.missing, " candidate=\"seed-cost\"", ""),
            "a missing score or repair names its candidate",
        ),
        (
            "error-about-an-init-with-the-reason-missing",
            edit(&edit(&m.missing, "what=\"score\"", "what=\"init\""), " candidate=\"seed-cost\"", ""),
            "an error about an init is busy, invalid or version",
        ),
        ("error-mismatch-about-a-score", edit(&m.missing, "reason=\"missing\"", "reason=\"mismatch\""), "a mismatch is about a result"),
        (
            "error-check-without-a-ref",
            edit(&edit(&m.missing, "reason=\"missing\"", "reason=\"check\""), " candidate=\"seed-cost\"", ""),
            "a check error names the uuid of the damaged message",
        ),
        (
            "error-to-an-agent-id-that-is-not-one",
            edit(&m.missing, "to=\"security\"", "to=\"se curity\""),
            "an agent id is 1 to 64 of A-Za-z0-9_-",
        ),
        ("resend-of-a-cancel", edit(&m.resend, &m.reply_security, &m.cancel), "a resend carries a data message"),
        (
            "resend-of-nothing",
            format!("<concord-resend version=\"0.3.0\" from=\"security\" run_id=\"{RUN}\" uuid=\"{}\"/>", uid(15)),
            "a resend carries one message",
        ),
        (
            "resend-with-a-reply-to-that-is-not-a-uuid",
            edit(&m.resend, &format!("reply_to=\"{}\"", uid(7)), "reply_to=\"nope\""),
            "reply_to is a uuid",
        ),
        (
            "resend-of-a-message-in-a-namespace",
            edit(&m.resend, "<concord-init-reply ", "<concord-init-reply xmlns=\"urn:x\" "),
            "the message of a resend is a message of the protocol, not an element of a namespace",
        ),
        (
            "score-id-with-a-space-in-the-agent",
            forge(score, "candidate_id=\"seed-security\"", "candidate_id=\"seed-secu rity\""),
            "an agent id is 1 to 64 of A-Za-z0-9_-",
        ),
        (
            "from-with-a-character-reference-to-a-line-feed",
            forge(score, " from=\"cost\"", " from=\"cost&#10;\""),
            "an agent id has no line feed",
        ),
    ]
}

// --------------------------------------------------------------------------
// the check and the patterns
// --------------------------------------------------------------------------

/// The first 8 hexadecimal characters of the SHA-256 of `text`, taken here and not by the library.
fn sha256_prefix(text: &str) -> String {
    Sha256::digest(text.as_bytes()).iter().take(4).map(|b| format!("{b:02x}")).collect()
}

/// The canonical text of a document that has one `check` on its root and plain text between its tags, worked out here with the rule of the
/// spec and not by the library: no `check`, and each run of blanks between `>` and `<` made one space and none at the ends.
fn canonical_of_plain(doc: &str) -> String {
    let doc = doc.trim_matches(|c| [' ', '\t', '\r', '\n'].contains(&c));
    let tag_end = doc.find('>').unwrap();
    let head = &doc[..=tag_end];
    let head = match head.find(" check=\"") {
        Some(at) => {
            let close = head[at + 8..].find('"').unwrap();
            format!("{}{}", &head[..at], &head[at + 8 + close + 1..])
        }
        None => head.to_string(),
    };
    let text = Regex::new(">([^<]*)<").unwrap().replace_all(&doc[tag_end + 1 - 1..], |c: &regex::Captures| {
        let squeezed: Vec<&str> = c[1].split([' ', '\t', '\r', '\n']).filter(|p| !p.is_empty()).collect();
        format!(">{}<", squeezed.join(" "))
    });
    format!("{}{}", &head[..head.len() - 1], text)
}

/// Documents with the canonical text and the declared check that the spec gives them: written by hand.
fn check_vectors(m: &Messages) -> Vec<(String, String, String, Option<String>)> {
    let by_hand: [(&str, &str, &str, Option<&str>); 6] = [
        (
            "a plain element",
            r#"<concord-cancel version="0.3.0" run_id="R" from="a" uuid="U" reason="superseded"/>"#,
            r#"<concord-cancel version="0.3.0" run_id="R" from="a" uuid="U" reason="superseded"/>"#,
            None,
        ),
        (
            "blank runs in text become one space",
            "<x a=\"1\">\n  <y>  one \t two\r\n three  </y>\n</x>",
            "<x a=\"1\"><y>one two three</y></x>",
            None,
        ),
        ("text of blanks only is removed", "<x><y>   </y><z>\n</z></x>", "<x><y></y><z></z></x>", None),
        ("a non-ASCII space is not whitespace", "<x><y>\u{a0}one\u{a0}</y></x>", "<x><y>\u{a0}one\u{a0}</y></x>", None),
        (
            "a check in the value of another attribute is not the check",
            r#"<x a='b check="c"' check="00000000"><y>t</y></x>"#,
            r#"<x a='b check="c"'><y>t</y></x>"#,
            Some("00000000"),
        ),
        (
            "quotes and entities stay as sent",
            r#"<x a='1' b="&quot;q&quot;"><y>a &amp; b &lt; c &gt; d "e"</y></x>"#,
            r#"<x a='1' b="&quot;q&quot;"><y>a &amp; b &lt; c &gt; d "e"</y></x>"#,
            None,
        ),
    ];
    let mut out: Vec<_> = by_hand.iter().map(|(n, d, c, k)| (n.to_string(), d.to_string(), c.to_string(), k.map(str::to_string))).collect();
    for (name, doc) in [("init", &m.init), ("score", &m.score), ("resend", &m.resend)] {
        let canonical = canonical_of_plain(doc);
        out.push((format!("the {name} of the fixtures"), doc.clone(), canonical, check_of(doc)));
    }
    out
}

type PatternCase = (String, Vec<String>, Vec<String>);

fn pattern_cases() -> (Vec<PatternCase>, Vec<&'static str>) {
    let all_a = "a".repeat(30);
    let cases = vec![
        ("ab", vec!["ab", "xaby"], vec!["a", "ba"]),
        ("^ab$", vec!["ab"], vec!["xab", "abx", "ab\n"]),
        ("a|bc", vec!["a", "xbc"], vec!["b", "c"]),
        ("^(?:ab)+$", vec!["ab", "ababab"], vec!["", "aba"]),
        ("^(a|b)c?$", vec!["a", "bc"], vec!["c", "abc"]),
        ("^a{2}$", vec!["aa"], vec!["a", "aaa"]),
        ("^a{2,}$", vec!["aa", "aaaa"], vec!["a"]),
        ("^a{1,2}$", vec!["a", "aa"], vec!["", "aaa"]),
        ("^[a-c]+$", vec!["abc", "cab"], vec!["d", "abd"]),
        ("^[^a-c]+$", vec!["xyz", "-"], vec!["a", "xa"]),
        ("^[-a]+$", vec!["-a-"], vec!["b"]),
        ("^\\d+$", vec!["0123"], vec!["\u{663}", "a", ""]),
        ("^\\w+$", vec!["aZ_9"], vec!["\u{e9}", "a b", "-"]),
        ("^\\s$", vec![" ", "\t", "\r", "\n"], vec!["\u{a0}", "a"]),
        ("^a\\.b$", vec!["a.b"], vec!["axb"]),
        ("^.$", vec!["a", "\u{e9}", "\u{1f600}", "\u{2028}", "\u{2029}", "\u{85}"], vec!["", "ab", "\r", "\n"]),
        ("a.b", vec!["a\u{2028}b", "a b"], vec!["a\rb", "a\nb"]),
        ("a$", vec!["b\na", "a"], vec!["a\n", "a\nb"]),
        ("^b", vec!["b"], vec!["a\nb"]),
        ("\u{e9}", vec!["caf\u{e9}"], vec!["cafe"]),
    ];
    let mut valid: Vec<PatternCase> = cases
        .into_iter()
        .map(|(p, yes, no)| (p.to_string(), yes.into_iter().map(String::from).collect(), no.into_iter().map(String::from).collect()))
        .collect();
    // a text that is long enough to take exponential time for a matcher that backtracks
    valid.push(("^(a+)+$".to_string(), vec![all_a.clone()], vec![format!("{all_a}!")]));
    let refused = vec![
        "(?=a)b", "(?!a)b", "(?<=a)b", "(?<n>a)", "(?i)a", "(a)\\1", "\\bab", "\\p{L}", "\\u0041", "\\x41", "\\n", "\\t", "\\D", "[\\d]",
        "[]", "[a", "[z-a]", "a)", "(a", "*a", "a**", "a+?", "a*?", "a{2}{3}", "a{", "a{3,2}", "^*", "a]", "a}",
    ];
    (valid, refused)
}

// --------------------------------------------------------------------------
// the saved state of a node
// --------------------------------------------------------------------------

fn standing(a: &Agent) -> Value {
    let s = a.status(None).expect("the agent is in a run");
    json!({"status": s.status, "state": s.state, "lacking": s.lacking})
}

fn agent(me: &str, prefix: &str, store: Option<MemoryStore>) -> Agent {
    let builder = Agent::builder(me).clock(|| 0.0).uuid_fn(uuids(prefix));
    match store {
        Some(store) => builder.store(store).build().unwrap(),
        None => builder.build().unwrap(),
    }
}

/// The saved state of `cost` after the init and both init replies, the score of `security` that arrives next, and what must follow.
fn state_files(init: &str, failures: &mut Vec<String>) -> (Value, String, Value) {
    let store = MemoryStore::new();
    let mut cost = agent("cost", "c0000000", Some(store.clone()));
    let mut security = agent("security", "b0000000", None);
    for a in [&mut cost, &mut security] {
        let _ = a.receive(init, Some(0.0)).unwrap();
    }
    let reply_cost = cost.submit_init_reply(RUN, 10, 90, "cache the answers", "because", 82.5, "I like it", Some(0.0)).unwrap().send;
    let reply_security = security.submit_init_reply(RUN, 0, 100, "check every input", "because", 40, "I like it", Some(0.0)).unwrap().send;
    let _ = security.receive(&reply_cost[0], Some(0.0)).unwrap();
    let _ = cost.receive(&reply_security[0], Some(0.0)).unwrap();
    let state = store.get().expect("the agent saved its state");
    let next = build_score("security", RUN, &[ScoreIn::new("seed-cost", 30, "slow")], Some(&uid(20))).unwrap();
    // what must follow is written here: nothing is lacking but cost's own rating of the seed of security, and cost sends nothing
    let expected = json!({
        "agent": "cost", "run_id": RUN, "next": "state/next-message.xml", "now": 0,
        "before": {"status": "active", "state": "wait", "lacking": [["security", "seed-cost"], ["cost", "seed-security"]]},
        "after": {"status": "active", "state": "wait", "lacking": [["cost", "seed-security"]]},
        "sends": [],
    });
    if standing(&cost) != expected["before"] {
        failures.push(format!("the standing of cost after the init replies is {}, by hand {}", standing(&cost), expected["before"]));
    }
    let mut again = MemoryStore::new();
    again.save(&state).unwrap();
    let mut reloaded = Agent::builder("cost").store(again).clock(|| 0.0).uuid_fn(uuids("c1000000")).build().unwrap();
    let step = reloaded.receive(&next, Some(0.0)).unwrap();
    if standing(&reloaded) != expected["after"] || !step.send.is_empty() {
        failures.push(format!("after the next message cost stands at {}, by hand {}", standing(&reloaded), expected["after"]));
    }
    (state, next, expected)
}

// --------------------------------------------------------------------------
// all of it
// --------------------------------------------------------------------------

fn xml(text: &str) -> String {
    format!("{text}\n")
}

fn json_text(v: &Value) -> String {
    format!("{}\n", serde_json::to_string_pretty(v).unwrap())
}

/// Every file of the fixtures, by its path under the fixtures directory, and the hand-written facts that did not hold.
pub fn generate() -> (BTreeMap<String, String>, Vec<String>) {
    let mut files = BTreeMap::new();
    let mut failures = Vec::new();
    let m = messages();
    let schema = spec();

    // the messages that are valid: what the library reads from each is a computed value; who sent a tricky one is a fact
    let base: [(&str, &String, &str, String); 12] = [
        ("init", &m.init, "alice", uid(1)),
        ("init-reply-cost", &m.reply_cost, "cost", uid(2)),
        ("init-reply-security", &m.reply_security, "security", uid(3)),
        ("score", &m.score, "cost", uid(4)),
        ("repair", &m.repair, "security", uid(5)),
        ("result", &m.result, "cost", uid(6)),
        ("error-missing", &m.missing, "cost", uid(7)),
        ("error-version", &m.version, "security", uid(8)),
        ("resend", &m.resend, "security", uid(9)),
        ("cancel", &m.cancel, "cost", uid(10)),
        ("init-defaults", &m.init_defaults, "alice", uid(11)),
        ("init-with-a-parameter-left-out", &m.init_partial, "alice", uid(12)),
    ];
    let tricks = tricky(&m);
    let mut cases = Vec::new();
    let all: Vec<(String, &String, &str, &String)> = base
        .iter()
        .map(|(name, doc, from, uuid)| (format!("wire/{name}.xml"), *doc, *from, uuid))
        .chain(tricks.iter().map(|(name, doc, from, uuid)| (format!("wire/tricky/{name}.xml"), doc, *from, uuid)))
        .collect();
    for (file, doc, from, uuid) in all {
        files.insert(file.clone(), xml(doc));
        let info = peek(doc);
        let read = |key: &str| info.as_ref().and_then(|i| i.attr(key)).map(str::to_string);
        for (key, want) in [("from", from), ("run_id", RUN), ("uuid", uuid.as_str())] {
            if read(key).as_deref() != Some(want) {
                failures.push(format!("{file}: {key} is read as {:?}, by hand {want:?}", read(key)));
            }
        }
        let spec_for = (!file.ends_with("init.xml")).then_some(&schema);
        match parse_message(doc, spec_for) {
            Ok(msg) => {
                // what the spec gives a parameter that is left out, written by hand (in millionths for the two that are numbers)
                let params = match file.as_str() {
                    "wire/init-defaults.xml" => {
                        Some(json!({"satisfaction_threshold": 600000, "eps": 50000, "B": 8, "timeout": 300, "retries": 3}))
                    }
                    "wire/init-with-a-parameter-left-out.xml" => {
                        Some(json!({"satisfaction_threshold": 700000, "eps": 50000, "B": 3, "timeout": 60, "retries": 2}))
                    }
                    _ => None,
                };
                if let Some(want) = params.filter(|want| parsed_json(&msg)["params"] != *want) {
                    failures.push(format!("{file}: the parameters are {}, by hand {want}", parsed_json(&msg)["params"]));
                }
                cases.push(json!({
                    "file": file, "kind": msg.kind().as_str(), "from": from, "run_id": RUN, "uuid": uuid, "check": msg.check,
                    "parsed": parsed_json(&msg),
                }));
            }
            Err(e) => failures.push(format!("{file}: refused ({e}), but it is a valid message")),
        }
    }
    files.insert("wire/valid.json".to_string(), json_text(&Value::Array(cases)));

    // the messages that must be refused: each is refused or the generation says so
    let mut why = Vec::new();
    for (name, doc, rule) in refused(&m) {
        files.insert(format!("wire/refused/{name}.xml"), xml(&doc));
        if let Ok(msg) = parse_message(&doc, Some(&schema)) {
            failures.push(format!("wire/refused/{name}.xml: read as a {} message, but {rule}", msg.kind().as_str()));
        }
        why.push(json!({"file": format!("wire/refused/{name}.xml"), "why": rule}));
    }
    files.insert("wire/refused.json".to_string(), json_text(&Value::Array(why)));

    // the check: hand-written canonical text, and the check from a hash taken here
    let mut checks = Vec::new();
    for (name, doc, canonical, declared) in check_vectors(&m) {
        let check = sha256_prefix(&canonical);
        if let Some(d) = declared.as_deref().filter(|d| name.starts_with("the ") && *d != check) {
            failures.push(format!("{name}: the document declares {d}, the hash of its canonical text is {check}"));
        }
        checks.push(json!({"name": name, "document": doc, "canonical": canonical, "check": check, "declared": declared}));
    }
    files.insert("check.json".to_string(), json_text(&Value::Array(checks)));

    // the patterns
    let (valid, refused_patterns) = pattern_cases();
    for (pattern, yes, no) in &valid {
        match compile_pattern(pattern) {
            Ok(re) => {
                for (texts, want) in [(yes, true), (no, false)] {
                    for t in texts {
                        if re.is_match(t) != want {
                            failures.push(format!("{pattern:?} on {t:?}: by hand it {}matches", if want { "" } else { "does not " }));
                        }
                    }
                }
            }
            Err(e) => failures.push(format!("{pattern:?}: refused ({e}), by hand it is in the subset")),
        }
    }
    for p in &refused_patterns {
        if compile_pattern(p).is_ok() {
            failures.push(format!("{p:?}: compiled, by hand it is outside the subset"));
        }
    }
    let patterns = json!({
        "valid": valid.iter().map(|(p, y, n)| json!({"pattern": p, "match": y, "no_match": n})).collect::<Vec<_>>(),
        "refused": refused_patterns,
    });
    files.insert("patterns.json".to_string(), json_text(&patterns));

    // the saved state
    let (state, next, expected) = state_files(&m.init, &mut failures);
    files.insert("state/cost-after-init-replies.json".to_string(), json_text(&state));
    files.insert("state/next-message.xml".to_string(), xml(&next));
    files.insert("state/expected.json".to_string(), json_text(&expected));
    (files, failures)
}
