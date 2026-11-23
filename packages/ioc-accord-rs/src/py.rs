//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The Python semantics the merges rely on, over JSON values: truth, `str()`, `repr()`, `==`, `in`, iteration, `strip()` and
//! `round()`.
//!
//! The replies are whatever a model wrote, so the script reads values of any type with Python's rules: `str(5)` is `"5"`, iterating a
//! string gives its characters, `[1] == [1.0]` holds. These functions give the same answers on a `serde_json::Value`, and fail where
//! Python raises, with the text Python 3.15 prints.

use serde_json::{Number, Value};

use crate::errors::{AccordError, Result};

/// Python's truth value of a JSON value: `null`, `false`, `0`, `0.0`, `""`, `[]` and `{}` are false.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => match number(n) {
            Num::Int(i) => i != 0,
            Num::Float(f) => f != 0.0,
        },
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The first truthy value of `a or b or ...`, or None when every one is false or missing.
pub fn or<'a>(values: impl IntoIterator<Item = Option<&'a Value>>) -> Option<&'a Value> {
    values.into_iter().flatten().find(|v| truthy(v))
}

/// The name of the Python type a JSON value decodes to.
pub fn type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => match number(n) {
            Num::Int(_) => "int",
            Num::Float(_) => "float",
        },
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

#[derive(Clone, Copy)]
enum Num {
    Int(i128),
    Float(f64),
}

fn number(n: &Number) -> Num {
    if let Some(i) = n.as_i64() {
        Num::Int(i as i128)
    } else if let Some(u) = n.as_u64() {
        Num::Int(u as i128)
    } else {
        Num::Float(n.as_f64().unwrap_or(f64::NAN))
    }
}

fn numeric(v: &Value) -> Option<Num> {
    match v {
        Value::Bool(b) => Some(Num::Int(*b as i128)),
        Value::Number(n) => Some(number(n)),
        _ => None,
    }
}

/// Python's `str()` of a JSON value.
pub fn str_of(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        _ => repr(v),
    }
}

/// `str(a or b or ... or "")`.
pub fn str_or<'a>(values: impl IntoIterator<Item = Option<&'a Value>>) -> String {
    or(values).map(str_of).unwrap_or_default()
}

/// Python's `repr()` of a JSON value.
pub fn repr(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => match number(n) {
            Num::Int(i) => i.to_string(),
            Num::Float(f) => float_repr(f),
        },
        Value::String(s) => repr_str(s),
        Value::Array(a) => format!("[{}]", a.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => {
            format!("{{{}}}", o.iter().map(|(k, v)| format!("{}: {}", repr_str(k), repr(v))).collect::<Vec<_>>().join(", "))
        }
    }
}

/// Whether Python prints a character as it is in the `repr()` of a string. Python asks the Unicode database (`str.isprintable`); this
/// covers the control characters, the separators and the format characters, which is where a reply can hold one.
fn printable(c: char) -> bool {
    if c.is_ascii() {
        return (' '..='~').contains(&c);
    }
    !(c.is_control()
        || (c.is_whitespace())
        || matches!(c, '\u{ad}' | '\u{600}'..='\u{605}' | '\u{61c}' | '\u{6dd}' | '\u{70f}' | '\u{180e}' | '\u{200b}'..='\u{200f}')
        || matches!(c, '\u{202a}'..='\u{202e}' | '\u{2060}'..='\u{2064}' | '\u{2066}'..='\u{206f}' | '\u{feff}' | '\u{fff9}'..='\u{fffb}')
        || matches!(c, '\u{e000}'..='\u{f8ff}' | '\u{f0000}'..='\u{10ffff}' | '\u{e0001}' | '\u{e0020}'..='\u{e007f}'))
}

/// Python's `repr()` of a string: single quotes unless the text holds one and no double quote.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
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

/// Python's `repr()` of a float, which `json.dumps` writes too: the shortest text that reads back as the same number, in fixed notation
/// between 1e-4 and 1e16 (with `.0` when it is whole), in exponent notation (`1e-05`, `1.5e+16`) outside.
pub fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0.0" } else { "0.0" }.to_string();
    }
    // Rust's `{:e}` is the shortest representation too: `d.ddde<exp>`
    let sci = format!("{:e}", x.abs());
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let exp: i32 = exp.parse().unwrap_or(0);
    let sign = if x < 0.0 { "-" } else { "" };
    let decpt = exp + 1;
    if -4 < decpt && decpt <= 16 {
        let n = digits.len() as i32;
        if decpt <= 0 {
            format!("{sign}0.{}{digits}", "0".repeat((-decpt) as usize))
        } else if decpt >= n {
            format!("{sign}{digits}{}.0", "0".repeat((decpt - n) as usize))
        } else {
            format!("{sign}{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
        }
    } else {
        let rest = if digits.len() > 1 { format!(".{}", &digits[1..]) } else { String::new() };
        let esign = if exp < 0 { '-' } else { '+' };
        format!("{sign}{}{rest}e{esign}{:02}", &digits[..1], exp.abs())
    }
}

