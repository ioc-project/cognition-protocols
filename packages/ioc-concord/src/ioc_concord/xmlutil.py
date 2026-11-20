#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""XML text helpers: escaping, the start tag of a document, and its integrity check.

The check works on the text of a document, exactly as it was sent: a document is never parsed and written again before its
check is computed or verified.

The root start tag is read here, once, for everything that looks at a document before it is parsed (the `check`, the `from` and
`uuid` that decide whether a message is dropped). `start_tag` finds its end and reads its attributes with an XML parser, so they are
exactly what the parser reads from the whole document: no attribute can be hidden in the value of another one.
"""
import hashlib
import re
import xml.etree.ElementTree as ET

from ioc_concord.protocol import WS, squeeze

_B = r"[ \t\r\n]"                                  # XML blanks: the same four characters as `WS`
_NAME = r"[^ \t\r\n\"'<>/=]+"
_VALUE = r"\"[^\"<]*\"|'[^'<]*'"
_ATTR_RE = re.compile(rf"{_B}+({_NAME}){_B}*={_B}*({_VALUE})")
_START_TAG_RE = re.compile(rf"<({_NAME})((?:{_B}+{_NAME}{_B}*={_B}*(?:{_VALUE}))*){_B}*(/?)>")
_TEXT_RUN_RE = re.compile(r">([^<]*)<")


def esc_text(v):
    """`v` as XML text: `<`, `>` and `&` are escaped, a `"` is not."""
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(v):
    """`v` as the value of an attribute: `<`, `>`, `&` and `"` are escaped."""
    return esc_text(v).replace('"', "&quot;")


def _squeeze(match):
    text = match.group(1)
    return ">" + squeeze(text) + "<"


def start_tag(doc):
    """The root start tag of `doc`, which must begin with it: (its match, its attributes), or None when `doc` does not begin with a
    well-formed start tag. In the match, group 1 is the name, group 3 is "/" for an empty element and the end is where the content
    begins. The attributes are a dict, as an XML parser reads them: quotes of either kind, character references, line breaks and
    all. A repeated attribute is not well-formed."""
    m = _START_TAG_RE.match(doc)
    if m is None:
        return None
    try:
        root = ET.fromstring(m.group(0) if m.group(3) else f"{m.group(0)}</{m.group(1)}>")
    except ET.ParseError:
        return None
    return m, dict(root.attrib)


def canonical_text(doc):
    """The text the check is computed over: the document without its `check` attribute (and the space before it), with
    each stretch of text between a `>` and the next `<` made of whitespace only removed, and each other one with its runs of
    whitespace made one space and none left at either end. Nothing else changes."""
    doc = doc.strip(WS)
    start = _START_TAG_RE.match(doc)
    if start:
        tag = start.group(0)
        for a in _ATTR_RE.finditer(tag, start.start(2), start.end(2)):
            if a.group(1) == "check":
                tag = tag[:a.start(1) - 1] + tag[a.end():]
                break
        doc = tag + doc[start.end():]
    return _TEXT_RUN_RE.sub(_squeeze, doc)


def compute_check(doc):
    """The `check` of `doc`: the first 8 lowercase hex characters of the SHA-256 of its canonical text, as UTF-8."""
    return hashlib.sha256(canonical_text(doc).encode("utf-8")).hexdigest()[:8]


def check_of(doc):
    """The value of the `check` attribute on the root element of `doc`, or None when it has none."""
    st = start_tag(doc.strip(WS))
    return st[1].get("check") if st else None


def verify_check(doc):
    """True when `doc` carries a `check` and it is the one `compute_check` gives."""
    claimed = check_of(doc)
    return claimed is not None and claimed == compute_check(doc)


def with_check(doc_without_check):
    """`doc_without_check`, whose root start tag has no `check`, with the attribute added as its last attribute."""
    doc = doc_without_check.strip(WS)
    start = _START_TAG_RE.match(doc)
    tag = start.group(0)
    closing = "/>" if tag.endswith("/>") else ">"
    return tag[:-len(closing)] + f' check="{compute_check(doc)}"' + closing + doc[start.end():]
