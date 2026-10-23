# accord-mce-metrics

The ACCORD evaluations as [MCE](https://github.com/outshift-open/observe-and-explain-platform) metric plugins:
convergence, correctness, benefit and protocol token cost.

Every metric is skipped (an error result, never stored, so it never reads as 0) for a session with no evidence that
ACCORD ran, i.e. the `l9-accord-initiator` skill was never activated. Some are also skipped when there is nothing to
judge (no lock, no selected terms). The initiator's calls are read once per session (`convergence.py`), every
metric reads that.

| Family | Metrics |
|---|---|
| Convergence (`convergence.py`) | `AccordConverged`, `AccordPhaseCoverage`, `AccordCanonicalValid`, `AccordRelayedValid`, `AccordFabricatedLock`, `AccordContractWarnings` |
| Correctness (`correctness.py`) | `AccordLockPrecision`, `AccordLockRecall`, `AccordTermUtilization`, `AccordLockedTerms`, `AccordOverSelected`, `AccordUnderSelected` |
| Protocol cost (`protocolcost.py`) | `AccordProtocolPromptTokens`, `AccordProtocolCachedTokens`, `AccordProtocolFreshTokens`, `AccordProtocolCompletionTokens`, `AccordContractOverheadTokens`, `AccordContractOverheadPerCall`, `AccordContractOverheadShare` |
| Benefit, LLM-judged (`benefit.py`) | `AccordGlossaryUptake`, `AccordGlossaryInfluence` |

Per-session detail the scripts printed (per-term verdicts, validation warnings, the similarity backend) is in the
result's `reasoning`, as JSON where it is structured.

## Use

```bash
uv run --extra dev python -m pytest
```

### Run from the CLI

`accord-convergence-trace` (also `python -m accord_mce_metrics.trace_cli`) runs **all** the metrics on MAS Lab
trace files, with no KG and no MCE server needed. It builds KG-style spans in memory from each trace's events and
runs the same metric classes the worker runs:

| Trace events | Span |
|---|---|
| `tool_call_start` / `tool_call_end` | tool spans (activation, the kernel's contract, relays) |
| `llm_call_start` / `llm_call_end` (`tokens_used`) and `context_assembled` (`messages`) | LLM spans: prompt, system prompts, provider usage |
| `execution_start` / `execution_end` and `user_response` | agent spans: delegations and replies |

```bash
# a table per trace: convergence, correctness and protocol cost
uv run accord-convergence-trace path/to/events.jsonl

# also the LLM-judged benefit metrics
# (needs the openai client, in the `judge` extra)
export LLM_API_KEY=... LLM_MODEL_NAME=... LLM_BASE_MODEL_URL=...   # the URL only for a non-OpenAI endpoint
uv run --extra judge accord-convergence-trace path/to/events.jsonl --judge
# or from a .env file
uv run --extra judge --env-file .env accord-convergence-trace path/to/events.jsonl --judge

# several traces, JSON output
uv run accord-convergence-trace traces/**/events.jsonl --json

# equivalent, without the installed script
uv run python -m accord_mce_metrics.trace_cli path/to/events.jsonl
```

Output, per trace, one line per metric: the value and its reasoning, or `skipped (<reason>)` when the metric does
not apply (ACCORD was not activated, nothing was locked, the trace carries no provider usage). `--judge` stops with
a message if `LLM_API_KEY` or `LLM_MODEL_NAME` is missing; a judge call that fails is reported on its metric's line
and the other metrics still run. `tests/test_trace_cli.py` checks that the CLI covers every metric registered in
`pyproject.toml`.

What a trace has to carry for each family: the correctness metrics read each agent's system prompt from
`context_assembled.messages`, and the protocol-cost metrics need `llm_call_end.tokens_used` (provider usage) and
the prompt messages. A trace without them skips those metrics rather than scoring them 0.

### Run in the MCE worker

Install the package next to `oxp-mce-core`; each metric is registered under `metrics_computation_engine.plugins`
in `pyproject.toml`, one entry per class. List the ones you want under `metrics: Session:` of an MCE config:

```yaml
cache:
  read: false
  write: false
metrics:
  Session:
    - AccordConverged
    - AccordPhaseCoverage
    - AccordLockPrecision
    - AccordLockRecall
    - AccordProtocolFreshTokens
    - AccordContractOverheadShare
    - AccordGlossaryUptake   # LLM-judged
```

The LLM-judged metrics need the `openai` package and the MCE LLM settings (`LLM_BASE_MODEL_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`).
