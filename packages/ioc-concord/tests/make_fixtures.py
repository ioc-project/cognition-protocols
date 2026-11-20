#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Writes the cross-implementation fixtures into protocols/concord/spec/fixtures/ (see the README there).

    uv run python tests/make_fixtures.py

The files are the contract: tests/test_fixtures.py reads them, and so can any other implementation. Run this only when the wire
format or the spec changes on purpose, and look at the diff: it is the change that every implementation has to follow.
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_agent import MemStore, Net  # noqa: E402

from ioc_concord import agent as agent_mod  # noqa: E402
from ioc_concord import schema, wire  # noqa: E402
from ioc_concord.xmlutil import canonical_text, check_of, compute_check, with_check  # noqa: E402

OUT = Path(__file__).resolve().parents[3] / "protocols" / "concord" / "spec" / "fixtures"
RUN = "11111111-1111-4111-8111-111111111111"
SPEC = {"description": "a short plan, in lower case", "pattern": "^[a-z ]+$", "maxLength": 80}
PARAMS = {"satisfaction_threshold": 0.7, "eps": 0.1, "B": 3, "timeout": 60, "retries": 2}


def uid(n):
    return f"a0000000-0000-4000-8000-{n:012x}"


def write(path, text):
    path = OUT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


def dump(path, value):
    write(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False))


def forge(doc, old, new):
    """`doc` with `old` replaced by `new` and the check computed again: intact, but not what an honest author sends."""
    assert old in doc, (old, doc[:200])
    own = wire.check_of(doc)
    return with_check(doc.replace(f' check="{own}"', "").replace(old, new, 1))


# --------------------------------------------------------------------------
# valid messages
# --------------------------------------------------------------------------

def messages():
    init = wire.build_init("alice", ["cost", "security"], "Cut errors to < 1% & keep the cost down", "errors stay as they are",
                           "no error and a low cost", SPEC, params=PARAMS, run_id=RUN, uid=uid(1))
    reply_cost = wire.build_init_reply("cost", RUN, 10, 90, "cache the answers", "cheap & fast", 82.5, "good for cost",
                                       spec=SPEC, uid=uid(2))
    reply_security = wire.build_init_reply("security", RUN, 0, 100, "check every input", 'the "safe" way', 40, "slow", spec=SPEC,
                                           uid=uid(3))
    score = wire.build_score("cost", RUN, [("seed-security", 41.5, "risky & slow"), ("r1-security", 60, "better")], uid=uid(4))
    repair = wire.build_repair("security", RUN, 1, "check only what is new", "less work < before", 70.25, "fair", spec=SPEC,
                               uid=uid(5))
    result = wire.build_result("cost", RUN, "SUCCESS", "r1-security", uid=uid(6))
    missing = wire.build_error("cost", RUN, "security", "score", "missing", candidate="seed-cost", text="never got it & asked", uid=uid(7))
    version = wire.build_error("security", RUN, "alice", "init", "version", text="I speak 0.2.0; this init is version 0.3.0.",
                               uid=uid(8))
    resend = wire.build_resend("security", RUN, reply_security, reply_to=uid(7), uid=uid(9))
    cancel = wire.build_cancel("cost", RUN, "missing-agent", agent="security", uid=uid(10))
    return {"init": init, "init-reply-cost": reply_cost, "init-reply-security": reply_security, "score": score, "repair": repair,
            "result": result, "error-missing": missing, "error-version": version, "resend": resend, "cancel": cancel}


def valid_cases(docs):
    out = []
    for name, doc in docs.items():
        attrs = wire.peek(doc)["attrs"]
        parsed = wire.parse_message(doc, SPEC if name != "init" else None)
        out.append({"file": f"wire/{name}.xml", "kind": parsed["kind"], "from": attrs["from"], "run_id": attrs["run_id"],
                    "uuid": attrs["uuid"], "check": attrs.get("check"), "parsed": parsed})
    return out


