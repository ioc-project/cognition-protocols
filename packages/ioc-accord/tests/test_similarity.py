#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The score every merge is built on."""
import pytest

from conftest import similarity  # noqa: F401


def test_normalize_keeps_only_alphanumeric_tokens(similarity):  # noqa: F811
    assert similarity.normalize_text("  Sev-1: User-Facing Outage!  ") == "sev 1 user facing outage"
    assert similarity.normalize_text("") == ""
    assert similarity.normalize_text(None) == ""


def test_the_same_text_scores_one_however_it_is_written(similarity):  # noqa: F811
    assert similarity.sim("errors per second", "Errors, per second.") == 1.0
    assert similarity.sim("", "") == 1.0


def test_text_against_nothing_scores_zero(similarity):  # noqa: F811
    assert similarity.sim("errors per second", "") == 0.0
    assert similarity.sim("", "errors per second") == 0.0


def test_unrelated_text_scores_below_the_merge_threshold(similarity):  # noqa: F811
    score = similarity.sim("packet loss on the edge link", "the invoice pdf is malformed")
    assert 0.0 <= score < 0.72


def test_a_near_match_lands_between_the_clarify_and_merge_thresholds(similarity):  # noqa: F811
    score = similarity.sim("sev1 means user facing outage", "sev1 is a total outage")
    assert 0.55 <= score < 0.72


def test_cosine_of_orthogonal_parallel_and_empty_vectors(similarity):  # noqa: F811
    assert similarity._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert similarity._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert similarity._cosine([1.0, 1.0], [2.0, 2.0]) == pytest.approx(1.0)
    assert similarity._cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0   # clamped: a score is never negative
