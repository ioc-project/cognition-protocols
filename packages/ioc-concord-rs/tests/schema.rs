//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The candidate schema: parse, validate, check a candidate, describe it, and the subset of regular expressions.

use ioc_concord::schema::*;
use ioc_concord::ConcordError;
use serde_json::{json, Value};

fn open() -> Value {
    json!({"type": "string", "description": "a plan"})
}

/// The reason a schema text is refused.
fn refused(text: &str, label: &str) -> String {
    match parse_schema(text, label) {
        Err(ConcordError::Schema(why)) => why,
        other => panic!("expected a schema error for {text:?}, got {other:?}"),
    }
}

fn matches(pattern: &str, text: &str) -> bool {
    compile_pattern(pattern).expect("a pattern of the subset").is_match(text)
}

fn check(spec: &Value, text: &str) -> Option<String> {
    check_candidate(spec, text).expect("a schema that can be enforced")
}

#[test]
fn a_supported_schema_is_returned_as_parsed() {
    assert_eq!(parse_schema(&open().to_string(), "schema").unwrap(), open());
    let spec = json!({"enum": ["A", "b"], "pattern": "^.$", "minLength": 1, "maxLength": 1, "title": "t"});
    assert_eq!(parse_schema(&spec.to_string(), "schema").unwrap(), spec);
}

#[test]
fn only_space_tab_and_line_breaks_are_whitespace_around_a_candidate() {
    let spec = json!({"description": "d", "pattern": "^ab$"});
    for other in ["\u{a0}", "\u{85}", "\u{2003}", "\u{2028}", "\u{feff}", "\u{b}", "\u{c}", "\u{1c}"] {
        assert!(check(&spec, &format!("{other}ab")).is_some(), "{other:?}");
        assert!(check(&json!({"enum": ["ab", "cd"]}), &format!("ab{other}")).is_some(), "{other:?}");
    }
    assert!(check(&json!({"description": "d", "maxLength": 2}), "\u{a0}a\u{a0}").is_some());
    assert!(check(&spec, " \t\r\nab\n ").is_none());
}

#[test]
fn an_enum_option_with_ascii_whitespace_at_either_end_is_refused_and_one_with_other_whitespace_is_not() {
    for text in [r#"{"enum": [" A ", "B"]}"#, r#"{"enum": ["A", "B\n"]}"#, r#"{"enum": ["A", "\tB"]}"#] {
        assert!(refused(text, "schema").contains("has an option with whitespace at either end"), "{text}");
    }
    let spec = parse_schema("{\"enum\": [\"Plan  A\", \"\u{a0}B\"]}", "schema").unwrap();
    assert!(check(&spec, "Plan  A").is_none() && check(&spec, " \u{a0}B\n").is_none() && check(&spec, "B").is_some());
}

