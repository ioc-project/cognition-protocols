# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""Compliance, rounds, satisfaction and token-cost metrics over a synthetic session."""

import json

from concord_mce_metrics import compliance as cp
from concord_mce_metrics import protocolcost as pc
from concord_mce_metrics import rounds as rd
from concord_mce_metrics import satisfaction as sa
from fixtures import STATE, anchor_reply, call, candidate_reply, context, handle, llm, score_reply, scores, tool_spans


def test_a_session_that_never_called_init_is_skipped_by_every_metric():
    ctx = context(spans=[{"id": "t", "startTime": 1, "toolName": "other", "toolArguments": "{}"}])
    for cls in (cp.ConcordReachedEnd, rd.ConcordRounds, sa.ConcordWorstOffSatisfaction, pc.ConcordRunTokens):
        assert "not used" in cls().compute("s", ctx).error


def test_a_rejected_init_does_not_count():
    spans = tool_spans()
    spans[0] = call(1, ["init", "--agents", "a,b"], "boom", exit_code=1)
    assert "not used" in cp.ConcordReachedEnd().compute("s", context(spans=spans)).error


def test_compliance_of_a_clean_commit():
    ctx = context()
    assert cp.ConcordReachedEnd().compute("s", ctx).value == 1
    assert cp.ConcordCommitted().compute("s", ctx).value == 1
    assert cp.ConcordUsedAllCandidates().compute("s", ctx).value == 1
    assert cp.ConcordAllSteps().compute("s", ctx).value == 1


def test_best_effort_reached_the_end_without_the_commit():
    ctx = context(finish="BEST_EFFORT")
    assert cp.ConcordReachedEnd().compute("s", ctx).value == 1
    assert cp.ConcordCommitted().compute("s", ctx).value == 0


def test_a_run_that_stopped_before_complete_fails_the_end_and_is_skipped_elsewhere():
    ctx = context(finish=None)
    assert cp.ConcordReachedEnd().compute("s", ctx).value == 0
    assert rd.ConcordRounds().compute("s", ctx).error
    assert cp.ConcordCommitted().compute("s", ctx).error


def test_an_agent_that_never_scored_breaks_used_all_candidates_and_all_steps():
    spans = tool_spans()
    spans[4] = handle(5, score_reply("a", **{"seed-a": (90, "x"), "seed-b": (20, "y")}))  # b never scores
    ctx = context(spans=spans)
    assert cp.ConcordUsedAllCandidates().compute("s", ctx).value == 0
    all_steps = cp.ConcordAllSteps().compute("s", ctx)
    assert all_steps.value == 0 and "scores" in all_steps.reasoning


def test_a_rejected_call_records_nothing():
    spans = tool_spans()
    both = (score_reply("a", **{"seed-a": (90, "x"), "seed-b": (20, "y")}), score_reply("b", **{"seed-a": (85, "x"), "seed-b": (90, "y")}))
    spans[4] = handle(5, *both, exit_code=2, stdout="STATUS: ERROR")
    assert cp.ConcordUsedAllCandidates().compute("s", context(spans=spans)).value == 0


def test_a_reply_of_another_run_or_an_unknown_agent_is_ignored():
    spans = tool_spans()
    other = score_reply("b", **{"seed-a": (85, "x"), "seed-b": (90, "y")}).replace(f'state="{STATE}"', 'state="other"')
    spans[4] = handle(5, score_reply("a", **{"seed-a": (90, "x"), "seed-b": (20, "y")}), other, score_reply("zed", **{"seed-a": (1, "x")}))
    assert cp.ConcordUsedAllCandidates().compute("s", context(spans=spans)).value == 0


def test_a_reply_without_its_author_takes_the_one_of_the_from_line():
    spans = tool_spans()
    bare = score_reply("b", **{"seed-a": (85, "x"), "seed-b": (90, "y")}).replace(' agent="b"', "")
    spans[4] = handle(5, score_reply("a", **{"seed-a": (90, "x"), "seed-b": (20, "y")}) + "\n--- from: b\n" + bare)
    spans[4]["toolOutput"] = tool_spans()[4]["toolOutput"]
    assert cp.ConcordUsedAllCandidates().compute("s", context(spans=spans)).value == 1


def test_the_end_is_read_from_the_error_output_too():
    spans = tool_spans()
    assert "RUN COMPLETE" in spans[-1]["toolOutput"] and '"stdout": "STATUS: NONE"' in spans[-1]["toolOutput"]
    assert cp.ConcordReachedEnd().compute("s", context(spans=spans)).value == 1


def test_rounds_count_one_initial_plus_the_repair_rounds():
    ctx = context(repairs=2)
    assert rd.ConcordRounds().compute("s", ctx).value == 3
    assert rd.ConcordRepairRounds().compute("s", ctx).value == 2
    assert rd.ConcordBudgetUsed().compute("s", ctx).value == 1.0  # -B 2
    assert rd.ConcordRepairsAfterMarkMissing().compute("s", ctx).value == 0
    assert rd.ConcordRounds().compute("s", context()).value == 1


