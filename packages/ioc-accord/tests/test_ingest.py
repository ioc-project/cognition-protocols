#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Reading replies: an agent answers in whatever shape it likes, and an unreadable answer is skipped, not reported."""
import json

from conftest import ingest  # noqa: F401


def test_a_reply_may_be_a_dict_a_fenced_block_or_prose_around_an_object(ingest):  # noqa: F811
    raw = [
        {"agent": "sre"},
        "```json\n{\"agent\": \"dba\"}\n```",
        "Sure! Here is my card:\n{\"agent\": \"net\"}\nLet me know if you need more.",
        "no json here at all",
        None,
    ]
    assert [d["agent"] for d in ingest.parse_responses(raw)] == ["sre", "dba", "net"]


def test_roster_accepts_either_name_for_a_field(ingest):  # noqa: F811
    cards = ingest.ingest_roster([
        {"agent": "sre", "roles": ["owner"], "access_scopes": ["prod"], "holdings_summary": "alerts"},
        {"identity": "dba", "accessScopes": ["db"], "holdingsSummary": "slow queries"},
        {"roles": ["nameless"]},
    ])
    assert [c["identity"] for c in cards] == ["sre", "dba"]
    assert cards[0]["accessScopes"] == ["prod"] and cards[0]["holdingsSummary"] == "alerts"
    assert cards[1]["accessScopes"] == ["db"] and cards[1]["roles"] == []


def test_a_frame_round_without_an_atoms_list_is_not_a_round(ingest):  # noqa: F811
    by_agent, truncated, saw_atoms = ingest.ingest_frame_round([{"agent": "sre", "notes": "thinking about it"}])
    assert (by_agent, truncated, saw_atoms) == ({}, [], False)


def test_frame_atoms_are_cut_at_the_limit_and_the_cut_is_reported(ingest):  # noqa: F811
    atoms = [{"text": f"atom {i}"} for i in range(ingest.MAX_FRAME_ATOMS + 3)]
    by_agent, truncated, saw_atoms = ingest.ingest_frame_round([{"agent": "net", "atoms": atoms}])
    assert saw_atoms
    assert len(by_agent["net"]) == ingest.MAX_FRAME_ATOMS
    assert truncated == [{"agent": "net", "submitted": ingest.MAX_FRAME_ATOMS + 3,
                          "kept": ingest.MAX_FRAME_ATOMS, "dropped": 3}]


def test_an_atom_carries_its_agent_and_defaults_to_a_term(ingest):  # noqa: F811
    by_agent, _, _ = ingest.ingest_frame_round([
        {"agent": "sre", "atoms": [{"text": "latency spike", "type": "observation"}, {"text": "  "}, "not a dict"]},
    ])
    assert by_agent["sre"] == [{"text": "latency spike", "agent": "sre", "kind": "observation"}]


def test_an_empty_atoms_list_is_still_a_round(ingest):  # noqa: F811
    by_agent, _, saw_atoms = ingest.ingest_frame_round([{"agent": "dba", "atoms": []}])
    assert saw_atoms and by_agent == {"dba": []}


def test_glossary_reads_the_ground_shapes_and_the_clarify_shape(ingest):  # noqa: F811
    terms = ingest.ingest_glossary([
        {"agent": "sre", "units": {"severity": "sev1 is an outage", "unknown_term": "ignored"}},
        {"agent": "dba", "groundings": [{"term": "error_rate", "unit": "percent of failed requests"}]},
        json.dumps({"agent": "net", "term": "severity", "extended_definition": "sev1 is a total outage"}),
    ], ["severity", "error_rate"])
    assert terms == [
        ("severity", "sev1 is an outage", "sre"),
        ("error_rate", "percent of failed requests", "dba"),
        ("severity", "sev1 is a total outage", "net"),
    ]


def test_one_agent_grounds_a_term_once(ingest):  # noqa: F811
    terms = ingest.ingest_glossary([
        {"agent": "sre", "units": {"severity": "from the units map"},
         "groundings": [{"term": "severity", "unit": "and again from groundings"}]},
    ], ["severity"])
    assert terms == [("severity", "from the units map", "sre")]


def test_verify_collects_holdings_and_checks_under_either_name(ingest):  # noqa: F811
    covers, checks, all_holds = ingest.ingest_verify([
        {"agent": "sre", "holds": ["alert history", " slo budget ", ""],
         "checks": [{"criterion": "p99 under 300ms"}]},
        {"agent": "dba", "holds": ["alert history"], "proposed_checks": [{"criterion": "no saturation"}, "junk"]},
        {"holds": ["nameless"]},
    ])
    assert covers == {"sre": ["alert history", "slo budget"], "dba": ["alert history"]}
    assert all_holds == ["alert history", "slo budget"]
    assert [(c["agent"], c["criterion"]) for c in checks] == [("sre", "p99 under 300ms"), ("dba", "no saturation")]
