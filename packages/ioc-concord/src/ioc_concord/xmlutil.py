#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""XML helpers shared by every CONCORD document: escaping, building elements, repairing copied text, and the patterns
that find a CONCORD document inside free text."""
import re
import xml.etree.ElementTree as ET

# A complete <concord-...> element anywhere in a text: last resort, so an invented tag still gets a precise error.
DOC_RE = re.compile(r"<(concord-[a-z-]+)\b[^>]*>.*?</\1\s*>", re.DOTALL)
# A complete <concord-...-reply> element.
REPLY_RE = re.compile(r"<(concord-[a-z-]+-reply)\b[^>]*>.*?</\1\s*>", re.DOTALL)
# What an agent sends back: a reply, an acknowledgement or an error.
AGENT_DOC_RE = re.compile(r"<(concord-(?:anchor|candidate|score)-reply|concord-ack|concord-error)\b[^>]*>.*?</\1\s*>",
                          re.DOTALL)

_BARE_LT_RE = re.compile(r"<(?![a-zA-Z/?!])")
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")


def esc(v):
    """`v` as XML text or attribute value."""
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def cdata(s):
    """`s` as a CDATA section."""
    return "<![CDATA[" + str(s).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def text_element(parent, tag, text):
    """Add <tag>text</tag> under `parent` and return it."""
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def escape_bare_chars(xml_text):
    """Escape what an agent un-escaped while copying: a `<` that does not start a tag and an `&` that is not part of an
    entity. The scripts print `&lt;` and `&amp;`, but a model copying the text often turns them back into `<` and `&`.
    Applied to every document before it is parsed, so one such character never costs a whole reply; the meaning of
    the text is unchanged."""
    return _BARE_LT_RE.sub("&lt;", _BARE_AMP_RE.sub("&amp;", xml_text))
