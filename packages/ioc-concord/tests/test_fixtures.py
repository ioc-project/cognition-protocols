#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The cross-implementation fixtures of protocols/concord/spec/fixtures (see the README there): fixed messages, the values a reader
must get from them, messages that must be refused, the check, the patterns and a saved state. Another implementation reads the same
files; this library has to give exactly these results, as imported and as bundled."""
import json
import re

import pytest
from conftest import ROOT

FIXTURES = ROOT / "protocols" / "concord" / "spec" / "fixtures"
SPEC = {"description": "a short plan, in lower case", "pattern": "^[a-z ]+$", "maxLength": 80}


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def data(name):
    return json.loads(text(name))


def jsonable(value):
    return json.loads(json.dumps(value))


VALID, REFUSED, CHECKS, PATTERNS = data("wire/valid.json"), data("wire/refused.json"), data("check.json"), data("patterns.json")


@pytest.mark.parametrize("case", VALID, ids=[c["file"] for c in VALID])
def test_a_valid_message_is_read_as_the_fixture_says(wire, xmlutil, case):
    doc = text(case["file"])
    assert wire.locate(doc) == doc.strip()
    attrs = wire.peek(doc)["attrs"]
    assert (wire.peek(doc)["tag"], attrs["from"], attrs["run_id"], attrs["uuid"], attrs.get("check")) == (
        "concord-" + case["kind"], case["from"], case["run_id"], case["uuid"], case["check"])
    assert jsonable(wire.parse_message(doc, None if case["kind"] == "init" else SPEC)) == case["parsed"]
    assert xmlutil.check_of(doc) == case["check"]
    if case["check"] is not None:
        assert wire.check_ok(doc)


def test_the_builders_write_the_fixtures_byte_for_byte():
    from make_fixtures import messages, tricky
    docs = messages()
    for name, doc in docs.items():
        assert text(f"wire/{name}.xml") == doc + "\n", name
    for name, doc in tricky(docs).items():
        assert text(f"wire/tricky/{name}.xml") == doc + "\n", name


@pytest.mark.parametrize("case", REFUSED, ids=[c["file"] for c in REFUSED])
def test_a_message_that_must_be_refused_is_refused(wire, agent, case):
    doc = text(case["file"])
    with pytest.raises(wire.InvalidMessage):
        wire.parse_message(doc, SPEC)
    fresh = agent.Agent("cost")                               # an agent that is handed it raises nothing and learns nothing
    fresh.receive(doc)
    assert fresh.state == agent.Agent("cost").state
    joined = agent.Agent("cost")
    joined.receive(text("wire/init.xml"))
    before = json.dumps(joined.state, sort_keys=True)
    joined.receive(doc)                                       # nor does it change the state of a run that it has joined
    assert json.dumps(joined.state, sort_keys=True) == before


@pytest.mark.parametrize("case", CHECKS, ids=[c["name"] for c in CHECKS])
def test_the_canonical_text_and_the_check_are_those_of_the_fixture(xmlutil, case):
    assert xmlutil.canonical_text(case["document"]) == case["canonical"]
    assert xmlutil.compute_check(case["document"]) == case["check"]
    assert xmlutil.check_of(case["document"]) == case["declared"]


@pytest.mark.parametrize("case", PATTERNS["valid"], ids=[c["pattern"] for c in PATTERNS["valid"]])
def test_a_pattern_matches_what_the_fixture_says(schema, case):
    compiled = schema.compile_pattern(case["pattern"])
    for t in case["match"]:
        assert compiled.search(t), (case["pattern"], t)
    for t in case["no_match"]:
        assert not compiled.search(t), (case["pattern"], t)


@pytest.mark.parametrize("pattern", PATTERNS["refused"])
def test_a_pattern_outside_the_subset_is_refused_as_the_fixture_says(schema, pattern):
    with pytest.raises(schema.SchemaError):
        schema.compile_pattern(pattern)


class Saved:
    def __init__(self, state):
        self.state = state

    def load(self):
        return jsonable(self.state)

    def save(self, state):
        self.saved = state


def test_a_saved_state_is_loaded_and_the_next_message_moves_it_as_the_fixture_says(agent):
    want, state = data("state/expected.json"), data("state/cost-after-init-replies.json")
    n = iter(range(1, 100))
    node = agent.Agent(want["agent"], store=Saved(state), clock=lambda: want["now"],
                       uuid_fn=lambda: f"c1000000-0000-4000-8000-{next(n):012x}")
    status = node.status()
    assert {k: jsonable(status[k]) for k in ("status", "state", "lacking")} == want["before"]
    step = node.receive(text(want["next"]), now=want["now"])
    after = node.status()
    assert {k: jsonable(after[k]) for k in ("status", "state", "lacking")} == want["after"]
    assert [re.match(r"<([^\s>/]+)", t).group(1) for t in step.send] == want["sends"]
    json.dumps(node.state)                                                    # and what it saves is JSON again
