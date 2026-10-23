# Coding Agent Team

<p class="eyebrow">A team of coding agents settling an architecture decision</p>

A team of coding agents has to settle how to build a feature. Three specialists disagree, and
an initiator coordinates them with a Cognition Protocol. Run it with the
[AgentBridge tutorial](../../index.md).

## The task

> We are adding a new notification-delivery capability to the product (a notification
> service): users can opt in to email / push / SMS notifications for events raised across
> several existing subsystems. The team must develop it, and needs one decision on how to
> build it that everyone can live with.

## The agents

| Agent | Role |
|---|---|
| `initiator` | Starts the protocol and coordinates the specialists. |
| `microservices` | Advocates a standalone notifications service with its own datastore and deploy pipeline. |
| `monolith` | Advocates a notifications module inside the existing monolith. |
| `pragmatist` | Owns on-call load and infrastructure cost, with no architectural preference. |

```mermaid
flowchart LR
    you(["<b>You</b>"])

    subgraph init ["Initiator"]
        direction TB
        I["<b>initiator</b>"]
    end

    subgraph spec ["Specialists"]
        direction TB
        M["<b>microservices</b>"]
        O["<b>monolith</b>"]
        P["<b>pragmatist</b>"]
    end

    you -- "task" --> I
    I -- "protocol messages" --> M
    I --> O
    I --> P
    I -. "final outcome" .-> you
```

The three specialists also use the words *service*, *deployment* and *outage* in different
senses, which is what a protocol has to bring to the surface before it can reconcile them.

## Role definitions