/// Python's `round(x, ndigits)` of a float: the decimal nearest to the exact value of `x`, ties to even, read back as a float.
pub fn round(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    // Rust formats with an exact algorithm, rounding a tie of the exact binary value to even, as Python's dtoa does
    format!("{x:.ndigits$}").parse().unwrap_or(x)
}

/// Python's `str.isspace()` of a character: Unicode White_Space, and the four separators U+001C to U+001F.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.strip()`.
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// Python's `==` between JSON values: a number equals the same number of the other kind (`1 == 1.0 == True`), a list and a dict are
/// compared item by item.
pub fn eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| eq(p, q)),
        (Value::Object(x), Value::Object(y)) => x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| eq(v, w))),
        _ => match (numeric(a), numeric(b)) {
            (Some(Num::Int(i)), Some(Num::Int(j))) => i == j,
            (Some(Num::Float(f)), Some(Num::Float(g))) => f == g,
            (Some(Num::Int(i)), Some(Num::Float(f))) | (Some(Num::Float(f)), Some(Num::Int(i))) => {
                f.is_finite() && f.fract() == 0.0 && f.abs() < 1e38 && f as i128 == i
            }
            _ => false,
        },
    }
}

/// Whether a value can be a key of a Python dict or an element of a set: a list and a dict cannot.
pub fn hashable(v: &Value) -> bool {
    !matches!(v, Value::Array(_) | Value::Object(_))
}

/// Python's `for x in v`: the items of a list, the characters of a string, the keys of a dict.
pub fn iter(v: &Value) -> Result<Vec<Value>> {
    match v {
        Value::Array(a) => Ok(a.clone()),
        Value::String(s) => Ok(s.chars().map(|c| Value::String(c.to_string())).collect()),
        Value::Object(o) => Ok(o.keys().map(|k| Value::String(k.clone())).collect()),
        _ => Err(AccordError::type_error(format!("'{}' object is not iterable", type_name(v)))),
    }
}

/// `list(v or [])`.
pub fn list_or_empty(v: Option<&Value>) -> Result<Vec<Value>> {
    match v {
        Some(v) if truthy(v) => iter(v),
        _ => Ok(Vec::new()),
    }
}

/// Python's `len(v)`.
pub fn len(v: &Value) -> Result<usize> {
    match v {
        Value::Array(a) => Ok(a.len()),
        Value::String(s) => Ok(s.chars().count()),
        Value::Object(o) => Ok(o.len()),
        _ => Err(AccordError::type_error(format!("object of type '{}' has no len()", type_name(v)))),
    }
}

/// Python's `item in container`.
pub fn contains(container: &Value, item: &Value) -> Result<bool> {
    match container {
        Value::Array(a) => Ok(a.iter().any(|x| eq(x, item))),
        Value::String(s) => match item {
            Value::String(sub) => Ok(s.contains(sub.as_str())),
            _ => Err(AccordError::type_error(format!("'in <string>' requires string as left operand, not {}", type_name(item)))),
        },
        Value::Object(o) => {
            if !hashable(item) {
                let t = type_name(item);
                return Err(AccordError::type_error(format!("cannot use '{t}' as a dict key (unhashable type: '{t}')")));
            }
            Ok(item.as_str().is_some_and(|k| o.contains_key(k)))
        }
        _ => Err(AccordError::type_error(format!("argument of type '{}' is not a container or iterable", type_name(container)))),
    }
}

/// `v.get(key)` on a value Python expects to be a dict: an `AttributeError` when it is not.
pub fn get<'a>(v: &'a Value, key: &str) -> Result<Option<&'a Value>> {
    match v {
        Value::Object(o) => Ok(o.get(key)),
        _ => Err(attribute_error(v, "get")),
    }
}

/// `v[key]` on a value Python expects to be a dict: a `KeyError` when the key is missing.
pub fn item<'a>(v: &'a Value, key: &str) -> Result<&'a Value> {
    match v {
        Value::Object(o) => o.get(key).ok_or_else(|| AccordError::python("KeyError", repr_str(key))),
        Value::Array(_) | Value::String(_) => {
            Err(AccordError::type_error(format!("{} indices must be integers or slices, not str", type_name(v))))
        }
        _ => Err(AccordError::type_error(format!("'{}' object is not subscriptable", type_name(v)))),
    }
}

