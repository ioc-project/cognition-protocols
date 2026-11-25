# ACCORD + SMC on the SRE triage lab

This folder lets you run the SRE triage lab with the ACCORD initiator skill connected to the
Shared Meaning Curator (SMC), and compare it with the original skill.

With SMC on, the `sre` orchestrator does two extra things:

- **Retrieve** before ACCORD starts: `init` asks SMC about the task. If SMC has evidence, its
  answer is added to the specialists' messages as a `PRIOR_CONTEXT` line. It is marked as
  unverified, and the specialists treat it as a hypothesis.
- **Ingest** after the lock: the locked intent contract is sent to SMC from a detached
  background process, so it never delays or fails the run.

SMC problems never fail a run. Retrieval times out quickly and carries on without memory.

## How the pieces fit

| Piece | Where it lives |
|---|---|
| The app (agents, tools, dataset, overlays) | `ioc-eval-labs` (Hugging Face), cloned read-only. Nothing is pushed there. |
| The ACCORD skills, with the SMC code | this repo, `protocols/accord/skills/` |
| The experiment file | this folder, copied into your `ioc-eval-labs` clone by `setup.sh` |

The lab finds the skills through two links in
`ioc-eval-labs/library-ioc-eval/apps/sre-triage/v2/skills/`. `use_skills.sh` points those links
at the SMC version of the skills (`smc`) or at the original ones (`orig`). Edit the skills in
this repo and the app picks the change up at once, because nothing is copied.

## What is in this folder

| File | Purpose |
|---|---|
| `setup.sh` | One-time setup in your `ioc-eval-labs` clone. |
| `use_skills.sh` | Switches the app between the SMC skills and the original skills. |
| `sre-triage-accord-smc-smoke.yaml` | The experiment: one incident, ACCORD on, cache off, 3 runs. |
| `config/` | Template for the model config. `setup.sh` renders it. |
| `check_run.py` | Summarises runs from their traces: lock, retrieval, ingest. |

`smc.example.json`, next to the skill in `protocols/accord/skills/accord-initiator/`, is the
template for your SMC settings.

## Prerequisites

1. **The lab, as in the [SRE triage tutorial](../../../docs/tutorials/mas-lab/examples/sre-triage/index.md).**
   Do steps 1, 2 and 4: the endpoint variables, the two checkouts, the virtual environment.
   Skip step 3 (release skills) and step 5 (model config), because `setup.sh` does both.
   The runs here used the commits the tutorial pins: `mas-lab` `0aeba46` and `ioc-eval-labs`
   `1208a07`.
2. **A running SMC service** you can reach, by default at `http://localhost:9004`, and a
   workspace id and MAS id to use.
3. **This repo**, on the branch with the SMC changes (`feat/accord-initiator-smc`, or `main`
   once it is merged).

## Setup

```bash
cd cognition-protocols/protocols/accord/ioc-eval-lab

# Optional: choose the model. The default is bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0
# export LLM_MODEL_NAME=...

./setup.sh ~/path/to/ioc-eval-labs
```

`setup.sh` writes only into your clone and into this folder's `.local/` directory:

1. copies the experiment file into `ioc-eval-labs/labs/cognitive-protocols/`,
2. links the app's ACCORD skills to this checkout,
3. creates `protocols/accord/skills/accord-initiator/smc.json` from the example, if there is none,
4. writes the model config to `.local/config/mas/`, with the output limit raised to 32768 tokens.

Then edit `smc.json`:

| Field | Meaning |
|---|---|
| `base_url` | The SMC service, for example `http://localhost:9004`. |
| `workspace_id`, `mas_id` | Your ids. The memory graph is named after the MAS id. |
| `retrieval.enabled`, `timeout_s`, `max_chars` | Retrieval switch, its timeout (15 s) and the cap on injected text (1500 characters). |
| `ingest.enabled`, `timeout_s` | Ingest switch and the timeout of the background worker (300 s). |

`references/details.md` in the skill folder has the full reference.

**Do not commit `smc.json`.** It holds your own ids.

