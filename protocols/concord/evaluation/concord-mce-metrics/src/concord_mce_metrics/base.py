# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""Common shape of every Concord metric: metadata, result builders and the skip rules."""

from __future__ import annotations

from typing import Any

from mce.core.metadata import MetricLayer, MetricMetadata, MetricNature, MetricScope
from mce.core.metric import Metric
from mce.core.types import MetricResult

from . import session as S

PROVIDER = "Concord"
NOT_USED = "CONCORD was not used (no successful init)"


def not_finished(s: S.Session) -> str:
    return f"the run did not reach COMMIT or BEST_EFFORT (it stopped in the {s.phase} phase)"


class ConcordMetric(Metric):
    """A session metric. ``facet`` gets the parsed CONCORD session; the gates are handled here.

    ``NEEDS_COMPLETE`` mirrors the scripts: most of them measure only runs that
    used CONCORD AND reached ``Phase: Complete``, and skip the others.
    """

    NAME = ""
    DESCRIPTION = ""
    LAYER = MetricLayer.GOVERNANCE
    NATURE = MetricNature.DETERMINISTIC
    NEEDS_COMPLETE = True

    def __init__(self) -> None:
        self.metadata = MetricMetadata(
            name=self.NAME,
            description=self.DESCRIPTION,
            layer=self.LAYER,
            nature=self.NATURE,
            scope=MetricScope.SESSION,
            ontology_class=self.NAME,
            target_types={"mas:Session"},
            provider=PROVIDER,
        )

    def compute(self, resource_id: str, context: dict[str, Any]) -> MetricResult:
        found = S.session_for(context)
        if found is None:
            return self._skip(resource_id, NOT_USED)
        if self.NEEDS_COMPLETE and not found.reached_end:
            return self._skip(resource_id, not_finished(found))
        return self.facet(resource_id, found, context)

    def facet(self, session_id: str, s: S.Session, context: dict[str, Any]) -> MetricResult:  # pragma: no cover
        raise NotImplementedError

    def _result(self, session_id: str, value: float, reasoning: str = "", **extra: Any) -> MetricResult:
        return MetricResult(
            metric_id=self.NAME,
            resource_id=session_id,
            provider=PROVIDER,
            value=float(value),
            reasoning=reasoning,
            metric_class=self.NAME,
            **extra,
        )

    def _skip(self, session_id: str, why: str) -> MetricResult:
        """Not applicable to this session. An error result is never stored, so it never reads as 0."""
        return MetricResult(
            metric_id=self.NAME,
            resource_id=session_id,
            provider=PROVIDER,
            value=0.0,
            metric_class=self.NAME,
            error=why,
            reasoning=why,
        )
