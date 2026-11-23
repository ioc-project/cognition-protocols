//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Text: the whitespace of the protocols, and the tolerance of a reader for what a model writes around a message (a markdown fence,
//! prose before and after).
//!
//! Every function here returns a slice of its input where it can: what a reader hands on is the sender's own text, byte for byte.

/// Whitespace is these four characters and no other: a text is trimmed of them, so that every implementation reads the same text,
/// whatever its language. (`str::trim` strips every Unicode space, which is more.)
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

/// `text` without the markdown fence around it, the whitespace being what `is_space` says: the text is trimmed, then an opening
/// "```" (followed by `lang` or not) and the whitespace after it are removed, and the text is trimmed again and loses a closing "```"
/// (with the line break before it, if any). A fence that is not at the start or the end of the text is left alone.
///
/// This is Python's `re.sub(r"\n?```\s*$", "", re.sub(r"^```(?:<lang>)?\s*\n?", "", text.strip()).strip())`, with `is_space` the
/// whitespace of Python's `strip` and `\s`.
#[must_use]
pub fn strip_fence<'a>(text: &'a str, lang: &str, is_space: impl Fn(char) -> bool + Copy) -> &'a str {
    let mut s = text.trim_matches(is_space);
    if let Some(rest) = s.strip_prefix("```") {
        let rest = rest.strip_prefix(lang).unwrap_or(rest);
        s = rest.trim_start_matches(is_space);
    }
    // the text is trimmed, so the closing fence can only be its last characters
    let s = s.trim_matches(is_space);
    s.strip_suffix("\n```").or_else(|| s.strip_suffix("```")).unwrap_or(s)
}

/// The part of `text` from its first `open` to its last `close`, when that one comes after it: an object with prose around it.
/// `re.search(r"\{.*\}", text, re.DOTALL)` with `open` `{` and `close` `}`.
#[must_use]
pub fn outermost(text: &str, open: char, close: char) -> Option<&str> {
    let start = text.find(open)?;
    let end = text.rfind(close)?;
    (end > start).then(|| &text[start..end + close.len_utf8()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn py_space(c: char) -> bool {
        c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
    }

    #[test]
    fn only_the_four_characters_are_whitespace() {
        assert_eq!(trim_ws(" \t\r\n a b \n"), "a b");
        assert_eq!(trim_ws("\u{a0}a\u{a0}"), "\u{a0}a\u{a0}");
        assert_eq!(squeeze("  a \t\n b\r\nc  "), "a b c");
        assert_eq!(squeeze("a\u{a0} b"), "a\u{a0} b");
        assert_eq!(squeeze(" \n "), "");
    }

    #[test]
    fn a_fence_around_the_text_is_removed() {
        assert_eq!(strip_fence("  ```json\n{\"a\": 1}\n```  ", "json", py_space), "{\"a\": 1}");
        assert_eq!(strip_fence("```\n[1]```", "json", py_space), "[1]");
        assert_eq!(strip_fence("x````", "json", py_space), "x`");
        assert_eq!(strip_fence("```xml <a/>\n```", "xml", py_space), "<a/>");
        assert_eq!(strip_fence("\u{1c}```json\u{a0}{}\u{3000}", "json", py_space), "{}");
        assert_eq!(strip_fence("say ```json {} ``` ok", "json", py_space), "say ```json {} ``` ok");
        assert_eq!(strip_fence("", "json", py_space), "");
    }

    #[test]
    fn the_outermost_pair_is_taken_from_prose() {
        assert_eq!(outermost("Sure: {\"a\": {\"b\": 1}} ok", '{', '}'), Some("{\"a\": {\"b\": 1}}"));
        assert_eq!(outermost("} {", '{', '}'), None);
        assert_eq!(outermost("{", '{', '}'), None);
        assert_eq!(outermost("no", '{', '}'), None);
        assert_eq!(outermost("«a»", '«', '»'), Some("«a»"));
    }
}