In the shell you will run from (the one with the lab's virtual environment active):

```bash
export XDG_CONFIG_HOME=<repo>/protocols/accord/ioc-eval-lab/.local/config   # printed by setup.sh
# plus OPENAI_API_KEY, OPENAI_BASE_URL, LLM_API_KEY, LLM_BASE_MODEL_URL, as in the tutorial
mas-lab control stop        # the daemon keeps the environment it started with
```

## Run it

Run from the lab directory, and run one arm after the other. Do not run `use_skills.sh` while
a benchmark is running.

**SMC on:**

```bash
./use_skills.sh smc          # from this folder
cd ~/path/to/ioc-eval-labs/labs/cognitive-protocols

mas-lab benchmark run sre-triage-accord-smc-smoke.yaml \
  --dataset-item ce-1-signup-flow-rollback \
  --max-runs 3 --force \
  -o $HOME/smc-results/smc-on \
  --trace-cache $HOME/smc-results/traces-smc-on
```

**Original skills, no SMC (baseline):**

```bash
./use_skills.sh orig         # from this folder
cd ~/path/to/ioc-eval-labs/labs/cognitive-protocols

mas-lab benchmark run sre-triage-accord-smc-smoke.yaml \
  --dataset-item ce-1-signup-flow-rollback \
  --max-runs 3 --force \
  -o $HOME/smc-results/original \
  --trace-cache $HOME/smc-results/traces-original
```

- Use **absolute** paths for `--trace-cache`. A relative path creates nested folders.
- `--max-runs 1` gives a quick single run. Three runs let run 2 and 3 retrieve what the
  earlier runs ingested.
- The experiment turns the trace cache off on purpose. The cache key ignores `smc.json` and
  the skill scripts, so with the cache on, an SMC-on run could silently reuse an SMC-off trace.
  Keep it off.
- A run takes several minutes, because every agent message calls the model live.
- `./use_skills.sh status` shows which version the app is using. Note it with your results,
  because the traces do not record it.

The experiment couples its scenario to `ce-1-signup-flow-rollback`. Other incidents from
`challenge-focused.yaml` that the team reports running reliably are `ce-1-checkout-timeout-restart`,
`ce-1-routing-policy-rollback`, `ce-1-signup-flow-enable-debug-logging`,
`ce-1-webhook-worker-enable-debug-logging`, `cr-1-checkout-timeout-restart`,
`cr-1-profile-api-restart`, `cr-1-routing-policy-rollback`, `cr-1-signup-flow-restart`,
`cr-1-signup-flow-rollback`, `dr-1-checkout-timeout-enable-debug-logging`,
`dr-1-profile-api-clear-cache`, `dr-1-signup-flow-enable-debug-logging`,
`dr-1-signup-flow-rollback` and `dr-1-webhook-worker-restart`. To run one, change the item
under `couplings` in the YAML (or pass the `--override` shown in the tutorial). We have not
tested the other incidents.

## Read the results

```bash
python3 check_run.py $HOME/smc-results/traces-smc-on
```

For each run it prints:

| Line | What to look for |
|---|---|
| `script calls` | The `accord_initiator.py` calls and how they ended. `handle:LOCKED` means the lock formed. |
| `reached lock` | `True` once the intent contract is locked. Ingest only happens then. |
| `init smc block` | Retrieval result. `no_evidence` on an empty memory; `ok` with `injected: true` when SMC had evidence. |
| `messages carrying PRIOR_CONTEXT` | More than 0 when memory was injected. |
| `lock result smc` | `ingest: started`, with the background worker's pid. |
| `ingest status file` | `ok`, with counts of concepts, relations and RAG chunks. |

The ingest status file is `protocols/accord/skills/accord-initiator/.accord/state.json.smc_ingest.json`.
All runs share one state file, so only the last run's ingest status stays on disk. Later
retrievals showing the earlier runs' content confirm those ingests landed.

The `output` column of `results.csv` in the `-o` folder is an excerpt of the final answer. The
full trace is under the `--trace-cache` folder.

Also check the log at the start of a run. The `summarizer llm:` lines show the model in use.
If they show a different model from the one you set, the daemon was not restarted.

**See the memory graph.** Open the SMC graph viewer (AgViewer in the standard SMC deployment,
usually on port 3000), log in with your deployment's credentials, use database
`ioc-knowledge-db`, and choose the graph named `graph_<mas_id with underscores>`. For a MAS id
`c8c031e6-5f7d-4e6a-a64f-0c1a84bc5629` the graph is
`graph_c8c031e6_5f7d_4e6a_a64f_0c1a84bc5629`. It stays empty until a run reaches the lock.

## What we saw

These are small samples, so read them as a sanity check, not a measurement.

- On `claude-haiku-4-5`, all three runs with SMC on reached the lock. Run 1 retrieved
  `no_evidence` (empty memory); runs 2 and 3 retrieved and injected earlier memory; every lock
  started an ingest, and the last one finished `ok`.
- The original skills also reached the lock on that model in the one baseline run we made.
- On `gpt-4o`, runs stopped before the lock, with or without SMC. The coordinator used its own
  tool names as agent ids and ended its turn early. This is the model, not SMC.
- On `gpt-5.2`, output was cut off at 4096 tokens until the output limit was raised in the model
  config. That limit is already set in `config/llm-proxy.yaml.template`.

## Behaviour to know

- **Switching SMC off:** delete or rename `smc.json`. With no file, the script behaves as it did
  before, in the steps we compared, but the skills' text still has the extra SMC paragraph. For a
  clean baseline, use `./use_skills.sh orig`.
- **Retrieved memory is only a hint.** It is added to the mission as `PRIOR_CONTEXT`, marked
  unverified, and the specialists still have to verify what they use.
- **Results depend on what SMC already holds.** Your SMC graph starts empty, so the first run
  of a new workspace gets no memory. Use a fresh `mas_id` for a clean experiment.
- **The model matters more than SMC** for whether a run locks at all.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `The controller daemon … does not match this shell` | `mas-lab control stop`, after any running worker finishes, then rerun from the shell with the right environment. |
| `summarizer llm:` shows the wrong model | `XDG_CONFIG_HOME` is not set in that shell, or the daemon was not restarted. |
| `use_skills.sh` says a path is a real folder, not a link | Run the `mv` command it prints, then run it again. |
| `Executed no runs` | The `--dataset-item` is not the one coupled in the YAML. |
| Flavour not found | Run from `labs/cognitive-protocols`, or pass an absolute `--flavour`. |
| `LLM completion truncated at max tokens` | Raise `MAX_TOKENS`, rerun `setup.sh`, restart the daemon. |
| The run stops before the lock | Usually the model. Try more runs or a stronger model. |
| No ingest status file after a lock | Check that SMC is reachable and `ingest.enabled` is `true`. The `smc` block in the lock result shows the error. |

## Undo

- `./use_skills.sh orig` puts the original skills back in the app.
- Delete `smc.json` to turn SMC off.
- Delete `.local/` here to remove the generated config and the extracted original skills.
