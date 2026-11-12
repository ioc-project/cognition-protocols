# concord-slim-agents

Three LLM agents decide where to evacuate, with CONCORD, over a SLIM group session. It is the hidden-profile task
(`evacuation-west-city`, from HiddenBench): each participant holds some facts alone, and the right answer (West City) shows only
when they are pooled.

- Every participant has the shared facts and its own private ones in its prompt, shuffled.
- `participant-a` is told to use CONCORD with the other two. It starts the run (`concord_start`); everybody then answers the
  tasks the node owes (`submit_init_reply`, `submit_score`, `submit_repair`). The only way a fact reaches the others is the
  rationale and reasoning the agent writes in those tools.
- The protocol is `ioc_concord.Agent` (the node). The model writes content only. SLIM carries every message as a broadcast.

| File | What it is |
|---|---|
| `concord_slim_agents/scenario.py` | the fixture and the system prompt of each participant |
| `concord_slim_agents/transport.py` | an in-process SLIM server, and the group session of one agent |
| `concord_slim_agents/agent.py` | the loop: receive, tick, tasks, a model turn with tools |
| `concord_slim_agents/fake_llm.py` | `--fake-llm`: keyword answers, to check the plumbing without an API key |
| `run.sh` | starts the SLIM node and the three agents |

## Run

    export LLM_BASE_URL=https://litellm.example.com      # optional: a LiteLLM proxy, or any Anthropic-compatible endpoint
    export LLM_API_KEY=...
    export LLM_MODEL=bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0     # passed to the endpoint as it is
    ./run.sh                       # --model, --threshold, --temperature, --timeout, --scenario, --fake-llm are passed to every agent

Each run writes `runs/<time>/`: the log of each agent, `<agent>.json` (report, status, events, the whole conversation with the
model, token use) and the node's state file. The exit status of `run.sh` is 0 when participant-a ends with the correct answer.

By hand, in four terminals: `uv run python -m concord_slim_agents server`, then `... agent participant-b`,
`... agent participant-c`, and last `... agent participant-a` (it invites the others, so they must be up first). Use the same
`--out` for all of them.

More than one run: `uv run python batch.py --thresholds 0.7 --repeats 5` runs them one after the other and prints
what each decided. `--temperature` is the sampling of the model (default 0.7). The facts of each agent are always shown in the same
order (`FACT_ORDER_SEED` in `scenario.py`).
