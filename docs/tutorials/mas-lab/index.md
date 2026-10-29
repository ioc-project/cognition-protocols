# Tutorial: running Cognition Protocols with MAS Lab

<p class="eyebrow">Agents defined as YAML manifests, run with mas-ctl</p>

[MAS Lab](https://ioc-project.github.io/mas-lab/) (`mas-lab`, `mas-ctl`) lets you define a
multi-agent system as a set of YAML manifests and run it with one command. Adding a Cognition
Protocol to a MAS Lab system means installing the protocol skills and listing them in the
agents that will use them. The protocol's own activation conditions determine which skill fires
for each agent — no changes to the MAS topology are needed. The [examples](examples/index.md) are complete labs
you can run.

## Prerequisites

- **MAS Lab installed.** Follow [Tutorial 0 — Environment Setup](https://ioc-project.github.io/mas-lab/tutorials/00-environment-setup/)
  in the MAS Lab documentation to install `mas-ctl` and configure LLM access.

    ```bash
    uv venv .venv
    uv pip install mas-lab
    source .venv/bin/activate
    mas-lab init     # configure LLM access once
    ```

    !!! note "Other installation paths"
        Docker and developer-workspace installs also work. See [Tutorial 0](https://ioc-project.github.io/mas-lab/tutorials/00-environment-setup/) for those paths. Prefix every `mas-ctl` command with `docker compose --profile tools run --rm --no-deps cli` when using Docker.

## 1. Create the project directory

Create an empty directory for the MAS:

```bash
mkdir -p ~/mas-lab-run && cd ~/mas-lab-run
```

Run every command from this directory.

## 2. Install the protocol skills

MAS Lab discovers skills by name from the `skills/` directory next to the manifests.
Each skill is released on its own, with its own tag, as `<skill>-v<version>`. Download the zip of
each skill from the
[GitHub releases page](https://github.com/ioc-project/cognition-protocols/releases) and copy the
skill folder into `skills/`:

```bash
PROTOCOL=concord   # or accord
VERSION=0.1.0      # the version of each skill, as on the releases page
RELEASES=https://github.com/ioc-project/cognition-protocols/releases/download

mkdir -p skills /tmp/cp-skills
for SKILL in ${PROTOCOL}-initiator ${PROTOCOL}-receiver; do
  curl -fsSL $RELEASES/${SKILL}-v${VERSION}/${SKILL}.zip -o /tmp/cp-skills/${SKILL}.zip
  unzip -q -o /tmp/cp-skills/${SKILL}.zip -d /tmp/cp-skills/
  cp -R /tmp/cp-skills/protocols/${PROTOCOL}/skills/${SKILL} skills/
done
```

## 3. Define the agents

Each agent is a `kind: Agent` manifest. Add both protocol skills to the `skills` list of each
agent that participates in the protocol — the protocol's activation conditions determine which
skill fires for a given agent at runtime.

```yaml
# agents/<agent-id>/agent.yaml
apiVersion: mas/v1
kind: Agent
metadata:
  name: <agent-id>
spec:
  description: >
    <one-line description of this agent's role>
  skills:
    - concord-initiator
    - concord-receiver
  context:
    role: |
      <the agent's full role description>
```

Create one manifest per agent in `agents/`:

```text
agents/
├── <agent-1>/
│   └── agent.yaml
├── <agent-2>/
│   └── agent.yaml
└── <agent-3>/
    └── agent.yaml
```

## 4. Write the MAS manifest

The `kind: MAS` manifest declares all agents and the workflow topology. `spec.agency.agents`
lists agent identities and their manifest paths. `spec.workflow` names the entry agent and the
delegation graph:

```yaml
# mas.yaml
apiVersion: mas/v1
kind: MAS
metadata:
  name: <your-mas-name>
  version: 1.0.0
spec:
  agency:
    agents:
      - id: <agent-1>
        ref: agents/<agent-1>/agent.yaml
      - id: <agent-2>
        ref: agents/<agent-2>/agent.yaml
      - id: <agent-3>
        ref: agents/<agent-3>/agent.yaml
  workflow:
    entry: <agent-1>
    nodes:
      - id: <agent-1>
        delegates_to:
          - <agent-2>
          - <agent-3>
      - id: <agent-2>
      - id: <agent-3>
```

### Resulting layout

```text
mas-lab-run/
├── mas.yaml
├── skills/
│   ├── concord-initiator/
│   └── concord-receiver/
└── agents/
    ├── <agent-1>/
    │   └── agent.yaml
    ├── <agent-2>/
    │   └── agent.yaml
    └── <agent-3>/
        └── agent.yaml
```

## 5. Validate the manifests

Check all manifests are well-formed before running:

```bash
mas-ctl validate mas.yaml
```

You should see `OK` for the MAS and for each agent.

## 6. Run the MAS

Run the MAS and pass the task as the query:

```bash
mas-ctl run-mas mas.yaml -q "<your task>"
```

The run prints each delegation as it happens. The agent that triggers the protocol loads the
initiator skill and runs it; agents that receive protocol messages load the receiver skill.
