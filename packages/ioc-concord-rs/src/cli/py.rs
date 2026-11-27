//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the command prints as the skill scripts printed it, where that is Python's own way of writing something: the `repr` of a text,
//! `shlex.quote`, `str.strip`, the name of a path (`str(Path(...))`), the messages of `json.loads` and of the UTF-8 decoder, the rounding
//! of `round` and the local time of `datetime`.

use serde_json::{Map, Number, Value};

/// `str.isspace` of Python: Unicode white space, and the separators U+001C to U+001F.
pub(crate) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub(crate) fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// Whether `str.isprintable` holds for `c`: not a control, a format character, a separator other than the space, a surrogate or a private
/// use character. (Unassigned code points, which Python does not print either, are taken as printable.)
fn printable(c: char) -> bool {
    let u = c as u32;
    !(c.is_control()
        || matches!(u, 0xa0 | 0xad | 0x1680 | 0x2000..=0x200f | 0x2028..=0x202f | 0x205f..=0x2064 | 0x2066..=0x206f | 0x3000 | 0xfeff)
        || matches!(u, 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0xfff9..=0xfffb | 0x110bd | 0x110cd | 0xe0001 | 0xe0020..=0xe007f)
        || matches!(u, 0xe000..=0xf8ff | 0xf0000..=0xffffd | 0x100000..=0x10fffd))
}

/// `repr(text)`.
pub(crate) fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if printable(c) => out.push(c),
            c if (c as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

/// `shlex.quote(text)`.
pub(crate) fn shell_quote(text: &str) -> String {
    if text.is_empty() {
        return "''".to_string();
    }
    if text.chars().all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c)) {
        return text.to_string();
    }
    format!("'{}'", text.replace('\'', "'\"'\"'"))
}

/// `str(Path(text))` on POSIX: no empty or `.` component, no slash at the end, and `.` for nothing.
pub(crate) fn path_name(text: &str) -> String {
    let root = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = text.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let joined = format!("{root}{}", parts.join("/"));
    if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

/// `str(Path(dir) / name)`.
pub(crate) fn path_join(dir: &str, name: &str) -> String {
    match dir {
        "." => name.to_string(),
        d if d.ends_with('/') => format!("{d}{name}"),
        d => format!("{d}/{name}"),
    }
}

/// What Python says of bytes that are not UTF-8, as `bytes.decode("utf-8")` raises it.
pub(crate) fn utf8_error(bytes: &[u8], error: &std::str::Utf8Error) -> String {
    let at = error.valid_up_to();
    let first = bytes[at];
    let reason = match error.error_len() {
        None => "unexpected end of data",
        Some(_) if !(0xc2..=0xf4).contains(&first) => "invalid start byte",
        Some(_) => "invalid continuation byte",
    };
    let span = error.error_len().unwrap_or(bytes.len() - at);
    if span > 1 && reason != "invalid start byte" {
        format!("'utf-8' codec can't decode bytes in position {at}-{}: {reason}", at + span - 1)
    } else {
        format!("'utf-8' codec can't decode byte 0x{first:02x} in position {at}: {reason}")
    }
}

/// `round(x)` of Python: to the nearest integer, ties to the even one.
pub(crate) fn round(x: f64) -> f64 {
    x.round_ties_even()
}

/// `min(a, b)` and `max(a, b)` of Python: the first unless the second is smaller (larger). A NaN is never smaller or larger.
pub(crate) fn min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

pub(crate) fn max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `int(text)` for an argument: blanks around it, a sign, digits and single underscores between them.
pub(crate) fn parse_int(text: &str) -> Option<i64> {
    let t = strip(text);
    let (sign, digits) = match t.strip_prefix('-') {
        Some(rest) => (-1, rest),
        None => (1, t.strip_prefix('+').unwrap_or(t)),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
        return None;
    }
    if !digits.chars().all(|c| c.is_ascii_digit() || c == '_') {
        return None;
    }
    digits.replace('_', "").parse::<i64>().ok().map(|n| sign * n)
}

/// `float(text)` for an argument.
pub(crate) fn parse_float(text: &str) -> Option<f64> {
    let t = strip(text);
    let lower = t.to_ascii_lowercase();
    let unsigned = lower.trim_start_matches(['+', '-']);
    if matches!(unsigned, "inf" | "infinity" | "nan") {
        return lower.parse::<f64>().ok().or(Some(if lower.starts_with('-') { f64::NEG_INFINITY } else { f64::INFINITY }));
    }
    if t.contains("__") || t.starts_with('_') || t.ends_with('_') {
        return None;
    }
    let plain = t.replace('_', "");
    if plain.is_empty() || !plain.chars().all(|c| c.is_ascii_digit() || "+-.eE".contains(c)) {
        return None;
    }
    plain.parse::<f64>().ok()
}

/// `datetime.datetime.fromtimestamp(when).astimezone().isoformat(timespec="seconds")`: the local time, with its offset.
pub(crate) fn local_time(when: f64) -> String {
    let micros = round(when * 1e6);
    let secs = (micros / 1e6).floor() as i64;
    let (fields, offset) = local_fields(secs);
    let sign = if offset < 0 { '-' } else { '+' };
    let off = offset.unsigned_abs();
    let (oh, om, os) = (off / 3600, off % 3600 / 60, off % 60);
    let zone = if os == 0 { format!("{sign}{oh:02}:{om:02}") } else { format!("{sign}{oh:02}:{om:02}:{os:02}") };
    let (y, mo, d, h, mi, s) = fields;
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}{zone}")
}

