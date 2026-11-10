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
    ('{"enum": ["A", "A"]}', '"enum" holds the same option twice'),
    ('{"description": "d", "pattern": 1}', '"pattern" must be a string'),
    ('{"description": "d", "pattern": "("}', '"pattern" is not in the supported subset (a ( is not closed)'),
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
    assert schema.check_candidate(enum, "  Plan A \n") is None
    assert schema.check_candidate(enum, "plan a") is not None
    assert schema.check_candidate({"enum": ["A", "a"]}, "a") is None
    assert "exactly one of the listed options (Plan A; Plan B)" in schema.check_candidate(enum, "Plan C")
    pat = {"description": "d", "pattern": "^ok-[0-9]+$"}
    assert schema.check_candidate(pat, "ok-12") is None
    assert "must match the regular expression" in schema.check_candidate(pat, "nope")
    length = {"description": "d", "minLength": 3, "maxLength": 6}
    assert schema.check_candidate(length, "abcd") is None
    assert schema.check_candidate(length, "ab") == "must be at least 3 characters"
    assert schema.check_candidate(length, "abcdefg") == "must be at most 6 characters"
    assert schema.check_candidate(OPEN, "anything") is None


def test_a_pattern_outside_the_subset_is_rejected_not_ignored(schema):
    bad = {"description": "d", "pattern": "("}
    with pytest.raises(schema.SchemaError, match="not in the supported subset"):
        schema.check_candidate(bad, "anything")


def test_render_schema_describes_what_the_agents_must_send(schema):
    assert schema.render_schema(OPEN) == "a plan"
    assert schema.render_schema({"enum": ["A", "B"]}) == "exactly one of: A | B"
    assert schema.render_schema({"description": "d", "pattern": "^x$", "minLength": 1, "maxLength": 4}) == \
        "d; text matching the regular expression ^x$; between 1 and 4 characters"
    assert schema.render_schema({"description": "d", "maxLength": 4}) == "d; at most 4 characters"
    assert schema.render_schema({"title": "t"}) == "t"
    assert schema.render_schema({}) == "text"


def matches(schema, pattern, text):
    return schema.compile_pattern(pattern).search(text) is not None


@pytest.mark.parametrize("pattern, yes, no", [
    ("ab", ["ab", "xaby"], ["a", "ba"]),
    ("^ab$", ["ab"], ["xab", "abx", "ab\n\n"]),
    ("a|bc", ["a", "xbc"], ["b", "c"]),
    ("^(?:ab)+$", ["ab", "ababab"], ["", "aba"]),
    ("^(a|b)c?$", ["a", "bc"], ["c", "abc"]),
    ("^a{2}$", ["aa"], ["a", "aaa"]),
    ("^a{2,}$", ["aa", "aaaa"], ["a"]),
    ("^a{1,2}$", ["a", "aa"], ["", "aaa"]),
    ("^[a-c]+$", ["abc", "cab"], ["d", "abd"]),
    ("^[^a-c]+$", ["xyz", "-"], ["a", "xa"]),
    ("^[-a]+$", ["-a-"], ["b"]),
    ("^[a\\]]+$", ["a]"], ["b"]),
    ("^[\\\\-]+$", ["\\-"], ["a"]),
    ("^\\d+$", ["0123"], ["\u0663", "a", ""]),
    ("^\\w+$", ["aZ_9"], ["\u00e9", "a b", "-"]),
    ("^\\s$", [" ", "\t"], ["\u00a0", "a"]),
    ("^a\\.b$", ["a.b"], ["axb"]),
    ("^a\\-b\\\\$", ["a-b\\"], ["a-b"]),
    ("^.$", ["a", "\u00e9", "\U0001f600", "\u2028"], ["", "ab"]),
    ("a.b", ["a\u2028b", "a b"], ["a\rb", "a\nb"]),
    ("^ok-[0-9]+$", ["ok-12"], ["ok-", "ok-1a"]),
    ("é", ["café"], ["cafe"]),
])
def test_a_pattern_of_the_subset_means_what_the_spec_says(schema, pattern, yes, no):
    for text in yes:
        assert matches(schema, pattern, text), (pattern, text)
    for text in no:
        assert not matches(schema, pattern, text), (pattern, text)


def test_a_dollar_does_not_match_before_a_final_line_feed_and_lengths_count_code_points(schema):
    assert not matches(schema, "^a$", "a\nb\n")
    assert matches(schema, "a$", "b\na") and not matches(schema, "a$", "a\nb")
    assert matches(schema, "^b", "b") and not matches(schema, "^b", "a\nb")
    emoji = {"description": "d", "minLength": 2, "maxLength": 2}
    assert schema.check_candidate(emoji, "\U0001f600\U0001f601") is None
    assert schema.check_candidate(emoji, "\U0001f600") is not None


@pytest.mark.parametrize("pattern", [
    "(?=a)b", "(?!a)b", "(?<=a)b", "(?<!a)b", "(?<n>a)", "(?P<n>a)", "(?i)a", "(?s:a)", "(a)\\1", "\\1", "\\k<n>", "\\bab", "\\p{L}",
    "\\u0041", "\\u{41}", "\\x41", "\\n", "\\t", "\\D", "\\W", "\\S", "\\A", "\\Z", "\\B", "\\a", "\\", "a\\",
    "[\\d]", "[\\w-]", "[]", "[^]", "[a", "[z-a]", "a)", "(a", "*a", "+a", "?a", "{2}a", "a**", "a+?", "a*?", "a{2}{3}", "a{",
    "a{x}", "a{,3}", "a{3,2}", "^*", "$+", "(*)", "|*", "a]", "a}", "]", "}", "{",
])
def test_a_pattern_outside_the_subset_is_refused(schema, pattern):
    text = json.dumps({"description": "d", "pattern": pattern})
    assert 'pattern" is not in the supported subset' in refused(schema, text)
    with pytest.raises(schema.SchemaError):
        schema.check_candidate({"description": "d", "pattern": pattern}, "a")


def test_empty_alternatives_and_groups_are_part_of_the_subset(schema):
    for pattern in ("a|", "|a", "()", "(a|)", "^$"):
        schema.validate_schema({"description": "d", "pattern": pattern})


def test_a_candidate_is_matched_without_the_whitespace_around_it(schema):
    spec = {"description": "d", "pattern": "^ab$"}
    assert schema.check_candidate(spec, " ab\n\n") is None
