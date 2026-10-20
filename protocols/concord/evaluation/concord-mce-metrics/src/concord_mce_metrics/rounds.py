# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""How many rounds the protocol took.

Ported from ``compute_rounds.py``. A run goes through one INITIAL round (anchors,
seeds, scoring of the seeds) and then as many REPAIR rounds as the tool asks for,
until every agent clears the satisfaction threshold (COMMIT) or the round budget
``-B`` is spent (BEST_EFFORT):

    rounds in a run = 1 (initial) + repair rounds

A repair round is counted each time the tool starts a ``Repair`` phase. It is
classified by what started it: ``scores`` (the normal case, the incumbent was below
the threshold) or ``mark-missing`` (the SRE marked an agent MISSING; a run that
loops through these is usually stuck).
"""

from __future__ import annotations

import json

from .base import ConcordMetric


def _budget(s) -> int | None:
    return int(s.budget) if s.budget is not None and str(s.budget).isdigit() else None


class ConcordRounds(ConcordMetric):
    NAME = "ConcordRounds"
    DESCRIPTION = "Rounds the protocol took: 1 initial round plus the repair rounds."

    def facet(self, session_id, s, context):
        detail = {"outcome": s.outcome, "agents": len(s.agents), "candidates": len(s.pool), "budget": _budget(s), "repair_rounds": s.repair_requested}
        return self._result(session_id, 1 + s.repair_requested, json.dumps(detail))


class ConcordRepairRounds(ConcordMetric):
    NAME = "ConcordRepairRounds"
    DESCRIPTION = "Repair rounds the protocol needed after the initial round (0 when the seeds already cleared the threshold)."

    def facet(self, session_id, s, context):
        return self._result(session_id, s.repair_requested, f"after scoring: {s.repair_causes.count('scores')}, after mark-missing: {s.repair_causes.count('mark-missing')}")


class ConcordRepairsAfterMarkMissing(ConcordMetric):
    NAME = "ConcordRepairsAfterMarkMissing"
    DESCRIPTION = "Repair rounds started by an agent being marked MISSING rather than by scoring; a run that loops through these is usually stuck."

    def facet(self, session_id, s, context):
        return self._result(session_id, s.repair_causes.count("mark-missing"), f"of {s.repair_requested} repair rounds")


class ConcordBudgetUsed(ConcordMetric):
    NAME = "ConcordBudgetUsed"
    DESCRIPTION = "Share of the round budget -B spent on repair rounds (1 or more means the whole budget went)."

    def facet(self, session_id, s, context):
        budget = _budget(s)
        if not budget:
            return self._skip(session_id, "the run recorded no round budget")
        return self._result(session_id, round(s.repair_requested / budget, 2), f"{s.repair_requested} repair rounds of a budget of {budget}")
