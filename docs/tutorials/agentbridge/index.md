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

Any Cognition Protocol can run this way, on any team of agents. The tutorial describes the steps
to run one: independent coding-harness processes, one initiator and one per specialist. It uses
the SLIM transport; the node and identity steps are specific to it. The
[examples](examples/index.md) are complete implementations with the real prompts.

```mermaid
flowchart LR
    you(["<b>You</b><br/>agentbridge delegate"])
    slim{{"<b>SLIM node</b><br/>127.0.0.1"}}

    subgraph init ["Initiator process"]
        direction TB
        I["<b>initiator</b><br/>&lt;protocol&gt;-initiator"]
    end

    subgraph spec ["Specialist processes · &lt;protocol&gt;-receiver + role"]
        direction TB
        S1["<b>specialist 1</b>"]
        S2["<b>specialist 2</b>"]
        S3["<b>specialist …</b>"]
    end

    you -- "task + kickoff" --> I
    I -- "agentbridge delegate" --> slim
    slim --> S1
    slim --> S2
    slim --> S3
    I -. "final outcome" .-> you
```

!!! note "Local setup"
    The agents could run on different systems, each reaching the others over the network. To keep
    this tutorial simple, everything runs on one machine, and the SLIM node listens on the
    loopback address `127.0.0.1`.

## Prerequisites

