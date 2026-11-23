//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! XML text: escaping, the root start tag of a document, the message in a text, and the integrity check of a document.
//!
//! The check works on the text of a document, exactly as it was sent: a document is never parsed and written again before its check
//! is computed or verified.
//!
//! The root start tag is read here, once, for everything that looks at a document before it is parsed. `start_tag` finds its end and
//! reads its attributes with an XML parser, so they are exactly what the parser reads from the whole document: no attribute can be
//! hidden in the value of another one.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::{Captures, Regex};
use roxmltree::Document;

use crate::check::short_digest;
use crate::message::MessageError;
use crate::text::{squeeze, trim_ws};

// What a start tag is made of. A name has none of the characters that end it, a value is quoted with either kind of quote and holds no `<`.
const BLANK: &str = r"[ \t\r\n]";
const NAME: &str = r#"[^ \t\r\n"'<>/=]+"#;
const VALUE: &str = r#""[^"<]*"|'[^'<]*'"#;

static START_TAG_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!("^<({NAME})((?:{BLANK}+{NAME}{BLANK}*={BLANK}*(?:{VALUE}))*){BLANK}*(/?)>")).unwrap());
static ATTR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!("{BLANK}+({NAME}){BLANK}*={BLANK}*({VALUE})")).unwrap());
static TEXT_RUN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r">([^<]*)<").unwrap());

/// `v` as XML text: `<`, `>` and `&` are escaped, a `"` is not.
#[must_use]
pub fn esc_text(v: &str) -> String {
    v.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// `v` as the value of an attribute: `<`, `>`, `&` and `"` are escaped.
#[must_use]
pub fn esc_attr(v: &str) -> String {
    esc_text(v).replace('"', "&quot;")
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

/// Where the messages of one protocol are found in a text: their root elements are named `<prefix><a-z and ->`, and a model may hand
/// one over inside a markdown fence (marked `lang` or not) or with prose around it. `label` names the protocol in the errors.
#[derive(Debug, Clone)]
pub struct Locator {
    label: String,
    marker: String,
    open: Regex,
    fence: Regex,
}

impl Locator {
    /// The locator of the messages whose root element names start with `prefix` (`concord-`), written `label` (`CONCORD`) in the
    /// errors, in a fence marked `lang` (`xml`) or not.
    pub fn new(prefix: &str, label: &str, lang: &str) -> Self {
        let (prefix_re, lang_re) = (regex::escape(prefix), regex::escape(lang));
        Locator {
            label: label.to_string(),
            marker: format!("<{prefix}"),
            open: Regex::new(&format!(r"<({prefix_re}[a-z-]+)\b")).expect("an escaped prefix"),
            fence: Regex::new(&format!(r"(?s)```(?:{lang_re})?[ \t\r\n]*\n(.*?)\n```")).expect("an escaped language"),
        }
    }

    /// The message in `text`, taken unchanged, from its first `<` to its last `>`. When a fenced block holds a message, the first such
    /// block is where it is looked for; else the whole text. The text returned is the sender's own, byte for byte.
    ///
    /// # Errors
    ///
    /// `NotFound` when `text` holds no message of the protocol, `Malformed` when its first tag is not well-formed, `CutOff` when the
    /// message ends inside its first tag or is not closed.
    pub fn locate<'t>(&self, text: &'t str) -> Result<&'t str, MessageError> {
        let label = &self.label;
        let mut s = trim_ws(text);
        if let Some(fenced) = self.fence.captures_iter(s).filter_map(|c| c.get(1)).find(|g| g.as_str().contains(&self.marker)) {
            s = trim_ws(fenced.as_str());
        }
        let Some(m) = self.open.captures(s) else {
            return Err(MessageError::not_found(format!("no {label} message in the text")));
        };
        let (start, name) = (m.get(0).expect("the whole match").start(), m.get(1).expect("the name").as_str());
        let Some(tag) = start_tag(&s[start..]) else {
            return Err(if s[start..].contains('>') {
                MessageError::malformed(format!("the first tag of the {label} message is not well-formed"))
            } else {
                MessageError::cut_off(format!("the {label} message is cut off inside its first tag"))
            });
        };
        let tag_end = start + tag.end - 1;
        if tag.empty {
            return Ok(&s[start..=tag_end]);
        }
        let close = s.rfind(&format!("</{name}")).filter(|c| *c > tag_end);
        let end = close.and_then(|c| s[c..].find('>').map(|i| c + i));
        match end {
            Some(end) => Ok(&s[start..=end]),
            None => Err(MessageError::cut_off(format!("the <{name}> message is not closed"))),
        }
    }
}

