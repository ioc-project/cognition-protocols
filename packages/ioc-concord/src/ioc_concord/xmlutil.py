#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""XML text helpers: escaping, and the integrity check of a document.

The check works on the text of a document, exactly as it was sent: a document is never parsed and written again before its
check is computed or verified.
"""
import hashlib
import re

_WS = " \t\r\n"
_START_TAG_RE = re.compile(r"<[^>]*>")
_CHECK_ATTR_RE = re.compile(r' check="[^"]*"')
_CHECK_VALUE_RE = re.compile(r'\scheck="([^"]*)"')
_TEXT_RUN_RE = re.compile(r">([^<]*)<")
_BLANKS_RE = re.compile(r"[ \t\r\n]+")


def esc_text(v):
    """`v` as XML text: `<`, `>` and `&` are escaped, a `"` is not."""
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(v):
    """`v` as the value of an attribute: `<`, `>`, `&` and `"` are escaped."""
    return esc_text(v).replace('"', "&quot;")


def _squeeze(match):
    text = match.group(1)
    return ">" + ("" if not text.strip(_WS) else _BLANKS_RE.sub(" ", text).strip(_WS)) + "<"


def canonical_text(doc):
    """The text the check is computed over: the document without its `check` attribute (and the space before it), with
    each stretch of text between a `>` and the next `<` made of whitespace only removed, and each other one with its runs of
    whitespace made one space and none left at either end. Nothing else changes."""
    doc = doc.strip(_WS)
    start = _START_TAG_RE.match(doc)
    if start:
        doc = _CHECK_ATTR_RE.sub("", start.group(0), count=1) + doc[start.end():]
    return _TEXT_RUN_RE.sub(_squeeze, doc)


def compute_check(doc):
    """The `check` of `doc`: the first 8 lowercase hex characters of the SHA-256 of its canonical text, as UTF-8."""
    return hashlib.sha256(canonical_text(doc).encode("utf-8")).hexdigest()[:8]


def check_of(doc):
    """The value of the `check` attribute on the root element of `doc`, or None when it has none."""
    start = _START_TAG_RE.match(doc.strip(_WS))
    m = _CHECK_VALUE_RE.search(start.group(0)) if start else None
    return m.group(1) if m else None


def verify_check(doc):
    """True when `doc` carries a `check` and it is the one `compute_check` gives."""
    claimed = check_of(doc)
    return claimed is not None and claimed == compute_check(doc)


def with_check(doc_without_check):
    """`doc_without_check`, whose root start tag has no `check`, with the attribute added as its last attribute."""
    doc = doc_without_check.strip(_WS)
    start = _START_TAG_RE.match(doc)
    tag = start.group(0)
    closing = "/>" if tag.endswith("/>") else ">"
    return tag[:-len(closing)] + f' check="{compute_check(doc)}"' + closing + doc[start.end():]
