# Tutorial: running a protocol with MAS Lab

<p class="eyebrow">Agents defined as YAML manifests, run with mas-ctl</p>

[MAS Lab](https://outshift-open.github.io/mas-lab/) (`mas-lab`, `mas-ctl`) lets you define a
multi-agent system as a set of YAML manifests and run it with one command. Adding a Cognition
Protocol to a MAS Lab system means installing the protocol skills and listing them in the
agents that will use them. The protocol's own activation conditions determine which skill fires
for each agent — no changes to the MAS structure are needed.

## Prerequisites

- **MAS Lab installed.** Follow [Tutorial 0 — Environment Setup](https://outshift-open.github.io/mas-lab/tutorials/00-environment-setup/)
  in the MAS Lab documentation to install `mas-ctl` and configure LLM access.

    ```bash
    uv venv .venv
    uv pip install mas-lab
    source .venv/bin/activate
    mas-lab init     # configure LLM access once
    ```

    !!! note "Other installation paths"
        Docker and developer-workspace installs also work. See [Tutorial 0](https://outshift-open.github.io/mas-lab/tutorials/00-environment-setup/) for those paths. Prefix every `mas-ctl` command with `docker compose --profile tools run --rm --no-deps cli` when using Docker.

- An existing MAS: a `kind: MAS` manifest and the `kind: Agent` manifests it references.

## 1. Install the protocol skills

MAS Lab discovers skills by name from the `skills/` directory next to the manifests. The
protocol skills follow the [agentskills.io](https://agentskills.io/) format: each skill is a
directory with a `SKILL.md` file. Download the skill zips from the
[GitHub releases page](https://github.com/ioc-project/cognition-protocols/releases) and
unzip them into `skills/`:

```bash
PROTOCOL=concord   # or accord
RELEASE=https://github.com/ioc-project/cognition-protocols/releases/latest/download

mkdir -p skills /tmp/cp-skills
for SKILL in ${PROTOCOL}-initiator ${PROTOCOL}-receiver; do
  curl -fsSL $RELEASE/${SKILL}.zip -o /tmp/cp-skills/${SKILL}.zip
  unzip -q /tmp/cp-skills/${SKILL}.zip -d /tmp/cp-skills/
  mv /tmp/cp-skills/${SKILL} skills/
done
```

The `skills/` directory should now contain:

```text
skills/
├── concord-initiator/
│   └── SKILL.md
└── concord-receiver/
    └── SKILL.md
```

## 2. Add the skills to your agents

Add both skills to the `skills` list of each agent that participates in the protocol. The
protocol's activation conditions determine which skill fires for a given agent:

```yaml
# In each participating agent's agent.yaml
spec:
  skills:
    - concord-initiator
    - concord-receiver
```

If an agent will only ever initiate or only ever receive, you can list just the one skill it
needs. When in doubt, list both.

## 3. Validate the manifests

Check all manifests are well-formed before running them:

```bash
mas-ctl validate mas.yaml
```

You should see `OK` for the MAS and for each agent.

## 4. Run the protocol

Run the MAS and pass the task as the query:

```bash
mas-ctl run-mas mas.yaml -q "<your task>"
```

The run prints each delegation as it happens. The agent that triggers the protocol loads the
initiator skill and runs it; agents that receive protocol messages load the receiver skill.
