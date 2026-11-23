//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! JSON as the script reads and writes it: `json.loads`, and `json.dumps` in the three forms it uses.
//!
//! * `dumps_pretty`: `json.dumps(v, indent=2)`, the state file, the output and the JSON inside a phase message. Every character outside
//!   ASCII is escaped (`\u00e9`, a surrogate pair above U+FFFF), as Python's `ensure_ascii` does.
//! * `canonical_json`: `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, the contract inside an execution
//!   message.
//! * `dumps_compact`: `json.dumps(v)`, one line with `", "` and `": "`.
//!
//! A float is written as Python writes it (`1.0`, `0.5`, `1e-05`), not as `serde_json` does (`1`, `1e-5`). An object keeps the order of
//! its keys (`serde_json`'s `preserve_order`), as a Python dict does.

use serde_json::Value;

use crate::errors::{AccordError, Result};
use crate::py::float_repr;

/// How `json.dumps` was called.
#[derive(Debug, Clone, Copy)]
pub struct Style {
    /// `indent`: None writes one line.
    pub indent: Option<usize>,
    /// Between two items: `","` with an indent, `", "` without, unless `separators` says otherwise.
    pub item_sep: &'static str,
    /// Between a key and its value.
    pub key_sep: &'static str,
    /// `sort_keys`.
    pub sort_keys: bool,
    /// `ensure_ascii`.
    pub ensure_ascii: bool,
}

/// `json.dumps(v, indent=2)`.
pub const PRETTY: Style = Style { indent: Some(2), item_sep: ",", key_sep: ": ", sort_keys: false, ensure_ascii: true };
/// `json.dumps(v)`.
pub const COMPACT: Style = Style { indent: None, item_sep: ", ", key_sep: ": ", sort_keys: false, ensure_ascii: true };
/// `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
pub const CANONICAL: Style = Style { indent: None, item_sep: ",", key_sep: ":", sort_keys: true, ensure_ascii: false };

/// `json.dumps(v, indent=2)`: the state file, the output of a command, the JSON inside a phase message.
pub fn dumps_pretty(v: &Value) -> String {
    dumps(v, PRETTY)
}

/// `json.dumps(v)`.
pub fn dumps_compact(v: &Value) -> String {
    dumps(v, COMPACT)
}

/// The canonical JSON of a value: sorted keys, no spaces, characters outside ASCII as they are.
pub fn canonical_json(v: &Value) -> String {
    dumps(v, CANONICAL)
}

/// `json.dumps` with the given style.
pub fn dumps(v: &Value, style: Style) -> String {
    let mut out = String::new();
    write_value(&mut out, v, style, 0);
    out
}

/// `json.loads`. Python reads a few things `serde_json` refuses, which a reply never holds on purpose: `NaN` and `Infinity`, a lone
/// surrogate (`"\ud800"`), and an integer beyond 64 bits (read here as a float). Nesting deeper than 128 levels is refused too.
///
/// The error is Python's: `JSONDecodeError: <what> (line, column and index of the character)`, from `decode_error`; `serde_json`'s
/// text only for what Python reads and `serde_json` refuses.
pub fn loads(text: &str) -> Result<Value> {
    serde_json::from_str(text).map_err(|e| AccordError::python("JSONDecodeError", decode_error(text).unwrap_or_else(|| e.to_string())))
}

/// Why `json.loads` refuses `text`, as Python words it: `Expecting value: line 1 column 1 (char 0)`; None when Python reads it.
///
/// A scan of the text the way Python's C scanner (`_json`) goes through it, which stops at the first error: what it expected, and
/// where, counted in characters. Nothing is decoded.
pub fn decode_error(text: &str) -> Option<String> {
    let s: Vec<char> = text.chars().collect();
    let (msg, pos) = if s.first() == Some(&'\u{feff}') {
        ("Unexpected UTF-8 BOM (decode using utf-8-sig)".to_string(), 0)
    } else {
        let scan = Scanner { s: &s };
        let start = scan.ws(0);
        match scan.value(start) {
            Err(e) => e,
            Ok(end) => {
                let end = scan.ws(end);
                if end == s.len() {
                    return None;
                }
                ("Extra data".to_string(), end)
            }
        }
    };
    let line = s[..pos].iter().filter(|c| **c == '\n').count() + 1;
    let column = pos - s[..pos].iter().rposition(|c| *c == '\n').map_or(0, |i| i + 1) + 1;
    Some(format!("{msg}: line {line} column {column} (char {pos})"))
}

/// The scanner of `decode_error`: each step returns the index after what it read, or the error and its index.
struct Scanner<'a> {
    s: &'a [char],
}

type Scan = std::result::Result<usize, (String, usize)>;

fn fail(msg: &str, pos: usize) -> Scan {
    Err((msg.to_string(), pos))
}

