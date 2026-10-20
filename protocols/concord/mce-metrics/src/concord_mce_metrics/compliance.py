# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""Protocol-compliance metrics: did the run follow the CONCORD protocol?

Ported from ``compute_compliance_metrics.py``. Among the sessions that used
CONCORD (a successful ``init``):

  ConcordReachedEnd        the tool printed ``Phase: Complete`` (COMMIT or BEST_EFFORT)
  ConcordCommitted         that end was the commit (SUCCESS), not BEST_EFFORT
  ConcordUsedAllCandidates every agent has an accepted score for every candidate of the pool
  ConcordAllSteps          init, anchors, seeds, scores, repairs and completed were all satisfied
"""

from __future__ import annotations

import json

from .base import ConcordMetric


class ConcordReachedEnd(ConcordMetric):
    NAME = "ConcordReachedEnd"
    DESCRIPTION = "1 if the run reached COMMIT or BEST_EFFORT (the tool printed `Phase: Complete`), else 0."
    NEEDS_COMPLETE = False

    def facet(self, session_id, s, context):
        return self._result(session_id, s.reached_end, f"outcome: {s.outcome}" if s.reached_end else f"stopped in the {s.phase} phase")


class ConcordCommitted(ConcordMetric):
    NAME = "ConcordCommitted"
    DESCRIPTION = "1 if the run ended in the commit (SUCCESS: every agent cleared the threshold), 0 if it ended BEST_EFFORT."

    def facet(self, session_id, s, context):
        return self._result(session_id, s.outcome == "SUCCESS", f"outcome: {s.outcome}")


class ConcordUsedAllCandidates(ConcordMetric):
    NAME = "ConcordUsedAllCandidates"
    DESCRIPTION = (
        "1 if every agent has an accepted score for every candidate of the pool (all seeds and every repair "
        "candidate, nothing marked MISSING), else 0."
    )
    NEEDS_COMPLETE = False

    def facet(self, session_id, s, context):
        scored, expected = s.used_cells
        return self._result(
            session_id,
            s.used_all_candidates,
            f"{len(s.pool)} candidates x {len(s.agents)} agents, {scored}/{expected} scored",
        )


class ConcordAllSteps(ConcordMetric):
    NAME = "ConcordAllSteps"
    DESCRIPTION = (
        "1 if the run executed every step the protocol dictates (init, anchors, seeds, scores, repairs, completed), "
        "else 0. Reasoning lists the failed steps."
    )
    NEEDS_COMPLETE = False

    def facet(self, session_id, s, context):
        steps = s.steps
        failed = [name for name, ok in steps.items() if not ok]
        detail = {
            "steps": steps,
            "anchored_agents": f"{len(s.anchored & set(s.agents))}/{len(s.agents)}",
            "seeded_agents": f"{len(s.seeded & set(s.agents))}/{len(s.agents)}",
            "repairs_answered": f"{s.repair_answered}/{s.repair_requested}",
        }
        return self._result(session_id, not failed, ("missing: " + ", ".join(failed) + "; " if failed else "") + json.dumps(detail))