- A coding harness for each agent. The tutorial uses Claude Code. The
  [other harnesses](#other-harnesses) AgentBridge supports work too.
- `agentbridge` and `shadictl` from [`agntcy/shadi`](https://github.com/agntcy/shadi), on your `PATH`. On macOS:

    ```bash
    brew tap agntcy/shadi https://github.com/agntcy/shadi.git
    brew install shadictl agentbridge
    ```

!!! note "Other operating systems"
    The commands above are for macOS. On another system, follow the installation instructions
    in the [`agntcy/shadi`](https://github.com/agntcy/shadi) repository instead.

## 1. Decide the team

A run needs three things, all of them specific to your use case:

- **A task**: the decision or piece of work the team has to deliver.
- **Specialists**: the agents that take part, each with an id.
- **A role for each specialist**: what it cares about, and how it judges a result.

The initiator is the lead of the team and is the same in every use case: it coordinates the
specialists with the protocol and has no stake in the outcome. Each agent runs in its own
harness; the tutorial uses Claude Code for all of them. Write the specialists' ids in `team.env`:

```bash
mkdir -p ~/agent-team && cd ~/agent-team

cat > team.env <<'EOF'
SPECIALISTS="specialist-a specialist-b specialist-c"   # your agent ids
EOF
source team.env
```

## 2. Create the project directory

Create a directory per agent, one for the AgentBridge profiles and one for scratch files:

```bash
mkdir -p profiles .tmp
for id in initiator $(echo $SPECIALISTS); do mkdir -p agents/$id; done
```

Run every command from this directory unless stated otherwise.

## 3. Install the protocol in each agent

The harness discovers skills and instructions from its working directory, so each agent has its
own directory, with the protocol's skills in it. With Claude Code, register the Cognition
Protocols marketplace once, then install the protocol's plugin in each agent's directory with
project scope, so that it is enabled for that agent only:

```bash
claude plugin marketplace add ioc-project/cognition-protocols

PROTOCOL=concord   # or accord
for id in initiator $(echo $SPECIALISTS); do
  (cd agents/$id && claude plugin install $PROTOCOL@cognition-protocols --scope project)
done
```

Each plugin carries both the initiator and the receiver skill. Their activation conditions
are precise, so the initiator skill only activates on the agent that starts the protocol and
the receiver skill on the agents that receive its messages.

For another harness, put the skills in the skills folder of its project directory instead, as
described in [Installing a skill](../../getting-started.md#installing-a-skill).

## 4. Write the role definitions

Each agent reads its role from the instruction file of its harness: `CLAUDE.md` for Claude Code.
The initiator's file only sets working rules, and is the same in every use case:

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

For each specialist, write its role description in `agents/<id>/CLAUDE.md`. The role is what makes
the specialist take a position: say what it cares about, and what it will and will not accept.

### Other harnesses

AgentBridge can run these harnesses as agents: Claude Code, Codex, Copilot, Cursor Agent, Goose
and opencode. They differ in the file they read their instructions from and the folder they load
skills from, so adapt steps 3 and 4:

| Harness | Role file in the agent directory |
|---|---|
| Claude Code | `CLAUDE.md` |
| Codex, opencode | `AGENTS.md` |
| Others | `AGENTS.md`, or the file the harness documents for project instructions |

Agents can use different harnesses in the same team. Steps 5 and 7 below cover the profile and the
sandbox of a harness other than Claude Code.

## 5. Create the AgentBridge profiles

Each agent needs an AgentBridge profile (`--tool <id>`) that tells AgentBridge how to launch the
harness and sets the agent id. AgentBridge bundles one profile per harness; a profile for your own
agent id is the bundled one with its `id` changed. For Claude Code, the profiles differ only in
their `id`, so generate them in a loop. The in-process `Task` tool is disallowed, so that agents
can only work with each other over the network:

```bash
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

For another harness, take its bundled profile from the
[AgentBridge repository](https://github.com/agntcy/shadi/tree/main/crates/agentbridge/profiles)
and change the `id`, for example for a Codex agent:

```bash
curl -fsSL https://raw.githubusercontent.com/agntcy/shadi/main/crates/agentbridge/profiles/codex.json \
  | sed 's/"id": "codex"/"id": "specialist-a"/' > profiles/specialist-a.json
```

## 6. Generate the certificates

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
native SLIM node, as shown in [step 8](#8-start-the-mesh).

## 7. Define the identities and the environment

AgentBridge authenticates members of the bus with DIDs. Each participant has one, derived
deterministically from a fixed seed and the participant's name. The participants are:

| Identity | Used for |
|---|---|
| `avatar` | You: the kickoff `agentbridge delegate` call from your terminal |
| `driver` | The initiator's own outbound calls to the receivers (the bus allows one active session per identity, so it cannot reuse `avatar` or `initiator`) |
| `initiator` and the specialists | The registered agents |

`env.sh` exports everything the agents and your terminal need. It derives the identities with
`shadictl derive-agent-identity` the first time, one at a time, and keeps them in
`.tmp/member-dids`. Run `source env.sh` once before starting the agents, so that they do not all
derive them at the same time.

```bash
cat > env.sh <<'EOF'
# Source this from the project directory: source env.sh
export SLIM_ENDPOINT=127.0.0.1:47560
export AGENTBRIDGE_PROFILES_DIR="$PWD/profiles"

# Where AgentBridge looks for the certificates (and the CA) generated in step 6
export SHADI_TMP_DIR="$PWD/.tmp"
export SLIM_TLS_CERT="$PWD/.tmp/shadi-slim-mtls/client-avatar.crt"
export SLIM_TLS_KEY="$PWD/.tmp/shadi-slim-mtls/client-avatar.key"

# DID authentication: the specialists, and the seed and identities if team.env gives them
export SHADI_SLIM_AUTH=did
source "$PWD/team.env"
export SLIM_HUMAN_SEED="${SLIM_HUMAN_SEED:-agent-team-seed}"

if [ -z "${SLIM_MEMBER_DIDS:-}" ]; then
  if [ ! -s "$PWD/.tmp/member-dids" ]; then
    printf '%s' "$SLIM_HUMAN_SEED" > "$PWD/.tmp/seed"
    dids=""
    # avatar, driver, initiator and the specialists, in this order
    for name in avatar driver initiator $(echo $SPECIALISTS); do
      did="$(shadictl derive-agent-identity --source seed --in "$PWD/.tmp/seed" -n "$name" | awk '/^DID: /{print $2; exit}')"
      dids="${dids:+$dids,}$did"
    done
    rm -f "$PWD/.tmp/seed"
    printf '%s' "$dids" > "$PWD/.tmp/member-dids"
  fi
  export SLIM_MEMBER_DIDS="$(cat "$PWD/.tmp/member-dids")"
fi
EOF
```

On macOS, deriving an identity also stores its keys in the keychain. An example can provide
the identities in its `team.env`, which skips the derivation.

Next, a script that registers one agent. It runs `agentbridge register` for an agent under
`shadictl`, from the agent's own directory:

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

For a harness other than Claude Code, adapt the `shadictl` options to it: allow the folders where
it keeps its configuration and skills for reading, and the host of its model API for network
access, in place of `~/.claude` and `api.anthropic.com`, and set that harness's credentials in
the shell instead of the Claude Code ones.

!!! note "Claude Code installed in `~/.local`"
    The sandbox grants read access to `~/.claude`, where Claude Code keeps its settings and the
    plugin you installed in step 3. If Claude Code was installed with its native installer, the
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

For a team with three specialists, the project directory now looks like this:

```text
agent-team/
├── team.env                  # the specialists' ids, and optionally their identities
├── env.sh                    # endpoint, certificates, DID identities
├── register-agent.sh         # registers one agent under the sandbox
├── profiles/                 # AgentBridge profiles, one per agent
│   ├── initiator.json
│   ├── specialist-a.json
│   ├── specialist-b.json
│   └── specialist-c.json
├── generate_slim_mtls_certs.sh
├── .tmp/
│   ├── member-dids           # the identities, derived once
│   └── shadi-slim-mtls/      # CA, server and client certificates
└── agents/
    ├── initiator/
    │   ├── CLAUDE.md         # role definition
    │   ├── .claude/
    │   │   └── settings.json # enables the protocol plugin (project scope)
    │   └── run/              # created by the initiator during the run
    ├── specialist-a/
    │   ├── CLAUDE.md
    │   └── .claude/settings.json
    ├── specialist-b/
    │   ├── CLAUDE.md
    │   └── .claude/settings.json
    └── specialist-c/
        ├── CLAUDE.md
        └── .claude/settings.json
```

## 8. Start the mesh

Use one terminal per process so that each agent's traffic is visible. In each terminal, go to
the project directory first. In the agent terminals, also export the credential from the
[Prerequisites](#prerequisites), for example `export CLAUDE_CODE_OAUTH_TOKEN=...`: the sandbox
cannot read your keychain login.

```bash
# terminal 1: the SLIM node. This also derives the identities, once.
source env.sh
shadictl slim start-node

# one more terminal per agent: the initiator and each specialist
./register-agent.sh initiator
./register-agent.sh <specialist id>   # for each id in $SPECIALISTS
```

Wait until every agent prints `ready — listening on ...`.

## 9. Prompt the initiator

Give the initiator its prompt **inline**: a registered agent cannot read files outside its own
directory. The prompt is a template with the task, the protocol to use, and how to reach the
specialists. The roles are not in it: each specialist already has its own, in its directory.
Replace what is in angle brackets, and save it as `prompt.md`.

```text
You coordinate a team of specialists (agent ids: <SPECIALIST IDS, COMMA-SEPARATED>). You cannot
read files outside this directory; everything you need is below. Put every file you write in
./run/.

TASK FOR THE TEAM: "<TASK>"

Load the <PROTOCOL>-initiator skill and follow it to the end. Reach each specialist with
'agentbridge delegate "<message>" --to <agent id> --agent-id driver --endpoint 127.0.0.1:47560'.
Never write a specialist's reply yourself. Report the skill's final output verbatim.
```

Then hand it to the initiator with `delegate`, as `avatar`, from another terminal:

```bash
source env.sh
agentbridge delegate "$(cat prompt.md)" \
  --to initiator --agent-id avatar --endpoint "$SLIM_ENDPOINT"
```

The initiator runs the skill's script itself. Every message the script asks for is a real
`agentbridge delegate` call to a live receiver, so a run takes minutes rather than seconds. The
protocol's own call budget, where it declares one, is printed when the initiator starts the run.

The initiator's state file in `./run/` holds the authoritative record of the run, in the
format defined by the protocol.

## 10. Look at the examples

The [examples](examples/index.md) are complete implementations with the real prompts and role
files. Use them as inspiration for adapting the steps to your own use case:

- [Coding Agent Team](examples/coding-agent-team/index.md): three specialists that must decide
  how to build a notification service.
