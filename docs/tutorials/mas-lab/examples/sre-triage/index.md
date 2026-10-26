# SRE Triage

<p class="eyebrow">An on-call team triaging a production incident</p>

A payment service is failing in production and an on-call team of agents has to find the cause
and decide how to remediate it. The same incident runs in four ways: with no protocol, with ACCORD,
with CONCORD, and with both. Run it with the [MAS Lab tutorial](../../index.md).

The application and the lab come from the
[`ioc-eval-labs`](https://huggingface.co/datasets/ioc-project/ioc-eval-labs) dataset, which holds
the apps (`library-ioc-eval`) and the experiments (`labs/cognitive-protocols`). Nothing else has to
be cloned.

## The task

The dataset holds 27 incidents. The example runs `payment-async-timeout`:

> ALERT: triage critical payment-service incident: p99 latency spiked to 4200ms, error rate at
> 0.23, checkout flow impacted.
>
> Find the root cause and remediate the issue.

The expected remediation is to roll `payment-service` back to 3.7.1. The database connection pool is
elevated, but it is a symptom and not the root cause. The incident also holds an unrelated second
problem, a notification service whose heap is at 91.4%, which calls for a separate and minor restart.

## The agents

| Agent | Role |
|---|---|
| `sre` | The orchestrator. Receives the alert, delegates and decides. It is the initiator of the protocols. |
| `telemetry` | Metrics, logs and traces. |
| `backend` | Services and deployments. |
| `db` | The datastores. |
| `comms` | Status communication. |
| `risk_assessment` | Assesses the risk of the remediation. |

```mermaid
flowchart LR
    you(["<b>You</b>"])
    S["<b>sre</b><br/>initiator"]

    subgraph spec ["Specialists · receiver"]
        direction TB
        T["<b>telemetry</b>"]
        B["<b>backend</b>"]
        D["<b>db</b>"]
        C["<b>comms</b>"]
        R["<b>risk_assessment</b>"]
    end

    you -- "alert" --> S
    S -- "delegates" --> T
    S --> B
    S --> D
    S --> C
    S --> R
    S -. "remediation" .-> you
```

## The scenarios

The lab (`sre-triage-experiments.yaml`) declares one scenario per way to run the team. Each
scenario is the same application with a different set of overlays:

| Scenario | Overlays | What changes |
|---|---|---|
| `baseline` | `raise-context-window` | No protocol. |
| `accord-enabled` | `with-accord-protocol` | `sre` gets `accord-initiator`, the specialists get `accord-receiver`. |
| `concord-enabled` | `with-concord-protocol`, `-forced` | `sre` gets `concord-initiator`, the specialists get `concord-receiver`. The `-forced` overlay tells `sre` to run CONCORD for the remediation decision whatever the specialists say. |
| `concord-and-accord-enabled` | `with-accord-and-concord-protocol` | Both protocols. |

An overlay is a patch on the MAS manifest, so the application itself is shared by every scenario.
Enabling a protocol is what the [tutorial](../../index.md#3-define-the-agents) describes: listing the
protocol's skills in the `skills` of the agents. Here the overlay does it for each agent, and
`raise-context-window` lets `sre` keep the long protocol exchange in its context.

## The script

[:material-download: Download run.sh](run.sh){ download="run.sh" }

The script carries out the steps of the [tutorial](../../index.md) for this example. It takes
the application and the lab at fixed commits, so a run can be reproduced:

| Step | Tutorial | What the script does |
|---|---|---|
| 1 | [Create the project directory](../../index.md#1-create-the-project-directory) | Creates the work directory and checks out MAS Lab and the `ioc-eval-labs` dataset, each at a pinned commit. |
| 2 | [Install the protocol skills](../../index.md#2-install-the-protocol-skills) | Downloads the four skills from the releases and copies them into the `skills/` of the app, where the overlays find them by name. |
| 3 | [Prerequisites](../../index.md#prerequisites) | Installs MAS Lab and the app library into a virtual environment. |
| 4 | | Points MAS Lab at your LLM endpoint, with a configuration kept in the work directory. |
| 5 | [Run the MAS](../../index.md#6-run-the-mas) | Runs the lab for each scenario and incident you ask for. |

The agents (steps 3 and 4 of the tutorial) and the MAS manifest are already in the application, so
there is nothing to write.

??? example "run.sh"

    ```bash
    --8<-- "tutorials/mas-lab/examples/sre-triage/run.sh"
    ```

## Running it

You need `uv`, `git`, `curl` and `unzip`, and an OpenAI-compatible endpoint for the model, such as a
[LiteLLM](https://docs.litellm.ai/) proxy. The script has no other prerequisite.

```bash
export OPENAI_API_KEY=...                           # key for the endpoint
export OPENAI_BASE_URL=https://litellm.example/v1   # base URL of the endpoint
export LLM_MODEL_NAME=...                           # model name, as the endpoint knows it

curl -fsSL https://ioc-project.github.io/cognition-protocols/tutorials/mas-lab/examples/sre-triage/run.sh -o run.sh
bash run.sh
```

With no option, it runs the four scenarios on `payment-async-timeout`, one run each. The options
choose what to run:

| Option | Meaning | Default |
|---|---|---|
| `-s` | Scenarios, separated by commas | all four |
| `-i` | Incidents, separated by commas. Any of the 27 item ids of the dataset | `payment-async-timeout` |
| `-r` | Runs per scenario and incident | 1 |
| `-j` | Runs executed in parallel | 1 |
| `-w` | Work directory | `./mas-lab-sre-triage` |

For example, to compare the baseline with both protocols, three runs each:

```bash
bash run.sh -s baseline,concord-and-accord-enabled -r 3
```

Each run calls the model live, for every agent and every message of the protocol, so it takes time
and costs tokens, more for the scenarios with a protocol than for the baseline.

### The results

The script writes one directory per scenario and incident under `mas-lab-sre-triage/results/`,
with the `results.csv` of the run and, next to it, a `.log` file of the output. A new run of a
scenario and incident replaces its previous results and leaves the others. A run that fails leaves a
`.failed` file and makes the script exit with an error.

In `results.csv`, `output` is an excerpt of the final answer of `sre`. The whole run, with the
messages of every agent, is the trace that `trace_path` points to, under `~/.cache/mas/traces`.

With `-j` above 1, the runs share one MAS Lab daemon, so each `.log` can also hold lines of the
other runs. The `results.csv` files are not affected.

On `claude-haiku-4-5`, a baseline run took about 2 minutes and a run with a protocol 5 to 6
minutes. The answer of the team varies from run to run, with or without a protocol, so use
several runs (`-r`) to compare scenarios.

The dataset also holds recorded runs of the labs, under `labs/cognitive-protocols/traces`, to
compare yours with.
