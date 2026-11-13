#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The decision engine, as imported and as bundled into each script (see conftest.py)."""
import pytest

M = 1_000_000                       # every number of the engine is a count of millionths
PARAMS = {"satisfaction_threshold": 600_000, "eps": 50_000, "B": 3, "timeout": 300, "retries": 3}
FULL = (0, 100)


def m(x):
    """`x` (a raw score or a normalized score written as in the spec) in millionths."""
    return round(x * M)


def new(engine, agents, anchors=None, **params):
    st = engine.new_engine_state(agents, {**PARAMS, **params})
    for a in agents:
        engine.add_anchor(st, a, *(m(v) for v in (anchors or {}).get(a, FULL)))
    return st


def seed(engine, st, agent, raw, text=None):
    engine.add_proposal(st, agent, f"seed-{agent}", text or f"s{agent}", "because", m(raw), "mine")


def rate(engine, st, agent, **raw):
    engine.add_ratings(st, agent, [(cid.replace("_", "-"), m(r), "reasons") for cid, r in raw.items()])


def repair(engine, st, agent, n, raw):
    engine.add_proposal(st, agent, f"r{n}-{agent}", f"r{n}", "because", m(raw), "mine")


# --- parameters and ids ----------------------------------------------------

def test_check_params_accepts_defaults_and_a_zero_timeout(engine, protocol):
    engine.check_params(["a", "b"], protocol.DEFAULT_PARAMS)
    engine.check_params(["a", "b"], {**protocol.DEFAULT_PARAMS, "timeout": 0, "retries": 0})
    engine.check_params(["a", "b"], {**protocol.DEFAULT_PARAMS, "satisfaction_threshold": 1_000_000, "eps": 1})


@pytest.mark.parametrize("agents, change, message", [
    (["a"], {}, "at least 2 agents"),
    (["a", "a"], {}, "duplicate"),
    (["a", " "], {}, "1 to 64 letters"),
    (["a", "b c"], {}, "1 to 64 letters"),
    (["a", "x" * 65], {}, "1 to 64 letters"),
    (["a", "b"], {"satisfaction_threshold": 0}, "above 0 and at most 1"),
    (["a", "b"], {"satisfaction_threshold": 1_000_001}, "above 0 and at most 1"),
    (["a", "b"], {"satisfaction_threshold": 0.6}, "above 0 and at most 1"),
    (["a", "b"], {"satisfaction_threshold": True}, "above 0 and at most 1"),
    (["a", "b"], {"eps": 0}, "eps"),
    (["a", "b"], {"eps": 0.05}, "eps"),
    (["a", "b"], {"B": 0}, "budget"),
    (["a", "b"], {"B": 1.5}, "budget"),
    (["a", "b"], {"timeout": -1}, "timeout"),
    (["a", "b"], {"retries": -1}, "retries"),
    (["a", "b"], {"retries": 1.5}, "retries"),
])
def test_check_params_refuses_what_the_spec_forbids(engine, protocol, agents, change, message):
    with pytest.raises(engine.InvalidRun, match=message):
        engine.check_params(agents, {**protocol.DEFAULT_PARAMS, **change})


def test_a_zero_timeout_switches_the_timers_off(engine):
    assert engine.timers_enabled({"timeout": 300}) and not engine.timers_enabled({"timeout": 0})


def test_candidate_ids_round_trip(engine):
    assert engine.seed_id("cost") == "seed-cost" and engine.repair_id(2, "security") == "r2-security"
    assert engine.parse_candidate_id("seed-cost") == (0, "cost")
    assert engine.parse_candidate_id("r12-my-agent") == (12, "my-agent")
    for bad in ("r0-a", "seed-", "x-a", "r1", "", None):
        assert engine.parse_candidate_id(bad) is None


# --- recording -------------------------------------------------------------

