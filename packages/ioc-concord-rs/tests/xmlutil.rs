//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The XML helpers: escaping, and the integrity check computed on the text of a document.
// Python `esc_text(5) == "5"` has no Rust equivalent: `esc_text` takes a `&str`, so it is never given a number.

use ioc_concord::xmlutil::*;
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
}

#[test]
fn canonical_text_leaves_attribute_order_quotes_and_entities_alone() {
    assert_eq!(canonical_text("<a z=\"1\"  b='2' check=\"x\">&amp; &quot;</a>"), "<a z=\"1\"  b='2'>&amp; &quot;</a>");
    assert_eq!(canonical_text("<e check=\"x\"/>"), "<e/>");
    assert_eq!(canonical_text("\n<a>\n</a>\n"), "<a></a>");
}

#[test]
fn compute_check_is_8_lowercase_hex_of_the_sha256_of_the_canonical_text() {
    let digest = Sha256::digest(canonical_text(DOC).as_bytes());
    let want: String = digest.iter().map(|b| format!("{b:02x}")).collect::<String>()[..8].to_string();
    assert_eq!(compute_check(DOC), want);
    assert_eq!(want.len(), 8);
}

#[test]
fn check_ignores_whitespace_and_the_check_attribute_but_not_content() {
    let base = compute_check(DOC);
    assert_eq!(compute_check(&DOC.replace("check=\"deadbeef\"", "check=\"other\"")), base);
    assert_eq!(compute_check(&DOC.replace("\n  ", "\n\n      ")), base);
    assert_ne!(compute_check(&DOC.replace("82.0", "28.0")), base);
    assert_eq!(compute_check(&DOC.replace("cost   is", "cost is")), base);
    assert_ne!(compute_check(&DOC.replace("\"quoted\"", "quoted")), base);
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
}
