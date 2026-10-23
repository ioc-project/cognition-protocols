"""Common shape of every Accord metric: metadata, result builders and the skip rule."""

from __future__ import annotations

from typing import Any

from mce.core.metadata import MetricLayer, MetricMetadata, MetricNature, MetricScope
from mce.core.metric import Metric
from mce.core.types import MetricResult

PROVIDER = "Accord"
NOT_ACTIVATED = "No evidence of ACCORD activation: the initiator skill was not activated"


class AccordMetric(Metric):
    NAME = ""
    DESCRIPTION = ""
    LAYER = MetricLayer.GOVERNANCE
    NATURE = MetricNature.DETERMINISTIC

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