#[test]
fn an_unsupported_schema_is_refused_with_a_reason() {
    let not_json = "must be a JSON Schema object, but it is not valid JSON";
    for (text, message) in [
        ("not json", not_json),
        ("", not_json),
        ("[1]", "must be a JSON object"),
        (r#"{"description": "d", "color": 1}"#, "uses keywords that are not enforced here: color"),
        (r#"{"type": "number", "description": "d"}"#, r#""type" can only be "string""#),
        ("{}", r#"needs a "description""#),
        (r#"{"enum": ["A"]}"#, r#""enum" must be a list of at least 2 non-empty strings"#),
        (r#"{"enum": ["A", " "]}"#, r#""enum" must be a list of at least 2 non-empty strings"#),
        (r#"{"enum": ["A", "A"]}"#, r#""enum" holds the same option twice"#),
        (r#"{"description": "d", "pattern": 1}"#, r#""pattern" must be a string"#),
        (r#"{"description": "d", "pattern": "("}"#, r#""pattern" is not in the supported subset (a ( is not closed)"#),
        (r#"{"description": "d", "minLength": -1}"#, r#""minLength" must be a non-negative integer"#),
        (r#"{"description": "d", "maxLength": true}"#, r#""maxLength" must be a non-negative integer"#),
        (r#"{"description": "d", "minLength": 5, "maxLength": 2}"#, r#""minLength" is greater than "maxLength""#),
    ] {
        let why = refused(text, "schema");
        assert!(why.contains(message), "{text:?}: {why:?} does not contain {message:?}");
    }
}

#[test]
fn the_label_names_the_schema_the_way_the_caller_does() {
    assert!(refused("{}", "--schema").starts_with("--schema needs a \"description\""));
    assert!(refused("{}", "schema").starts_with("schema needs a \"description\""));
}

#[test]
fn check_candidate_enforces_each_keyword() {
    let en = json!({"enum": ["Plan A", "Plan B"]});
    assert_eq!(check(&en, "  Plan A \n"), None);
    assert!(check(&en, "plan a").is_some());
    assert_eq!(check(&json!({"enum": ["A", "a"]}), "a"), None);
    assert!(check(&en, "Plan C").unwrap().contains("exactly one of the listed options (Plan A; Plan B)"));
    let pat = json!({"description": "d", "pattern": "^ok-[0-9]+$"});
    assert_eq!(check(&pat, "ok-12"), None);
    assert!(check(&pat, "nope").unwrap().contains("must match the regular expression"));
    let length = json!({"description": "d", "minLength": 3, "maxLength": 6});
    assert_eq!(check(&length, "abcd"), None);
    assert_eq!(check(&length, "ab").as_deref(), Some("must be at least 3 characters"));
    assert_eq!(check(&length, "abcdefg").as_deref(), Some("must be at most 6 characters"));
    assert_eq!(check(&open(), "anything"), None);
}

#[test]
fn a_pattern_outside_the_subset_is_rejected_not_ignored() {
    let bad = json!({"description": "d", "pattern": "("});
    match check_candidate(&bad, "anything") {
        Err(ConcordError::Schema(why)) => assert!(why.contains("not in the supported subset"), "{why}"),
        other => panic!("expected a schema error, got {other:?}"),
    }
}

#[test]
fn render_schema_describes_what_the_agents_must_send() {
    assert_eq!(render_schema(&open()), "a plan");
    assert_eq!(render_schema(&json!({"enum": ["A", "B"]})), "exactly one of: A | B");
    assert_eq!(
        render_schema(&json!({"description": "d", "pattern": "^x$", "minLength": 1, "maxLength": 4})),
        "d; text matching the regular expression ^x$; between 1 and 4 characters"
    );
    assert_eq!(render_schema(&json!({"description": "d", "maxLength": 4})), "d; at most 4 characters");
    assert_eq!(render_schema(&json!({"title": "t"})), "t");
    assert_eq!(render_schema(&json!({})), "text");
}

#[test]
fn a_pattern_of_the_subset_means_what_the_spec_says() {
    let cases: Vec<(&str, Vec<&str>, Vec<&str>)> = vec![
        ("ab", vec!["ab", "xaby"], vec!["a", "ba"]),
        ("^ab$", vec!["ab"], vec!["xab", "abx", "ab\n\n"]),
        ("a|bc", vec!["a", "xbc"], vec!["b", "c"]),
        ("^(?:ab)+$", vec!["ab", "ababab"], vec!["", "aba"]),
        ("^(a|b)c?$", vec!["a", "bc"], vec!["c", "abc"]),
        ("^a{2}$", vec!["aa"], vec!["a", "aaa"]),
        ("^a{2,}$", vec!["aa", "aaaa"], vec!["a"]),
        ("^a{1,2}$", vec!["a", "aa"], vec!["", "aaa"]),
        ("^[a-c]+$", vec!["abc", "cab"], vec!["d", "abd"]),
        ("^[^a-c]+$", vec!["xyz", "-"], vec!["a", "xa"]),
        ("^[-a]+$", vec!["-a-"], vec!["b"]),
        ("^[a\\]]+$", vec!["a]"], vec!["b"]),
        ("^[\\\\-]+$", vec!["\\-"], vec!["a"]),
        ("^\\d+$", vec!["0123"], vec!["\u{663}", "a", ""]),
        ("^\\w+$", vec!["aZ_9"], vec!["\u{e9}", "a b", "-"]),
        ("^\\s$", vec![" ", "\t"], vec!["\u{a0}", "a"]),
        ("^a\\.b$", vec!["a.b"], vec!["axb"]),
        ("^a\\-b\\\\$", vec!["a-b\\"], vec!["a-b"]),
        ("^.$", vec!["a", "\u{e9}", "\u{1f600}", "\u{2028}"], vec!["", "ab"]),
        ("a.b", vec!["a\u{2028}b", "a b"], vec!["a\rb", "a\nb"]),
        ("^ok-[0-9]+$", vec!["ok-12"], vec!["ok-", "ok-1a"]),
        ("é", vec!["café"], vec!["cafe"]),
    ];
    for (pattern, yes, no) in cases {
        for text in yes {
            assert!(matches(pattern, text), "{pattern:?} should match {text:?}");
        }
        for text in no {
            assert!(!matches(pattern, text), "{pattern:?} should not match {text:?}");
        }
    }
}

#[test]
fn a_dollar_does_not_match_before_a_final_line_feed_and_lengths_count_code_points() {
    assert!(!matches("^a$", "a\nb\n"));
    assert!(matches("a$", "b\na") && !matches("a$", "a\nb"));
    assert!(matches("^b", "b") && !matches("^b", "a\nb"));
    let emoji = json!({"description": "d", "minLength": 2, "maxLength": 2});
    assert_eq!(check(&emoji, "\u{1f600}\u{1f601}"), None);
    assert!(check(&emoji, "\u{1f600}").is_some());
}

#[test]
fn a_pattern_outside_the_subset_is_refused() {
    let patterns = [
        "(?=a)b", "(?!a)b", "(?<=a)b", "(?<!a)b", "(?<n>a)", "(?P<n>a)", "(?i)a", "(?s:a)", "(a)\\1", "\\1", "\\k<n>", "\\bab", "\\p{L}",
        "\\u0041", "\\u{41}", "\\x41", "\\n", "\\t", "\\D", "\\W", "\\S", "\\A", "\\Z", "\\B", "\\a", "\\", "a\\", "[\\d]", "[\\w-]", "[]",
        "[^]", "[a", "[z-a]", "a)", "(a", "*a", "+a", "?a", "{2}a", "a**", "a+?", "a*?", "a{2}{3}", "a{", "a{x}", "a{,3}", "a{3,2}", "^*",
        "$+", "(*)", "|*", "a]", "a}", "]", "}", "{",
    ];
    for pattern in patterns {
        let spec = json!({"description": "d", "pattern": pattern});
        let why = refused(&spec.to_string(), "schema");
        assert!(why.contains("pattern\" is not in the supported subset"), "{pattern:?}: {why}");
        assert!(matches!(check_candidate(&spec, "a"), Err(ConcordError::Schema(_))), "{pattern:?}");
    }
}

#[test]
fn empty_alternatives_and_groups_are_part_of_the_subset() {
    for pattern in ["a|", "|a", "()", "(a|)", "^$"] {
        validate_schema(&json!({"description": "d", "pattern": pattern}), "schema").unwrap_or_else(|e| panic!("{pattern:?}: {e}"));
    }
}

#[test]
fn a_candidate_is_matched_without_the_whitespace_around_it() {
    let spec = json!({"description": "d", "pattern": "^ab$"});
    assert_eq!(check(&spec, " ab\n\n"), None);
}