impl Scanner<'_> {
    fn at(&self, i: usize) -> Option<char> {
        self.s.get(i).copied()
    }

    fn ws(&self, mut i: usize) -> usize {
        while matches!(self.at(i), Some(' ' | '\t' | '\n' | '\r')) {
            i += 1;
        }
        i
    }

    fn word(&self, i: usize, word: &str) -> bool {
        word.chars().enumerate().all(|(k, c)| self.at(i + k) == Some(c))
    }

    fn value(&self, i: usize) -> Scan {
        match self.at(i) {
            None => return fail("Expecting value", i),
            Some('"') => return self.string(i + 1),
            Some('{') => return self.object(i + 1),
            Some('[') => return self.array(i + 1),
            _ => {}
        }
        for word in ["null", "true", "false", "NaN", "Infinity", "-Infinity"] {
            if self.word(i, word) {
                return Ok(i + word.chars().count());
            }
        }
        self.number(i)
    }

    fn number(&self, start: usize) -> Scan {
        let digit = |i: usize| self.at(i).is_some_and(|c| c.is_ascii_digit());
        let mut i = start;
        if self.at(i) == Some('-') {
            i += 1;
        }
        match self.at(i) {
            Some('0') => i += 1,
            Some('1'..='9') => {
                while digit(i) {
                    i += 1;
                }
            }
            _ => return fail("Expecting value", start),
        }
        if self.at(i) == Some('.') && digit(i + 1) {
            i += 2;
            while digit(i) {
                i += 1;
            }
        }
        if matches!(self.at(i), Some('e' | 'E')) && i + 1 < self.s.len() {
            let e = i;
            i += 1;
            if matches!(self.at(i), Some('-' | '+')) && i + 1 < self.s.len() {
                i += 1;
            }
            while digit(i) {
                i += 1;
            }
            if !digit(i - 1) {
                i = e;
            }
        }
        Ok(i)
    }

    /// A string whose opening quote is before `end`.
    fn string(&self, end: usize) -> Scan {
        let begin = end - 1;
        let len = self.s.len();
        let mut next = end;
        loop {
            while next < len && !matches!(self.s[next], '"' | '\\') {
                if (self.s[next] as u32) <= 0x1f {
                    return fail("Invalid control character at", next);
                }
                next += 1;
            }
            if next == len {
                return fail("Unterminated string starting at", begin);
            }
            if self.s[next] == '"' {
                return Ok(next + 1);
            }
            next += 1; // the backslash
            if next == len {
                return fail("Unterminated string starting at", begin);
            }
            if self.s[next] != 'u' {
                if !matches!(self.s[next], '"' | '\\' | '/' | 'b' | 'f' | 'n' | 'r' | 't') {
                    return fail("Invalid \\escape", next - 1);
                }
                next += 1;
                continue;
            }
            // `next` is at the `u`: four hex digits follow, and a second `\u` escape after a high surrogate needs four too
            let code = self.hex(next)?;
            next += 5;
            let pair = (0xd800..=0xdbff).contains(&code) && self.at(next) == Some('\\') && self.at(next + 1) == Some('u');
            if pair && (0xdc00..=0xdfff).contains(&self.hex(next + 1)?) {
                next += 6;
            }
        }
    }

    /// The four hex digits after the `u` (at `u`) of a `\u` escape; the error is at the `u`.
    fn hex(&self, u: usize) -> std::result::Result<u32, (String, usize)> {
        let mut code = 0;
        for i in u + 1..u + 5 {
            match self.at(i).and_then(|c| c.to_digit(16)) {
                Some(d) => code = code * 16 + d,
                None => return Err(("Invalid \\uXXXX escape".to_string(), u)),
            }
        }
        Ok(code)
    }

    /// An object whose `{` is before `i`.
    fn object(&self, i: usize) -> Scan {
        let mut i = self.ws(i);
        if self.at(i) == Some('}') {
            return Ok(i + 1);
        }
        loop {
            if self.at(i) != Some('"') {
                return fail("Expecting property name enclosed in double quotes", i);
            }
            i = self.ws(self.string(i + 1)?);
            if self.at(i) != Some(':') {
                return fail("Expecting ':' delimiter", i);
            }
            i = self.ws(i + 1);
            i = self.ws(self.value(i)?);
            match self.at(i) {
                Some('}') => return Ok(i + 1),
                Some(',') => {}
                _ => return fail("Expecting ',' delimiter", i),
            }
            let comma = i;
            i = self.ws(i + 1);
            if self.at(i) == Some('}') {
                return fail("Illegal trailing comma before end of object", comma);
            }
        }
    }

    /// An array whose `[` is before `i`.
    fn array(&self, i: usize) -> Scan {
        let mut i = self.ws(i);
        if self.at(i) == Some(']') {
            return Ok(i + 1);
        }
        loop {
            i = self.ws(self.value(i)?);
            match self.at(i) {
                Some(']') => return Ok(i + 1),
                Some(',') => {}
                _ => return fail("Expecting ',' delimiter", i),
            }
            let comma = i;
            i = self.ws(i + 1);
            if self.at(i) == Some(']') {
                return fail("Illegal trailing comma before end of array", comma);
            }
        }
    }
}