Each specialist runs in its own process and reads its role definition from the instruction file
of its harness (`CLAUDE.md` for Claude Code). Each definition is a plain-text file you can
download, or copy from the tab. The initiator's file only sets working rules, and is written in
[Running it](#running-it).

=== "microservices"

    [:material-download: Download](roles/microservices.txt){ download="microservices.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/microservices.txt"
    ```

=== "monolith"

    [:material-download: Download](roles/monolith.txt){ download="monolith.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/monolith.txt"
    ```

=== "pragmatist"

    [:material-download: Download](roles/pragmatist.txt){ download="pragmatist.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/pragmatist.txt"
    ```

## The initiator's prompt

The prompt hands the initiator the task and tells it which protocol skill to use and how to reach
the specialists. It follows the [template](../../index.md#7-prompt-the-initiator) of the tutorial,
filled in for this example and for the `concord` protocol; for `accord`, replace
`concord-initiator` with `accord-initiator`.

[:material-download: Download prompt.md](prompt.md){ download="prompt.md" }

??? example "prompt.md"

    ```text
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/prompt.md"
    ```

## Running it

The commands below carry out the steps of the [tutorial](../../index.md) for this example, with
Claude Code for every agent. The example publishes the role files, the prompt and the identities
of the team, so there is nothing to write or derive.

### 1. Prerequisites

Install `agentbridge` and `shadictl` as in the tutorial, and have Claude Code installed.
Set one of `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_AUTH_TOKEN` in the shell of
each agent terminal: the sandbox that wraps each agent cannot read your keychain login.

### 2. Create the agents

Create the project directory, with a directory per agent, and download the example's files: the
team and its identities, the role of each specialist, and the initiator's prompt. Then install the
protocol's plugin in each agent directory, and write the initiator's rules:

```bash
mkdir -p ~/agent-team && cd ~/agent-team
EXAMPLE=https://ioc-project.github.io/cognition-protocols/tutorials/agentbridge/examples/coding-agent-team

curl -fsSL $EXAMPLE/team.env -o team.env && source team.env
curl -fsSL $EXAMPLE/prompt.md -o prompt.md
for id in initiator $(echo $SPECIALISTS); do mkdir -p agents/$id; done
for id in $(echo $SPECIALISTS); do
  curl -fsSL $EXAMPLE/roles/$id.txt -o agents/$id/CLAUDE.md
done

grep -qi '<html' prompt.md agents/*/CLAUDE.md && echo "download failed"

claude plugin marketplace add ioc-project/cognition-protocols

PROTOCOL=concord   # or accord
for id in initiator $(echo $SPECIALISTS); do
  (cd agents/$id && claude plugin install $PROTOCOL@cognition-protocols --scope project)
done

cat > agents/initiator/CLAUDE.md <<'EOF'
# Role: initiator

- Keep every file you write under `./run/` (create it with `mkdir -p run`), never in the
  top level of this directory.
- The task is given to you inline in the message that starts the run. Work from that
  message; do not look for it in a file.
- When the protocol ends, report its final output verbatim, not your own summary of it.
EOF
```

### 3. Create the AgentBridge profiles

The profiles of the four agents differ only in their `id`, so generate them in a loop. They are
the bundled Claude Code profile, with the permission prompts bypassed, since nobody is there to
answer them, and the in-process `Task` tool disallowed, so that agents can only work with each
other over the network:

```bash
mkdir -p profiles .tmp
for id in initiator $(echo $SPECIALISTS); do
  cat > profiles/$id.json <<EOF
{
  "id": "$id",
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

### 4. Generate the certificates

```bash
curl -fsSL https://raw.githubusercontent.com/agntcy/shadi/main/tools/generate_slim_mtls_certs.sh \
  -o generate_slim_mtls_certs.sh
bash generate_slim_mtls_certs.sh "$PWD/.tmp/shadi-slim-mtls"
```

### 5. Define the identities and the environment

`team.env` already exports the seed and the identities of the team (`SLIM_HUMAN_SEED` and
`SLIM_MEMBER_DIDS`), in this order: `avatar`, `driver`, `initiator`, then the specialists.
`env.sh` adds the rest of what the agents and your terminal need:

```bash
cat > env.sh <<'EOF'
# Source this from the project directory: source env.sh
export SLIM_ENDPOINT=127.0.0.1:47560
export AGENTBRIDGE_PROFILES_DIR="$PWD/profiles"

# Where AgentBridge looks for the certificates (and the CA) generated in step 4
export SHADI_TMP_DIR="$PWD/.tmp"
export SLIM_TLS_CERT="$PWD/.tmp/shadi-slim-mtls/client-avatar.crt"
export SLIM_TLS_KEY="$PWD/.tmp/shadi-slim-mtls/client-avatar.key"

# DID authentication: the seed and the identities come from team.env
export SHADI_SLIM_AUTH=did
source "$PWD/team.env"
EOF
```

### 6. Register the agents

A script registers one agent, from its own directory, with `agentbridge register` under
`shadictl`:

```bash
cat > register-agent.sh <<'EOF'
#!/usr/bin/env bash
# Usage: ./register-agent.sh <agent id>
set -euo pipefail
ID="${1:?usage: register-agent.sh <agent id>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
  echo "Set ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_AUTH_TOKEN first." >&2
  exit 1
fi

cd "$ROOT" && source ./env.sh
cd "agents/$ID"

shadictl \
  --allow . --read ~/.claude \
  --allow "$ROOT/.tmp" --read "$ROOT/profiles" --allow /tmp --allow /private/tmp \
  --net-block --net-allow api.anthropic.com --net-allow "$SLIM_ENDPOINT" \
  -- agentbridge register --tool "$ID" --command "$PWD" \
  --slim-endpoint "$SLIM_ENDPOINT"
EOF
chmod +x register-agent.sh
```

The sandbox grants write access to the agent's own directory, `.tmp` and the temp directories,
and read access to `~/.claude`, where Claude Code keeps its settings and the plugin, and to the
profiles. The network is blocked, except for the bus and `api.anthropic.com`.

!!! note "Claude Code installed in `~/.local`"
    If Claude Code was installed with its native installer, the `claude` binary lives in
    `~/.local/bin` and `~/.local/share/claude`, which the sandbox blocks by default. Add
    `--read ~/.local/bin --read ~/.local/share/claude` to the `shadictl` command. With a Homebrew
    install this is not needed.

!!! warning "Sandboxing on macOS"
    On some macOS setups Claude Code cannot resolve hostnames under the sandbox, so its API calls
    fail. If that happens on a loopback-only bus, you can remove the `shadictl` wrapper from
    `register-agent.sh` and run `agentbridge register` directly. Do not do this once the bus
    endpoint is reachable beyond `127.0.0.1`.

The project directory now looks like this:

```text
agent-team/
├── team.env                  # the specialists' ids and the identities of the team
├── prompt.md                 # the initiator's prompt
├── env.sh                    # endpoint, certificates, DID authentication
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
    │   ├── CLAUDE.md         # working rules
    │   ├── .claude/
    │   │   └── settings.json # enables the protocol plugin (project scope)
    │   └── run/              # created by the initiator during the run
    ├── microservices/
    │   ├── CLAUDE.md         # role definition
    │   └── .claude/settings.json
    ├── monolith/
    │   ├── CLAUDE.md
    │   └── .claude/settings.json
    └── pragmatist/
        ├── CLAUDE.md
        └── .claude/settings.json
```

Start the SLIM node, then the agents. Use one terminal per process, from the project directory,
with the credential of Claude Code exported in the agent terminals:

```bash
# terminal 1: the SLIM node
source env.sh
shadictl slim start-node

# terminals 2 to 5: one per agent
./register-agent.sh initiator
./register-agent.sh microservices
./register-agent.sh monolith
./register-agent.sh pragmatist
```

Wait until every agent prints `ready — listening on ...`.

### 7. Prompt the initiator

From another terminal, hand the prompt to the initiator as `avatar`:

```bash
source env.sh
agentbridge delegate "$(cat prompt.md)" \
  --to initiator --agent-id avatar --endpoint "$SLIM_ENDPOINT"
```

A run takes minutes: every message of the protocol is a real `agentbridge delegate` call to a live
specialist. The initiator's state file in `./run/` holds the record of the run.
