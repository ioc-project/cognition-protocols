# concord-mce-metrics

Evaluation metrics for [CONCORD](../../../docs/cognition-protocols/concord/index.md), packaged as
[MCE](https://github.com/outshift-open/observe-and-explain-platform) (Metrics Computation Engine) metric plugins.

CONCORD is a protocol for reaching a decision among agents whose positions diverge: an initiator drives the agents
through anchors, seeds, scoring and, if needed, repair rounds, until every agent is satisfied (COMMIT) or the round
budget runs out (BEST_EFFORT). These metrics measure how a CONCORD run went: did it follow the protocol, how many
rounds did it take, how satisfied were the agents, and what did it cost in tokens.

## Where the data comes from

The metrics are computed from a recorded run of CONCORD, in three steps:

```
MAS-Lab run  ->  events.jsonl  ->  knowledge graph (KG)  ->  these metrics
```

1. A [MAS-Lab](https://github.com/outshift-open/mas-lab) run executes the agents. MAS-Lab writes a log of the run,
   `events.jsonl`: one JSON line per event, such as the start and the end of each tool call and each LLM call.
2. MCE loads that log into a knowledge graph (KG), where each call becomes a *span*.
3. The metrics read the tool and LLM spans of one session and return one value per metric for that session.

## What it measures

| Group | Metrics | Question | LLM |
| --- | --- | --- | --- |
| Compliance | `ConcordReachedEnd`, `ConcordCommitted`, `ConcordUsedAllCandidates`, `ConcordAllSteps` | Did the run follow the protocol to the end? | no |
| Rounds | `ConcordRounds`, `ConcordRepairRounds`, `ConcordRepairsAfterMarkMissing`, `ConcordBudgetUsed` | How many rounds did it take, and how much of the budget? | no |
| Satisfaction | `ConcordWorstOffSatisfaction`, `ConcordMeanSatisfaction`, `ConcordWorstOffAboveTau`, `ConcordSelfPreferenceGap` | What did the protocol itself record about each agent's score? | no |
| Token cost | `ConcordRunTokens`, `ConcordTokensBeforeConcord`, `ConcordTokensAnchors`, `ConcordTokensSeeds`, `ConcordTokensScores`, `ConcordTokensRepair`, `ConcordTokensAfterCommit`, `ConcordProtocolTokens`, `ConcordProtocolShare` | Where do the tokens go, by phase? | no |
| Judged | `ConcordOutcomeSatisfied`, `ConcordSatisfiedAgents`, `ConcordScoreFair`, `ConcordUnfairScoreShare` | Is each agent really satisfied with the outcome, and did it score the others' proposals fairly? | yes |

Each metric's description is in its class (`src/concord_mce_metrics/`), and each is registered under
`metrics_computation_engine.plugins` in `pyproject.toml`, one entry per class.

## Install and test

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). `oxp-mce-core` is fetched from GitHub over SSH
(see `[tool.uv.sources]` in `pyproject.toml`), so you need access to the `outshift-open` repository.

```bash
uv sync --extra dev
uv run pytest
```

The tests use a small synthetic session and a canned judge answer, so they need no KG and no LLM.

## Run

### In the MCE worker

List the metrics under `metrics: Session:` of an MCE config. `concord_mce_config.yaml` is the full set: comment out
any you don't want, for example the judged ones. The config also turns the MCE cache off in both directions, since a
cached result would be stored on the Session and could be served stale after a rerun.

### On traces, without a KG

`concord-mce-trace` builds the span dicts in memory from a [MAS-Lab](https://github.com/outshift-open/mas-lab) `events.jsonl` (its tool and LLM call events) and
runs the same metric classes the worker runs. It is a development tool for trying the metrics on a trace.

```bash
# deterministic metrics only
uv run concord-mce-trace <path-to-trace>/events.jsonl

# add the LLM-judged metrics, or print JSON
uv run concord-mce-trace <path-to-trace>/events.jsonl --judge
uv run concord-mce-trace <path-to-trace>/events.jsonl --json
```

The traces are not in this repository. They are `events.jsonl` files from MAS-Lab runs of the CONCORD skills in
`../skills/`.

## LLM settings for the judged metrics

The judged metrics need the MCE LLM settings, read from the environment or from a `.env` in this directory:

| Variable | Meaning |
| --- | --- |
| `LLM_BASE_MODEL_URL` | base URL of the judge model's API |
| `LLM_API_KEY` | API key |
| `LLM_MODEL_NAME` | model name |

`--judge` fails fast if one is missing. Each judged session costs judge tokens.