def test_the_first_anchors_stand_and_a_bad_scale_is_refused(engine):
    st = engine.new_engine_state(["a", "b"], PARAMS)
    assert engine.add_anchor(st, "a", m(20), m(80))
    assert not engine.add_anchor(st, "a", m(0), m(100))
    assert st["anchors"]["a"] == {"raw_reject": 20 * M, "raw_ideal": 80 * M}
    for bad in ((m(50), m(50)), (m(80), m(20)), (0, 1.5), (-1, m(50)), (m(10), m(101)), (True, m(50)), (m(20), 80.0)):
        with pytest.raises(engine.InvalidMessage):
            engine.add_anchor(st, "b", *bad)
    assert "b" not in st["anchors"]


def test_a_proposal_carries_its_own_score_and_the_first_one_stands(engine):
    st = new(engine, ["a", "b"])
    assert engine.add_proposal(st, "a", "seed-a", "X", "why", m(80), "good")
    assert st["raw"]["a"] == {"seed-a": 80 * M} and st["reasoning"]["a"] == {"seed-a": "good"}
    assert not engine.add_proposal(st, "a", "seed-a", "Y", "other", m(10), "bad")
    assert [c["candidate"] for c in st["pool"]] == ["X"] and st["raw"]["a"]["seed-a"] == 80 * M


def test_a_proposal_with_the_wrong_id_or_score_is_refused(engine):
    st = new(engine, ["a", "b"])
    for cid in ("seed-b", "r1-b", "r0-a", "a", "seed-z"):
        with pytest.raises(engine.InvalidMessage, match="is not seed-a or"):
            engine.add_proposal(st, "a", cid, "X", "r", m(50), "w")
    for raw in (m(101), -1, 50.0):
        with pytest.raises(engine.InvalidMessage, match="0 to 100"):
            engine.add_proposal(st, "a", "seed-a", "X", "r", raw, "w")
    assert st["pool"] == []


