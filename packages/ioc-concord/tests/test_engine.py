#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The decision engine, as imported and as bundled into each script (see conftest.py)."""
import pytest

PARAMS = {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 3, "timeout": 300, "retries": 3}
FULL = (0, 100)


def new(engine, agents, anchors=None, **params):
    st = engine.new_engine_state(agents, {**PARAMS, **params})
    for a in agents:
        engine.add_anchor(st, a, *(anchors or {}).get(a, FULL))
    return st


def seed(engine, st, agent, raw, text=None):
    engine.add_proposal(st, agent, f"seed-{agent}", text or f"s{agent}", "because", raw, "mine")


def rate(engine, st, agent, **raw):
    engine.add_ratings(st, agent, [(cid.replace("_", "-"), r, "reasons") for cid, r in raw.items()])


def repair(engine, st, agent, n, raw):
    engine.add_proposal(st, agent, f"r{n}-{agent}", f"r{n}", "because", raw, "mine")


# --- parameters and ids ----------------------------------------------------

def test_check_params_accepts_defaults_and_a_zero_timeout(engine, protocol):
    engine.check_params(["a", "b"], protocol.DEFAULT_PARAMS)
    engine.check_params(["a", "b"], {**protocol.DEFAULT_PARAMS, "timeout": 0, "retries": 0})


