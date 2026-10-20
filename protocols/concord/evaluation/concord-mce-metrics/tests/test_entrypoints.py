"""Every metric registered in pyproject.toml loads, names itself as registered, and appears in the config."""

import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def registered() -> dict[str, str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["entry-points"]["metrics_computation_engine.plugins"]


def test_each_entry_point_is_a_metric_that_names_itself_as_registered():
    for name, target in registered().items():
        module, attr = target.split(":")
        metric = getattr(importlib.import_module(module), attr)()
        assert metric.metric_id == name == attr
        assert metric.metadata.description


def test_the_config_runs_exactly_the_registered_metrics():
    config = (ROOT / "concord_mce_config.yaml").read_text()
    listed = {line.split("- ", 1)[1].strip() for line in config.splitlines() if line.strip().startswith("- ")}
    assert listed == set(registered())
