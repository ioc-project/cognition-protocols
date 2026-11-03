#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The documents, as imported and as bundled into each script (see conftest.py): what a builder writes, the matching parser
reads back."""
import json
import xml.etree.ElementTree as ET

import pytest

OPEN = {"type": "string", "description": "a plan"}
ENUM = {"enum": ["Plan A", "Plan B"]}


def run_state(spec=OPEN, agents=("a", "b")):
    """The run state an initiator holds (engine state plus the brief), halfway through a run."""
    return {
        "params": {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 10, "W": "leximin"}, "agents": list(agents),
        "schema": "a plan" if spec is OPEN else "exactly one of: Plan A | Plan B",
        "schema_json": json.dumps(spec, sort_keys=True), "options": spec.get("enum"), "context": "Pick <one> & go",
        "x_reject": "nothing decided", "x_ideal": "a plan everyone accepts", "state_id": "s1",
        "anchors": {a: {"raw_reject": 10, "raw_ideal": 90} for a in agents},
        "pool": [{"id": "seed-a", "proposer": "a", "round": 0, "candidate": "Plan A", "rationale": "cheap"},
                 {"id": "seed-b", "proposer": "b", "round": 0, "candidate": "Plan B", "rationale": None}],
        "raw": {"a": {"seed-a": 90, "seed-b": 30}, "b": {"seed-a": 20}},
        "score_reasoning": {"a": {"seed-a": "ok & cheap"}}, "x_star": "seed-a", "t": 1,
        "pending": {"agent": "b", "round": 1}, "outcome": None, "log": [],
    }


def root_of(xml):
    return ET.fromstring(xml)


# ---- requests: built by the initiator, read by an agent --------------------------------------------------------------

def test_an_anchor_request_reads_back(wire):
    req = wire.parse_request(root_of(wire.build_anchor_msg(run_state(), ["a", "b"])))
    assert req["kind"] == "anchor" and req["participants"] == ["a", "b"] and req["addressed_to"] == ["a", "b"]
    assert req["params"] == {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 10}
    assert req["brief"] == {"x_reject": "nothing decided", "x_ideal": "a plan everyone accepts"}


def test_a_seed_request_carries_the_schema(wire):
    req = wire.parse_request(root_of(wire.build_seed_msg(run_state(ENUM), ["a"])))
    assert req["kind"] == "seed" and req["addressed_to"] == ["a"]
    assert req["schema_spec"] == ENUM and req["options"] == ["Plan A", "Plan B"]
    assert req["brief"]["context"] == "Pick <one> & go" and req["brief"]["candidate_schema"].startswith("exactly one of")
    open_req = wire.parse_request(root_of(wire.build_seed_msg(run_state(OPEN), ["a"])))
    assert open_req["schema_spec"] == OPEN and open_req["options"] is None


def test_the_instructions_name_the_format_when_it_is_enforced(wire):
    assert "exactly one of the listed options" in wire.build_seed_msg(run_state(ENUM), ["a"])
    pattern = {"type": "string", "description": "d", "pattern": "^x$"}
    assert "satisfy the candidate_schema exactly" in wire.build_repair_msg(run_state(pattern), "b")
    assert "exactly one of the listed" not in wire.build_seed_msg(run_state(OPEN), ["a"])


def test_a_score_request_lists_only_what_is_still_to_rate(wire):
    st = run_state()                                    # b has rated seed-a but not seed-b
    req = wire.parse_request(root_of(wire.build_score_msg(st, ["b"])))
    assert req["kind"] == "score" and req["candidates"] == [("seed-b", "Plan B", None)]
    st["raw"]["b"]["seed-b"] = 50                       # everything rated: the whole pool is shown
    assert [c[0] for c in wire.parse_request(root_of(wire.build_score_msg(st, ["a"])))["candidates"]] == \
        ["seed-a", "seed-b"]
    st["pool"].append({"id": "r1-b", "proposer": "b", "round": 1, "candidate": "Plan C", "rationale": "mix"})
    assert wire.parse_request(root_of(wire.build_score_msg(st, ["a"])))["candidates"] == [("r1-b", "Plan C", "mix")]


def test_a_repair_request_names_the_candidate_to_write_and_the_incumbent(wire):
    req = wire.parse_request(root_of(wire.build_repair_msg(run_state(), "b")))
    assert req["kind"] == "repair" and req["addressed_to"] == ["b"] and req["new_candidate_id"] == "r1-b"
    assert req["incumbent"] == ("seed-a", "Plan A", "cheap")


def test_a_commit_announces_the_outcome(wire):
    st = run_state()
    st["x_star"], st["outcome"] = "seed-a", "BEST_EFFORT"
    req = wire.parse_request(root_of(wire.build_commit_msg(st, "BEST_EFFORT")))
    assert req["kind"] == "commit" and req["outcome"] == "BEST_EFFORT" and req["winning_candidate_id"] == "seed-a"
    assert 'agent="b" value="no-reply"' not in wire.build_commit_msg(st, "BEST_EFFORT")
    st["raw"]["b"] = {}                                                    # b never rated the winner: shown as unknown
    assert 'agent="b" value="no-reply"' in wire.build_commit_msg(st, "BEST_EFFORT")


def test_every_request_builder_writes_a_tag_of_the_vocabulary(wire):
    st = run_state()
    docs = [wire.build_anchor_msg(st, ["a"]), wire.build_seed_msg(st, ["a"]), wire.build_score_msg(st, ["a"]),
            wire.build_repair_msg(st, "b"), wire.build_commit_msg(st, "SUCCESS")]
    assert [root_of(d).tag for d in docs] == list(wire.REQUEST_TAGS)
    assert [wire.parse_request(root_of(d))["kind"] for d in docs] == ["anchor", "seed", "score", "repair", "commit"]


def test_text_in_a_request_survives_escaping(wire):
    req = wire.parse_request(root_of(wire.build_anchor_msg(run_state(), ["a"])))
    st = run_state()
    st["x_reject"] = 'a < b & "c"'
    assert wire.parse_request(root_of(wire.build_anchor_msg(st, ["a"])))["brief"]["x_reject"] == 'a < b & "c"'
    assert req


def test_parse_request_ignores_what_is_not_a_request(wire):
    assert wire.parse_request(root_of("<concord-anchor-reply/>")) is None
    assert wire.parse_request(root_of("<other/>")) is None


def test_a_request_silent_about_its_params_gets_the_defaults(wire):
    req = wire.parse_request(root_of("<concord-anchor><participants><agent>a</agent></participants></concord-anchor>"))
    assert req["params"] == wire.DEFAULT_PARAMS and req["participants"] == ["a"] and req["addressed_to"] == []
    assert wire.parse_request(root_of("<concord-anchor/>"))["participants"] is None


def test_a_param_that_is_not_a_number_is_refused(wire):
    with pytest.raises(wire.ReplyError, match="<eps>.*not a number"):
        wire.parse_request(root_of("<concord-anchor><params><eps>soon</eps></params></concord-anchor>"))


# ---- replies: built by an agent, read by the initiator ---------------------------------------------------------------

def test_an_anchor_reply_reads_back_and_names_its_author(wire):
    xml = wire.build_anchor_reply(10.0, 90.0, note="n", agent="a", state="s1", ack=2)
    root = root_of(xml)
    assert (root.get("agent"), root.get("state"), root.get("ack")) == ("a", "s1", "2")
    assert wire.parse_reply(xml) == {"kind": "anchor", "raw_reject": 10.0, "raw_ideal": 90.0}
    assert root.find("note").text == "n"


def test_an_inverted_anchor_is_refused_before_it_is_written(wire):
    for bad in ((90, 10), (50, 50), (float("nan"), 50)):
        with pytest.raises(wire.ReplyError, match="must be strictly greater than raw_reject"):
            wire.build_anchor_reply(*bad)


def test_a_candidate_reply_reads_back(wire):
    xml = wire.build_candidate_reply("Plan <A> & more", "because", cid="seed-a", agent="a", state="s1")
    assert wire.parse_reply(xml) == {"kind": "candidate", "candidate": "Plan <A> & more", "rationale": "because",
                                     "id": "seed-a"}
    assert root_of(xml).get("id") == "seed-a" and root_of(xml).get("ack") is None
    assert wire.parse_reply(wire.build_candidate_reply("X", "y"))["id"] is None


def test_a_score_reply_reads_back(wire):
    xml = wire.build_score_reply([("seed-a", 70.5, "fine & cheap"), ("seed-b", 20, "slow")], agent="a")
    assert wire.parse_reply(xml) == {"kind": "scores", "scores": [
        {"id": "seed-a", "raw": 70.5, "reasoning": "fine & cheap"}, {"id": "seed-b", "raw": 20.0, "reasoning": "slow"}]}


def test_a_reply_is_found_inside_prose_a_fence_or_a_quoted_request(wire):
    reply = wire.build_candidate_reply("X", "why", agent="a")
    expect = {"kind": "candidate", "candidate": "X", "rationale": "why", "id": None}
    assert wire.parse_reply(reply) == expect
    assert wire.parse_reply("Here you go:\n\n```xml\n" + reply + "\n```\nThanks") == expect
    assert wire.parse_reply("I think so. " + reply + " Done.") == expect
    quoted = "As asked:\n" + wire.build_seed_msg(run_state(), ["a"]) + "\n" + reply   # quoted the request, then answered
    assert wire.parse_reply(quoted) == expect


def test_the_older_score_shapes_are_still_read(wire):
    legacy = ('<concord-score><score><c candidate_id="seed-a" raw="70">fine</c><c candidate_id="seed-b" raw="20">bad</c>'
              "</score></concord-score>")
    assert wire.parse_reply(legacy) == {"kind": "scores", "scores": [
        {"id": "seed-a", "raw": 70.0, "reasoning": "fine"}, {"id": "seed-b", "raw": 20.0, "reasoning": "bad"}]}


def test_unescaped_characters_copied_by_a_model_do_not_cost_the_reply(wire):
    xml = "<concord-candidate-reply><candidate>cost < 5 & fast</candidate><rationale>r</rationale></concord-candidate-reply>"
    assert wire.parse_reply(xml)["candidate"] == "cost < 5 & fast"


@pytest.mark.parametrize("text, message", [
    ("I would pick plan A, it is cheaper.", "reply contains no CONCORD XML"),
    ("<concord-candidate-reply><candidate>x", "reply is not valid XML"),
    ("<concord-nonsense/>", "unrecognised reply root tag <concord-nonsense>"),
    ("<concord-anchor-reply><raw_reject>1</raw_reject></concord-anchor-reply>", "anchor reply missing"),
    ("<concord-candidate-reply><rationale>r</rationale></concord-candidate-reply>", "candidate reply missing <candidate>"),
    ("<concord-candidate-reply><candidate> </candidate><rationale>r</rationale></concord-candidate-reply>",
     "empty <candidate>"),
    ("<concord-candidate-reply><candidate>x</candidate></concord-candidate-reply>", "candidate reply missing <rationale>"),
    ("<concord-score-reply></concord-score-reply>", "contains no <score> elements"),
    ("<concord-score-reply><score><raw>1</raw><reasoning>r</reasoning></score></concord-score-reply>",
     "missing candidate_id"),
    ("<concord-score-reply><score candidate_id='a'><reasoning>r</reasoning></score></concord-score-reply>",
     "no <raw> found for candidate_id 'a'"),
    ("<concord-score-reply><score candidate_id='a'><raw>1</raw></score></concord-score-reply>",
     "no <reasoning> found for candidate_id 'a'"),
])
def test_a_reply_that_cannot_be_read_is_refused_with_a_reason(wire, text, message):
    with pytest.raises(wire.ReplyError, match=message.replace("<", "<").replace("(", r"\(")):
        wire.parse_reply(text)


# ---- notices ---------------------------------------------------------------------------------------------------------

def test_a_delivery_tag_reads_back(wire):
    plain = root_of(wire.build_delivery_tag(3, "s1"))
    assert plain.attrib == {"seq": "3", "state": "s1"}
    assert wire.parse_delivery(plain) == {"seq": 3, "full": False, "awaiting": [""]}
    full = root_of(wire.build_delivery_tag(4, "s1", check="ab12cd34", full=True, awaiting=["a", "b"]))
    assert list(full.attrib) == ["seq", "state", "check", "full", "awaiting"]
    assert wire.parse_delivery(full) == {"seq": 4, "full": True, "awaiting": ["a", "b"]}
    assert wire.parse_delivery(root_of('<concord-delivery seq="x"/>')) is None
    assert wire.parse_delivery(root_of("<concord-delivery/>"))["seq"] == 0


def test_the_delivery_check_matches_the_replies_the_tag_carries(wire, engine):
    reply = wire.build_candidate_reply("X", "why", agent="a")
    check = engine.reply_check(reply)
    assert root_of(wire.build_delivery_tag(1, "s1", check=check)).get("check") == check


def test_ack_error_and_missing_name_their_agent_state_and_subject(wire):
    ack = root_of(wire.build_ack("a", "s1", 4))
    assert (ack.tag, ack.attrib) == (wire.ACK, {"agent": "a", "state": "s1", "seq": "4"})
    err = root_of(wire.build_error("a", "s1", 4, "send <it> again"))
    assert (err.tag, err.text, err.get("seq")) == (wire.ERROR, "send <it> again", "4")
    missing = root_of(wire.build_missing("b", "seed-a", "s1"))
    assert (missing.tag, missing.attrib) == (wire.MISSING, {"agent": "b", "candidate": "seed-a", "state": "s1"})


def test_the_vocabulary_is_consistent(wire):
    assert set(wire.KIND_OF_REQUEST) == set(wire.REQUEST_TAGS) and len(wire.REPLY_TAGS) == 3
    assert all(t.startswith("concord-") for t in (*wire.REQUEST_TAGS, *wire.REPLY_TAGS, wire.DELIVERY, wire.MISSING,
                                                  wire.ACK, wire.ERROR))