def test_ratings_are_recorded_once_and_unknown_candidates_are_reported_not_refused(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    assert engine.add_ratings(st, "b", [("seed-a", m(60), "ok"), ("seed-x", m(10), "?")]) == (["seed-a"], ["seed-x"], [])
    assert st["raw"]["b"] == {"seed-a": 60 * M}


def test_a_rating_the_agent_already_gave_is_ignored_and_the_first_one_stands(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    assert engine.add_ratings(st, "b", [("seed-a", m(60), "ok")]) == (["seed-a"], [], [])
    assert engine.add_ratings(st, "b", [("seed-a", m(5), "again")]) == ([], [], ["seed-a"])
    assert st["raw"]["b"] == {"seed-a": 60 * M} and st["reasoning"]["b"] == {"seed-a": "ok"}
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    assert engine.add_ratings(st, "b", [("seed-a", m(50), "x"), ("seed-a", m(60), "y")]) == (["seed-a"], [], ["seed-a"])
    assert st["raw"]["b"] == {"seed-a": 50 * M}


def test_an_entry_that_rates_the_senders_own_candidate_is_ignored_and_the_others_are_kept(engine):
    st = new(engine, ["a", "b", "c"])
    seed(engine, st, "a", 80)
    seed(engine, st, "b", 70)
    seed(engine, st, "c", 60)
    got = engine.add_ratings(st, "b", [("seed-a", m(50), "x"), ("seed-b", m(50), "own"), ("seed-c", m(40), "y")])
    assert got == (["seed-a", "seed-c"], [], ["seed-b"])
    assert st["raw"]["b"] == {"seed-b": 70 * M, "seed-a": 50 * M, "seed-c": 40 * M}
    # an own candidate the receiver does not hold is ignored too, not asked for
    assert engine.add_ratings(st, "b", [("r1-b", m(50), "own")]) == ([], [], ["r1-b"])


def test_a_score_out_of_range_makes_the_whole_message_invalid_and_records_nothing(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    seed(engine, st, "b", 80)
    for ratings in ([("seed-a", m(150), "x")], [("seed-a", m(50), "x"), ("seed-b", -1, "x")], [("seed-a", 50.5, "x")]):
        with pytest.raises(engine.InvalidMessage, match="0 to 100"):
            engine.add_ratings(st, "b", ratings)
    assert st["raw"]["b"] == {"seed-b": 80 * M}


# --- scores ----------------------------------------------------------------

def test_norm_is_relative_to_the_anchors_and_clamped(engine):
    st = new(engine, ["a", "b"], {"a": (20, 80)})
    assert engine.norm(st, "a", m(50)) == 500_000
    assert engine.norm(st, "a", m(10)) == 0 and engine.norm(st, "a", m(95)) == M
    assert engine.norm(st, "a", m(20)) == 0 and engine.norm(st, "a", m(80)) == M


def test_norm_is_exact_and_rounds_half_up_to_six_places(engine):
    st = new(engine, ["a", "b"], {"a": (0, 3)})
    assert engine.norm(st, "a", m(1)) == 333_333           # 0.3333333...
    assert engine.norm(st, "a", m(2)) == 666_667           # 0.6666666...
    st = new(engine, ["a", "b"], {"a": (0, 2)})
    assert engine.norm(st, "a", 1) == 1                    # 0.0000005 exactly, half up
    assert engine.norm(st, "a", 3) == 2                    # 0.0000015 exactly
    assert engine.norm(st, "a", 5) == 3
    st = new(engine, ["a", "b"], {"a": (0, 8)})
    assert engine.norm(st, "a", 1) == 0                    # 0.000000125 -> 0
    assert engine.norm(st, "a", 7) == 1                    # 0.000000875 -> 1
    st = new(engine, ["a", "b"], {"a": (0.1, 0.9)})
    assert engine.norm(st, "a", m(0.5)) == 500_000         # no float error in 0.5 - 0.1


def test_pool_order_is_round_then_place_in_the_roster_never_arrival(engine):
    st = new(engine, ["a", "b", "c"])
    seed(engine, st, "c", 50)
    seed(engine, st, "a", 50)
    seed(engine, st, "b", 50)
    assert [c["id"] for c in engine.ordered_pool(st)] == ["seed-a", "seed-b", "seed-c"]
    repair(engine, st, "b", 1, 50)
    assert [c["id"] for c in engine.ordered_pool(st)][-1] == "r1-b"
    assert engine.repairs_in_pool(st) == 1 and engine.next_round(st) == 2


def test_the_reporter_is_the_first_participant(engine):
    assert engine.reporter(engine.new_engine_state(["z", "a"], PARAMS)) == "z"


def test_lex_gt_compares_position_by_position(engine):
    assert engine.lex_gt([500_000, 900_000], [500_000, 800_000]) and not engine.lex_gt([400_000, M], [500_000, 0])
    assert not engine.lex_gt([500_000, 500_000], [500_000, 500_000])
    assert engine.lex_gt([500_001], [500_000]) and not engine.lex_gt([500_000], [500_001])


# --- SELECT and ACCEPT -----------------------------------------------------

def test_select_takes_the_best_leximin_and_the_first_in_the_pool_on_a_tie(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 70)
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=70)
    # seed-a: (0.7, 0.9) -> sorted [0.7, 0.9]; seed-b: [0.7, 0.7]
    assert engine.select(st, ["seed-a", "seed-b"]) == "seed-a"
    assert engine.select(st, ["seed-b", "seed-a"]) == "seed-a"
    st2 = new(engine, ["a", "b"])
    seed(engine, st2, "a", 70)
    seed(engine, st2, "b", 70)
    rate(engine, st2, "a", seed_b=70)
    rate(engine, st2, "b", seed_a=70)
    assert engine.select(st2, ["seed-a", "seed-b"]) == "seed-a"
    assert engine.select(st2, ["seed-b", "seed-a"]) == "seed-b"


def duel(engine, inc, cand, **params):
    """Two agents that give both candidates the same raw score: `inc` for seed-a, `cand` for seed-b."""
    st = new(engine, ["a", "b"], **params)
    seed(engine, st, "a", inc)
    seed(engine, st, "b", cand)
    rate(engine, st, "a", seed_b=cand)
    rate(engine, st, "b", seed_a=inc)
    return st


def test_accept_needs_eps_on_the_same_side_of_tau_but_not_above_it(engine):
    assert not engine.accept(duel(engine, 50, 52), "seed-b", "seed-a")      # [.52,.52] vs [.50,.50]: gain .02 < eps
    assert not engine.accept(duel(engine, 61, 60), "seed-a", "seed-b")      # both clear tau: gain .01 < eps
    assert engine.accept(duel(engine, 59, 62), "seed-b", "seed-a")          # [.62,.62] clears tau, [.59,.59] does not


def test_accept_compares_exactly_at_the_edge_of_eps_and_tau(engine):
    # 0.3 - 0.2 is 0.09999999999999998 in floating point, so a float engine would refuse a gain of exactly eps
    assert engine.accept(duel(engine, 20, 30, satisfaction_threshold=M, eps=100_000), "seed-b", "seed-a")
    assert not engine.accept(duel(engine, 20, 29.99994, satisfaction_threshold=M, eps=100_000), "seed-b", "seed-a")
    # the floor of the candidate is exactly tau (0.599999 is below it, and 59.99994 on a scale of 100 rounds to that)
    assert engine.accept(duel(engine, 59.99994, 60), "seed-b", "seed-a")
    assert not engine.accept(duel(engine, 59.99992, 59.99994), "seed-b", "seed-a")


def test_accept_refuses_a_gain_that_costs_an_earlier_position(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 40)
    seed(engine, st, "b", 30)
    rate(engine, st, "a", seed_b=90)        # seed-b: a=.90, b=.30 -> [.30, .90]; seed-a: b rates .50 -> [.40, .50]
    rate(engine, st, "b", seed_a=50)
    assert not engine.accept(st, "seed-b", "seed-a")                    # position 0 loses .10
    assert engine.accept(st, "seed-a", "seed-b")                        # position 0 gains .10


# --- evaluate --------------------------------------------------------------

def test_evaluate_waits_for_init_replies_and_for_ratings(engine):
    st = engine.new_engine_state(["a", "b"], PARAMS)
    assert engine.evaluate(st)["state"] == "wait"
    engine.add_anchor(st, "a", 0, m(100))
    seed(engine, st, "a", 90)
    assert engine.evaluate(st)["state"] == "wait"
    engine.add_anchor(st, "b", 0, m(100))
    seed(engine, st, "b", 90)
    ev = engine.evaluate(st)
    assert ev["state"] == "wait" and ev["x_star"] is None
    assert sorted(engine.lacking(st)) == [("a", "seed-b"), ("b", "seed-a")]


def test_success_when_everyone_clears_tau(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=75)
    ev = engine.evaluate(st)
    assert (ev["state"], ev["outcome"], ev["x_star"], ev["reporter"]) == ("done", "SUCCESS", "seed-a", "a")
    assert ev["floor"] == 750_000 and ev["shortfall"] == {}
    assert ev["scores"] == {"a": 900_000, "b": 750_000}


def test_the_floor_exactly_at_tau_is_success(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 60)
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=60)
    assert engine.evaluate(st)["outcome"] == "SUCCESS"
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 59.99994)
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=59.99994)
    ev = engine.evaluate(st)
    assert ev["state"] == "repair" and ev["shortfall"] == {"b": 1}


