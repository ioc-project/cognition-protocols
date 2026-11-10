#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The deterministic merges, one phase at a time."""
import pytest

from conftest import engine  # noqa: F401


def atoms(*pairs):
    return [{"text": text, "agent": agent, "kind": "term"} for agent, text in pairs]


def test_the_roster_is_the_union_of_the_cards_first_one_wins(engine):  # noqa: F811
    cards = [{"identity": "sre", "roles": ["owner"]}, {"identity": "sre", "roles": ["observer"]},
             {"identity": "dba", "roles": []}]
    assert engine.union_roster(cards) == [{"identity": "sre", "roles": ["owner"]}, {"identity": "dba", "roles": []}]


def test_two_agents_claiming_one_exclusive_role_is_a_defect(engine):  # noqa: F811
    st = {"roster": [], "defects": []}
    engine.merge_roster(st, [{"agent": "sre", "roles": ["Owner"]}, {"agent": "dba", "roles": ["owner"]}])
    assert st["defects"] == ["duplicate exclusive role 'owner' claimed by ['sre', 'dba']"]


def test_support_counts_agents_not_atoms(engine):  # noqa: F811
    frame = engine.group_atoms(atoms(
        ("sre", "latency spike on checkout"),
        ("dba", "latency spike on checkout"),
        ("dba", "latency spike on checkout"),
        ("net", "packet loss on the edge link"),
    ))
    assert [(s["id"], s["text"], s["support"], s["agents"]) for s in frame] == [
        ("s1", "latency spike on checkout", 2, ["sre", "dba"]),
        ("s2", "packet loss on the edge link", 1, ["net"]),
    ]


def test_coverage_estimators_on_a_frame_of_singletons(engine):  # noqa: F811
    assert engine.good_turing_missing_mass([1, 1, 1]) == 1.0
    assert engine.good_turing_missing_mass([]) == 1.0
    assert engine.good_turing_missing_mass([2, 2]) == 0.0
    assert engine.chao1([]) == 0.0
    assert engine.chao1([1, 1, 2]) == 3 + (2 * 2) / 2.0
    assert engine.chao1([1, 1, 3]) == 3 + (2 * 1) / 2.0


def test_one_definition_per_term_is_an_agreed_glossary_entry(engine):  # noqa: F811
    entries, ambiguous = engine.build_glossary([
        ("error_rate", "errors per second", "sre"),
        ("error_rate", "Errors per second!", "dba"),
    ])
    assert ambiguous == []
    assert entries == [{"term": "error_rate", "status": "agreed", "definition": "errors per second",
                        "agents": ["sre", "dba"]}]