def tricky(docs):
    """Messages that are valid but read differently by a careless reader: the text of a document is never rewritten."""
    score = docs["score"]
    who = wire.peek(score)["attrs"]["from"]
    return {
        "hidden-from-in-a-single-quoted-value": forge(score, f' from="{who}"', f' from="{who}" x=\' from="security"\''),
        "hidden-check-in-a-single-quoted-value": forge(score, f' from="{who}"', f' x=\'a check="00000000"\' from="{who}"'),
        "single-quotes-and-a-gt-in-a-value": forge(score, f' from="{who}"', f" from='{who}' x='a>b' y=\"/>\""),
        "blanks-and-unicode-spaces-in-the-text": forge(docs["init-reply-cost"], "<rationale>cheap &amp; fast</rationale>",
                                                       "<rationale>\n   cheap   &amp;\t\tfast\n</rationale>"),
    }


# --------------------------------------------------------------------------
# messages that must be refused
# --------------------------------------------------------------------------

def refused(docs):
    init, reply, score, repair, result = (docs[k] for k in ("init", "init-reply-cost", "score", "repair", "result"))
    cases = [
        ("score-id-with-a-line-feed", forge(score, 'candidate_id="seed-security"', 'candidate_id="seed-security&#10;"'),
         "a candidate id is exactly seed-<agent> or r<N>-<agent>"),
        ("proposal-id-with-a-line-feed", forge(reply, 'id="seed-cost"', 'id="seed-cost&#10;"'), "the id of a proposal"),
        ("proposal-id-of-another-author", forge(reply, 'id="seed-cost"', 'id="seed-security"'), "an init reply proposes seed-<from>"),
        ("repair-round-zero", forge(repair, 'id="r1-security"', 'id="r0-security"'), "a repair round starts at 1"),
        ("winner-that-is-not-an-id", forge(result, "r1-security", "nope"), "the winner is a candidate id"),
        ("result-outcome-aborted", forge(result, "SUCCESS", "ABORTED"), "a result is SUCCESS or BEST_EFFORT"),
        ("timeout-zero", forge(init, "<timeout>60</timeout>", "<timeout>0</timeout>"), "timeout is above 0"),
        ("budget-zero", forge(init, "<budget>3</budget>", "<budget>0</budget>"), "B is at least 1"),
        ("retries-negative", forge(init, "<retries>2</retries>", "<retries>-1</retries>"), "retries is at least 0"),
        ("empty-timeout", forge(init, "<timeout>60</timeout>", "<timeout></timeout>"), "an empty number is not in the form"),
        ("empty-eps", forge(init, "<eps>0.1</eps>", "<eps/>"), "an empty number is not in the form"),
        ("threshold-above-one", forge(init, "<satisfaction_threshold>0.7<", "<satisfaction_threshold>1.5<"), "tau is at most 1"),
        ("eps-zero", forge(init, "<eps>0.1</eps>", "<eps>0</eps>"), "eps is above 0"),
        ("number-without-leading-digit", forge(score, "<raw>60.0</raw>", "<raw>.5</raw>"), "a number is [0-9]{1,3}(.[0-9]{1,6})?"),
        ("number-with-an-exponent", forge(score, "<raw>60.0</raw>", "<raw>1e2</raw>"), "a number is [0-9]{1,3}(.[0-9]{1,6})?"),
        ("number-with-a-plus-sign", forge(score, "<raw>60.0</raw>", "<raw>+5</raw>"), "a number is [0-9]{1,3}(.[0-9]{1,6})?"),
        ("number-with-seven-decimals", forge(score, "<raw>60.0</raw>", "<raw>5.1234567</raw>"), "at most 6 decimals"),
        ("score-above-100", forge(score, "<raw>60.0</raw>", "<raw>101</raw>"), "a score is 0 to 100"),
        ("anchors-not-ordered", forge(reply, "<raw_ideal>90.0</raw_ideal>", "<raw_ideal>5.0</raw_ideal>"), "raw_ideal exceeds raw_reject"),
        ("agent-id-too-long", forge(score, 'from="cost"', 'from="' + "a" * 65 + '"'), "an agent id is 1 to 64 of A-Za-z0-9_-"),
        ("agent-id-with-a-space", forge(score, 'from="cost"', 'from="co st"'), "an agent id is 1 to 64 of A-Za-z0-9_-"),
        ("upper-case-uuid", forge(score, f'uuid="{uid(4)}"', f'uuid="{uid(4).upper()}"'), "a uuid is lower case"),
        ("run-id-not-a-uuid", forge(score, f'run_id="{RUN}"', 'run_id="run-1"'), "a run id is a uuid"),
        ("repeated-attribute", score.replace(' from="cost"', ' from="cost" from="security"', 1), "an element holds an attribute once"),
        ("a-comment", forge(score, "<score ", "<!-- no --><score "), "no comments"),
        ("a-cdata-section", forge(score, "risky &amp; slow", "<![CDATA[risky]]>"), "no CDATA"),
        ("a-processing-instruction", "<?xml version=\"1.0\"?>" + score, "no processing instructions"),
        ("enum-option-with-outer-blanks", _enum_init(init, '{"enum": [" a ", "b"]}'), "an enum option is trimmed like a candidate"),
        ("pattern-with-a-lookahead", _enum_init(init, '{"description": "d", "pattern": "(?=a)b"}'), "a pattern is the subset"),
        ("pattern-with-a-backslash-b", _enum_init(init, '{"description": "d", "pattern": "\\\\bab"}'), "a pattern is the subset"),
        ("schema-keyword-not-supported", _enum_init(init, '{"description": "d", "format": "uri"}'), "an unsupported schema keyword"),
        ("candidate-breaks-the-schema", forge(reply, "cache the answers", "Cache The Answers"), "a candidate satisfies the schema"),
        ("cancel-missing-agent-without-agent", forge(docs["cancel"], ' agent="security"', ""), "missing-agent needs agent"),
    ]
    return cases