fn squeeze_run(m: &Captures) -> String {
    format!(">{}<", squeeze(&m[1]))
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

/// The `check` of `doc`: the fingerprint (`check::short_digest`) of its canonical text.
pub fn compute_check(doc: &str) -> String {
    short_digest(&canonical_text(doc))
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
/// A `Malformed` `MessageError` when the document does not start with a tag.
pub fn with_check(doc_without_check: &str) -> Result<String, MessageError> {
    let doc = trim_ws(doc_without_check);
    let start = START_TAG_RE.find(doc).ok_or_else(|| MessageError::malformed("a document starts with a tag"))?;
    let tag = start.as_str();
    let closing = if tag.ends_with("/>") { "/>" } else { ">" };
    Ok(format!("{} check=\"{}\"{}{}", &tag[..tag.len() - closing.len()], compute_check(doc), closing, &doc[start.end()..]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::ErrorKind;
    use sha2::{Digest, Sha256};

    const DOC: &str = "<concord-score version=\"0.3.0\" from=\"cost\" run_id=\"R\" uuid=\"U\" check=\"deadbeef\">
  <score candidate_id=\"seed-a\">
    <raw>82.0</raw>
    <reasoning>cost   is   fine,
      \"quoted\" &lt; 5</reasoning>
  </score>
</concord-score>";

    #[test]
    fn esc_text_keeps_quotes_and_esc_attr_escapes_them() {
        assert_eq!(esc_text("a < b & \"c\" > d"), "a &lt; b &amp; \"c\" &gt; d");
        assert_eq!(esc_attr("a < b & \"c\" > d"), "a &lt; b &amp; &quot;c&quot; &gt; d");
        assert_eq!(esc_attr("&amp;"), "&amp;amp;");
    }

    #[test]
    fn canonical_text_drops_check_and_squeezes_text_only() {
        assert_eq!(
            canonical_text(DOC),
            concat!(
                "<concord-score version=\"0.3.0\" from=\"cost\" run_id=\"R\" uuid=\"U\"><score candidate_id=\"seed-a\"><raw>82.0</raw>",
                "<reasoning>cost is fine, \"quoted\" &lt; 5</reasoning></score></concord-score>"
            )
        );
        assert_eq!(canonical_text("<a z=\"1\"  b='2' check=\"x\">&amp; &quot;</a>"), "<a z=\"1\"  b='2'>&amp; &quot;</a>");
        assert_eq!(canonical_text("<e check=\"x\"/>"), "<e/>");
        assert_eq!(canonical_text("\n<a>\n</a>\n"), "<a></a>");
    }

    #[test]
    fn the_check_is_the_short_digest_of_the_canonical_text() {
        let digest = Sha256::digest(canonical_text(DOC).as_bytes());
        let want: String = digest.iter().map(|b| format!("{b:02x}")).collect::<String>()[..8].to_string();
        assert_eq!(compute_check(DOC), want);
        let base = compute_check(DOC);
        assert_eq!(compute_check(&DOC.replace("check=\"deadbeef\"", "check=\"other\"")), base);
        assert_eq!(compute_check(&DOC.replace("\n  ", "\n\n      ")), base);
        assert_ne!(compute_check(&DOC.replace("82.0", "28.0")), base);
    }

    #[test]
    fn with_check_adds_a_check_that_verifies() {
        let bare = DOC.replace(" check=\"deadbeef\"", "");
        let doc = with_check(&bare).unwrap();
        assert_eq!(check_of(&doc), Some(compute_check(&bare)));
        assert!(verify_check(&doc));
        assert!(!verify_check(&doc.replace("82.0", "81.0")));
        assert!(!verify_check(&bare));
        assert!(with_check("<c a=\"1\"/>").unwrap().ends_with(&format!("check=\"{}\"/>", compute_check("<c a=\"1\"/>"))));
        let err = with_check("words").unwrap_err();
        assert_eq!((err.kind(), err.message()), (ErrorKind::Malformed, "a document starts with a tag"));
    }

    #[test]
    fn a_check_inside_the_value_of_another_attribute_is_not_the_check() {
        let doc = r#"<x a='b check="c"' check="00000000"><y>t</y></x>"#;
        assert_eq!(check_of(doc), Some("00000000".to_string()));
        assert_eq!(canonical_text(doc), r#"<x a='b check="c"'><y>t</y></x>"#);
        let hidden_only = r#"<x a='b check="c"'><y>t</y></x>"#;
        assert_eq!(check_of(hidden_only), None);
        assert_eq!(canonical_text(hidden_only), hidden_only);
    }

    #[test]
    fn the_start_tag_is_read_as_an_xml_parser_reads_it() {
        let t = start_tag(r#"<a x='1' y = "&#97;lice" z="a&gt;b" w='/>' v=">">text</a>"#).unwrap();
        assert_eq!((t.name.as_str(), t.empty), ("a", false));
        let get = |k: &str| t.attrs.get(k).map(String::as_str);
        assert_eq!((get("x"), get("y"), get("z"), get("w"), get("v")), (Some("1"), Some("alice"), Some("a>b"), Some("/>"), Some(">")));
        let empty = start_tag(r#"<a x="1" />"#).unwrap();
        assert!(empty.empty && empty.attrs["x"] == "1" && empty.end == r#"<a x="1" />"#.len());
        let ns = start_tag(r#"<a xmlns:p="urn:p" p:k="v">"#).unwrap();
        assert_eq!(ns.attrs.get("{urn:p}k").map(String::as_str), Some("v"));
        for bad in [r#"<a x="1" x="2">"#, r#"<a x=1>"#, r#"<a x="a<b">"#, r#"<a x="&nope;">"#, r#"<a x="1""#, "words", "", " <a>"] {
            assert!(start_tag(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn a_message_is_located_in_a_fence_or_in_prose_and_returned_unchanged() {
        let loc = Locator::new("p-", "P", "xml");
        assert_eq!(loc.locate("  <p-a k=\"v\">\n x </p-a>  ").unwrap(), "<p-a k=\"v\">\n x </p-a>");
        assert_eq!(loc.locate("Here it is: <p-a/> thanks").unwrap(), "<p-a/>");
        assert_eq!(loc.locate("intro\n```xml\n<p-b>t</p-b>\n```\nbye").unwrap(), "<p-b>t</p-b>");
        assert_eq!(loc.locate("```\n<other/>\n```\n```\n<p-b>t</p-b>\n```").unwrap(), "<p-b>t</p-b>");
        // the last closing tag of the root name ends it
        assert_eq!(loc.locate("<p-a><p-a></p-a></p-a> tail").unwrap(), "<p-a><p-a></p-a></p-a>");
        // a name that only starts like the prefix is not one
        assert!(loc.locate("<p-A/>").is_err());
        let cases = [
            ("no message here", ErrorKind::NotFound, "no P message in the text"),
            ("<p-a x=1>t</p-a>", ErrorKind::Malformed, "the first tag of the P message is not well-formed"),
            ("<p-a x=\"1", ErrorKind::CutOff, "the P message is cut off inside its first tag"),
            ("<p-a>text", ErrorKind::CutOff, "the <p-a> message is not closed"),
        ];
        for (text, kind, message) in cases {
            let err = loc.locate(text).unwrap_err();
            assert_eq!((err.kind(), err.message()), (kind, message), "{text:?}");
        }
    }

    #[test]
    fn a_prefix_is_text_not_a_pattern() {
        let loc = Locator::new("a.", "A", "x+");
        assert!(loc.locate("<ab-c/>").is_err());
        assert_eq!(loc.locate("<a.b/>").unwrap(), "<a.b/>");
        assert_eq!(loc.locate("```x+\n<a.b/>\n```").unwrap(), "<a.b/>");
    }
}