/// The local date and time of `secs` since the epoch, and the offset of the zone in seconds.
#[cfg(unix)]
fn local_fields(secs: i64) -> ((i64, u32, u32, u32, u32, u32), i64) {
    // the type `localtime_r` takes, inferred from the call: `libc::time_t` is deprecated on musl, where it is to become 64-bit
    let t = secs as _;
    // SAFETY: `localtime_r` writes into the `tm` it is given and reads only `t`; an all-zero `tm` is a valid value of the struct.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    let ok = unsafe { !libc::localtime_r(&t, &mut tm).is_null() };
    if !ok {
        return (utc_fields(secs), 0);
    }
    let fields =
        (i64::from(tm.tm_year) + 1900, tm.tm_mon as u32 + 1, tm.tm_mday as u32, tm.tm_hour as u32, tm.tm_min as u32, tm.tm_sec as u32);
    (fields, tm.tm_gmtoff as i64)
}

/// Where the local zone cannot be read, the time is in UTC.
#[cfg(not(unix))]
fn local_fields(secs: i64) -> ((i64, u32, u32, u32, u32, u32), i64) {
    (utc_fields(secs), 0)
}

/// The UTC date and time of `secs` since the epoch.
fn utc_fields(secs: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // the civil date of a day number (Howard Hinnant's algorithm)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day, (rem / 3600) as u32, (rem % 3600 / 60) as u32, (rem % 60) as u32)
}

/// `difflib.get_close_matches(word, possibilities, 1)`: the possibility most like `word`, if one is alike enough (a ratio of 0.6).
pub(crate) fn close_match<'p>(word: &str, possibilities: &[&'p str]) -> Option<&'p str> {
    let b: Vec<char> = word.chars().collect();
    let mut best: Option<(f64, &str)> = None;
    for x in possibilities {
        let a: Vec<char> = x.chars().collect();
        let total = a.len() + b.len();
        let ratio = if total == 0 { 1.0 } else { 2.0 * matching(&a, &b, 0, a.len(), 0, b.len()) as f64 / total as f64 };
        if ratio >= 0.6 && best.is_none_or(|(r, y)| (ratio, *x) > (r, y)) {
            best = Some((ratio, x));
        }
    }
    best.map(|(_, x)| x)
}

