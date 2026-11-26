# blog-v2

The hidden-profile task of `../concord-comparison`, with **four agents, all in the discussion, and no facilitator or hub**. Each agent
(`agent-a` to `agent-d`) holds the 4 shared facts and 1 of the 4 hidden ones; the right answer shows only when the hidden facts are pooled.
`../concord-comparison` (the star set-up) is untouched.

| | Protocol | Transport |
|---|---|---|
| **M** | free speech | full mesh over TCP (a link between every pair; every post goes to all the others) |
| **S** | free speech | SLIM group session (broadcast) |
| **C** | CONCORD, after a free-text step | SLIM group session |

M vs S compares the transport, S vs C the protocol.

## With a coordinator (O1 to O3)

A fifth model, the **coordinator** (`orchestrator`), holds no facts and has no vote: it opens the session and runs it. The four agents and
their facts are the same as above, so the two families are comparable.

| | Protocol | Transport | What the coordinator does |
|---|---|---|---|
| **O1** | free speech | star | passes every post on with `delegate_to` (the harness sends the text, with the author in front); summarizes the outcome |
| **O2** | free speech | SLIM group session | opens and waits (nothing to pass on); summarizes the outcome |
| **O3** | CONCORD, after step 1 | SLIM group session | the initiator, not a participant: its model writes the init (`concord_start`); the result is the run's |

(Batches made before this renumbering have an ask-and-decide scenario as O1 and the three above as O2 to O4; `metrics.py` maps them and leaves
the ask rows out.)

- **O1 and O2:** an agent that is done calls `done` (its option and why): the message goes to the coordinator only (the harness hides it from
  the other agents on a shared channel). The coordinator closes when all four are done, or after `--idle` s without a post, `--max-time`
  s or `--max-posts` posts (then it asks whoever is not done), and a model summarizes the outcome (`report_outcome`).
- **Prompts:** the four agents' prompt has another "how you decide together" block per scenario (it mentions the coordinator);
  the coordinator's prompt is `orchestrator_prompt` in `blog_v2/prompts.py`. `python -m blog_v2.prompts` prints the distributed ones.
- **Results:** O1 and O2 the coordinator's summary (the `done` choices of the agents are kept: `unanimous` is a check); O3 the CONCORD result. The coordinator's tokens are in `orchestrator_tokens`.

    ./run.sh --orchestrated --mode free    --transport star      # O1
    ./run.sh --orchestrated --mode free    --transport slim      # O2
    ./run.sh --orchestrated --mode concord --transport slim      # O3
    uv run python batch.py --scenarios O1,O2,O3 --thresholds 0.7 --repeats 10 --parallel 5

- **One prompt for all four** (`blog_v2/prompts.py`). Who starts a CONCORD run is a harness choice (`--initiator`, rotated by `batch.py`
  over the repeats); the initiator is a participant like the rest and its run content is the fixed one of the scenario file (`scenarios/`).
- **Step 1 is the same in M, S and C**: every agent gets the same opening and posts once (forced `post`). In C the initiator starts the run
  once it has the 3 other posts (or after 60 s); in M and S the discussion goes on.
- **No one closes a free-speech run**: each agent stops by itself after `--idle` s (60) without a post, `--max-time` s (600) or
  `--max-posts` posts (100), then gives its private final answer. A free run is AGREED when the four finals are the same option.
- **SLIM readiness handshake** (`Group.ready`): agents that all post right after joining lose posts otherwise. `agent-a` creates the SLIM
  session (a transport role only).
- Flags, defaults and the `batch.py` report are those of `../concord-comparison` (`--model`, `--temperature 0.7`, `--fresh`, `--threshold`,
  `--timeout`, `--tasks`, `--repeats`, `--parallel`, `--run-timeout 900`, `--fake-llm`).

## Run

    export LLM_BASE_URL=...  LLM_API_KEY=...  LLM_MODEL=...
    ./run.sh --mode free --transport mesh         # M
    ./run.sh --mode free --transport slim         # S
    ./run.sh --mode concord --transport slim      # C
    uv run python batch.py --scenarios M,S,C --tasks all --thresholds 0.7 --repeats 10 --parallel 3
    uv run python show.py runs/<run folder>       # the posts / messages of a run (--verify for the CONCORD referee)
    uv run python metrics.py runs/batch-<time>    # the cost of a batch

## The use cases of the mas-lab library (`scenarios/e-commerce/`, `scenarios/renewal/`)

Two apps of `ioc-eval-labs/library-ioc-eval/apps`, run with a coordinator (O1 to O3) as the original moderator: **e-commerce** (four roles
settle one sourcing plan: merchandising, procurement, fulfillment, finance) and **renewal-discount** (three specialists settle the
discount to offer Acme: salesforce, finance, customer_success). Nothing is invented: the agents are the original roles, the role prompt and
the skills attached to it are the original text, the request that starts the work is the original dataset item, and the coordinator is the
original moderator (the lines about the lab's own delegation tools are replaced by the ones of this harness).

- **The tools are real.** A role knows what its tools return, and calls them (a tool result goes back to the model, and the turn goes on until
  it says something). `blog_v2/tools_mas.py` ports the original tools; `calculate_expected_loss` is the original calculation: finance
  gets 5% on its own, 18% with the churn multiplier of customer success, 20% with the expansion value of salesforce, so the numbers
  have to travel between the roles.
- **Confidentiality is the original's.** In e-commerce each role has a confidential file (the `handling` text of the tool: relay a 0-1 rating
  and a reason, never the file). Renewal has none: peers pass numbers to each other.
- **Candidates.** E-commerce: the nine plans of the fixture. Renewal: an open format (a discount as a whole percentage, `15%`), no fixed list;
  CONCORD takes it as its candidate schema.
- **Ground truth.** The settleable option (`split-early-plus-booked-replenish`; `15%`, the finance self-approval ceiling: no tool returns
  it) is the correct answer; the decoy (`split-early-plus-replenish`; the opener of the account executive, 18% to 35% by dataset) is
  counted apart (`decoy chosen` in the batch table).
- The scenario files were made once from the library and are committed; they are self-contained (the original texts and fixtures are in them).

        ./run.sh --orchestrated --mode concord --transport slim --scenario scenarios/renewal/renewal-acme-finance-gate.yaml
        uv run python batch.py --scenarios O1,O2,O3 --tasks commerce --repeats 10 --parallel 5      # or renewal, or mas (all twelve)

Not done yet: the distributed scenarios (M, S, C) for these apps (a scenario can have any number of agents now: `scenario.agents`).
