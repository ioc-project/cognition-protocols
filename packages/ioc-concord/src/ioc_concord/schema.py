#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The candidate schema: the JSON Schema subset that says what one option may look like, checked on every proposal.

Only a small subset is supported, and anything else is refused, so a rule is never silently left unenforced. An open
format is `{"type": "string", "description": "..."}`; a closed list is `{"enum": ["A", "B"]}`.
"""
import json
import re

from ioc_concord.errors import SchemaError

SCHEMA_KEYS = ("type", "enum", "pattern", "minLength", "maxLength", "description", "title")


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
        if len({" ".join(x.lower().split()) for x in e}) != len(e):
            raise SchemaError(f'{label} "enum" holds the same option twice')
    if "pattern" in spec:
        if not isinstance(spec["pattern"], str):
            raise SchemaError(f'{label} "pattern" must be a string')
        try:
            re.compile(spec["pattern"])
        except re.error as exc:
            raise SchemaError(f'{label} "pattern" is not a valid regular expression ({exc})')
    for k in ("minLength", "maxLength"):
        if k in spec and not (isinstance(spec[k], int) and not isinstance(spec[k], bool) and spec[k] >= 0):
            raise SchemaError(f'{label} "{k}" must be a non-negative integer')
    if "minLength" in spec and "maxLength" in spec and spec["minLength"] > spec["maxLength"]:
        raise SchemaError(f'{label} "minLength" is greater than "maxLength"')
    return spec


def check_candidate(spec, text):
    """Why `text` does not satisfy the schema `spec` (None when it does). Raises SchemaError if `spec` carries a
    pattern that is not a valid regular expression: a schema that cannot be enforced accepts nothing."""
    t = " ".join(str(text).split()) if "enum" in spec else str(text).strip()
    if "enum" in spec and not any(" ".join(str(o).lower().split()) == t.lower() for o in spec["enum"]):
        return "must be exactly one of the listed options (" + "; ".join(spec["enum"]) + ")"
    t = next((o for o in spec.get("enum", []) if " ".join(str(o).lower().split()) == t.lower()), t)
    if "pattern" in spec:
        try:
            if not re.search(spec["pattern"], t):
                return f"must match the regular expression {spec['pattern']}"
        except re.error as exc:
            raise SchemaError(f'the schema "pattern" is not a valid regular expression ({exc})')
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
