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

## What an agent has in its context

One conversation per agent, kept whole for the whole run (`ConcordAgent.messages`, saved as `transcript` in `<agent>.json`):

1. the system prompt: the scenario, its facts, the run description (and, for participant-a, how to start the run);
2. before each task, **the messages of the other participants it has not read yet, exactly as they were sent** (the init with its
   parameters, their first answers, their scores with their reasoning, their repairs), as the deliveries of the skills were;
3. the task (`Agent.instructions`, wording in `ioc_concord.guidance`), and its own answer and any refusal;
4. at the end, the result announced by the reporter (the first participant) and how the run ended: no model call follows.

`show.py` prints what went over SLIM; the transcript is what each model read.

## Checking a run

`uv run python verify.py runs/<run folder>` (or `show.py <run folder> --verify`) is a referee. It reads the messages each agent kept and
checks them with its own code, written from the spec, not with the library's engine: that the right agent repaired in every round, that
the incumbent and the outcome follow the rules and match the result message, that every message is valid (integrity check, ids, numbers,
schema, who rated what), that all agents recorded every message, and that the conversation of each model holds what its node
recorded and the threshold is the one asked for. A failed check means the run cannot be trusted. `batch.py` runs it on every run and
leaves the runs that fail out of the totals. What the models did (refusals, repeated repairs, scores close to the bar) is only
reported. Each agent's record also holds its configuration (model, temperature, threshold, versions, the system prompt).