def _enum_init(init, schema_json):
    """`init` with its candidate schema replaced by the JSON `schema_json`, the check computed again."""
    m = re.search(r"<candidate_schema>.*?</candidate_schema>", init, re.S)
    return forge(init, m.group(0), f"<candidate_schema>{schema_json.replace('&', '&amp;').replace('<', '&lt;')}</candidate_schema>")


# --------------------------------------------------------------------------
# the check, the patterns
# --------------------------------------------------------------------------

def check_vectors(docs):
    raw = [
        ("a plain element", '<concord-cancel version="0.3.0" run_id="R" from="a" uuid="U" reason="superseded"/>'),
        ("blank runs in text become one space", '<x a="1">\n  <y>  one \t two\r\n three  </y>\n</x>'),
        ("text of blanks only is removed", "<x><y>   </y><z>\n</z></x>"),
        ("a non-ASCII space is not whitespace", "<x><y> one </y></x>"),
        ("a check in the value of another attribute is not the check", "<x a='b check=\"c\"' check=\"00000000\"><y>t</y></x>"),
        ("quotes and entities stay as sent", "<x a='1' b=\"&quot;q&quot;\"><y>a &amp; b &lt; c &gt; d \"e\"</y></x>"),
    ]
    out = []
    for name, doc in raw:
        out.append({"name": name, "document": doc, "canonical": canonical_text(doc), "check": compute_check(doc),
                    "declared": check_of(doc)})
    for name in ("init", "score", "resend"):
        doc = docs[name]
        out.append({"name": f"the {name} of the fixtures", "document": doc, "canonical": canonical_text(doc),
                    "check": compute_check(doc), "declared": check_of(doc)})
    return out


def pattern_cases():
    valid = [
        ("ab", ["ab", "xaby"], ["a", "ba"]),
        ("^ab$", ["ab"], ["xab", "abx", "ab\n"]),
        ("a|bc", ["a", "xbc"], ["b", "c"]),
        ("^(?:ab)+$", ["ab", "ababab"], ["", "aba"]),
        ("^(a|b)c?$", ["a", "bc"], ["c", "abc"]),
        ("^a{2}$", ["aa"], ["a", "aaa"]),
        ("^a{2,}$", ["aa", "aaaa"], ["a"]),
        ("^a{1,2}$", ["a", "aa"], ["", "aaa"]),
        ("^[a-c]+$", ["abc", "cab"], ["d", "abd"]),
        ("^[^a-c]+$", ["xyz", "-"], ["a", "xa"]),
        ("^[-a]+$", ["-a-"], ["b"]),
        ("^\\d+$", ["0123"], ["٣", "a", ""]),
        ("^\\w+$", ["aZ_9"], ["é", "a b", "-"]),
        ("^\\s$", [" ", "\t", "\r", "\n"], [" ", "a"]),
        ("^a\\.b$", ["a.b"], ["axb"]),
        ("^.$", ["a", "é", "\U0001f600", " ", " ", "\u0085"], ["", "ab", "\r", "\n"]),
        ("a.b", ["a b", "a b"], ["a\rb", "a\nb"]),
        ("a$", ["b\na", "a"], ["a\n", "a\nb"]),
        ("^b", ["b"], ["a\nb"]),
        ("é", ["café"], ["cafe"]),
        ("^(a+)+$", ["a" * 30], ["a" * 30 + "!"]),
    ]
    refused = ["(?=a)b", "(?!a)b", "(?<=a)b", "(?<n>a)", "(?i)a", "(a)\\1", "\\bab", "\\p{L}", "\\u0041", "\\x41", "\\n", "\\t", "\\D",
               "[\\d]", "[]", "[a", "[z-a]", "a)", "(a", "*a", "a**", "a+?", "a*?", "a{2}{3}", "a{", "a{3,2}", "^*", "a]", "a}"]
    for p, yes, no in valid:
        schema.compile_pattern(p)
    return {"valid": [{"pattern": p, "match": yes, "no_match": no} for p, yes, no in valid], "refused": refused}


