# Tutorial: running a protocol with Claude Agent Teams

<p class="eyebrow">Agents in one local Claude Code session</p>

To try a protocol on your own machine without a message bus, use the experimental
agent teams feature of Claude Code. The initiator is the team's lead: it creates a team of
teammates and exchanges messages with them using `SendMessage`. The teammates play the
receivers, and the protocol is unchanged.
It works with any Cognition Protocol and any [example](examples/index.md).

```mermaid
flowchart LR
    you(["<b>You</b><br/>claude &quot;kickoff&quot;"])

    subgraph session ["One Claude Code session"]
        direction LR
        I["<b>initiator</b> (team lead)<br/>&lt;protocol&gt;-initiator"]

        subgraph team ["Teammates · spawned with their role"]
            direction TB
            S1["<b>specialist 1</b>"]
            S2["<b>specialist 2</b>"]
            S3["<b>specialist …</b>"]
        end
    end

    you -- "task + kickoff" --> I
    I -- "SendMessage" --> S1
    I --> S2
    I --> S3
    I -. "final outcome" .-> you
```

Agent teams work only on a single machine, with no network between the agents, and only
with Claude Code. Use AgentBridge when agents must run in separate processes, containers or
harnesses.

## Prerequisites

- Claude Code, logged in, with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in the environment
  of the session that acts as the initiator.
- The Cognition Protocols marketplace, added once:

    ```bash
    claude plugin marketplace add ioc-project/cognition-protocols
    ```

## 1. Create the project directory

Create an empty directory outside any repository, with a directory for the initiator's files:

```bash
mkdir -p ~/agent-team/run && cd ~/agent-team
```

Run every command from this directory.

## 2. Install the protocol

Install the protocol's plugin in this directory with project scope, so that it is enabled for the
initiator and its teammates only:

```bash
PROTOCOL=concord   # or accord
claude plugin install $PROTOCOL@cognition-protocols --scope project
```

## 3. Choose the example

An [example](examples/index.md) provides the task, the specialists and their role
definitions. Point `SCENARIO` at the example page, for example the
[Coding Agent Team](examples/coding-agent-team/index.md), and download its files from this
documentation site. The role definitions are not saved as files: they are collected in `ROLES`,
and go straight into the initiator's prompt.

```bash
SCENARIO=https://ioc-project.github.io/cognition-protocols/tutorials/agent-teams/examples/coding-agent-team   # an example page

curl -fsSL $SCENARIO/scenario.env -o scenario.env && source scenario.env
curl -fsSL $SCENARIO/task.txt -o task.txt

ROLES="$(for role in $(echo $SPECIALISTS); do
  printf '=== %s ===\n' "$role"
  curl -fsSL $SCENARIO/roles/$role.txt
  printf '\n'
done)"

echo "$ROLES" | grep -qi '<html' && echo "download failed"
grep -qi '<html' task.txt && echo "download failed"
```

### Resulting layout

```text
agent-team/
├── .claude/
│   └── settings.json         # enables the protocol plugin (project scope)
├── scenario.env              # the specialists' ids
├── task.txt                  # the task
├── kickoff.md                # the initiator's prompt, written in the next step
└── run/                      # the initiator's files, including the protocol's state file
```

## 4. Write the initiator's prompt

The initiator gets one prompt, which is a template with four parts: the task, the role of each
specialist, the protocol to use, and the team setup with the messaging rules. The initiator
spawns each teammate directly with its role description, so the specialists need no files.
Wherever the protocol says to send a message to an agent, the initiator sends it with
`SendMessage` to the teammate of that name, one teammate at a time, waits for the reply, and
uses that reply text as the agent's answer.

The template takes everything from the example: to run another example, change `SCENARIO` in the
previous step and fill it in again.

```bash
source scenario.env
PROTOCOL=concord             # the protocol you installed in step 2
: "${SPECIALISTS:?}" "${PROTOCOL:?}"   # stop if one is empty

NAMES="$(echo $SPECIALISTS | sed 's/ /, /g')"

cat > kickoff.md <<EOF
You coordinate a team of specialists (agent ids: $NAMES). Put every file you write in ./run/.

TASK FOR THE TEAM: "$(cat task.txt)"

SPECIALISTS. The role of each specialist is described below, one block per agent id.

$ROLES

PROTOCOL. Use the $PROTOCOL-initiator skill and follow it to the end, with --state
./run/<name>.json on every call of its script. The specialists are named exactly $NAMES.

TEAM SETUP (do this first). You are the initiator of an agent team. Create a team with one
teammate per specialist, named exactly $NAMES. Spawn each one with this instruction, replacing
ROLE with its name and DESCRIPTION with its block from the SPECIALISTS section, verbatim: "You
are the ROLE specialist of a team working on a task. Act as this role: DESCRIPTION. Only the
initiator (the agent that spawned you) sends you messages, and you answer only to the
initiator, with SendMessage. Never message another teammate. Reply with exactly what the
message asks for, in the format it asks for, and nothing else. Skills in .claude/skills/ are
yours to load when a message calls for them." Use the descriptions only to spawn the
teammates: never use them to write, predict or judge a specialist's reply.

MESSAGING RULES. Where the skill says to send a message to an agent, send it with SendMessage
to the teammate of that name, one specialist at a time, and wait for that specialist's reply
before the next message. Never write a specialist's reply yourself and record only real replies.

FINAL REPORT: the skill's final output, exactly as its script prints it.
EOF
```

## 5. Run it

Start Claude Code in the project directory with the kickoff message:

```bash
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude "$(cat kickoff.md)"
```

The session stays open, so you can follow the team while the protocol runs. The initiator ends with
the protocol's final output, and its files, including the script's state file, are in `run/`.
