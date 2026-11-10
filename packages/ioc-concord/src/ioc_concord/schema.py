#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The candidate schema: the JSON Schema subset that says what one option may look like, checked on every proposal.

Only a small subset is supported, and anything else is refused, so a rule is never silently left unenforced. An open
format is `{"type": "string", "description": "..."}`; a closed list is `{"enum": ["A", "B"]}`.

A `pattern` is limited to the subset the spec lists, so that every agent reads it the same way: `compile_pattern` reads it
character by character, refuses what is outside the subset and writes the same expression in Python's own syntax.
"""
import json
import re
import string

from ioc_concord.errors import SchemaError

SCHEMA_KEYS = ("type", "enum", "pattern", "minLength", "maxLength", "description", "title")

# What `\d`, `\w`, `\s` and `.` stand for in the spec, written out so that Python's Unicode-aware classes never apply.
_CLASSES = {"d": "[0-9]", "w": "[A-Za-z0-9_]", "s": "[ \\t\\r\\n]"}
_DOT = "[^\\r\\n]"
_QUANT_RE = re.compile(r"\{([0-9]+)(?:(,)([0-9]*))?\}")


def _escaped(pattern, i, in_class):
    """(the character after the backslash at `pattern[i]`, what it becomes, the next index). Raises ValueError."""
    nxt = pattern[i + 1] if i + 1 < len(pattern) else ""
    if nxt and nxt in string.punctuation:
        return nxt, re.escape(nxt), i + 2
    if nxt in _CLASSES and not in_class:
        return None, _CLASSES[nxt], i + 2
    raise ValueError(f"\\{nxt} is outside the subset" if nxt else "a backslash at the end")


def _class(pattern, i):
    """(the Python text of the class that starts at `pattern[i]` == "[", the next index). Raises ValueError."""
    i += 1
    negate = pattern.startswith("^", i)
    i += negate
    out, atoms = [], 0

    def atom(j):
        if j >= len(pattern):
            raise ValueError("a class is not closed")
        if pattern[j] == "\\":
            ch, _, j = _escaped(pattern, j, True)
            if ch is None:
                raise ValueError("\\d, \\w and \\s are outside the subset inside a class")
            return ch, j
        return pattern[j], j + 1

    while i < len(pattern) and pattern[i] != "]":
        lo, i = atom(i)
        if pattern.startswith("-", i) and i + 1 < len(pattern) and pattern[i + 1] != "]":
            hi, i = atom(i + 1)
            if ord(lo) > ord(hi):
                raise ValueError("a range of a class runs backwards")
            out.append(f"{re.escape(lo)}-{re.escape(hi)}")
        else:
            out.append(re.escape(lo))
        atoms += 1
    if i >= len(pattern):
        raise ValueError("a class is not closed")
    if not atoms:
        raise ValueError("a class is empty")
    return "[" + "^" * negate + "".join(out) + "]", i + 1


def compile_pattern(pattern):
    """The compiled regular expression for the `pattern` of a schema. Raises SchemaError, with the reason, when the pattern
    is outside the subset of the spec. It matches if found anywhere in the text, unless anchored."""
    try:
        return re.compile(_translate(pattern))
    except (ValueError, re.error) as exc:
        raise SchemaError(f"is not in the supported subset ({exc})")


def _translate(pattern):
    out, i, depth, prev = [], 0, 0, None     # prev: what came before, "atom", "anchor", "quantifier" or None
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            _, text, i = _escaped(pattern, i, False)
            out.append(text)
            prev = "atom"
        elif c == "[":
            text, i = _class(pattern, i)
            out.append(text)
            prev = "atom"
        elif c == "(":
            if pattern.startswith("(?", i):
                if not pattern.startswith("(?:", i):
                    raise ValueError("(? is outside the subset except (?:")
                out.append("(?:")
                i += 3
            else:
                out.append("(")
                i += 1
            depth += 1
            prev = None
        elif c == ")":
            if not depth:
                raise ValueError("a ) has no (")
            depth -= 1
            out.append(")")
            i += 1
            prev = "atom"
        elif c == "|":
            out.append("|")
            i += 1
            prev = None
        elif c in "^$":
            out.append("\\A" if c == "^" else "\\Z")
            i += 1
            prev = "anchor"
        elif c == ".":
            out.append(_DOT)
            i += 1
            prev = "atom"
        elif c in "*+?{":
            if prev != "atom":
                raise ValueError(f"a {c} has nothing to repeat" if prev != "quantifier" else f"a {c} after a quantifier")
            if c == "{":
                m = _QUANT_RE.match(pattern, i)
                if m is None:
                    raise ValueError("a { is not a quantifier")
                if m.group(3) and int(m.group(1)) > int(m.group(3)):
                    raise ValueError("a quantifier runs backwards")
                out.append(m.group(0))
                i = m.end()
            else:
                out.append(c)
                i += 1
            prev = "quantifier"
        elif c in "]}":
            raise ValueError(f"a {c} stands alone")
        else:
            out.append(re.escape(c))
            i += 1
            prev = "atom"
    if depth:
        raise ValueError("a ( is not closed")
    return "".join(out)


def parse_schema(text, label="schema"):
    """The schema in the JSON `text`, checked. `label` is what the caller calls it in its error messages (a CLI says
    `--schema`). Raises SchemaError."""
    try:
        spec = json.loads((text or "").strip())
    except ValueError as exc:
        raise SchemaError(f"{label} must be a JSON Schema object, but it is not valid JSON ({exc}). For an open format use "
                          '\'{"type": "string", "description": "what one option is, in plain words"}\'.')
    return validate_schema(spec, label)


def validate_schema(spec, label="schema"):
    """`spec` itself, if it is a supported schema. Raises SchemaError."""
    if not isinstance(spec, dict):
        raise SchemaError(f"{label} must be a JSON object")
    unknown = sorted(set(spec) - set(SCHEMA_KEYS))
    if unknown:
        raise SchemaError(f"{label} uses keywords that are not enforced here: {', '.join(unknown)}. Supported: "
                          f"{', '.join(SCHEMA_KEYS)}.")
    if "type" in spec and spec["type"] != "string":
        raise SchemaError(f'{label} "type" can only be "string": a candidate is text')
    if not (spec.get("enum") or str(spec.get("description") or "").strip()):
        raise SchemaError(f'{label} needs a "description" (what one option is, in plain words) or an "enum"')
    if "enum" in spec:
        e = spec["enum"]
        if not (isinstance(e, list) and len(e) >= 2 and all(isinstance(x, str) and x.strip() for x in e)):
            raise SchemaError(f'{label} "enum" must be a list of at least 2 non-empty strings')
        if len(set(e)) != len(e):
            raise SchemaError(f'{label} "enum" holds the same option twice')
    if "pattern" in spec:
        if not isinstance(spec["pattern"], str):
            raise SchemaError(f'{label} "pattern" must be a string')
        try:
            compile_pattern(spec["pattern"])
        except SchemaError as exc:
            raise SchemaError(f'{label} "pattern" {exc}')
    for k in ("minLength", "maxLength"):
        if k in spec and not (isinstance(spec[k], int) and not isinstance(spec[k], bool) and spec[k] >= 0):
            raise SchemaError(f'{label} "{k}" must be a non-negative integer')
    if "minLength" in spec and "maxLength" in spec and spec["minLength"] > spec["maxLength"]:
        raise SchemaError(f'{label} "minLength" is greater than "maxLength"')
    return spec


def check_candidate(spec, text):
    """Why `text` does not satisfy the schema `spec` (None when it does). The candidate is the text without the whitespace
    around it; an option of a closed list must match exactly, and lengths count Unicode code points. Raises SchemaError if
    `spec` carries a pattern outside the supported subset: a schema that cannot be enforced accepts nothing."""
    t = str(text).strip()
    if "enum" in spec and t not in spec["enum"]:
        return "must be exactly one of the listed options (" + "; ".join(spec["enum"]) + ")"
    if "pattern" in spec:
        try:
            found = compile_pattern(spec["pattern"]).search(t)
        except SchemaError as exc:
            raise SchemaError(f'the schema "pattern" {exc}')
        if not found:
            return f"must match the regular expression {spec['pattern']}"
    if "minLength" in spec and len(t) < spec["minLength"]:
        return f"must be at least {spec['minLength']} characters"
    if "maxLength" in spec and len(t) > spec["maxLength"]:
        return f"must be at most {spec['maxLength']} characters"
    return None


def render_schema(spec):
    """The candidate format as the agents read it."""
    parts = []
    if spec.get("description"):
        parts.append(str(spec["description"]))
    if "enum" in spec:
        parts.append("exactly one of: " + " | ".join(spec["enum"]))
    if "pattern" in spec:
        parts.append(f"text matching the regular expression {spec['pattern']}")
    if "minLength" in spec or "maxLength" in spec:
        lo, hi = spec.get("minLength"), spec.get("maxLength")
        parts.append("between %s and %s characters" % (lo if lo is not None else 0, hi if hi is not None else "any number of")
                     if lo is not None and hi is not None else
                     (f"at least {lo} characters" if lo is not None else f"at most {hi} characters"))
    return "; ".join(parts) or spec.get("title") or "text"