def test_a_near_match_is_flagged_and_sent_to_clarify(engine):  # noqa: F811
    entries, ambiguous = engine.build_glossary([
        ("severity", "sev1 means user facing outage", "sre"),
        ("severity", "sev1 is a total outage", "net"),
    ])
    assert entries[0]["status"] == "flagged"
    assert [v["agent"] for v in entries[0]["variants"]] == ["sre", "net"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["term"] == "severity"
    assert engine.SIM_CLARIFY <= ambiguous[0]["sim"] < engine.SIM_MERGE


def test_definitions_too_far_apart_are_flagged_without_a_clarify_round(engine):  # noqa: F811
    entries, ambiguous = engine.build_glossary([
        ("error_rate", "errors per second", "sre"),
        ("error_rate", "the share of orders a customer abandons", "dba"),
    ])
    assert entries[0]["status"] == "flagged" and ambiguous == []


def test_set_cover_takes_the_largest_remaining_contribution_first(engine):  # noqa: F811
    owned, uncovered = engine.greedy_set_cover(["a", "b", "c"], {"sre": ["a"], "dba": ["a", "b"]})
    assert owned == {"sre": [], "dba": ["a", "b"]} and uncovered == ["c"]


def test_the_verifier_never_owns_knowledge(engine):  # noqa: F811
    owned, uncovered = engine.assign_holdings(["alerts", "audit log"],
                                              {"sre": ["alerts"], "Verifier": ["audit log"]})
    assert owned == {"sre": ["alerts"], "Verifier": []} and uncovered == ["audit log"]


def test_gaps_beyond_the_tolerance_are_handed_to_the_first_agent(engine):  # noqa: F811
    universe = ["a", "b", "c", "d"]
    owned, uncovered = engine.assign_holdings(universe, {"sre": ["a"], "dba": []})
    assert len(uncovered) == engine.MAX_UNCOVERED_HOLDINGS
    assert sorted(owned["sre"] + owned["dba"] + uncovered) == universe


def test_a_check_is_kept_only_while_it_discharges_a_new_clause(engine):  # noqa: F811
    clauses = ["latency returns to normal", "pool has headroom"]
    checks = engine.assign_checks([
        {"criterion": "p99 under 300ms", "covers": ["latency returns to normal"], "agent": "sre"},
        {"criterion": "also p99", "covers": ["Latency returns to normal"], "agent": "dba"},
        {"criterion": "no saturation", "covers": ["pool has headroom"], "agent": "dba"},
        {"criterion": "unrelated", "covers": ["nothing at all"], "agent": "net"},
    ], clauses)
    assert [(c["atom"], c["assigned_to"]) for c in checks] == [
        ("latency returns to normal", "sre"), ("pool has headroom", "dba")]


def test_a_frame_of_more_than_three_singletons_is_fragile(engine, session):  # noqa: F811
    session["frame"] = engine.group_atoms(atoms(("sre", "one"), ("dba", "two"), ("net", "three"), ("sre", "four")))
    contract = engine.assemble_lock(session)
    assert contract["status"] == "INTENT-LOCKED"
    assert "fragile_frame: all 4 atoms are unverified singletons" in contract["flagged_items"]
    assert all(a["flagged_singleton"] for a in contract["problem_frame"])


def test_every_unresolved_item_reaches_the_contract(engine, session):  # noqa: F811
    session["glossary"] = [{"term": "severity", "status": "flagged", "variants": []}]
    session["uncovered"] = ["runbook"]
    session["coverage_warnings"] = ["coverage_untested: no required knowledge"]
    session["owned"] = {"sre": ["alerts"]}
    session["checks"] = [{"atom": "x", "criterion": "c", "assigned_to": "sre"}, {"atom": "", "criterion": ""}]
    contract = engine.assemble_lock(session)
    assert contract["flagged_items"] == ["ambiguous_term: severity", "uncovered_atom: runbook",
                                         "coverage_untested: no required knowledge"]
    assert contract["verifiable_objective"]["owners"] == {"alerts": "sre"}
    assert contract["verifiable_objective"]["checks"] == [{"atom": "x", "criterion": "c", "assigned_to": "sre"}]


def test_a_frame_round_replaces_only_the_atoms_of_the_agents_that_answered(engine, session):  # noqa: F811
    session["obs"] = atoms(("sre", "latency spike"), ("dba", "pool exhausted"))
    engine.record_frame_round(session, {"sre": atoms(("sre", "retries amplify load"))})
    assert [(o["agent"], o["text"]) for o in session["obs"]] == [
        ("dba", "pool exhausted"), ("sre", "retries amplify load")]
    assert session["frame_rounds"] == 1
    assert [s["text"] for s in session["frame"]] == ["pool exhausted", "retries amplify load"]


def test_a_clarify_round_leaves_the_terms_it_was_not_asked_about_alone(engine, session):  # noqa: F811
    session["glossary"] = [{"term": "error_rate", "status": "agreed", "definition": "errors per second",
                            "agents": ["sre"]}]
    engine.record_glossary(session, [("severity", "sev1 is an outage", "sre")])
    assert [(e["term"], e["status"]) for e in session["glossary"]] == [("error_rate", "agreed"), ("severity", "agreed")]


def test_verify_maps_knowledge_and_checks_onto_the_session(engine, session):  # noqa: F811
    covers, universe = engine.record_verify(
        session,
        [{"agent": "sre", "holds": ["alerts"], "checks": [{"criterion": "p99", "covers": ["latency is fine"]}]}],
        ["alerts", "runbook"],
        ["latency is fine"],
    )
    assert covers == {"sre": ["alerts"]} and universe == ["alerts", "runbook"]
    assert session["owned"] == {"sre": ["alerts"]} and session["uncovered"] == ["runbook"]
    assert session["checks"][0]["assigned_to"] == "sre"


def test_verify_falls_back_to_what_was_reported_when_no_universe_is_given(engine, session):  # noqa: F811
    _, universe = engine.record_verify(session, [{"agent": "sre", "holds": ["alerts"]}], [], [])
    assert universe == ["alerts"] and session["uncovered"] == []


def test_the_contract_is_kept_on_the_session(engine, session):  # noqa: F811
    contract = engine.store_contract(session)
    assert session["locked_contract"] == contract


@pytest.mark.parametrize("name", ["GROUP_THRESHOLD", "SIM_MERGE", "SIM_CLARIFY"])
def test_the_thresholds_are_scores(engine, name):  # noqa: F811
    assert 0.0 < getattr(engine, name) <= 1.0