# --------------------------------------------------------------------------
# the state of a node
# --------------------------------------------------------------------------

def state_case(docs):
    """The state of `cost` after the init and both init replies, and the score of `security` that arrives next."""
    store = MemStore(agent_mod)
    net = Net(agent_mod, ["cost", "security"], store=None)
    cost = agent_mod.Agent("cost", store=store, clock=lambda: 0, uuid_fn=_uuids("c0000000"))
    net.agents["cost"] = cost
    net.agents["security"] = agent_mod.Agent("security", clock=lambda: 0, uuid_fn=_uuids("b0000000"))
    net.start(docs["init"])
    net.answer_init("cost", 10, 90, candidate="cache the answers", raw=82.5)
    net.answer_init("security", 0, 100, candidate="check every input", raw=40)
    nxt = wire.build_score("security", RUN, [("seed-cost", 30, "slow")], uid=uid(20))
    return json.loads(store.text), nxt, cost


def _uuids(prefix):
    import itertools
    n = itertools.count(1)
    return lambda: f"{prefix}-0000-4000-8000-{next(n):012x}"


def main():
    for stale in ("wire", "state"):                               # what is generated goes; the README stays
        shutil.rmtree(OUT / stale, ignore_errors=True)
    docs = messages()
    for name, doc in docs.items():
        write(f"wire/{name}.xml", doc)
    for name, doc in tricky(docs).items():
        write(f"wire/tricky/{name}.xml", doc)
    cases = valid_cases(docs)
    for name, doc in tricky(docs).items():
        attrs = wire.peek(doc)["attrs"]
        cases.append({"file": f"wire/tricky/{name}.xml", "kind": wire.parse_message(doc, SPEC)["kind"], "from": attrs["from"],
                      "run_id": attrs["run_id"], "uuid": attrs["uuid"], "check": attrs["check"], "parsed": wire.parse_message(doc, SPEC)})
    dump("wire/valid.json", cases)
    bad = refused(docs)
    for name, doc, _ in bad:
        write(f"wire/refused/{name}.xml", doc)
    dump("wire/refused.json", [{"file": f"wire/refused/{name}.xml", "why": why} for name, _, why in bad])
    dump("check.json", check_vectors(docs))
    dump("patterns.json", pattern_cases())
    state, nxt, cost = state_case(docs)
    dump("state/cost-after-init-replies.json", state)
    write("state/next-message.xml", nxt)
    before = cost.status()
    after = agent_mod.Agent("cost", store=MemStore_from(state), clock=lambda: 0, uuid_fn=_uuids("c1000000"))
    step = after.receive(nxt, now=0)
    dump("state/expected.json", {"agent": "cost", "run_id": RUN, "next": "state/next-message.xml", "now": 0,
                                 "before": {"status": before["status"], "state": before["state"], "lacking": before["lacking"]},
                                 "after": {"status": after.status()["status"], "state": after.status()["state"],
                                           "lacking": after.status()["lacking"]},
                                 "sends": [wire.peek(t)["tag"] for t in step.send]})


def MemStore_from(state):
    class S:
        def load(self):
            return json.loads(json.dumps(state))

        def save(self, st):
            pass
    return S()


if __name__ == "__main__":
    main()
    print(f"fixtures written to {OUT}")
