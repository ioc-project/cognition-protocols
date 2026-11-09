#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The messages, as imported and as bundled into each script (see conftest.py)."""
import pytest

RUN = "11111111-1111-4111-8111-111111111111"
SPEC = {"type": "string", "description": "a plan"}
ENUM = {"enum": ["A", "B"]}


def init(wire, **kw):
    args = dict(from_="alice", participants=["cost", "security"], context="pick one", x_reject="nothing", x_ideal="all agree",
                spec=SPEC, run_id=RUN)
    return wire.build_init(**{**args, **kw})


def init_reply(wire, **kw):
    args = dict(from_="cost", run_id=RUN, raw_reject=8, raw_ideal=95, candidate="local cache", rationale="because",
                raw=82, reasoning="good")
    return wire.build_init_reply(**{**args, **kw})


def repair(wire, **kw):
    args = dict(from_="cost", run_id=RUN, n=1, candidate="bigger cache", rationale="because", raw=70, reasoning="ok")
    return wire.build_repair(**{**args, **kw})


def score(wire, **kw):
    args = dict(from_="cost", run_id=RUN, scores=[("seed-security", 41, "risky"), ("r1-security", 60.5, "better")])
    return wire.build_score(**{**args, **kw})


def refuses(wire, fn, match, *a, **kw):
    with pytest.raises(wire.InvalidMessage, match=match):
        fn(*a, **kw)


# --- every message round-trips ----------------------------------------------

def test_every_data_message_carries_a_correct_check_and_reads_back(wire):
    docs = {"init": init(wire), "init-reply": init_reply(wire), "repair": repair(wire), "score": score(wire),
            "result": wire.build_result("cost", RUN, "SUCCESS", "seed-cost")}
    for kind, doc in docs.items():
        assert wire.check_ok(doc), kind
        m = wire.parse_message(doc)
        assert (m["kind"], m["version"], m["run_id"], m["check"]) == (kind, "0.3.0", RUN, wire.peek(doc)["attrs"]["check"])
        assert wire.is_uuid(m["uuid"])


def test_init_reads_back_with_every_parameter(wire):
    m = wire.parse_message(init(wire, params={"satisfaction_threshold": 0.7, "eps": 0.1, "B": 4, "timeout": 60,
                                              "retries": 1}))
    assert m["participants"] == ["cost", "security"] and m["from"] == "alice"
    assert m["params"] == {"satisfaction_threshold": 0.7, "eps": 0.1, "B": 4, "timeout": 60, "retries": 1}
    assert (m["context"], m["x_reject"], m["x_ideal"], m["schema"]) == ("pick one", "nothing", "all agree", SPEC)


def test_init_defaults_fill_what_is_left_out_and_a_zero_timeout_is_valid(wire, protocol):
    assert wire.parse_message(init(wire))["params"] == protocol.DEFAULT_PARAMS
    assert wire.parse_message(init(wire, params={"timeout": 0}))["params"]["timeout"] == 0
    bare = init(wire).replace("<params>", "<x>").replace("</params>", "</x>")
    assert wire.parse_message(bare)["params"] == protocol.DEFAULT_PARAMS


def test_init_makes_a_run_id_when_none_is_given(wire):
    a, b = (wire.peek(init(wire, run_id=None))["attrs"]["run_id"] for _ in range(2))
    assert wire.is_uuid(a) and a != b


def test_init_reply_and_repair_carry_the_proposal_and_the_proposers_own_score(wire):
    m = wire.parse_message(init_reply(wire))
    assert (m["raw_reject"], m["raw_ideal"]) == (8.0, 95.0)
    assert m["proposal"] == {"id": "seed-cost", "round": 0, "candidate": "local cache", "rationale": "because", "raw": 82.0,
                             "reasoning": "good"}
    p = wire.parse_message(repair(wire, n=3))["proposal"]
    assert (p["id"], p["round"], p["raw"]) == ("r3-cost", 3, 70.0)


def test_score_reads_back_every_rating_in_order(wire):
    assert wire.parse_message(score(wire))["scores"] == [
        {"id": "seed-security", "raw": 41.0, "reasoning": "risky"}, {"id": "r1-security", "raw": 60.5, "reasoning": "better"}]


def test_result_with_and_without_a_missing_list_and_aborted(wire):
    m = wire.parse_message(wire.build_result("cost", RUN, "BEST_EFFORT", "seed-cost", missing=["security"]))
    assert (m["outcome"], m["winning_candidate_id"], m["missing"]) == ("BEST_EFFORT", "seed-cost", ["security"])
    assert wire.parse_message(wire.build_result("cost", RUN, "SUCCESS", "seed-cost"))["missing"] == []
    doc = wire.build_result("cost", RUN, "ABORTED", "seed-cost")
    assert "winning_candidate_id" not in doc and wire.parse_message(doc)["winning_candidate_id"] is None


