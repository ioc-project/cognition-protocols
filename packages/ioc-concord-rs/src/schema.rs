//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The candidate schema: the JSON Schema subset that says what one option may look like, checked on every proposal.
//!
//! Only a small subset is supported, and anything else is refused, so a rule is never silently left unenforced. An open format is
//! `{"type": "string", "description": "..."}`; a closed list is `{"enum": ["A", "B"]}`.
//!
//! A `pattern` is limited to the subset the spec lists, so that every agent reads it the same way: `compile_pattern` reads it
//! character by character, refuses what is outside the subset and writes the same expression in the syntax of the `regex` crate.

use std::collections::BTreeSet;

use regex::Regex;
use serde_json::{ser::Formatter, Map, Value};

use crate::errors::{ConcordError, Result};
use crate::protocol::trim_ws;

pub const SCHEMA_KEYS: [&str; 7] = ["type", "enum", "pattern", "minLength", "maxLength", "description", "title"];

/// How large a compiled pattern may be, in bytes. The pattern comes from the init of a peer, and matching it costs more than its size
/// grows: `.{60000}` needs 62 MB and takes 11 s on a candidate of 200,000 characters, `.{1000}` needs 1 MB and takes 4 ms, and the slowest
/// pattern below the limit takes about 0.4 s. So the limit is what a peer can make every participant allocate and spend on each check.
/// A pattern above it is refused as outside the supported subset. A limit of this library, not of the spec.
const REGEX_SIZE_LIMIT: usize = 1 << 20;

/// How deep groups may nest in a pattern. A limit of this library, not of the spec. The `regex` crate counts a repeated group twice, so
/// 100 levels need 200 of its own nesting limit, which `compile_pattern` sets to `REGEX_NEST_LIMIT` so that the crate's limit is never
/// what refuses a pattern that passed this one.
const MAX_GROUP_DEPTH: usize = 100;
const REGEX_NEST_LIMIT: u32 = 256;

/// What `\.` and `.` stand for in the spec, written out so that Unicode-aware classes never apply.
const DOT: &str = "[^\\r\\n]";

/// The result of reading one piece of a pattern: a reason when it is outside the subset.
type Parsed<T> = std::result::Result<T, String>;

fn is_punctuation(c: char) -> bool {
    c.is_ascii_punctuation()
}

fn escape(c: char) -> String {
    regex::escape(&c.to_string())
}

fn class_text(c: char) -> Option<&'static str> {
    match c {
        'd' => Some("[0-9]"),
        'w' => Some("[A-Za-z0-9_]"),
        's' => Some("[ \\t\\r\\n]"),
        _ => None,
    }
}

/// (the character after the backslash at `pattern[i]` when it stands for itself, what it becomes, the next index).
fn escaped(pattern: &[char], i: usize, in_class: bool) -> Parsed<(Option<char>, String, usize)> {
    let nxt = pattern.get(i + 1).copied();
    match nxt {
        Some(c) if is_punctuation(c) => Ok((Some(c), escape(c), i + 2)),
        Some(c) => match class_text(c).filter(|_| !in_class) {
            Some(text) => Ok((None, text.to_string(), i + 2)),
            None => Err(format!("\\{c} is outside the subset")),
        },
        None => Err("a backslash at the end".to_string()),
    }
}

/// (the text of the class that starts at `pattern[i]` == `[`, the next index).
fn class(pattern: &[char], i: usize) -> Parsed<(String, usize)> {
    let mut i = i + 1;
    let negate = pattern.get(i) == Some(&'^');
    if negate {
        i += 1;
    }
    let mut out = Vec::new();
    let mut atoms = 0;

    let atom = |j: usize| -> Parsed<(char, usize)> {
        let Some(&c) = pattern.get(j) else { return Err("a class is not closed".to_string()) };
        if c == '\\' {
            return match escaped(pattern, j, true)? {
                (Some(ch), _, next) => Ok((ch, next)),
                (None, _, _) => Err("\\d, \\w and \\s are outside the subset inside a class".to_string()),
            };
        }
        Ok((c, j + 1))
    };

    while i < pattern.len() && pattern[i] != ']' {
        let (lo, next) = atom(i)?;
        i = next;
        if pattern.get(i) == Some(&'-') && i + 1 < pattern.len() && pattern[i + 1] != ']' {
            let (hi, next) = atom(i + 1)?;
            i = next;
            if lo as u32 > hi as u32 {
                return Err("a range of a class runs backwards".to_string());
            }
            out.push(format!("{}-{}", escape(lo), escape(hi)));
        } else {
            out.push(escape(lo));
        }
        atoms += 1;
    }
    if i >= pattern.len() {
        return Err("a class is not closed".to_string());
    }
    if atoms == 0 {
        return Err("a class is empty".to_string());
    }
    Ok((format!("[{}{}]", if negate { "^" } else { "" }, out.concat()), i + 1))
}