@pytest.mark.parametrize("agents, change, message", [
    (["a"], {}, "at least 2 agents"),
    (["a", "a"], {}, "duplicate"),
    (["a", " "], {}, "cannot be empty"),
    (["a", "b"], {"satisfaction_threshold": 0}, r"\(0, 1\]"),
    (["a", "b"], {"satisfaction_threshold": 1.1}, r"\(0, 1\]"),
    (["a", "b"], {"satisfaction_threshold": float("nan")}, r"\(0, 1\]"),
    (["a", "b"], {"eps": 0}, "eps"),
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
    assert engine.add_anchor(st, "a", 20, 80)
    assert not engine.add_anchor(st, "a", 0, 100)
    assert st["anchors"]["a"] == {"raw_reject": 20, "raw_ideal": 80}
    for bad in ((50, 50), (80, 20), (0, float("nan")), (-1, 50), (10, 101), (True, 50)):
        with pytest.raises(engine.InvalidMessage):
            engine.add_anchor(st, "b", *bad)
    assert "b" not in st["anchors"]


def test_a_proposal_carries_its_own_score_and_the_first_one_stands(engine):
    st = new(engine, ["a", "b"])
    assert engine.add_proposal(st, "a", "seed-a", "X", "why", 80, "good")
    assert st["raw"]["a"] == {"seed-a": 80} and st["reasoning"]["a"] == {"seed-a": "good"}
    assert not engine.add_proposal(st, "a", "seed-a", "Y", "other", 10, "bad")
    assert [c["candidate"] for c in st["pool"]] == ["X"] and st["raw"]["a"]["seed-a"] == 80


def test_a_proposal_with_the_wrong_id_or_score_is_refused(engine):
    st = new(engine, ["a", "b"])
    for cid in ("seed-b", "r1-b", "r0-a", "a", "seed-z"):
        with pytest.raises(engine.InvalidMessage, match="is not seed-a or"):
            engine.add_proposal(st, "a", cid, "X", "r", 50, "w")
    with pytest.raises(engine.InvalidMessage, match="0 to 100"):
        engine.add_proposal(st, "a", "seed-a", "X", "r", 101, "w")
    assert st["pool"] == []


def test_ratings_are_recorded_once_and_unknown_candidates_are_reported_not_refused(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    assert engine.add_ratings(st, "b", [("seed-a", 60, "ok"), ("seed-x", 10, "?")]) == (["seed-a"], ["seed-x"])
    assert engine.add_ratings(st, "b", [("seed-a", 5, "again")]) == ([], [])
    assert st["raw"]["b"] == {"seed-a": 60}


def test_a_score_message_that_rates_its_own_candidate_twice_or_out_of_range_is_invalid_and_records_nothing(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 80)
    seed(engine, st, "b", 80)
    for ratings, message in (([("seed-b", 50, "x")], "which it proposed"),
                             ([("seed-a", 50, "x"), ("seed-a", 60, "y")], "rated twice"),
                             ([("seed-a", 150, "x")], "0 to 100"),
                             ([("seed-a", 50, "x"), ("seed-b", 50, "x")], "which it proposed")):
        with pytest.raises(engine.InvalidMessage, match=message):
            engine.add_ratings(st, "b", ratings)
    assert st["raw"]["b"] == {"seed-b": 80}


# --- scores ----------------------------------------------------------------

def test_norm_is_relative_to_the_anchors_and_clamped(engine):
    st = new(engine, ["a", "b"], {"a": (20, 80)})
    assert engine.norm(st, "a", 50) == pytest.approx(0.5)
    assert engine.norm(st, "a", 10) == 0.0 and engine.norm(st, "a", 95) == 1.0


def test_pool_order_is_round_then_place_in_the_roster_never_arrival(engine):
    st = new(engine, ["a", "b", "c"])
    seed(engine, st, "c", 50)
    seed(engine, st, "a", 50)
    seed(engine, st, "b", 50)
    assert [c["id"] for c in engine.ordered_pool(st)] == ["seed-a", "seed-b", "seed-c"]
    repair(engine, st, "b", 1, 50)
    assert [c["id"] for c in engine.ordered_pool(st)][-1] == "r1-b"
    assert engine.repairs_in_pool(st) == 1 and engine.next_round(st) == 2


def test_lex_gt_compares_position_by_position(engine):
    assert engine.lex_gt([0.5, 0.9], [0.5, 0.8]) and not engine.lex_gt([0.4, 1.0], [0.5, 0.0])
    assert not engine.lex_gt([0.5, 0.5], [0.5, 0.5])


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
    rate(engine, st, "a", seed_b=70)
    st2 = new(engine, ["a", "b"])
    seed(engine, st2, "a", 70)
    seed(engine, st2, "b", 70)
    rate(engine, st2, "a", seed_b=70)
    rate(engine, st2, "b", seed_a=70)
    assert engine.select(st2, ["seed-a", "seed-b"]) == "seed-a"


def test_accept_needs_eps_on_the_same_side_of_tau_but_not_above_it(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 50)
    seed(engine, st, "b", 52)
    rate(engine, st, "a", seed_b=52)
    rate(engine, st, "b", seed_a=50)
    assert not engine.accept(st, "seed-b", "seed-a")                    # [.52,.52] vs [.50,.50]: gain .02 < eps
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 61)
    seed(engine, st, "b", 60)
    rate(engine, st, "a", seed_b=60)
    rate(engine, st, "b", seed_a=61)
    assert not engine.accept(st, "seed-a", "seed-b")                    # both clear tau: gain .01 < eps
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 59)
    seed(engine, st, "b", 62)
    rate(engine, st, "a", seed_b=62)
    rate(engine, st, "b", seed_a=59)
    assert engine.accept(st, "seed-b", "seed-a")                        # [.62,.62] clears tau, [.59,.59] does not


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
    engine.add_anchor(st, "a", 0, 100)
    seed(engine, st, "a", 90)
    assert engine.evaluate(st)["state"] == "wait"
    engine.add_anchor(st, "b", 0, 100)
    seed(engine, st, "b", 90)
    ev = engine.evaluate(st)
    assert ev["state"] == "wait" and ev["x_star"] is None
    assert sorted(engine.lacking(st)) == [("a", "seed-b"), ("b", "seed-a")]


def test_success_when_everyone_clears_tau_and_nobody_is_missing(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=75)
    ev = engine.evaluate(st)
    assert (ev["state"], ev["outcome"], ev["x_star"], ev["reporter"]) == ("done", "SUCCESS", "seed-a", "a")
    assert ev["floor"] == pytest.approx(0.75) and ev["shortfall"] == {}
    assert ev["scores"] == {"a": pytest.approx(0.9), "b": pytest.approx(0.75)}


def test_the_bottleneck_proposes_a_repair_and_the_round_is_numbered(engine):
    st = new(engine, ["a", "b"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    rate(engine, st, "a", seed_b=20)
    rate(engine, st, "b", seed_a=30)
    ev = engine.evaluate(st)
    # seed-a [.30,.90], seed-b [.20,.80] -> incumbent seed-a; the bottleneck is b (.30)
    assert (ev["state"], ev["x_star"], ev["bottleneck"], ev["round"], ev["t"]) == ("repair", "seed-a", "b", 1, 0)
    assert ev["shortfall"] == {"b": pytest.approx(0.3)}


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
    assert ev["shortfall"] == {"b": pytest.approx(0.3)}
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


# --- missing participants --------------------------------------------------

def table(engine, raw, **params):
    """A run of a, b and c whose raw scores are `raw[agent][candidate]` (each agent's seed is its own `seed-<agent>`)."""
    st = new(engine, ["a", "b", "c"], **params)
    for ag in "abc":
        seed(engine, st, ag, raw[ag][f"seed-{ag}"])
    for ag in "abc":
        engine.add_ratings(st, ag, [(cid, r, "w") for cid, r in raw[ag].items() if cid != f"seed-{ag}"])
    return st


UNHAPPY_C = {"a": {"seed-a": 90, "seed-b": 50, "seed-c": 30},
             "b": {"seed-a": 70, "seed-b": 80, "seed-c": 30},
             "c": {"seed-a": 30, "seed-b": 30, "seed-c": 30}}
three = lambda engine: table(engine, UNHAPPY_C)


def test_a_missing_participant_ends_success_as_best_effort_and_its_ratings_no_longer_count(engine):
    st = three(engine)
    ev = engine.evaluate(st)
    assert (ev["state"], ev["x_star"], ev["bottleneck"]) == ("repair", "seed-a", "c")
    assert engine.mark_missing(st, "c") and not engine.mark_missing(st, "c") and not engine.mark_missing(st, "z")
    ev = engine.evaluate(st)
    assert (ev["outcome"], ev["x_star"], ev["floor"]) == ("BEST_EFFORT", "seed-a", pytest.approx(0.7))
    assert sorted(ev["scores"]) == ["a", "b"] and ev["reporter"] == "a"


def test_success_needs_nobody_missing_even_when_everyone_clears_tau(engine):
    st = table(engine, {"a": {"seed-a": 90, "seed-b": 70, "seed-c": 70}, "b": {"seed-a": 70, "seed-b": 80, "seed-c": 70},
                        "c": {"seed-a": 70, "seed-b": 70, "seed-c": 70}})
    assert engine.evaluate(st)["outcome"] == "SUCCESS"
    engine.mark_missing(st, "c")
    assert engine.evaluate(st)["outcome"] == "BEST_EFFORT"


def test_the_bottleneck_changes_when_the_one_that_owed_a_repair_goes_missing(engine):
    st = table(engine, {**UNHAPPY_C, "b": {"seed-a": 40, "seed-b": 80, "seed-c": 30}})
    ev = engine.evaluate(st)
    assert (ev["x_star"], ev["bottleneck"], ev["round"]) == ("seed-b", "c", 1)
    engine.mark_missing(st, "c")
    ev = engine.evaluate(st)
    assert (ev["state"], ev["x_star"], ev["bottleneck"], ev["round"]) == ("repair", "seed-b", "a", 1)


def test_the_incumbent_can_change_when_the_evaluations_are_replayed_without_a_participant(engine):
    st = new(engine, ["a", "b", "c"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 60)
    seed(engine, st, "c", 60)
    rate(engine, st, "a", seed_b=70, seed_c=70)
    rate(engine, st, "b", seed_a=20, seed_c=60)      # b dislikes seed-a
    rate(engine, st, "c", seed_a=90, seed_b=60)
    assert engine.evaluate(st)["x_star"] == "seed-b"                  # [.6,.6,.7] beats seed-a's [.2,.9,.9]
    engine.mark_missing(st, "b")
    assert engine.evaluate(st)["x_star"] == "seed-a"                  # without b: [.9,.9] beats [.6,.7]


def test_a_missing_participant_is_not_waited_for_but_its_seed_stays_in_the_pool(engine):
    st = new(engine, ["a", "b", "c"])
    seed(engine, st, "a", 90)
    seed(engine, st, "b", 80)
    engine.mark_missing(st, "c")                                      # no init reply from c ever
    assert engine.evaluate(st)["state"] == "wait"
    rate(engine, st, "a", seed_b=70)
    rate(engine, st, "b", seed_a=75)
    ev = engine.evaluate(st)
    assert (ev["outcome"], ev["reporter"]) == ("BEST_EFFORT", "a")
    assert [c["id"] for c in engine.ordered_pool(st)] == ["seed-a", "seed-b"]
    assert engine.lacking(st) == []


def test_a_participant_that_proposed_and_went_missing_leaves_its_candidate_in_the_pool(engine):
    st = three(engine)
    engine.mark_missing(st, "a")
    ev = engine.evaluate(st)
    assert {c["id"] for c in engine.ordered_pool(st)} == {"seed-a", "seed-b", "seed-c"}
    assert (ev["state"], ev["x_star"], ev["bottleneck"], ev["reporter"]) == ("repair", "seed-b", "c", "b")


def test_fewer_than_two_participants_left_aborts(engine):
    st = three(engine)
    engine.mark_missing(st, "a")
    engine.mark_missing(st, "b")
    ev = engine.evaluate(st)
    assert (ev["state"], ev["outcome"], ev["reason"], ev["reporter"]) == ("done", "ABORTED", "too few participants", "c")
    assert ev["x_star"] is None


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
