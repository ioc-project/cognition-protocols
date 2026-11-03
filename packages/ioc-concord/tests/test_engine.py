#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The decision engine, as imported and as bundled into each script (see conftest.py)."""
import xml.etree.ElementTree as ET

import pytest

PARAMS = {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 3}


def run(engine, anchors, seeds, **params):
    st = engine.new_engine_state(list(anchors), {**PARAMS, **params})
    for agent, (reject, ideal) in anchors.items():
        st["anchors"][agent] = {"raw_reject": reject, "raw_ideal": ideal}
    for agent, text in seeds.items():
        engine.add_candidate(st, agent, text, "because", cid=f"seed-{agent}")
    return st


def rate(engine, st, agent, **raw):
    engine.add_ratings(st, agent, [(cid.replace("_", "-"), r, "reasons") for cid, r in raw.items()])


def test_norm_is_relative_to_the_anchors_and_clamped(engine):
    st = run(engine, {"a": (20, 80)}, {})
    assert engine.norm(st, "a", 50) == pytest.approx(0.5)
    assert engine.norm(st, "a", 10) == 0.0
    assert engine.norm(st, "a", 95) == 1.0


def test_succeeds_when_one_candidate_clears_every_agent(engine):
    st = run(engine, {"a": (10, 90), "b": (20, 80)}, {"a": "X", "b": "Y"})
    rate(engine, st, "a", seed_a=90, seed_b=50)
    rate(engine, st, "b", seed_a=75, seed_b=70)
    engine.advance(st)
    assert st["outcome"] == "SUCCESS" and st["x_star"] == "seed-a"
    assert st["pending"] is None


def test_waits_until_every_rating_is_in(engine):
    st = run(engine, {"a": (10, 90), "b": (20, 80)}, {"a": "X", "b": "Y"})
    rate(engine, st, "a", seed_a=90, seed_b=50)
    engine.advance(st)
    assert st["outcome"] is None and st["x_star"] is None


def test_repair_goes_to_the_agent_least_satisfied_with_the_incumbent_then_succeeds(engine):
    st = run(engine, {"a": (10, 90), "b": (20, 80)}, {"a": "X", "b": "Y"}, satisfaction_threshold=0.7)
    rate(engine, st, "a", seed_a=90, seed_b=20)
    rate(engine, st, "b", seed_a=25, seed_b=80)
    engine.advance(st)
    # Y has the higher floor (0.125 against 0.083), so it is the incumbent, and a rates it lowest
    assert st["x_star"] == "seed-b" and st["outcome"] is None and st["pending"] == {"agent": "a", "round": 1}
    assert engine.add_candidate(st, "a", "Z", "fixes both", cid="r1-a") == "r1-a"
    assert st["pending"] is None
    rate(engine, st, "a", r1_a=85)
    rate(engine, st, "b", r1_a=75)
    engine.advance(st)
    assert st["outcome"] == "SUCCESS" and st["x_star"] == "r1-a" and st["t"] == 1


def test_best_effort_when_the_budget_is_spent(engine):
    st = run(engine, {"a": (10, 90), "b": (20, 80)}, {"a": "X", "b": "Y"}, satisfaction_threshold=0.9, B=0)
    rate(engine, st, "a", seed_a=90, seed_b=20)
    rate(engine, st, "b", seed_a=25, seed_b=80)
    engine.advance(st)
    assert st["outcome"] == "BEST_EFFORT"


def test_the_incumbent_does_not_depend_on_arrival_order(engine):
    picks = set()
    for order in (("a", "b"), ("b", "a")):
        st = run(engine, {"a": (0, 100), "b": (0, 100)}, {})
        for agent in order:
            engine.add_candidate(st, agent, agent.upper(), "r", cid=f"seed-{agent}")
        for agent in order:
            rate(engine, st, agent, seed_a=70, seed_b=70)
        picks.add(engine.select(st))
    assert picks == {("seed-a", True)}


def test_duplicates_and_unknown_ids_are_ignored(engine):
    st = run(engine, {"a": (0, 100)}, {"a": "X"})
    assert engine.add_candidate(st, "a", "again", "r", cid="seed-a") is None
    assert engine.add_ratings(st, "a", [("seed-a", 50, "r"), ("nope", 10, "r"), ("seed-a", 99, "r")]) == ["seed-a"]
    assert st["raw"]["a"] == {"seed-a": 50}


def test_a_missing_rating_blocks_success(engine):
    st = run(engine, {"a": (0, 100), "b": (0, 100)}, {"a": "X"})
    rate(engine, st, "a", seed_a=90)
    rate(engine, st, "b", seed_a="MISSING")
    engine.advance(st)
    assert st["outcome"] != "SUCCESS"


def test_escape_bare_chars_repairs_copied_text_and_leaves_markup(engine):
    fixed = engine.escape_bare_chars("<r>cost < 5 & risk &amp; <b>ok</b> &#38;</r>")
    assert fixed == "<r>cost &lt; 5 &amp; risk &amp; <b>ok</b> &#38;</r>"
    ET.fromstring(fixed)


def test_parse_reply_doc_reads_each_kind(engine):
    parse = lambda xml: engine.parse_reply_doc(ET.fromstring(xml))   # noqa: E731
    assert parse("<concord-anchor-reply><raw_reject>10</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>") \
        == ("anchor", (10.0, 90.0))
    assert parse('<concord-candidate-reply id="seed-a"><candidate>X</candidate><rationale>why</rationale>'
                 "</concord-candidate-reply>") == ("candidate", ("X", "why", "seed-a"))
    assert parse('<concord-score-reply><score candidate_id="seed-a"><raw>70</raw><reasoning>ok</reasoning></score>'
                 "</concord-score-reply>") == ("score", [("seed-a", 70.0, "ok")])
    assert parse("<concord-anchor-reply><raw_reject>x</raw_reject><raw_ideal>1</raw_ideal></concord-anchor-reply>") is None
    assert parse("<other/>") is None


def test_reply_check_ignores_whitespace_and_notices_changes(engine):
    doc = "<concord-score-reply><score candidate_id='a'><raw>1</raw></score></concord-score-reply>"
    spaced = "<concord-score-reply>\n  <score candidate_id='a'> <raw>1</raw> </score>\n</concord-score-reply>"
    changed = doc.replace("<raw>1</raw>", "<raw>2</raw>")
    assert engine.reply_check("hello") is None
    assert engine.reply_check("x " + doc) == engine.reply_check(spaced)
    assert engine.reply_check(doc) != engine.reply_check(changed)