/// `v.strip()` on a value Python expects to be a string.
pub fn strip_str(v: &Value) -> Result<&str> {
    v.as_str().map(strip).ok_or_else(|| attribute_error(v, "strip"))
}

/// A value Python expects to be a string, as one.
pub fn as_str<'a>(v: &'a Value, method: &str) -> Result<&'a str> {
    v.as_str().ok_or_else(|| attribute_error(v, method))
}

/// The `AttributeError` of calling `method` on a value without it.
pub fn attribute_error(v: &Value, method: &str) -> AccordError {
    AccordError::python("AttributeError", format!("'{}' object has no attribute '{method}'", type_name(v)))
}

/// The text `json.dumps` writes for a dict key that is not a string: `1`, `1.5`, `true`, `null`.
pub fn key_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        _ => repr(v),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn truth_follows_python() {
        for v in [json!(null), json!(false), json!(0), json!(0.0), json!(""), json!([]), json!({})] {
            assert!(!truthy(&v), "{v}");
        }
        for v in [json!(true), json!(-1), json!(0.1), json!(" "), json!([0]), json!({"a": null})] {
            assert!(truthy(&v), "{v}");
        }
    }

    #[test]
    fn str_and_repr_follow_python() {
        assert_eq!(str_of(&json!(5)), "5");
        assert_eq!(str_of(&json!(true)), "True");
        assert_eq!(str_of(&json!(null)), "None");
        assert_eq!(str_of(&json!(1.0)), "1.0");
        assert_eq!(str_of(&json!(["a", 1, {"k": "it's"}])), "['a', 1, {'k': \"it's\"}]");
        assert_eq!(repr_str("a'b\"c\n\u{7}é"), "'a\\'b\"c\\n\\x07é'");
        assert_eq!(repr_str("\u{a0}\u{200b}"), "'\\xa0\\u200b'");
    }

    #[test]
    fn floats_print_like_python() {
        let cases = [
            (1.0, "1.0"),
            (0.5, "0.5"),
            (0.1, "0.1"),
            (100.0, "100.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (1e15, "1000000000000000.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.2345e-7, "1.2345e-07"),
            (-2.5, "-2.5"),
            (-0.0, "-0.0"),
            (1e100, "1e+100"),
            (123456789.125, "123456789.125"),
        ];
        for (x, want) in cases {
            assert_eq!(float_repr(x), want, "{x}");
        }
    }

    #[test]
    fn round_is_half_even_on_the_exact_value() {
        assert_eq!(round(0.6875, 3), 0.688);
        assert_eq!(round(0.0625, 3), 0.062);
        assert_eq!(round(2.5, 0), 2.0);
        assert_eq!(round(2.675, 2), 2.67); // 2.675 is just below the tie
        assert_eq!(round(0.00015, 4), 0.0001);
    }

    #[test]
    fn strip_knows_the_separators_python_counts_as_space() {
        assert_eq!(strip("\u{1c}\u{a0} x \u{3000}\u{1f}"), "x");
        assert_eq!(strip("\u{200b}x"), "\u{200b}x");
    }

    #[test]
    fn equality_and_membership_follow_python() {
        assert!(eq(&json!(1), &json!(1.0)));
        assert!(eq(&json!(true), &json!(1)));
        assert!(eq(&json!([1, {"a": 2}]), &json!([1.0, {"a": 2.0}])));
        assert!(!eq(&json!("1"), &json!(1)));
        assert!(contains(&json!(["a", 1]), &json!(1.0)).unwrap());
        assert!(contains(&json!("abc"), &json!("bc")).unwrap());
        assert!(contains(&json!({"k": 1}), &json!("k")).unwrap());
        assert_eq!(
            contains(&json!("abc"), &json!(1)).unwrap_err().message(),
            "TypeError: 'in <string>' requires string as left operand, not int"
        );
        assert_eq!(
            contains(&json!(5), &json!(1)).unwrap_err().message(),
            "TypeError: argument of type 'int' is not a container or iterable"
        );
        assert_eq!(
            contains(&json!({}), &json!([1])).unwrap_err().message(),
            "TypeError: cannot use 'list' as a dict key (unhashable type: 'list')"
        );
    }

    #[test]
    fn iteration_follows_python() {
        assert_eq!(iter(&json!("ab")).unwrap(), vec![json!("a"), json!("b")]);
        assert_eq!(iter(&json!({"x": 1, "y": 2})).unwrap(), vec![json!("x"), json!("y")]);
        assert_eq!(iter(&json!(3)).unwrap_err().message(), "TypeError: 'int' object is not iterable");
        assert_eq!(len(&json!(null)).unwrap_err().message(), "TypeError: object of type 'NoneType' has no len()");
        assert_eq!(list_or_empty(Some(&json!(0))).unwrap(), Vec::<Value>::new());
    }
}
