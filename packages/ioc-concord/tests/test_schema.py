#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The candidate schema, as imported and as bundled into each script (see conftest.py)."""
import json

import pytest

OPEN = {"type": "string", "description": "a plan"}


def refused(schema, text, label="schema"):
    with pytest.raises(schema.SchemaError) as e:
        schema.parse_schema(text, label)
    return str(e.value)


def test_a_supported_schema_is_returned_as_parsed(schema):
    assert schema.parse_schema(json.dumps(OPEN)) == OPEN
    spec = {"enum": ["A", "b"], "pattern": "^.$", "minLength": 1, "maxLength": 1, "title": "t"}
    assert schema.parse_schema(json.dumps(spec)) == spec


@pytest.mark.parametrize("text, message", [
    ("not json", "must be a JSON Schema object, but it is not valid JSON"),
    ("", "must be a JSON Schema object, but it is not valid JSON"),
    ("[1]", "must be a JSON object"),
    ('{"description": "d", "color": 1}', "uses keywords that are not enforced here: color"),
    ('{"type": "number", "description": "d"}', '"type" can only be "string"'),
    ("{}", 'needs a "description"'),
    ('{"enum": ["A"]}', '"enum" must be a list of at least 2 non-empty strings'),
    ('{"enum": ["A", " "]}', '"enum" must be a list of at least 2 non-empty strings'),
    ('{"enum": ["A", "a "]}', '"enum" holds the same option twice'),
    ('{"description": "d", "pattern": 1}', '"pattern" must be a string'),
    ('{"description": "d", "pattern": "("}', '"pattern" is not a valid regular expression'),
    ('{"description": "d", "minLength": -1}', '"minLength" must be a non-negative integer'),
    ('{"description": "d", "maxLength": true}', '"maxLength" must be a non-negative integer'),
    ('{"description": "d", "minLength": 5, "maxLength": 2}', '"minLength" is greater than "maxLength"'),
])
def test_an_unsupported_schema_is_refused_with_a_reason(schema, text, message):
    assert message in refused(schema, text)


def test_the_label_names_the_schema_the_way_the_caller_does(schema):
    assert refused(schema, "{}", "--schema").startswith('--schema needs a "description"')
    assert refused(schema, "{}").startswith('schema needs a "description"')


def test_check_candidate_enforces_each_keyword(schema):
    enum = {"enum": ["Plan A", "Plan B"]}
    assert schema.check_candidate(enum, "plan   a") is None
    assert "exactly one of the listed options (Plan A; Plan B)" in schema.check_candidate(enum, "Plan C")
    pat = {"description": "d", "pattern": "^ok-[0-9]+$"}
    assert schema.check_candidate(pat, "ok-12") is None
    assert "must match the regular expression" in schema.check_candidate(pat, "nope")
    length = {"description": "d", "minLength": 3, "maxLength": 6}
    assert schema.check_candidate(length, "abcd") is None
    assert schema.check_candidate(length, "ab") == "must be at least 3 characters"
    assert schema.check_candidate(length, "abcdefg") == "must be at most 6 characters"
    assert schema.check_candidate(OPEN, "anything") is None


def test_a_pattern_that_is_not_a_regular_expression_is_rejected_not_ignored(schema):
    bad = {"description": "d", "pattern": "("}
    with pytest.raises(schema.SchemaError, match="not a valid regular expression"):
        schema.check_candidate(bad, "anything")


def test_render_schema_describes_what_the_agents_must_send(schema):
    assert schema.render_schema(OPEN) == "a plan"
    assert schema.render_schema({"enum": ["A", "B"]}) == "exactly one of: A | B"
    assert schema.render_schema({"description": "d", "pattern": "^x$", "minLength": 1, "maxLength": 4}) == \
        "d; text matching the regular expression ^x$; between 1 and 4 characters"
    assert schema.render_schema({"description": "d", "maxLength": 4}) == "d; at most 4 characters"
    assert schema.render_schema({"title": "t"}) == "t"
    assert schema.render_schema({}) == "text"
