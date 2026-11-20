//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! XML text helpers: escaping, the start tag of a document, and its integrity check.
//!
//! The check works on the text of a document, exactly as it was sent: a document is never parsed and written again before its
//! check is computed or verified.
//!
//! The root start tag is read here, once, for everything that looks at a document before it is parsed (the `check`, and the `from` and
//! `uuid` that decide whether a message is dropped). `start_tag` finds its end and reads its attributes with an XML parser, so they are
//! exactly what the parser reads from the whole document: no attribute can be hidden in the value of another one.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::{Captures, Regex};
use roxmltree::Document;
use sha2::{Digest, Sha256};

use crate::errors::{ConcordError, Result};
use crate::protocol::{squeeze, trim_ws};

// What a start tag is made of. A name has none of the characters that end it, a value is quoted with either kind of quote and holds no `<`.
const BLANK: &str = r"[ \t\r\n]";
const NAME: &str = r#"[^ \t\r\n"'<>/=]+"#;
const VALUE: &str = r#""[^"<]*"|'[^'<]*'"#;

static START_TAG_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!("^<({NAME})((?:{BLANK}+{NAME}{BLANK}*={BLANK}*(?:{VALUE}))*){BLANK}*(/?)>")).unwrap());
static ATTR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!("{BLANK}+({NAME}){BLANK}*={BLANK}*({VALUE})")).unwrap());
static TEXT_RUN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r">([^<]*)<").unwrap());

/// `v` as XML text: `<`, `>` and `&` are escaped, a `"` is not.
pub fn esc_text(v: &str) -> String {
    v.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// `v` as the value of an attribute: `<`, `>`, `&` and `"` are escaped.
pub fn esc_attr(v: &str) -> String {
    esc_text(v).replace('"', "&quot;")
}

fn squeeze_run(m: &Captures) -> String {
    format!(">{}<", squeeze(&m[1]))
}

/// The root start tag of a document: its name, where it ends, whether it closes itself, and its attributes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartTag {
    /// The name of the element.
    pub name: String,
    /// The index in the document where the content of the element begins, which is just after the `>`.
    pub end: usize,
    /// Whether the tag ends with `/>`.
    pub empty: bool,
    /// The attributes as an XML parser reads them: quotes of either kind, character references and line breaks included. A name in a
    /// namespace is written `{namespace}name`.
    pub attrs: HashMap<String, String>,
}

/// The root start tag of `doc`, which must begin with it, or None when `doc` does not begin with a well-formed start tag (a repeated
/// attribute is not well-formed).
pub fn start_tag(doc: &str) -> Option<StartTag> {
    let caps = START_TAG_RE.captures(doc)?;
    let whole = caps.get(0)?;
    let name = caps.get(1)?.as_str();
    let empty = !caps.get(3)?.as_str().is_empty();
    let probe = if empty { whole.as_str().to_string() } else { format!("{}</{name}>", whole.as_str()) };
    let tree = Document::parse(&probe).ok()?;
    let attrs = tree
        .root_element()
        .attributes()
        .map(|a| {
            let key = a.namespace().map_or_else(|| a.name().to_string(), |ns| format!("{{{ns}}}{}", a.name()));
            (key, a.value().to_string())
        })
        .collect();
    Some(StartTag { name: name.to_string(), end: whole.end(), empty, attrs })
}

/// The text the check is computed over: the document without its `check` attribute (and the space before it), with each stretch of
/// text between a `>` and the next `<` made of whitespace only removed, and each other one with its runs of whitespace made one
/// space and none left at either end. Nothing else changes.
pub fn canonical_text(doc: &str) -> String {
    let doc = trim_ws(doc);
    let doc = match START_TAG_RE.captures(doc) {
        Some(caps) => {
            let (whole, region) = (caps.get(0).expect("the whole match"), caps.get(2).expect("the attributes"));
            let mut tag = whole.as_str().to_string();
            // the first attribute that is named `check`, by its place in the tag: a value that holds ` check="…"` is not one
            if let Some(a) = ATTR_RE.captures_iter(region.as_str()).find(|a| &a[1] == "check") {
                let name_start = region.start() + a.get(1).expect("the name").start();
                tag.replace_range(name_start - 1..region.start() + a.get(0).expect("the attribute").end(), "");
            }
            format!("{tag}{}", &doc[whole.end()..])
        }
        None => doc.to_string(),
    };
    TEXT_RUN_RE.replace_all(&doc, |m: &Captures| squeeze_run(m)).into_owned()
}

/// The `check` of `doc`: the first 8 lowercase hex characters of the SHA-256 of its canonical text, as UTF-8.
pub fn compute_check(doc: &str) -> String {
    let digest = Sha256::digest(canonical_text(doc).as_bytes());
    digest.iter().take(4).map(|b| format!("{b:02x}")).collect()
}

/// The value of the `check` attribute on the root element of `doc`, or None when it has none.
pub fn check_of(doc: &str) -> Option<String> {
    start_tag(trim_ws(doc))?.attrs.remove("check")
}

/// True when `doc` carries a `check` and it is the one `compute_check` gives.
pub fn verify_check(doc: &str) -> bool {
    check_of(doc).is_some_and(|claimed| claimed == compute_check(doc))
}

/// `doc_without_check`, whose root start tag has no `check`, with the attribute added as its last attribute.
///
/// # Errors
///
/// `ConcordError::InvalidMessage` when the document does not start with a tag.
pub fn with_check(doc_without_check: &str) -> Result<String> {
    let doc = trim_ws(doc_without_check);
    let start = START_TAG_RE.find(doc).ok_or_else(|| ConcordError::invalid_message("a document starts with a tag"))?;
    let tag = start.as_str();
    let closing = if tag.ends_with("/>") { "/>" } else { ">" };
    Ok(format!("{} check=\"{}\"{}{}", &tag[..tag.len() - closing.len()], compute_check(doc), closing, &doc[start.end()..]))
}
