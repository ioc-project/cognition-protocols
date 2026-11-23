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
pub fn loads(text: &str) -> Result<Value> {
    serde_json::from_str(text).map_err(|e| AccordError::python("JSONDecodeError", e))
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
}