#[derive(Clone, Copy, PartialEq)]
enum Prev {
    None,
    Atom,
    Anchor,
    Quantifier,
}

/// The text of `{n}`, `{n,}` or `{n,m}` at `pattern[i]`, and the next index.
fn quantifier(pattern: &[char], i: usize) -> Parsed<(String, usize)> {
    let not_quantifier = || "a { is not a quantifier".to_string();
    let mut j = i + 1;
    let digits = |j: &mut usize| {
        let start = *j;
        while pattern.get(*j).is_some_and(|c| c.is_ascii_digit()) {
            *j += 1;
        }
        pattern[start..*j].iter().collect::<String>()
    };
    let low = digits(&mut j);
    if low.is_empty() {
        return Err(not_quantifier());
    }
    let mut high = None;
    if pattern.get(j) == Some(&',') {
        j += 1;
        high = Some(digits(&mut j));
    }
    if pattern.get(j) != Some(&'}') {
        return Err(not_quantifier());
    }
    let too_large = || "a quantifier is too large".to_string();
    let n: u64 = low.parse().map_err(|_| too_large())?;
    if let Some(h) = high.as_deref().filter(|h| !h.is_empty()) {
        if n > h.parse::<u64>().map_err(|_| too_large())? {
            return Err("a quantifier runs backwards".to_string());
        }
    }
    Ok((pattern[i..=j].iter().collect(), j + 1))
}

fn translate(pattern: &[char]) -> Parsed<String> {
    let (mut out, mut i, mut depth, mut prev) = (String::new(), 0, 0usize, Prev::None);
    while i < pattern.len() {
        let c = pattern[i];
        match c {
            '\\' => {
                let (_, text, next) = escaped(pattern, i, false)?;
                out.push_str(&text);
                i = next;
                prev = Prev::Atom;
            }
            '[' => {
                let (text, next) = class(pattern, i)?;
                out.push_str(&text);
                i = next;
                prev = Prev::Atom;
            }
            '(' => {
                if depth >= MAX_GROUP_DEPTH {
                    return Err(format!("groups nest deeper than {MAX_GROUP_DEPTH} levels"));
                }
                if pattern.get(i + 1) == Some(&'?') {
                    if pattern.get(i + 2) != Some(&':') {
                        return Err("(? is outside the subset except (?:".to_string());
                    }
                    out.push_str("(?:");
                    i += 3;
                } else {
                    out.push('(');
                    i += 1;
                }
                depth += 1;
                prev = Prev::None;
            }
            ')' => {
                if depth == 0 {
                    return Err("a ) has no (".to_string());
                }
                depth -= 1;
                out.push(')');
                i += 1;
                prev = Prev::Atom;
            }
            '|' => {
                out.push('|');
                i += 1;
                prev = Prev::None;
            }
            '^' | '$' => {
                out.push_str(if c == '^' { "\\A" } else { "\\z" });
                i += 1;
                prev = Prev::Anchor;
            }
            '.' => {
                out.push_str(DOT);
                i += 1;
                prev = Prev::Atom;
            }
            '*' | '+' | '?' | '{' => {
                if prev != Prev::Atom {
                    return Err(if prev != Prev::Quantifier {
                        format!("a {c} has nothing to repeat")
                    } else {
                        format!("a {c} after a quantifier")
                    });
                }
                if c == '{' {
                    let (text, next) = quantifier(pattern, i)?;
                    out.push_str(&text);
                    i = next;
                } else {
                    out.push(c);
                    i += 1;
                }
                prev = Prev::Quantifier;
            }
            ']' | '}' => return Err(format!("a {c} stands alone")),
            _ => {
                out.push_str(&escape(c));
                i += 1;
                prev = Prev::Atom;
            }
        }
    }
    if depth > 0 {
        return Err("a ( is not closed".to_string());
    }
    Ok(out)
}

