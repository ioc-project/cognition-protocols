//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! How close two texts are: the string similarity of the script, `difflib.SequenceMatcher(None, a, b).ratio()` on the normalized
//! texts.
//!
//! The script can use the cosine of sentence-transformers embeddings instead when the package is installed. That mode is not ported:
//! this is the script with `ACCORD_EMBEDDING=off`, which is what it does anyway without sentence-transformers.
//!
//! `SequenceMatcher` is a port of Python's, on characters (Unicode scalar values) as Python's is on a `str`: the same longest match
//! with the same ties (the lowest index in `a`, then in `b`), and the same "autojunk" heuristic, which ignores the characters of `b` that
//! are too frequent when `b` has at least 200 characters. Its ratio is the same float as Python's.

use std::collections::HashMap;

use crate::py::is_space;

/// The tokens of a text, `[a-z0-9]+` of its lowercase form, joined with one space.
pub fn normalize_text(text: &str) -> String {
    let lower = text.to_lowercase();
    let mut out = String::with_capacity(lower.len());
    let mut in_token = false;
    for c in lower.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if !in_token && !out.is_empty() {
                out.push(' ');
            }
            out.push(c);
            in_token = true;
        } else {
            in_token = false;
        }
    }
    out
}

/// The similarity of two texts in `[0, 1]`: 1 when both normalize to the same text (both empty included), 0 when only one of them is
/// empty, the ratio of `SequenceMatcher` on the normalized texts otherwise.
pub fn similarity(a: &str, b: &str) -> f64 {
    let (na, nb) = (normalize_text(a), normalize_text(b));
    if na.is_empty() && nb.is_empty() {
        return 1.0;
    }
    if na.is_empty() || nb.is_empty() {
        return 0.0;
    }
    if na == nb {
        return 1.0;
    }
    ratio(&na, &nb)
}

/// `difflib.SequenceMatcher(None, a, b).ratio()`.
pub fn ratio(a: &str, b: &str) -> f64 {
    SequenceMatcher::new(a, b).ratio()
}

/// A match: `a[a..a + size] == b[b..b + size]`, indices in characters.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Match {
    pub a: usize,
    pub b: usize,
    pub size: usize,
}

/// Python's `difflib.SequenceMatcher` with no junk function and `autojunk=True`, on the characters of two strings.
#[derive(Debug, Clone)]
pub struct SequenceMatcher {
    a: Vec<char>,
    b: Vec<char>,
    /// Where each character of `b` is, the popular ones left out.
    b2j: HashMap<char, Vec<usize>>,
}

impl SequenceMatcher {
    pub fn new(a: &str, b: &str) -> Self {
        let a: Vec<char> = a.chars().collect();
        let b: Vec<char> = b.chars().collect();
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (i, c) in b.iter().enumerate() {
            b2j.entry(*c).or_default().push(i);
        }
        // autojunk: in a `b` of 200 characters or more, a character found more than 1% of the time (plus one) is popular, and is no
        // longer a place a match can start. It is not junk: a match can still be extended over it.
        let n = b.len();
        if n >= 200 {
            let ntest = n / 100 + 1;
            b2j.retain(|_, idxs| idxs.len() <= ntest);
        }
        SequenceMatcher { a, b, b2j }
    }