def test_a_repair_started_by_mark_missing_is_told_apart():
    spans = tool_spans()[:4]
    spans.append(handle(5, score_reply("a", **{"seed-a": (90, "x"), "seed-b": (20, "y")}), score_reply("b", **{"seed-b": (90, "y")})))
    spans.append(call(6, ["--state", f"concord-state-{STATE}.json", "mark-missing", "b", "seed-a"]))  # b never rated seed-a
    spans.append(handle(7, candidate_reply("b", "r1-b", "new plan"), score_reply("b", **{"r1-b": (95, "mine")}), score_reply("a", **{"r1-b": (90, "fine")}),
                        stderr="RUN COMPLETE: outcome BEST_EFFORT.\nWinning candidate r1-b (proposed by b): new plan"))
    ctx = context(spans=spans)
    assert rd.ConcordRepairRounds().compute("s", ctx).value == 1
    assert rd.ConcordRepairsAfterMarkMissing().compute("s", ctx).value == 1
    assert cp.ConcordUsedAllCandidates().compute("s", ctx).value == 0  # seed-a stays MISSING for b


def test_worst_off_is_the_lowest_normalised_score_on_the_winner():
    ctx = context()  # a gave seed-a 90, b gave it 85, both on 0..100
    w = sa.ConcordWorstOffSatisfaction().compute("s", ctx)
    assert w.value == 0.85 and json.loads(w.reasoning)["worst_agent"] == "b"
    assert sa.ConcordMeanSatisfaction().compute("s", ctx).value == 0.88
    assert sa.ConcordWorstOffAboveTau().compute("s", ctx).value == 1  # tau 0.7


def test_below_tau_is_reported():
    ctx = context(score_b=scores(**{"seed-a": (40, "meh"), "seed-b": (90, "mine")}))
    assert sa.ConcordWorstOffAboveTau().compute("s", ctx).value == 0


def test_a_degenerate_anchor_range_is_an_outlier_and_is_skipped():
    spans = tool_spans(score_b=scores(**{"seed-a": (51, "ok"), "seed-b": (52, "mine")}))
    spans[2] = handle(3, anchor_reply("a"), anchor_reply("b", 50, 53))
    result = sa.ConcordWorstOffSatisfaction().compute("s", context(spans=spans))
    assert result.error and "OUTLIER" in result.error and "b" in result.error


def test_self_preference_gap_is_own_minus_others():
    # a: own seed-a 0.90, other seed-b 0.20 -> +0.70. b: own seed-b 0.90, other seed-a 0.85 -> +0.05.
    gap = sa.ConcordSelfPreferenceGap().compute("s", context())
    assert abs(gap.value - 0.375) < 1e-9


def test_tokens_are_put_in_the_phase_active_when_the_call_ended():
    llms = [
        llm("l0", 0, 0.5, 100),  # before init (at t=1)
        llm("l1", 2, 2.5, 200),  # anchors
        llm("l2", 3.6, 3.8, 300),  # seeds: the anchors were in at t=3.5
        llm("l3", 4.8, 5.0, 400),  # first scoring round: the seeds were in at t=4.5
        llm("l4", 6.0, 6.2, 500),  # repair: the scores at t=5 left every option below tau
        llm("l5", 20, 20.5, 600),  # after the commit
    ]
    ctx = context(llms=llms, repairs=1)
    tokens = {cls.NAME: cls().compute("s", ctx).value for cls in (
        pc.ConcordRunTokens, pc.ConcordTokensBeforeConcord, pc.ConcordTokensAnchors, pc.ConcordTokensSeeds,
        pc.ConcordTokensScores, pc.ConcordTokensRepair, pc.ConcordTokensAfterCommit, pc.ConcordProtocolTokens)}
    # every call also completes 10 tokens
    assert tokens["ConcordRunTokens"] == 2100 + 60
    assert tokens["ConcordTokensBeforeConcord"] == 110 and tokens["ConcordTokensAnchors"] == 210
    assert tokens["ConcordTokensSeeds"] == 310 and tokens["ConcordTokensScores"] == 410
    assert tokens["ConcordTokensRepair"] == 510 and tokens["ConcordTokensAfterCommit"] == 610
    assert tokens["ConcordProtocolTokens"] == 210 + 310 + 410 + 510
    share = pc.ConcordProtocolShare().compute("s", ctx)
    assert abs(share.value - 1440 / 2160) < 1e-9


def test_repair_rounds_are_split_in_the_reasoning():
    llms = [llm("l1", 6.0, 6.2, 500), llm("l2", 8.0, 8.2, 700)]
    result = pc.ConcordTokensRepair().compute("s", context(llms=llms, repairs=2))
    rounds = json.loads(result.reasoning)["per_round"]
    assert [r["total_tokens"] for r in rounds] == [510, 710]


def test_calls_without_provider_usage_are_not_counted():
    ctx = context(llms=[{"id": "l", "startTime": 2, "endTime": 3}])
    assert "no LLM call" in pc.ConcordRunTokens().compute("s", ctx).error