/// The compiled regular expression for the `pattern` of a schema. Fails with a `Schema` error, with the reason, when the pattern is
/// outside the subset of the spec. It matches if found anywhere in the text, unless anchored.
///
/// # Errors
///
/// `ConcordError::Schema` with the reason when the pattern is outside the subset of the spec, or its compiled form is too large.
pub fn compile_pattern(pattern: &str) -> Result<Regex> {
    let chars: Vec<char> = pattern.chars().collect();
    let text = translate(&chars).map_err(|why| not_supported(&why))?;
    regex::RegexBuilder::new(&text)
        .size_limit(REGEX_SIZE_LIMIT)
        .nest_limit(REGEX_NEST_LIMIT)
        .build()
        .map_err(|e| not_supported(&e.to_string()))
}

fn not_supported(why: &str) -> ConcordError {
    ConcordError::schema(format!("is not in the supported subset ({why})"))
}

/// The schema in the JSON `text`, checked. `label` is what the caller calls it in its error messages (a CLI says `--schema`).
///
/// # Errors
///
/// `ConcordError::Schema` when `text` is not JSON, or not a supported schema.
pub fn parse_schema(text: &str, label: &str) -> Result<Value> {
    match serde_json::from_str::<Value>(trim_ws(text)) {
        Ok(spec) => validate_schema(&spec, label).map(|()| spec),
        Err(exc) => Err(ConcordError::schema(format!(
            "{label} must be a JSON Schema object, but it is not valid JSON ({exc}). For an open format use \
             '{{\"type\": \"string\", \"description\": \"what one option is, in plain words\"}}'."
        ))),
    }
}

