#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The XML helpers, as imported and as bundled into each script (see conftest.py)."""
import hashlib

DOC = '''<concord-score version="0.3.0" from="cost" run_id="R" uuid="U" check="deadbeef">
  <score candidate_id="seed-a">
    <raw>82.0</raw>
    <reasoning>cost   is   fine,
      "quoted" &lt; 5</reasoning>
  </score>
</concord-score>'''


def test_esc_text_keeps_quotes_and_esc_attr_escapes_them(xmlutil):
    assert xmlutil.esc_text('a < b & "c" > d') == 'a &lt; b &amp; "c" &gt; d'
    assert xmlutil.esc_attr('a < b & "c" > d') == "a &lt; b &amp; &quot;c&quot; &gt; d"
    assert xmlutil.esc_text(5) == "5"


def test_canonical_text_drops_check_and_squeezes_text_only(xmlutil):
    assert xmlutil.canonical_text(DOC) == (
        '<concord-score version="0.3.0" from="cost" run_id="R" uuid="U"><score candidate_id="seed-a"><raw>82.0</raw>'
        '<reasoning>cost is fine, "quoted" &lt; 5</reasoning></score></concord-score>')


def test_canonical_text_leaves_attribute_order_quotes_and_entities_alone(xmlutil):
    assert xmlutil.canonical_text('<a z="1"  b=\'2\' check="x">&amp; &quot;</a>') == '<a z="1"  b=\'2\'>&amp; &quot;</a>'
    assert xmlutil.canonical_text('<e check="x"/>') == "<e/>"
    assert xmlutil.canonical_text("\n<a>\n</a>\n") == "<a></a>"


def test_compute_check_is_8_lowercase_hex_of_the_sha256_of_the_canonical_text(xmlutil):
    want = hashlib.sha256(xmlutil.canonical_text(DOC).encode("utf-8")).hexdigest()[:8]
    assert xmlutil.compute_check(DOC) == want and len(want) == 8


def test_check_ignores_whitespace_and_the_check_attribute_but_not_content(xmlutil):
    base = xmlutil.compute_check(DOC)
    assert xmlutil.compute_check(DOC.replace('check="deadbeef"', 'check="other"')) == base
    assert xmlutil.compute_check(DOC.replace("\n  ", "\n\n      ")) == base
    assert xmlutil.compute_check(DOC.replace("82.0", "28.0")) != base
    assert xmlutil.compute_check(DOC.replace("cost   is", "cost is")) == base
    assert xmlutil.compute_check(DOC.replace('"quoted"', "quoted")) != base


def test_with_check_adds_a_check_that_verifies(xmlutil):
    bare = DOC.replace(' check="deadbeef"', "")
    doc = xmlutil.with_check(bare)
    assert xmlutil.check_of(doc) == xmlutil.compute_check(bare)
    assert xmlutil.verify_check(doc)
    assert not xmlutil.verify_check(doc.replace("82.0", "81.0"))
    assert not xmlutil.verify_check(bare)
    assert xmlutil.with_check('<c a="1"/>').endswith('check="%s"/>' % xmlutil.compute_check('<c a="1"/>'))