def test_the_bottleneck_proposes_a_repair_and_the_round_is_numbered(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=20)
    rate(engine, st, "b", seed_a=30)
    ev = engine.evaluate(st)
    # seed-a [.30,.90], seed-b [.20,.80] -> incumbent seed-a; the bottleneck is b (.30)
    assert (ev["state"], ev["x_star"], ev["bottleneck"], ev["round"], ev["t"]) == ("repair", "seed-a", "b", 1, 0)
    assert ev["shortfall"] == {"b": 300_000}


def test_a_tie_for_bottleneck_goes_to_the_first_in_participants(engine):
    st = new(engine, ["a", "b", "c"])
    for ag in "abc":
        seed(engine, st, ag, 50, text=f"s{ag}")
    for ag in "abc":
        for other in "abc":
            if other != ag:
                rate(engine, st, ag, **{f"seed_{other}": 50})
    assert engine.evaluate(st)["bottleneck"] == "a"


def test_a_repair_that_clears_tau_for_everyone_ends_in_success(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=20)
    rate(engine, st, "b", seed_a=30)
    repair(engine, st, "b", 1, 70)
    assert engine.evaluate(st)["state"] == "wait"
    rate(engine, st, "a", r1_b=65)
    ev = engine.evaluate(st)
    assert (ev["outcome"], ev["x_star"], ev["t"]) == ("SUCCESS", "r1-b", 1)
    assert [e["replaced"] for e in ev["log"]] == [True, True]