/// Whether a JSON value is set: not `null`, `false`, `0`, or an empty text, array or object. A schema key with an unset value is read as absent.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A description as text: a string as it is, anything else as its JSON.
fn text_of(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn non_negative(v: &Value) -> Option<u64> {
    v.as_u64()
}

/// Whether `spec` is a supported schema.
///
/// # Errors
///
/// `ConcordError::Schema` when `spec` is not a JSON object, uses a keyword that is not supported, or one of its values is not valid.
pub fn validate_schema(spec: &Value, label: &str) -> Result<()> {
    let Value::Object(map) = spec else {
        return Err(ConcordError::schema(format!("{label} must be a JSON object")));
    };
    let unknown: BTreeSet<&str> = map.keys().map(String::as_str).filter(|k| !SCHEMA_KEYS.contains(k)).collect();
    if !unknown.is_empty() {
        return Err(ConcordError::schema(format!(
            "{label} uses keywords that are not enforced here: {}. Supported: {}.",
            unknown.into_iter().collect::<Vec<_>>().join(", "),
            SCHEMA_KEYS.join(", ")
        )));
    }
    if map.get("type").is_some_and(|t| t != "string") {
        return Err(ConcordError::schema(format!("{label} \"type\" can only be \"string\": a candidate is text")));
    }
    let has_enum = map.get("enum").is_some_and(truthy);
    let has_description = map.get("description").is_some_and(|d| truthy(d) && !trim_ws(&text_of(d)).is_empty());
    if !(has_enum || has_description) {
        return Err(ConcordError::schema(format!("{label} needs a \"description\" (what one option is, in plain words) or an \"enum\"")));
    }
    if let Some(e) = map.get("enum") {
        let options = e.as_array().filter(|a| a.len() >= 2 && a.iter().all(|x| x.as_str().is_some_and(|s| !trim_ws(s).is_empty())));
        let Some(options) = options else {
            return Err(ConcordError::schema(format!("{label} \"enum\" must be a list of at least 2 non-empty strings")));
        };
        if options.iter().filter_map(Value::as_str).any(|o| o != trim_ws(o)) {
            return Err(ConcordError::schema(format!(
                "{label} \"enum\" has an option with whitespace at either end: a candidate is trimmed before it is compared, so the option could never match"
            )));
        }
        let distinct: BTreeSet<&str> = options.iter().filter_map(Value::as_str).collect();
        if distinct.len() != options.len() {
            return Err(ConcordError::schema(format!("{label} \"enum\" holds the same option twice")));
        }
    }
    if let Some(p) = map.get("pattern") {
        let Some(pattern) = p.as_str() else {
            return Err(ConcordError::schema(format!("{label} \"pattern\" must be a string")));
        };
        compile_pattern(pattern).map_err(|exc| ConcordError::schema(format!("{label} \"pattern\" {exc}")))?;
    }
    for k in ["minLength", "maxLength"] {
        if map.get(k).is_some_and(|v| non_negative(v).is_none()) {
            return Err(ConcordError::schema(format!("{label} \"{k}\" must be a non-negative integer")));
        }
    }
    if let (Some(lo), Some(hi)) = (map.get("minLength").and_then(non_negative), map.get("maxLength").and_then(non_negative)) {
        if lo > hi {
            return Err(ConcordError::schema(format!("{label} \"minLength\" is greater than \"maxLength\"")));
        }
    }
    Ok(())
}

fn options_of(spec: &Map<String, Value>) -> Vec<&str> {
    spec.get("enum").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default()
}

/// Why `text` does not satisfy the schema `spec` (None when it does). The candidate is the text without the whitespace around it; an
/// option of a closed list must match exactly, and lengths count Unicode code points. Fails with a `Schema` error if `spec` carries a
/// pattern outside the supported subset: a schema that cannot be enforced accepts nothing.
///
/// # Errors
///
/// `ConcordError::Schema` when `spec` is not an object, or carries a pattern outside the supported subset.
pub fn check_candidate(spec: &Value, text: &str) -> Result<Option<String>> {
    let Value::Object(map) = spec else {
        return Err(ConcordError::schema("the schema is not a JSON object: nothing can be checked against it"));
    };
    let t = trim_ws(text);
    if map.contains_key("enum") {
        let options = options_of(map);
        if !options.contains(&t) {
            return Ok(Some(format!("must be exactly one of the listed options ({})", options.join("; "))));
        }
    }
    if let Some(pattern) = map.get("pattern").and_then(Value::as_str) {
        let re = compile_pattern(pattern).map_err(|exc| ConcordError::schema(format!("the schema \"pattern\" {exc}")))?;
        if !re.is_match(t) {
            return Ok(Some(format!("must match the regular expression {pattern}")));
        }
    }
    let length = t.chars().count() as u64;
    if let Some(n) = map.get("minLength").and_then(Value::as_u64).filter(|n| length < *n) {
        return Ok(Some(format!("must be at least {n} characters")));
    }
    if let Some(n) = map.get("maxLength").and_then(Value::as_u64).filter(|n| length > *n) {
        return Ok(Some(format!("must be at most {n} characters")));
    }
    Ok(None)
}

/// The candidate format as the agents read it.
pub fn render_schema(spec: &Value) -> String {
    let empty = Map::new();
    let map = spec.as_object().unwrap_or(&empty);
    let mut parts = Vec::new();
    if let Some(d) = map.get("description").filter(|d| truthy(d)) {
        parts.push(text_of(d));
    }
    if map.contains_key("enum") {
        parts.push(format!("exactly one of: {}", options_of(map).join(" | ")));
    }
    if let Some(p) = map.get("pattern") {
        parts.push(format!("text matching the regular expression {}", text_of(p)));
    }
    let (lo, hi) = (map.get("minLength").and_then(Value::as_u64), map.get("maxLength").and_then(Value::as_u64));
    match (lo, hi) {
        (Some(lo), Some(hi)) => parts.push(format!("between {lo} and {hi} characters")),
        (Some(lo), None) => parts.push(format!("at least {lo} characters")),
        (None, Some(hi)) => parts.push(format!("at most {hi} characters")),
        (None, None) => {}
    }
    if !parts.is_empty() {
        return parts.join("; ");
    }
    map.get("title").filter(|t| truthy(t)).map(text_of).unwrap_or_else(|| "text".to_string())
}

/// Writes JSON with `, ` between items and `: ` between a key and its value, the layout of the examples of the spec.
struct SpacedFormatter;

impl Formatter for SpacedFormatter {
    fn begin_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }

    fn begin_object_key<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }

    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        w.write_all(b": ")
    }
}

/// The schema as JSON text, with its keys sorted and non-ASCII characters as they are: the form `<candidate_schema>` carries.
pub fn schema_text(spec: &Value) -> String {
    use serde::Serialize;
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, SpacedFormatter);
    // `Value` keeps its keys as they were written, so they are sorted here, as `sort_keys=True` does.
    sorted(spec).serialize(&mut ser).expect("a JSON value can be written");
    String::from_utf8(out).expect("JSON is UTF-8")
}

/// `v` with the keys of every object sorted.
fn sorted(v: &Value) -> Value {
    match v {
        Value::Object(map) => {
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            Value::Object(entries.into_iter().map(|(k, v)| (k.clone(), sorted(v))).collect())
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted).collect()),
        other => other.clone(),
    }
}