def test_error_resend_and_cancel_read_back(wire):
    err = wire.build_error("cost", RUN, "security", "score", "missing", candidate="seed-cost", text="I miss <it> & more")
    m = wire.parse_message(err)
    assert (m["to"], m["what"], m["candidate"], m["reason"], m["text"]) == ("security", "score", "seed-cost", "missing",
                                                                          "I miss <it> & more")
    assert wire.peek(err)["attrs"].get("check") is None
    bare = wire.build_error("cost", RUN, "security", "init", "busy")
    assert bare.endswith("/>") and wire.parse_message(bare)["text"] == ""
    chk = wire.build_error("cost", RUN, "security", "score", "check", ref=RUN)
    assert wire.parse_message(chk)["ref"] == RUN and wire.parse_message(chk)["candidate"] is None
    c = wire.parse_message(wire.build_cancel("alice", RUN, "superseded", superseded_by="00000000-0000-4000-8000-000000000000"))
    assert (c["reason"], c["superseded_by"]) == ("superseded", "00000000-0000-4000-8000-000000000000")
    assert wire.parse_message(wire.build_cancel("alice", RUN, "version"))["superseded_by"] is None


def test_every_send_gets_a_new_uuid_and_a_given_one_is_kept(wire):
    assert wire.peek(score(wire))["attrs"]["uuid"] != wire.peek(score(wire))["attrs"]["uuid"]
    assert wire.peek(score(wire, uid=RUN))["attrs"]["uuid"] == RUN


def test_the_text_of_a_message_survives_escaping_and_quotes(wire):
    cand = 'a < b & c > d, "quoted", \'single\''
    m = wire.parse_message(init_reply(wire, candidate=cand, rationale="x &amp; <y>"))
    assert m["proposal"]["candidate"] == cand and m["proposal"]["rationale"] == "x &amp; <y>"
    doc = init_reply(wire, candidate=cand)
    assert '"quoted"' in doc and "&lt;" in doc
    odd = wire.build_score('we"ird&agent', RUN, [("seed-cost", 5, "r")])
    assert wire.peek(odd)["attrs"]["from"] == 'we"ird&agent' and wire.parse_message(odd)["from"] == 'we"ird&agent'
    assert wire.check_ok(odd)


# --- the author's responsibility --------------------------------------------

def test_init_is_refused_when_it_breaks_a_rule_of_the_configuration(wire):
    refuses(wire, init, "at least 2", wire, participants=["a"])
    refuses(wire, init, "duplicate", wire, participants=["a", "a"])
    refuses(wire, init, "satisfaction_threshold", wire, params={"satisfaction_threshold": 0})
    refuses(wire, init, "budget", wire, params={"B": 0})
    refuses(wire, init, "retries", wire, params={"retries": -1})
    refuses(wire, init, "timeout", wire, params={"timeout": -5})
    refuses(wire, init, "unknown parameters: nope", wire, params={"nope": 1})
    refuses(wire, init, "context is required", wire, context=" ")
    refuses(wire, init, "x_ideal is required", wire, x_ideal="")
    refuses(wire, init, "keywords that are not enforced", wire, spec={"description": "d", "color": 1})
    refuses(wire, init, "needs a", wire, spec={})
    refuses(wire, init, "run_id must be a UUID", wire, run_id="nope")


def test_init_reply_is_refused_when_invalid(wire):
    refuses(wire, init_reply, "strictly greater", wire, raw_reject=50, raw_ideal=50)
    refuses(wire, init_reply, "raw_reject must be a number from 0 to 100", wire, raw_reject=-1)
    refuses(wire, init_reply, "raw_ideal must be a number from 0 to 100", wire, raw_ideal=101)
    refuses(wire, init_reply, "raw must be a number from 0 to 100", wire, raw=float("nan"))
    refuses(wire, init_reply, "the candidate is required", wire, candidate="  ")
    refuses(wire, init_reply, "the rationale is required", wire, rationale="")
    refuses(wire, init_reply, "the reasoning is required", wire, reasoning="")
    refuses(wire, init_reply, "must be exactly one of", wire, spec=ENUM, candidate="C")
    assert wire.parse_message(init_reply(wire, spec=ENUM, candidate="A"))["proposal"]["candidate"] == "A"