def test_best_effort_when_the_budget_is_spent_and_not_before(engine):
    st = new(engine, ["a", "b"], B=1)
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=20)
    rate(engine, st, "b", seed_a=30)
    assert engine.evaluate(st)["state"] == "repair"
    repair(engine, st, "b", 1, 30)
    rate(engine, st, "a", r1_b=30)
    ev = engine.evaluate(st)
    assert (ev["outcome"], ev["t"], ev["x_star"]) == ("BEST_EFFORT", 1, "seed-a")
    assert ev["shortfall"] == {"b": 300_000}
    assert [e["replaced"] for e in ev["log"]] == [True, False]


def test_every_repair_counts_in_t_even_when_the_incumbent_stays(engine):
    st = new(engine, ["a", "b"], B=2)
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=20)
    rate(engine, st, "b", seed_a=30)
    repair(engine, st, "b", 1, 30)
    rate(engine, st, "a", r1_b=30)
    ev = engine.evaluate(st)
    assert (ev["state"], ev["round"], ev["t"]) == ("repair", 2, 1)


def test_the_run_has_no_other_outcome_than_success_or_best_effort(engine):
    st = new(engine, ["a", "b"], B=1)
    seed(engine, st, "a", 10)
    seed(engine, st, "b", 10)
    rate(engine, st, "a", seed_b=10)
    rate(engine, st, "b", seed_a=10)
    repair(engine, st, "a", 1, 10)
    rate(engine, st, "b", r1_a=10)
    assert engine.evaluate(st)["outcome"] == "BEST_EFFORT"


def table(engine, raw, **params):
    """A run of a, b and c whose raw scores are `raw[agent][candidate]` (each agent's seed is its own `seed-<agent>`)."""
    st = new(engine, ["a", "b", "c"], **params)
    for ag in "abc":
        seed(engine, st, ag, raw[ag][f"seed-{ag}"])
    for ag in "abc":
        engine.add_ratings(st, ag, [(cid, m(r), "w") for cid, r in raw[ag].items() if cid != f"seed-{ag}"])
    return st


UNHAPPY_C = {"a": {"seed-a": 90, "seed-b": 50, "seed-c": 30},
             "b": {"seed-a": 70, "seed-b": 80, "seed-c": 30},
             "c": {"seed-a": 30, "seed-b": 30, "seed-c": 30}}
three = lambda engine: table(engine, UNHAPPY_C)


def test_three_agents_pick_the_best_leximin_and_the_lowest_scorer_repairs(engine):
    ev = engine.evaluate(three(engine))
    assert (ev["state"], ev["x_star"], ev["bottleneck"], ev["round"], ev["reporter"]) == ("repair", "seed-a", "c", 1, "a")
    assert ev["scores"] == {"a": 900_000, "b": 700_000, "c": 300_000}


def test_evaluate_does_not_change_the_state(engine):
    import copy
    st = three(engine)
    before = copy.deepcopy(st)
    engine.evaluate(st)
    engine.evaluate(st)
    assert st == before


def test_agents_that_hold_the_same_messages_decide_alike_whatever_the_arrival_order(engine):
    def build(order):
        st = new(engine, ["a", "b", "c"])
        for ag in order:
            seed(engine, st, ag, {"a": 70, "b": 70, "c": 70}[ag])
        for ag in order:
            rate(engine, st, ag, **{f"seed_{o}": 70 for o in "abc" if o != ag})
        return engine.evaluate(st)
    assert build("abc") == build("cba") == build("bca")
