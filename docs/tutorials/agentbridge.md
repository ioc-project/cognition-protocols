# Tutorial: running a protocol over AgentBridge

<p class="eyebrow">Agents on separate processes, joined by AgentBridge</p>

[AgentBridge](https://github.com/agntcy/shadi) ([`agentbridge`](https://github.com/agntcy/shadi/tree/main/crates/agentbridge_cli)
and [`shadictl`](https://github.com/agntcy/shadi/tree/main/crates/shadictl), from the
[`agntcy/shadi`](https://github.com/agntcy/shadi) repository) registers each coding harness instance as a live agent and lets
one instance send a prompt to another with `agentbridge delegate`. Agents talk
[A2A](https://a2a-protocol.org), either natively or over a [SLIM](https://github.com/agntcy/slim)
message bus. It is used purely as process launcher and transport: the protocol is unchanged,
and the initiator uses `agentbridge delegate` as its delegation primitive, where an in-process
orchestrator would use its sub-agent tool.

Any Cognition Protocol can run this way, on any [scenario](scenarios/index.md). The tutorial
uses independent Claude Code processes: one initiator and one receiver per specialist of the
scenario. It uses the SLIM transport; the node and identity steps are specific to it.

!!! note "Local setup"
    The agents could run on different systems, each reaching the others over the network. To keep
    this tutorial simple, everything runs on one machine, and the SLIM node listens on the
    loopback address `127.0.0.1`.

## Prerequisites

- Claude Code, with `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_AUTH_TOKEN`
  set in the shell that registers the agents. The sandbox that wraps each agent cannot
  read your keychain login.
- `agentbridge` and `shadictl` from [`agntcy/shadi`](https://github.com/agntcy/shadi), on your `PATH`. On macOS:

    ```bash
    brew tap agntcy/shadi https://github.com/agntcy/shadi.git
    brew install shadictl agentbridge
    ```

!!! note "Other operating systems"
    The commands above are for macOS. On another system, follow the installation instructions
    in the [`agntcy/shadi`](https://github.com/agntcy/shadi) repository instead.

## 1. Choose the scenario

A [scenario](scenarios/index.md) provides the task, the specialists and their role
definitions. Point `SCENARIO` at the scenario page, for example the
[Coding Agent Team](scenarios/coding-agent-team/index.md), and create the project directory:

```bash
SCENARIO=https://ioc-project.github.io/cognition-protocols/tutorials/scenarios/coding-agent-team   # a scenario page

mkdir -p ~/agent-team && cd ~/agent-team
curl -fsSL $SCENARIO/scenario.env -o scenario.env && source scenario.env
curl -fsSL $SCENARIO/task.txt -o task.txt

mkdir -p profiles .tmp
for role in initiator $(echo $SPECIALISTS); do mkdir -p agents/$role; done
```

`scenario.env` defines `SPECIALISTS`, the ids of the scenario's agents, and the identities used
in [step 6](#6-define-the-identities-and-the-environment). Run every command from this
directory unless stated otherwise.

## 2. Install the protocol in each agent

Claude Code discovers skills and role files from its working directory, so each agent has its own
directory. Register the Cognition Protocols marketplace once, then install the protocol's plugin
in each agent's directory with project scope, so that it is enabled for that agent only:

```bash
claude plugin marketplace add ioc-project/cognition-protocols

PROTOCOL=concord   # or accord
for role in initiator $(echo $SPECIALISTS); do
  (cd agents/$role && claude plugin install $PROTOCOL@cognition-protocols --scope project)
done
```

Each plugin carries both the initiator and the receiver skill. Their activation conditions
are precise, so the initiator skill only activates on the agent that starts the protocol and
the receiver skill on the agents that receive its messages.

## 3. Write the role definitions

Each agent's `CLAUDE.md` holds its role definition. The initiator's file only sets working rules,
and is the same in every scenario:

```bash
cat > agents/initiator/CLAUDE.md <<'EOF'
# Role: initiator

- Keep every file you write under `./run/` (create it with `mkdir -p run`), never in the
  top level of this directory.
- The task is given to you inline in the message that starts the run. Work from that
  message; do not look for it in a file.
- When the protocol ends, report its final output verbatim, not your own summary of it.
EOF
```

Download one role definition per specialist from the scenario, and save each as `CLAUDE.md` in
the agent's directory:

```bash
for role in $(echo $SPECIALISTS); do
  curl -fsSL $SCENARIO/roles/$role.txt -o agents/$role/CLAUDE.md
done

for f in task.txt agents/*/CLAUDE.md; do
  grep -qi '<html' $f && echo "download failed for $f"
done
```

## 4. Create the AgentBridge profiles

Each role needs an AgentBridge profile (`--tool <role>`) that launches Claude Code in print mode
and sets the agent id. The profiles differ only in their `id`, so generate them in a loop.
The in-process `Task` tool is disallowed, so that agents can only work with each other over the
network:

```bash
for role in initiator $(echo $SPECIALISTS); do
  cat > profiles/$role.json <<EOF
{
  "id": "$role",
  "bin": "claude",
  "pin_tmpdir": true,
  "env": {
    "TMPDIR": "{workdir}", "TMP": "{workdir}", "TEMP": "{workdir}",
    "CLAUDE_CODE_TMPDIR": "{workdir}", "CLAUDE_TMPDIR": "{workdir}"
  },
  "workdir_flags": ["--add-dir", "{workdir}"],
  "workdir_flags_on_execute": true,
  "workdir_flags_on_snapshot": true,
  "workdir_flags_on_inject": false,
  "current_dir_workdir": true,
  "system_flag": "--system-prompt",
  "split_large_prompt": false,
  "execute": {
    "args": ["--print", "--output-format", "json",
             "--permission-mode", "bypassPermissions",
             "--disallowedTools", "Task",
             "{workdir_flags}", "{system}", "{session}",
             "--", "{prompt}"]
  },
  "session": {
    "json_field": "session_id",
    "flag": "--session-id",
    "retry_stderr_contains": "already in use"
  },
  "result": { "kind": "json_field", "field": "result" }
}
EOF
done
```

## 5. Generate the certificates

The SLIM node and every agent authenticate each other with mutual TLS. AgentBridge refuses to
connect without it, even on the loopback address. The `agntcy/shadi` repository has a script
that generates a local certificate authority, a server certificate for the node and a client
certificate for your own terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/agntcy/shadi/main/tools/generate_slim_mtls_certs.sh \
  -o generate_slim_mtls_certs.sh
bash generate_slim_mtls_certs.sh "$PWD/.tmp/shadi-slim-mtls"
```

The node itself needs no installation or configuration file: `shadictl slim start-node` runs a
native SLIM node, as shown in [step 7](#7-start-the-mesh).

## 6. Define the identities and the environment

AgentBridge authenticates members of the bus with DIDs. The DIDs are derived deterministically
from a fixed seed and the participant's name, and the scenario's `scenario.env` already exports
them (`SLIM_HUMAN_SEED` and `SLIM_MEMBER_DIDS`). `env.sh` exports everything else the agents and
your terminal need. The participants are:

| Identity | Used for |
|---|---|
| `avatar` | You: the kickoff `agentbridge delegate` call from your terminal |
| `driver` | The initiator's own outbound calls to the receivers (the bus allows one active session per identity, so it cannot reuse `avatar` or `initiator`) |
| `initiator` and the scenario's specialists | The registered agents |

```bash
cat > env.sh <<'EOF'
# Source this from the project directory: source env.sh
export SLIM_ENDPOINT=127.0.0.1:47560
export AGENTBRIDGE_PROFILES_DIR="$PWD/profiles"

# Where AgentBridge looks for the certificates (and the CA) generated in step 5
export SHADI_TMP_DIR="$PWD/.tmp"
export SLIM_TLS_CERT="$PWD/.tmp/shadi-slim-mtls/client-avatar.crt"
export SLIM_TLS_KEY="$PWD/.tmp/shadi-slim-mtls/client-avatar.key"

# DID authentication. The scenario provides the seed and the member DIDs.
export SHADI_SLIM_AUTH=did
source "$PWD/scenario.env"
EOF
```

For your own scenario, derive the identities as described in
[Adding a scenario](scenarios/index.md#adding-a-scenario).

Next, a script that registers one agent. It runs `agentbridge register` for a role under
`shadictl`, from the agent's own directory:

```bash
cat > register-agent.sh <<'EOF'
#!/usr/bin/env bash
# Usage: ./register-agent.sh <role>
set -euo pipefail
ROLE="${1:?usage: register-agent.sh <role>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
  echo "Set ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_AUTH_TOKEN first." >&2
  exit 1
fi

cd "$ROOT" && source ./env.sh
cd "agents/$ROLE"

shadictl \
  --allow . --read ~/.claude \
  --allow "$ROOT/.tmp" --allow /tmp --allow /private/tmp \
  --net-block --net-allow api.anthropic.com --net-allow "$SLIM_ENDPOINT" \
  -- agentbridge register --tool "$ROLE" --command "$PWD" \
  --slim-endpoint "$SLIM_ENDPOINT"
EOF
chmod +x register-agent.sh
```

!!! note "Claude Code installed in `~/.local`"
    The sandbox grants read access to `~/.claude`, where Claude Code keeps its settings and the
    plugin you installed in step 2. If Claude Code was installed with its native installer, the
    `claude` binary lives in `~/.local/bin` and `~/.local/share/claude`, which the sandbox blocks
    by default. Add `--read ~/.local/bin --read ~/.local/share/claude` to the `shadictl` command.
    With a Homebrew install this is not needed.

!!! note "Why the agents run sandboxed"
    With AgentBridge, agents are reached over the network: any member of the bus can send a
    prompt to an agent, and that prompt is acted on by a coding harness that can run commands
    and read and write files on your machine. An agent is therefore only as safe as the least
    trusted party that can message it. Each agent is launched under `shadictl`, which sandboxes
    the harness and restricts what it can do locally and what it can connect to:

    - **Filesystem:** the agent can write only to its own directory (plus the harness's own
      configuration and temp directories) and read only the files it needs.
    - **Network:** outbound connections are blocked except for an explicit allow-list: the bus
      endpoint and the model API (`api.anthropic.com`).

    Bus membership is restricted in the same spirit: only the DIDs listed in `SLIM_MEMBER_DIDS`
    are admitted, and a protocol message can only trigger what the agent's own sandbox allows.

!!! warning "Sandboxing on macOS"
    On some macOS setups Claude Code cannot resolve hostnames under the sandbox, so its API calls
    fail. If that happens on a loopback-only bus, you can remove the `shadictl` wrapper from
    `register-agent.sh` and run `agentbridge register` directly. Do not do this once the bus
    endpoint is reachable beyond `127.0.0.1`.

### Resulting layout

For the Coding Agent Team scenario, the project directory now looks like this:

```text
agent-team/
├── scenario.env              # the specialists' ids and identities
├── task.txt                  # the task
├── env.sh                    # endpoint, certificates, DID identities
├── register-agent.sh         # registers one agent under the sandbox
├── profiles/                 # AgentBridge profiles, one per agent
│   ├── initiator.json
│   ├── microservices.json
│   ├── monolith.json
│   └── pragmatist.json
├── generate_slim_mtls_certs.sh
├── .tmp/
│   └── shadi-slim-mtls/      # CA, server and client certificates
└── agents/
    ├── initiator/
    │   ├── CLAUDE.md         # role definition
    │   ├── .claude/
    │   │   └── settings.json # enables the protocol plugin (project scope)
    │   └── run/              # created by the initiator during the run
    ├── microservices/
    │   ├── CLAUDE.md
    │   └── .claude/settings.json
    ├── monolith/
    │   ├── CLAUDE.md
    │   └── .claude/settings.json
    └── pragmatist/
        ├── CLAUDE.md
        └── .claude/settings.json
```

## 7. Start the mesh

Use one terminal per process so that each agent's traffic is visible. In each terminal, go to
the project directory first. In the agent terminals, also export the credential from the
[Prerequisites](#prerequisites), for example `export CLAUDE_CODE_OAUTH_TOKEN=...`: the sandbox
cannot read your keychain login.

```bash
# terminal 1: the SLIM node
source env.sh
shadictl slim start-node

# one more terminal per agent: the initiator and each specialist of the scenario
./register-agent.sh initiator
./register-agent.sh <specialist id>   # for each id in $SPECIALISTS
```

Wait until every agent prints `ready — listening on ...`.

## 8. Run the protocol

Write the kickoff message, in the shell where you set `PROTOCOL` and sourced `env.sh`. Give the
initiator the task **inline**: a registered Claude Code instance cannot read files outside its
own directory.

This message is the only part that depends on the scenario, and it takes everything from the
files downloaded in step 1: to run another scenario, change `SCENARIO` there and write it again.

```bash
NAMES="$(echo $SPECIALISTS | sed 's/ /, /g')"

cat > kickoff.md <<EOF
You coordinate a team of specialists (agent ids: $NAMES). You cannot read files outside this
directory; everything you need is below. Put every file you write in ./run/.

TASK FOR THE TEAM: "$(cat task.txt)"

Load the $PROTOCOL-initiator skill and follow it to the end. Reach each specialist with
'agentbridge delegate "<message>" --to <agent id> --agent-id driver --endpoint $SLIM_ENDPOINT'.
Never write a specialist's reply yourself. Report the skill's final output verbatim.
EOF
```

Then hand it to the initiator with `delegate`, as `avatar`, from a sixth terminal:

```bash
source env.sh
agentbridge delegate "$(cat kickoff.md)" \
  --to initiator --agent-id avatar --endpoint "$SLIM_ENDPOINT"
```

The initiator runs the skill's script itself. Every message the script asks for is a real
`agentbridge delegate` call to a live receiver, so a run takes minutes rather than seconds. The
protocol's own call budget, where it declares one, is printed when the initiator starts the run.

The initiator's state file in `./run/` holds the authoritative record of the run, in the
format defined by the protocol.