/// How many characters the matching blocks of `SequenceMatcher` cover in `a[alo..ahi]` and `b[blo..bhi]`.
fn matching(a: &[char], b: &[char], alo: usize, ahi: usize, blo: usize, bhi: usize) -> usize {
    // find_longest_match, without junk: the first longest block, by its end in `a`, then in `b`
    let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
    let mut j2len: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    for (i, ca) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut next = std::collections::HashMap::new();
        for (j, cb) in b.iter().enumerate().take(bhi).skip(blo) {
            if cb != ca {
                continue;
            }
            let k = j.checked_sub(1).and_then(|p| j2len.get(&p)).copied().unwrap_or(0) + 1;
            next.insert(j, k);
            if k > bestsize {
                (besti, bestj, bestsize) = (i + 1 - k, j + 1 - k, k);
            }
        }
        j2len = next;
    }
    if bestsize == 0 {
        return 0;
    }
    bestsize + matching(a, b, alo, besti, blo, bestj) + matching(a, b, besti + bestsize, ahi, bestj + bestsize, bhi)
}

// ---------------------------------------------------------------------------------------------------------------------------
// json.loads
// ---------------------------------------------------------------------------------------------------------------------------

/// `json.loads(text)`, with the message of its error as `str(JSONDecodeError)` says it. A number that is not finite (NaN, Infinity, or
/// too large for a float) is the text of the number, since JSON has no such value.
pub(crate) fn json_loads(text: &str) -> Result<Value, String> {
    let chars: Vec<char> = text.chars().collect();
    let parser = JsonParser { s: &chars };
    let fail = |msg: &str, pos: usize| {
        let before = &chars[..pos.min(chars.len())];
        let line = before.iter().filter(|c| **c == '\n').count() + 1;
        let col = match before.iter().rposition(|c| *c == '\n') {
            Some(nl) => pos - nl,
            None => pos + 1,
        };
        format!("{msg}: line {line} column {col} (char {pos})")
    };
    if chars.first() == Some(&'\u{feff}') {
        return Err(fail("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
    }
    let start = parser.ws(0);
    let (value, end) = parser.value(start).map_err(|e| match e {
        JsonError::Stop(pos) => fail("Expecting value", pos),
        JsonError::Msg(msg, pos) => fail(msg, pos),
    })?;
    let end = parser.ws(end);
    if end != chars.len() {
        return Err(fail("Extra data", end));
    }
    Ok(value)
}

enum JsonError {
    /// Nothing that starts a value at this position (`StopIteration`).
    Stop(usize),
    Msg(&'static str, usize),
}

struct JsonParser<'a> {
    s: &'a [char],
}

impl JsonParser<'_> {
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

    fn value(&self, i: usize) -> Result<(Value, usize), JsonError> {
        match self.at(i) {
            None => Err(JsonError::Stop(i)),
            Some('"') => self.string(i + 1).map(|(s, e)| (Value::String(s), e)),
            Some('{') => self.object(i + 1),
            Some('[') => self.array(i + 1),
            Some('n') if self.word(i, "null") => Ok((Value::Null, i + 4)),
            Some('t') if self.word(i, "true") => Ok((Value::Bool(true), i + 4)),
            Some('f') if self.word(i, "false") => Ok((Value::Bool(false), i + 5)),
            Some('N') if self.word(i, "NaN") => Ok((Value::String("NaN".into()), i + 3)),
            Some('I') if self.word(i, "Infinity") => Ok((Value::String("Infinity".into()), i + 8)),
            Some('-') if self.word(i, "-Infinity") => Ok((Value::String("-Infinity".into()), i + 9)),
            Some(_) => self.number(i),
        }
    }

    fn digits(&self, mut i: usize) -> usize {
        while matches!(self.at(i), Some('0'..='9')) {
            i += 1;
        }
        i
    }

    fn number(&self, start: usize) -> Result<(Value, usize), JsonError> {
        let mut i = start;
        if self.at(i) == Some('-') {
            i += 1;
        }
        match self.at(i) {
            Some('1'..='9') => i = self.digits(i + 1),
            Some('0') => i += 1,
            _ => return Err(JsonError::Stop(start)),
        }
        let mut float = false;
        if self.at(i) == Some('.') && matches!(self.at(i + 1), Some('0'..='9')) {
            i = self.digits(i + 1);
            float = true;
        }
        if matches!(self.at(i), Some('e' | 'E')) {
            let mut j = i + 1;
            if matches!(self.at(j), Some('+' | '-')) {
                j += 1;
            }
            let k = self.digits(j);
            if k > j {
                i = k;
                float = true;
            }
        }
        let text: String = self.s[start..i].iter().collect();
        let value = if float {
            match text.parse::<f64>().ok().and_then(Number::from_f64) {
                Some(n) => Value::Number(n),
                None => Value::String(text),
            }
        } else if let Ok(n) = text.parse::<i64>() {
            Value::Number(n.into())
        } else if let Ok(n) = text.parse::<u64>() {
            Value::Number(n.into())
        } else {
            text.parse::<f64>().ok().and_then(Number::from_f64).map_or(Value::String(text), Value::Number)
        };
        Ok((value, i))
    }

    fn hex4(&self, i: usize) -> Option<u32> {
        (0..4).try_fold(0u32, |acc, k| self.at(i + k).and_then(|c| c.to_digit(16)).map(|d| acc * 16 + d))
    }

    /// The text of a string whose opening quote is at `end - 1`, as the C scanner of `json` reads it (strict: no control character).
    fn string(&self, mut end: usize) -> Result<(String, usize), JsonError> {
        let begin = end - 1;
        let len = self.s.len();
        let mut out = String::new();
        loop {
            let mut next = end;
            let mut c = None;
            while next < len {
                let ch = self.s[next];
                if ch == '"' || ch == '\\' {
                    c = Some(ch);
                    break;
                }
                if (ch as u32) <= 0x1f {
                    return Err(JsonError::Msg("Invalid control character at", next));
                }
                out.push(ch);
                next += 1;
            }
            let Some(c) = c else { return Err(JsonError::Msg("Unterminated string starting at", begin)) };
            next += 1;
            if c == '"' {
                return Ok((out, next));
            }
            if next == len {
                return Err(JsonError::Msg("Unterminated string starting at", begin));
            }
            let e = self.s[next];
            if e != 'u' {
                end = next + 1;
                out.push(match e {
                    '"' => '"',
                    '\\' => '\\',
                    '/' => '/',
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    _ => return Err(JsonError::Msg("Invalid \\escape", end - 2)),
                });
                continue;
            }
            next += 1;
            end = next + 4;
            if end >= len {
                return Err(JsonError::Msg("Invalid \\uXXXX escape", next - 1));
            }
            let Some(mut code) = self.hex4(next) else { return Err(JsonError::Msg("Invalid \\uXXXX escape", end - 5)) };
            if (0xd800..0xdc00).contains(&code) && end + 6 < len && self.at(end) == Some('\\') && self.at(end + 1) == Some('u') {
                let Some(low) = self.hex4(end + 2) else { return Err(JsonError::Msg("Invalid \\uXXXX escape", end + 1)) };
                if (0xdc00..0xe000).contains(&low) {
                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                    end += 6;
                }
            }
            out.push(char::from_u32(code).unwrap_or('\u{fffd}')); // a lone surrogate, which a Rust text cannot hold
        }
    }

    fn object(&self, start: usize) -> Result<(Value, usize), JsonError> {
        let mut map = Map::new();
        let mut i = self.ws(start);
        if self.at(i) == Some('}') {
            return Ok((Value::Object(map), i + 1));
        }
        loop {
            if self.at(i) != Some('"') {
                return Err(JsonError::Msg("Expecting property name enclosed in double quotes", i));
            }
            let (key, after) = self.string(i + 1)?;
            i = self.ws(after);
            if self.at(i) != Some(':') {
                return Err(JsonError::Msg("Expecting ':' delimiter", i));
            }
            i = self.ws(i + 1);
            let (value, after) = self.value(i)?;
            map.insert(key, value);
            i = self.ws(after);
            match self.at(i) {
                Some('}') => return Ok((Value::Object(map), i + 1)),
                Some(',') => {}
                _ => return Err(JsonError::Msg("Expecting ',' delimiter", i)),
            }
            let comma = i;
            i = self.ws(i + 1);
            if self.at(i) == Some('}') {
                return Err(JsonError::Msg("Illegal trailing comma before end of object", comma));
            }
        }
    }

    fn array(&self, start: usize) -> Result<(Value, usize), JsonError> {
        let mut items = Vec::new();
        let mut i = self.ws(start);
        if self.at(i) == Some(']') {
            return Ok((Value::Array(items), i + 1));
        }
        loop {
            let (value, after) = self.value(i)?;
            items.push(value);
            i = self.ws(after);
            match self.at(i) {
                Some(']') => return Ok((Value::Array(items), i + 1)),
                Some(',') => {}
                _ => return Err(JsonError::Msg("Expecting ',' delimiter", i)),
            }
            let comma = i;
            i = self.ws(i + 1);
            if self.at(i) == Some(']') {
                return Err(JsonError::Msg("Illegal trailing comma before end of array", comma));
            }
        }
    }
}

/// `json.dumps(value)` with its default separators (`, ` and `: `), for the small examples the command prints.
pub(crate) fn json_dumps(value: &Value) -> String {
    match value {
        Value::Array(items) => format!("[{}]", items.iter().map(json_dumps).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => {
            format!(
                "{{{}}}",
                map.iter()
                    .map(|(k, v)| format!("{}: {}", json_dumps(&Value::String(k.clone())), json_dumps(v)))
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        }
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::guidance::float_repr;

    #[test]
    fn the_messages_of_json_loads_are_those_of_python() {
        let cases = [
            ("{not json", "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
            ("", "Expecting value: line 1 column 1 (char 0)"),
            ("[1,]", "Illegal trailing comma before end of array: line 1 column 3 (char 2)"),
            ("{\"a\":1,}", "Illegal trailing comma before end of object: line 1 column 7 (char 6)"),
            ("{\"a\" 1}", "Expecting ':' delimiter: line 1 column 6 (char 5)"),
            ("[1 2]", "Expecting ',' delimiter: line 1 column 4 (char 3)"),
            ("\"abc", "Unterminated string starting at: line 1 column 1 (char 0)"),
            ("\"a\nb\"", "Invalid control character at: line 1 column 3 (char 2)"),
            ("\"\\q\"", "Invalid \\escape: line 1 column 2 (char 1)"),
            ("\"\\u12\"", "Invalid \\uXXXX escape: line 1 column 3 (char 2)"),
            ("01", "Extra data: line 1 column 2 (char 1)"),
            ("{\"a\":}", "Expecting value: line 1 column 6 (char 5)"),
            ("\u{feff}{}", "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)"),
            ("nul", "Expecting value: line 1 column 1 (char 0)"),
            ("[\n1,\n\n x]", "Expecting value: line 4 column 2 (char 7)"),
            ("-", "Expecting value: line 1 column 1 (char 0)"),
            ("1.", "Extra data: line 1 column 2 (char 1)"),
            ("{\"a\":1 \"b\"}", "Expecting ',' delimiter: line 1 column 8 (char 7)"),
        ];
        for (text, message) in cases {
            assert_eq!(json_loads(text).unwrap_err(), message, "{text:?}");
        }
        let v = json_loads(" {\"a\": [1, 2.5, \"\\u00e9\\ud83d\\ude00\", null, true], \"a\": -0} ").unwrap();
        assert_eq!(v, serde_json::json!({"a": 0}));
        assert_eq!(json_loads("NaN").unwrap(), Value::String("NaN".into()));
    }

    #[test]
    fn texts_are_written_as_python_writes_them() {
        assert_eq!(repr("a b"), "'a b'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("a'\"b\n\u{7}\u{a0}é"), "'a\\'\"b\\n\\x07\\xa0é'");
        assert_eq!(shell_quote("dir/x-1.json"), "dir/x-1.json");
        assert_eq!(shell_quote("my dir"), "'my dir'");
        assert_eq!(shell_quote("it's"), "'it'\"'\"'s'");
        assert_eq!(shell_quote(""), "''");
        assert_eq!(path_name("./"), ".");
        assert_eq!(path_name(""), ".");
        assert_eq!(path_name("a//b/./c/"), "a/b/c");
        assert_eq!(path_name("/tmp/x/"), "/tmp/x");
        assert_eq!(path_join(".", "f.json"), "f.json");
        assert_eq!(path_join("/", "f.json"), "/f.json");
        assert_eq!(path_join("a/b", "f.json"), "a/b/f.json");
        assert_eq!(strip("\u{1f} x \n"), "x");
        assert_eq!(json_dumps(&serde_json::json!({"a": [{"b": 70, "c": "..."}]})), "{\"a\": [{\"b\": 70, \"c\": \"...\"}]}");
    }

    #[test]
    fn a_close_match_is_the_one_of_difflib() {
        let commands = ["start", "receive", "tick", "wait", "report", "forward"];
        assert_eq!(close_match("status", &commands), Some("start"));
        assert_eq!(close_match("recieve", &commands), Some("receive"));
        assert_eq!(close_match("nonsense", &commands), None);
        assert_eq!(close_match("", &commands), None);
        for (word, close) in [("repor", "report"), ("tock", "tick"), ("wiat", "wait"), ("fwd", "forward"), ("strat", "start")] {
            assert_eq!(close_match(word, &commands), Some(close), "{word}");
        }
        assert_eq!(close_match("answer", &commands), None);
        let commands = ["receive", "answer", "tick", "wait", "task", "status", "report"];
        for (word, close) in [("start", "status"), ("stat", "status"), ("ansver", "answer"), ("tasks", "task"), ("reprot", "report")] {
            assert_eq!(close_match(word, &commands), Some(close), "{word}");
        }
        assert_eq!(close_match("forward", &commands), None);
    }

    #[test]
    fn numbers_are_read_and_rounded_as_python_does() {
        assert_eq!(
            (float_repr(1e-05), float_repr(0.8), float_repr(100.0), float_repr(1e16), float_repr(-2.5e-7)),
            ("1e-05".to_string(), "0.8".to_string(), "100.0".to_string(), "1e+16".to_string(), "-2.5e-07".to_string())
        );
        assert_eq!(parse_int(" 1_000 "), Some(1000));
        assert_eq!(parse_int("-5"), Some(-5));
        assert_eq!(parse_int("slow"), None);
        assert_eq!(parse_int("1__0"), None);
        assert_eq!(parse_float("0.2"), Some(0.2));
        assert_eq!(parse_float("1e3"), Some(1000.0));
        assert_eq!(parse_float("x"), None);
        assert_eq!(parse_float("-inf"), Some(f64::NEG_INFINITY));
        assert_eq!((round(0.5), round(1.5), round(2.5)), (0.0, 2.0, 2.0));
        assert_eq!(utc_fields(0), (1970, 1, 1, 0, 0, 0));
        assert_eq!(utc_fields(1_700_000_000), (2023, 11, 14, 22, 13, 20));
        let stamp = local_time(1_700_000_000.4);
        assert_eq!(stamp.len(), 25, "{stamp}");
    }

    #[test]
    fn bytes_that_are_not_utf8_are_described_as_python_does() {
        let bad = b"d\xe9cision".to_vec();
        let error = std::str::from_utf8(&bad).unwrap_err();
        assert_eq!(utf8_error(&bad, &error), "'utf-8' codec can't decode byte 0xe9 in position 1: invalid continuation byte");
        let bad = b"\xff".to_vec();
        let error = std::str::from_utf8(&bad).unwrap_err();
        assert_eq!(utf8_error(&bad, &error), "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte");
    }
}
