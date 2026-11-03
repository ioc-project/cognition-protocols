#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The XML helpers, as imported and as bundled into each script (see conftest.py)."""
import xml.etree.ElementTree as ET


def test_esc_makes_text_and_attribute_values_safe(xmlutil):
    assert xmlutil.esc('a < b & "c" > d') == "a &lt; b &amp; &quot;c&quot; &gt; d"
    assert xmlutil.esc(5) == "5"


def test_cdata_survives_its_own_terminator(xmlutil):
    el = ET.fromstring("<r>" + xmlutil.cdata("x ]]> <y> & z") + "</r>")
    assert el.text == "x ]]> <y> & z"


def test_text_element_adds_a_child_with_text(xmlutil):
    root = ET.Element("r")
    child = xmlutil.text_element(root, "k", "v")
    assert child is root.find("k") and child.text == "v"


def test_escape_bare_chars_repairs_copied_text_and_leaves_markup(xmlutil):
    fixed = xmlutil.escape_bare_chars("<r>cost < 5 & risk &amp; <b>ok</b> &#38;</r>")
    assert fixed == "<r>cost &lt; 5 &amp; risk &amp; <b>ok</b> &#38;</r>"
    ET.fromstring(fixed)


def test_document_patterns_find_a_complete_element_in_prose(xmlutil):
    reply = "<concord-score-reply agent='a'><score/></concord-score-reply>"
    ack = "<concord-ack agent='a'></concord-ack>"
    anchor = "<concord-anchor><x/></concord-anchor>"
    text = f"words {reply} more {ack} and {anchor} and <concord-score-reply>never closed"
    assert [m.group(0) for m in xmlutil.DOC_RE.finditer(text)] == [reply, ack, anchor]
    assert [m.group(0) for m in xmlutil.REPLY_RE.finditer(text)] == [reply]
    assert [m.group(0) for m in xmlutil.AGENT_DOC_RE.finditer(text)] == [reply, ack]
