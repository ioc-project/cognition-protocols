# concord-mce-metrics

The CONCORD evaluation metrics as [MCE](https://github.com/outshift-open/observe-and-explain-platform) metric
plugins.
Metrics are computed from a session's KG spans instead of an `events.jsonl`, one value per session and metric.

Every metric is skipped (an error result, never stored, so it never reads as 0) for a session where CONCORD was not
used (no successful `init`). Most are also skipped for a run that did not reach `Phase: Complete`. The initiator's calls are read once per session (`session.py`), every metric reads that.

## Use

```bash
uv sync --extra dev && uv run pytest

# run the deterministic metrics on MAS Lab traces, no KG needed (add --judge for the LLM-judged ones)
uv run python -m concord_mce_metrics.trace_cli ../../traces/**/events.jsonl
```

To run them in the worker, list them under `metrics: Session:` of an MCE config: `concord_mce_config.yaml` is
the full set. Each metric is registered under `metrics_computation_engine.plugins` in `pyproject.toml`, one entry
per class.

The LLM-judged metrics need the MCE LLM settings (`LLM_BASE_MODEL_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`), read
from the environment or from a `.env` in this directory. `--judge` fails fast if one is missing.
