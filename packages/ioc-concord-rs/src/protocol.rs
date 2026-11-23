//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The constants of the protocol, and the forms the spec gives to ids and numbers: the one place that names its version, its
//! defaults and its outcomes, and that says what a valid agent id, UUID or number looks like.

use serde::{Deserialize, Serialize};
use std::fmt;

/// The version every message carries on its root element. An agent processes only messages of exactly this version.
pub const PROTOCOL_VERSION: &str = "0.3.0";

/// One million: every number of the protocol (a raw score, an anchor, `satisfaction_threshold`, `eps`) is held as an integer count
/// of millionths, so that no floating-point error can change a decision (see Numbers in the spec). Text appears only on the wire
/// and in reports.
pub const MILLION: i64 = 1_000_000;

/// Whitespace is these four characters and no other: a text is trimmed of them, and `\s` of a pattern stands for them, so that every
/// implementation reads the same text, whatever its language. (`str::trim` strips every Unicode space, which is more.)
pub const WS: [char; 4] = [' ', '\t', '\r', '\n'];

/// `s` without the whitespace (see `WS`) at either end.
#[must_use]
pub fn trim_ws(s: &str) -> &str {
    s.trim_matches(&WS[..])
}

/// `s` with each run of whitespace (see `WS`) made one space, and none left at either end.
#[must_use]
pub fn squeeze(s: &str) -> String {
    s.split(&WS[..]).filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ")
}

/// The default `satisfaction_threshold` (0.6), in millionths.
pub const DEFAULT_THRESHOLD: i64 = 600_000;
/// The default `eps` (0.05), in millionths.
pub const DEFAULT_EPS: i64 = 50_000;
/// The default budget of repair rounds.
pub const DEFAULT_BUDGET: i64 = 8;
/// The default `timeout`, in seconds.
pub const DEFAULT_TIMEOUT: i64 = 300;
/// The default number of requests before an agent aborts.
pub const DEFAULT_RETRIES: i64 = 3;
/// The largest `budget`, `timeout` or `retries` of a run. The spec sets no top; this library refuses more than 9 digits, so that every value
/// is one that someone can use, and says so clearly. A limit of this library, not of the spec.
pub const MAX_INTEGER: i64 = 999_999_999;

/// How a run ends. `Aborted` is the end of a run that cannot complete; a `<concord-result>` never says it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Outcome {
    #[serde(rename = "SUCCESS")]
    Success,
    #[serde(rename = "BEST_EFFORT")]
    BestEffort,
    #[serde(rename = "ABORTED")]
    Aborted,
}

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Success => "SUCCESS",
            Outcome::BestEffort => "BEST_EFFORT",
            Outcome::Aborted => "ABORTED",
        }
    }

    /// The outcome written as the spec writes it, or None.
    pub fn parse(text: &str) -> Option<Outcome> {
        match text {
            "SUCCESS" => Some(Outcome::Success),
            "BEST_EFFORT" => Some(Outcome::BestEffort),
            "ABORTED" => Some(Outcome::Aborted),
            _ => None,
        }
    }
}

impl fmt::Display for Outcome {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// What a `<concord-result>` can say: an aborted run sends no result.
pub const RESULT_OUTCOMES: [Outcome; 2] = [Outcome::Success, Outcome::BestEffort];

/// True if `text` is an agent id: 1 to 64 ASCII letters, digits, `_` or `-`.
pub fn is_agent_id(text: &str) -> bool {
    (1..=64).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// True if `text` is a UUID in lowercase hexadecimal with hyphens (a run id, a `uuid`, a `reply_to`, ...).
pub fn is_uuid(text: &str) -> bool {
    let b = text.as_bytes();
    b.len() == 36
        && b.iter().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => *c == b'-',
            _ => c.is_ascii_digit() || (b'a'..=b'f').contains(c),
        })
}

/// The value of `text` in millionths, or None unless it is a number of the spec: one to three ASCII digits, then optionally a
/// point and one to six digits (`82`, `82.0`, `0.5`, `007`; not `.5`, `82.`, `+5`, `1e2`, `NaN`). The range is for the caller to
/// check.
pub fn parse_number(text: &str) -> Option<i64> {
    let (whole, frac) = match text.split_once('.') {
        Some((w, f)) => (w, Some(f)),
        None => (text, None),
    };
    let digits = |s: &str, max: usize| (1..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(whole, 3) || !frac.is_none_or(|f| digits(f, 6)) {
        return None;
    }
    let frac = format!("{:0<6}", frac.unwrap_or(""));
    Some(whole.parse::<i64>().ok()? * MILLION + frac.parse::<i64>().ok()?)
}

/// A number as the caller gives it: the text of a number, an integer, or a float.
#[derive(Debug, Clone, PartialEq)]
pub enum Num {
    Text(String),
    Int(i64),
    Float(f64),
}

impl Num {
    /// The value in millionths, or None. A text is read by `parse_number`, an integer from its text, and a float from its shortest
    /// text (82.5 is 82.5, 0.1 is 0.1), so it needs at most 6 decimal places and no exponent.
    pub fn to_millionths(&self) -> Option<i64> {
        match self {
            Num::Text(t) => parse_number(t),
            Num::Int(i) if *i >= 0 => parse_number(&i.to_string()),
            Num::Int(_) => None,
            // A float below 1e-4 or from 1e16 is written with an exponent (`1e-06`, `1e+16`) by its shortest text: not a number of the spec.
            Num::Float(f) if *f != 0.0 && (f.abs() < 1e-4 || f.abs() >= 1e16) => None,
            Num::Float(f) => parse_number(&f.to_string()),
        }
    }
}

impl fmt::Display for Num {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Num::Text(t) => f.write_str(t),
            Num::Int(i) => write!(f, "{i}"),
            Num::Float(x) => write!(f, "{x:?}"),
        }
    }
}

impl From<&str> for Num {
    fn from(v: &str) -> Self {
        Num::Text(v.to_string())
    }
}
impl From<String> for Num {
    fn from(v: String) -> Self {
        Num::Text(v)
    }
}
impl From<&String> for Num {
    fn from(v: &String) -> Self {
        Num::Text(v.clone())
    }
}
impl From<i32> for Num {
    fn from(v: i32) -> Self {
        Num::Int(v.into())
    }
}
impl From<i64> for Num {
    fn from(v: i64) -> Self {
        Num::Int(v)
    }
}
impl From<u32> for Num {
    fn from(v: u32) -> Self {
        Num::Int(v.into())
    }
}
impl From<f64> for Num {
    fn from(v: f64) -> Self {
        Num::Float(v)
    }
}

/// The text of a number of millionths, as the spec writes it: `82.0`, `0.5`, `0.000001`.
pub fn format_number(millionths: i64) -> String {
    let (whole, frac) = (millionths.div_euclid(MILLION), millionths.rem_euclid(MILLION));
    let frac = format!("{frac:06}");
    let frac = frac.trim_end_matches('0');
    format!("{whole}.{}", if frac.is_empty() { "0" } else { frac })
}

/// `text` between quotes, for an error message that shows exactly what was wrong: single quotes, or double quotes when the text holds a
/// single quote and no double quote. A backslash, a line feed, a carriage return, a tab and the quote itself are escaped.
pub(crate) fn quote(text: &str) -> String {
    let mark = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::from(mark);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == mark => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(mark);
    out
}