fn newline(out: &mut String, style: Style, level: usize) {
    if let Some(indent) = style.indent {
        out.push('\n');
        out.push_str(&" ".repeat(indent * level));
    }
}

fn write_value(out: &mut String, v: &Value, style: Style, level: usize) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if n.is_i64() || n.is_u64() {
                out.push_str(&n.to_string());
            } else {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f.is_nan() {
                    out.push_str("NaN");
                } else if f.is_infinite() {
                    out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
                } else {
                    out.push_str(&float_repr(f));
                }
            }
        }
        Value::String(s) => write_str(out, s, style.ensure_ascii),
        Value::Array(a) => {
            if a.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(style.item_sep);
                }
                newline(out, style, level + 1);
                write_value(out, item, style, level + 1);
            }
            newline(out, style, level);
            out.push(']');
        }
        Value::Object(o) => {
            if o.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut items: Vec<(&String, &Value)> = o.iter().collect();
            if style.sort_keys {
                // byte order of UTF-8 is the order of the code points, which is Python's order of strings
                items.sort_by(|a, b| a.0.cmp(b.0));
            }
            out.push('{');
            for (i, (k, item)) in items.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(style.item_sep);
                }
                newline(out, style, level + 1);
                write_str(out, k, style.ensure_ascii);
                out.push_str(style.key_sep);
                write_value(out, item, style, level + 1);
            }
            newline(out, style, level);
            out.push('}');
        }
    }
}

/// A JSON string as Python's encoder writes it: `"` and `\` escaped, `\n` `\r` `\t` `\b` `\f` by name, other control characters as
/// `\u00XX`; with `ensure_ascii`, everything outside printable ASCII as `\uXXXX` (lowercase hex, surrogate pairs above U+FFFF).
fn write_str(out: &mut String, s: &str, ensure_ascii: bool) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ensure_ascii && !(' '..='~').contains(&c) => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn pretty_is_python_indent_2() {
        let v = json!({"a": [1, 2.0, {"b": []}], "c": {}, "d": "é😀\u{7f}\n"});
        assert_eq!(
            dumps_pretty(&v),
            "{\n  \"a\": [\n    1,\n    2.0,\n    {\n      \"b\": []\n    }\n  ],\n  \"c\": {},\n  \"d\": \"\\u00e9\\ud83d\\ude00\\u007f\\n\"\n}"
        );
    }

    #[test]
    fn canonical_sorts_and_keeps_unicode() {
        let v = json!({"b": 1, "a": ["é", 0.5], "\u{1}": true});
        assert_eq!(canonical_json(&v), "{\"\\u0001\":true,\"a\":[\"é\",0.5],\"b\":1}");
    }

    #[test]
    fn compact_has_spaces_after_separators() {
        assert_eq!(dumps_compact(&json!({"a": [1, null]})), "{\"a\": [1, null]}");
    }

    #[test]
    fn loads_keeps_the_last_of_a_repeated_key_at_its_first_place() {
        let v = loads("{\"a\": 1, \"b\": 2, \"a\": 3}").unwrap();
        assert_eq!(dumps_compact(&v), "{\"a\": 3, \"b\": 2}");
        assert!(loads("[1,").unwrap_err().message().starts_with("JSONDecodeError: "));
    }

    #[test]
    fn a_text_that_is_not_json_is_refused_as_python_refuses_it() {
        let cases = [
            ("", "Expecting value: line 1 column 1 (char 0)"),
            ("\n\n  [é, 1]", "Expecting value: line 3 column 4 (char 5)"),
            ("[1,]", "Illegal trailing comma before end of array: line 1 column 3 (char 2)"),
            ("{\"a\":1,}", "Illegal trailing comma before end of object: line 1 column 7 (char 6)"),
            ("{\"a\" 1}", "Expecting ':' delimiter: line 1 column 6 (char 5)"),
            ("{a}", "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
            ("[1 2]", "Expecting ',' delimiter: line 1 column 4 (char 3)"),
            ("1 2", "Extra data: line 1 column 3 (char 2)"),
            ("\"a\u{1}\"", "Invalid control character at: line 1 column 3 (char 2)"),
            ("\"a\\x\"", "Invalid \\escape: line 1 column 3 (char 2)"),
            ("[\"\\u12\"]", "Invalid \\uXXXX escape: line 1 column 4 (char 3)"),
            ("[\"\\ud83d\\u", "Invalid \\uXXXX escape: line 1 column 10 (char 9)"),
            ("[\"\\u1234", "Unterminated string starting at: line 1 column 2 (char 1)"),
            ("\u{feff}[]", "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)"),
        ];
        for (text, want) in cases {
            assert_eq!(decode_error(text).as_deref(), Some(want), "{text:?}");
            assert_eq!(loads(text).unwrap_err().message(), format!("JSONDecodeError: {want}"));
        }
        assert_eq!(decode_error("[1e5, -0, 1.5E-3, \"\\ud83d\\ude00\"]"), None);
    }
}