def test_repair_is_refused_when_invalid(wire):
    refuses(wire, repair, "at least 1", wire, n=0)
    refuses(wire, repair, "must be a number from 0", wire, raw=500)
    refuses(wire, repair, "must be exactly one of", wire, spec=ENUM, candidate="zzz")


def test_score_is_refused_when_empty_duplicated_or_for_its_own_candidate(wire):
    refuses(wire, score, "at least one", wire, scores=[])
    refuses(wire, score, "which it proposed", wire, scores=[("seed-cost", 5, "r")])
    refuses(wire, score, "which it proposed", wire, scores=[("r2-cost", 5, "r")])
    refuses(wire, score, "rated twice", wire, scores=[("seed-b", 5, "r"), ("seed-b", 6, "r")])
    refuses(wire, score, "not a candidate id", wire, scores=[("nope", 5, "r")])
    refuses(wire, score, "0 to 100", wire, scores=[("seed-b", 101, "r")])
    refuses(wire, score, "reasoning for seed-b is required", wire, scores=[("seed-b", 5, " ")])


def test_result_error_resend_cancel_are_refused_when_inconsistent(wire):
    refuses(wire, wire.build_result, "outcome must be", "a", RUN, "WON", "seed-a")
    refuses(wire, wire.build_result, "winning candidate", "a", RUN, "SUCCESS")
    refuses(wire, wire.build_result, "winning candidate", "a", RUN, "BEST_EFFORT", "junk")
    e = lambda **kw: wire.build_error(**{"from_": "a", "run_id": RUN, "to": "b", "what": "score", "reason": "missing",
                                         "candidate": "seed-b", **kw})
    refuses(wire, e, "reason must be", reason="lost")
    refuses(wire, e, "what must be", what="thing")
    refuses(wire, e, "needs candidate", candidate=None)
    refuses(wire, e, "candidate goes only with", what="result", candidate="seed-b")
    refuses(wire, e, "needs ref", reason="check", candidate=None)
    refuses(wire, e, "ref goes only", ref=RUN)
    refuses(wire, e, "goes only with reason", what="init", reason="missing", candidate=None)
    refuses(wire, e, "what must be \"init\"", what="score", reason="busy")
    refuses(wire, e, "goes only with reason", what="init", reason="check", ref=RUN, candidate=None)
    refuses(wire, wire.build_cancel, "reason must be", "a", RUN, "because")
    refuses(wire, wire.build_cancel, "superseded_by", "a", RUN, "superseded")
    refuses(wire, wire.build_cancel, "superseded_by", "a", RUN, "version", superseded_by=RUN)


def test_a_participant_can_send_again_only_its_own_data_messages(wire):
    original = init_reply(wire)
    refuses(wire, wire.build_resend, "only send again its own", "security", RUN, original)
    refuses(wire, wire.build_resend, "a message of the run", "cost", RUN, wire.build_cancel("cost", RUN, "version"))
    refuses(wire, wire.build_resend, "a message of the run", "cost", RUN, "just text")


# --- resend ------------------------------------------------------------------

def test_resend_carries_the_stored_original_untouched(wire):
    original = score(wire, from_="security", scores=[("seed-cost", 44, "no")])
    doc = wire.build_resend("security", RUN, original, reply_to=RUN)
    m = wire.parse_message(doc)
    assert m["kind"] == "resend" and m["reply_to"] == RUN and m["inner"] == original
    assert wire.check_ok(m["inner"]) and wire.peek(doc)["inner"]["attrs"]["from"] == "security"
    assert wire.peek(doc)["inner"]["attrs"]["uuid"] == wire.peek(original)["attrs"]["uuid"] != wire.peek(doc)["attrs"]["uuid"]
    assert wire.parse_message(wire.build_resend("security", RUN, original))["reply_to"] is None


def test_the_initiator_sends_init_again_inside_a_resend(wire):
    original = init(wire)
    m = wire.parse_message(wire.build_resend("alice", RUN, original))
    assert m["inner"] == original and wire.parse_message(m["inner"])["kind"] == "init"


def test_a_resend_must_hold_exactly_one_message_of_the_run(wire):
    uid = "22222222-2222-4222-8222-222222222222"
    head = f'<concord-resend version="0.3.0" from="a" run_id="{RUN}" uuid="{uid}">'
    refuses(wire, wire.parse_message, "exactly one", head + "</concord-resend>")
    refuses(wire, wire.parse_message, "exactly one", head + "<x/></concord-resend>")
    refuses(wire, wire.parse_message, "exactly one", head + score(wire) + score(wire) + "</concord-resend>")