    /// The longest matching block in `a[alo..ahi]` and `b[blo..bhi]`; of the longest, the one that starts first in `a`, then in `b`.
    /// Its size is 0 when there is none.
    pub fn find_longest_match(&self, alo: usize, ahi: usize, blo: usize, bhi: usize) -> Match {
        let (a, b) = (&self.a, &self.b);
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        // j2len[j] is the length of the longest match ending at a[i - 1] and b[j]
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            if let Some(js) = self.b2j.get(c) {
                for &j in js {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = j.checked_sub(1).and_then(|p| j2len.get(&p)).copied().unwrap_or(0) + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        // Extend the match over equal characters on each side: the popular ones, which could not start a match, are the ones this adds.
        // There is no junk, so Python's second pair of loops (over junk) never runs.
        while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] == b[bestj + bestsize] {
            bestsize += 1;
        }
        Match { a: besti, b: bestj, size: bestsize }
    }

    /// The matching blocks, in order, adjacent ones joined, without Python's final block of size 0.
    pub fn matching_blocks(&self) -> Vec<Match> {
        let mut queue = vec![(0, self.a.len(), 0, self.b.len())];
        let mut blocks = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let m = self.find_longest_match(alo, ahi, blo, bhi);
            if m.size > 0 {
                blocks.push(m);
                if alo < m.a && blo < m.b {
                    queue.push((alo, m.a, blo, m.b));
                }
                if m.a + m.size < ahi && m.b + m.size < bhi {
                    queue.push((m.a + m.size, ahi, m.b + m.size, bhi));
                }
            }
        }
        blocks.sort();
        let mut joined: Vec<Match> = Vec::with_capacity(blocks.len());
        for m in blocks {
            match joined.last_mut() {
                Some(last) if last.a + last.size == m.a && last.b + last.size == m.b => last.size += m.size,
                _ => joined.push(m),
            }
        }
        joined
    }

    /// `2 * M / T`, M the characters matched and T the characters of both strings; 1 when both are empty.
    pub fn ratio(&self) -> f64 {
        let matches: usize = self.matching_blocks().iter().map(|m| m.size).sum();
        let length = self.a.len() + self.b.len();
        if length == 0 {
            return 1.0;
        }
        2.0 * matches as f64 / length as f64
    }
}

/// `re.sub(r"\s+", "", text)`: the text without its whitespace, Python's whitespace.
pub(crate) fn without_space(text: &str) -> String {
    text.chars().filter(|c| !is_space(*c)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_keeps_ascii_tokens_of_the_lowercase_text() {
        assert_eq!(normalize_text("  Error-Rate (p95), ms!  "), "error rate p95 ms");
        assert_eq!(normalize_text("Été KELVIN \u{212a}"), "t kelvin k");
        assert_eq!(normalize_text(""), "");
    }

    #[test]
    fn ratio_matches_difflib_on_known_values() {
        // values printed by Python 3: SequenceMatcher(None, a, b).ratio()
        assert_eq!(ratio("abcd", "bcde"), 0.75);
        assert_eq!(ratio("", ""), 1.0);
        assert_eq!(ratio("abc", ""), 0.0);
        assert_eq!(ratio("tide", "diet"), 0.25);
        assert_eq!(ratio("diet", "tide"), 0.5);
        assert_eq!(ratio("qabxcd", "abycdf"), 2.0 * 4.0 / 12.0);
    }

    #[test]
    fn longest_match_prefers_the_first_in_a_then_in_b() {
        let m = SequenceMatcher::new(" abcd", "abcd abcd");
        assert_eq!(m.find_longest_match(0, 5, 0, 9), Match { a: 0, b: 4, size: 5 });
        let m = SequenceMatcher::new("ab", "abab");
        assert_eq!(m.find_longest_match(0, 2, 0, 4), Match { a: 0, b: 0, size: 2 });
    }

    #[test]
    fn autojunk_applies_from_200_characters_of_b() {
        // with 200 characters, "a" (199 times) is popular: no match can start on it, but one can extend over it (values printed by
        // Python)
        let b = format!("{}b", "a".repeat(199));
        let m = SequenceMatcher::new("aab", &b);
        assert_eq!(m.find_longest_match(0, 3, 0, 200), Match { a: 0, b: 197, size: 3 });
        let only_a = SequenceMatcher::new("aaa", &"a".repeat(200));
        assert_eq!(only_a.ratio(), 0.029556650246305417);
        let short = SequenceMatcher::new("aaa", &"a".repeat(199));
        assert_eq!(short.ratio(), 2.0 * 3.0 / 202.0);
    }

    #[test]
    fn similarity_short_cuts_empty_and_equal_texts() {
        assert_eq!(similarity("", "  !"), 1.0);
        assert_eq!(similarity("x", "!"), 0.0);
        assert_eq!(similarity("Error rate", "error-RATE"), 1.0);
        assert_eq!(similarity("errors per minute", "errors per second"), ratio("errors per minute", "errors per second"));
    }
}