# --- the integrity check -----------------------------------------------------

def test_a_reindented_or_rewrapped_message_still_verifies_and_a_changed_word_does_not(wire):
    doc = score(wire)
    assert wire.check_ok(doc.replace("\n  ", "\n\n        ").replace("</raw>", "</raw>   "))
    assert not wire.check_ok(doc.replace("risky", "risk"))
    assert not wire.check_ok(doc.replace("41.0", "14.0"))
    assert not wire.check_ok(doc.replace("seed-security", "seed-secure"))
    assert not wire.check_ok(doc.replace(' check="', ' check="0'))
    assert not wire.check_ok(wire.build_error("a", RUN, "b", "init", "busy"))


def test_a_damaged_message_can_still_be_peeked_to_know_whom_to_ask(wire):
    attrs = wire.peek(score(wire).replace("risky", "risk"))["attrs"]
    assert (attrs["from"], attrs["run_id"], attrs["version"]) == ("cost", RUN, "0.3.0") and wire.is_uuid(attrs["uuid"])


# --- locating and peeking ----------------------------------------------------

def test_locate_takes_the_document_unchanged_out_of_prose_or_a_fence(wire):
    doc = score(wire)
    assert wire.locate(doc) == doc and wire.locate(f"\n\n  {doc}\n") == doc
    assert wire.locate(f"Here you go:\n```xml\n{doc}\n```\nbye") == doc
    assert wire.locate(f"I will send {doc} now, ok?") == doc
    err = wire.build_error("a", RUN, "b", "init", "busy")
    assert wire.locate(f"x {err} y") == err
    res = wire.build_resend("cost", RUN, init_reply(wire))
    assert wire.locate(f"> {res} <") == res


def test_locate_refuses_what_is_not_a_complete_message(wire):
    for text in ("just words", "<other>x</other>", "<concord-score from='a'>never closed", "<concord-score from='a'"):
        refuses(wire, wire.locate, "CONCORD|closed|cut off", text)


def test_peek_reads_the_root_of_anything_and_nothing_of_the_rest(wire):
    assert wire.peek("<other/>") is None and wire.peek("words") is None
    err = '<concord-error version="9.9.9" run_id="R" from="x" to="y" what="init" reason="version" uuid="u">I speak 9.9.9, ' \
          'got 0.3.0 &amp; more</concord-error>'
    p = wire.peek(err)
    assert (p["tag"], p["attrs"]["reason"], p["attrs"]["version"], p["text"]) == (
        "concord-error", "version", "9.9.9", "I speak 9.9.9, got 0.3.0 & more")


# --- the receiver's side -----------------------------------------------------

def test_a_receiver_refuses_what_is_not_well_formed_or_holds_forbidden_markup(wire):
    ok = score(wire)
    refuses(wire, wire.parse_message, "not well-formed", ok.replace("risky", "a < b"))
    refuses(wire, wire.parse_message, "not well-formed", ok.replace("risky", "a & b"))
    refuses(wire, wire.parse_message, "no comments", ok.replace("<raw>41.0", "<!-- hi --><raw>41.0"))
    refuses(wire, wire.parse_message, "no comments", ok.replace("risky", "<![CDATA[risky]]>"))
    refuses(wire, wire.parse_message, "no comments", "<?xml version='1.0'?>" + ok)
    refuses(wire, wire.parse_message, "unknown message", "<concord-nope/>")


def test_a_receiver_refuses_missing_attributes_and_bad_uuids(wire):
    ok = score(wire)
    for attr in ("version", "from", "run_id", "uuid"):
        import re
        refuses(wire, wire.parse_message, f"needs the attribute {attr}", re.sub(rf' {attr}="[^"]*"', "", ok, count=1))
    refuses(wire, wire.parse_message, "run_id must be a UUID", ok.replace(RUN, "not-a-uuid"))


def test_a_receiver_refuses_a_proposal_with_the_wrong_id(wire):
    refuses(wire, wire.parse_message, "is not seed-cost", init_reply(wire).replace('id="seed-cost"', 'id="seed-security"'))
    refuses(wire, wire.parse_message, "is not seed-cost", init_reply(wire).replace('id="seed-cost"', 'id="r1-cost"'))
    refuses(wire, wire.parse_message, "is not r<N>-cost", repair(wire).replace('id="r1-cost"', 'id="seed-cost"'))
    refuses(wire, wire.parse_message, "is not r<N>-cost", repair(wire).replace('id="r1-cost"', 'id="r1-security"'))


def test_a_receiver_refuses_invalid_scores_and_anchors_it_reads(wire):
    refuses(wire, wire.parse_message, "rates seed-cost, which it proposed", score(wire).replace("seed-security", "seed-cost"))
    refuses(wire, wire.parse_message, "rated twice", score(wire).replace("r1-security", "seed-security"))
    refuses(wire, wire.parse_message, "0 to 100", score(wire).replace("41.0", "141.0"))
    refuses(wire, wire.parse_message, "is not a number", score(wire).replace("41.0", "lots"))
    refuses(wire, wire.parse_message, "must exceed", init_reply(wire).replace("<raw_ideal>95.0", "<raw_ideal>5.0"))
    refuses(wire, wire.parse_message, "0 to 100", init_reply(wire).replace("<raw_reject>8.0", "<raw_reject>-8.0"))
    refuses(wire, wire.parse_message, "<reasoning> in <score> is empty",
            init_reply(wire).replace("<reasoning>good</reasoning>", "<reasoning> </reasoning>"))
    refuses(wire, wire.parse_message, r"<anchors> is missing", init_reply(wire).replace("anchors", "ancho"))


def test_a_receiver_refuses_an_init_that_breaks_the_configuration(wire):
    ok = init(wire)
    refuses(wire, wire.parse_message, "at least 2", ok.replace("<agent>security</agent>", ""))
    refuses(wire, wire.parse_message, "duplicate", ok.replace("<agent>security</agent>", "<agent>cost</agent>"))
    refuses(wire, wire.parse_message, "budget is not an integer|<budget> is not an integer", ok.replace("<budget>8", "<budget>8.5"))
    refuses(wire, wire.parse_message, "eps", ok.replace("<eps>0.05", "<eps>0"))
    refuses(wire, wire.parse_message, "timeout", ok.replace("<timeout>300", "<timeout>-1"))
    refuses(wire, wire.parse_message, "not valid JSON", ok.replace("&quot;", "").replace('"type"', "type").replace("{", "{{"))
    refuses(wire, wire.parse_message, "not enforced", ok.replace("&lt;", "<").replace('"type"', '"color": 1, "type"'))
    refuses(wire, wire.parse_message, "<context> in <concord-init> is empty", ok.replace("pick one", " "))


def test_the_schema_of_the_run_is_enforced_when_the_caller_has_it(wire):
    doc = init_reply(wire, candidate="C")
    assert wire.parse_message(doc)["proposal"]["candidate"] == "C"
    refuses(wire, wire.parse_message, "must be exactly one of", doc, ENUM)
    refuses(wire, wire.parse_message, "must be exactly one of", repair(wire, candidate="C"), ENUM)


def test_error_and_cancel_are_checked_on_the_way_in_too(wire):
    err = wire.build_error("a", RUN, "b", "score", "missing", candidate="seed-b")
    refuses(wire, wire.parse_message, "needs candidate", err.replace(' candidate="seed-b"', ""))
    refuses(wire, wire.parse_message, "needs the attribute to", err.replace(' to="b"', ""))
    cancel = wire.build_cancel("a", RUN, "version")
    refuses(wire, wire.parse_message, "reason must be", cancel.replace('"version"', '"why"'))


# --- the examples of the spec ------------------------------------------------

def test_the_examples_of_the_spec_read(wire):
    spec_reply = '''<concord-init-reply version="0.3.0" from="cost" run_id="%s" uuid="6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51" check="e5f6a7b8">
  <anchors>
    <raw_reject>8.0</raw_reject>
    <raw_ideal>95.0</raw_ideal>
  </anchors>
  <proposal id="seed-cost">
    <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
    <rationale>evidence and reasoning</rationale>
    <score>
      <raw>82.0</raw>
      <reasoning>full reasoning text</reasoning>
    </score>
  </proposal>
</concord-init-reply>''' % RUN
    assert wire.parse_message(spec_reply)["proposal"]["raw"] == 82.0
    result = '''<concord-result version="0.3.0" from="cost" run_id="%s" uuid="9d2f4c60-18a7-4e35-b3c9-5a0e7d1f6b82" check="0f1e2d3c">
  <outcome>BEST_EFFORT</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
  <missing>
    <agent>security</agent>
  </missing>
</concord-result>''' % RUN
    assert wire.parse_message(result)["missing"] == ["security"]
    cancel = '<concord-cancel version="0.3.0" from="alice" run_id="%s" uuid="8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36" ' \
             'reason="superseded" superseded_by="OTHER-RUNID"/>' % RUN
    assert wire.parse_message(cancel)["superseded_by"] == "OTHER-RUNID"
