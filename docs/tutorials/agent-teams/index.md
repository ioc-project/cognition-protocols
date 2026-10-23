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

        subgraph team ["Teammates · role file in personas/"]
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

Create an empty directory outside any repository, with a directory for the teammates' role
files and one for the initiator's files:

```bash
mkdir -p ~/agent-team && cd ~/agent-team
mkdir -p personas run
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
documentation site: the task, and one role definition per specialist, into `personas/`:

```bash
SCENARIO=https://ioc-project.github.io/cognition-protocols/tutorials/agent-teams/examples/coding-agent-team   # an example page

curl -fsSL $SCENARIO/scenario.env -o scenario.env && source scenario.env
curl -fsSL $SCENARIO/task.txt -o task.txt
for role in $(echo $SPECIALISTS); do
  curl -fsSL $SCENARIO/roles/$role.txt -o personas/$role.md
done

for f in task.txt personas/*.md; do
  grep -qi '<html' $f && echo "download failed for $f"
done
```

The initiator needs no file: its rules are in the kickoff message below.

### Resulting layout

For the Coding Agent Team example:

```text
agent-team/
├── .claude/
│   └── settings.json         # enables the protocol plugin (project scope)
├── scenario.env              # the specialists' ids
├── task.txt                  # the task
├── kickoff.md                # the initiator's prompt, written in the next step
├── personas/
│   ├── microservices.md      # one role definition per specialist
│   ├── monolith.md
│   └── pragmatist.md
└── run/                      # the initiator's files, including the protocol's state file
```

## 4. Write the kickoff message

The kickoff message has three parts: the task, the protocol to use, and the team setup with the
messaging rules. Wherever the protocol says to send a message to an agent, the initiator sends
it with `SendMessage` to the teammate of that name, one teammate at a time, waits for the reply,
and uses that reply text as the agent's answer.

This message is the only part that depends on the example, and it takes everything from the
files above: to run another example, change `SCENARIO` in the previous step and write it again.

```bash
source scenario.env
PROTOCOL=concord             # the protocol you installed in step 2
: "${SPECIALISTS:?}" "${PROTOCOL:?}"   # stop if one is empty

NAMES="$(echo $SPECIALISTS | sed 's/ /, /g')"

cat > kickoff.md <<EOF
You coordinate a team of specialists (agent ids: $NAMES). You cannot read files outside this
directory; everything you need is below. Put every file you write in ./run/.

TASK FOR THE TEAM: "$(cat task.txt)"

Use the $PROTOCOL-initiator skill and follow it to the end, with --state ./run/<name>.json on
every call of its script. The specialists are named exactly $NAMES.

Team setup (do this first). You are the initiator of an agent team. Create a team with one
teammate per specialist, named exactly $NAMES. Spawn each one with this instruction, replacing
ROLE with its name: "You are the ROLE specialist of a team working on a task. Read
./personas/ROLE.md first and act as that role. Do not open any other file in ./personas/. Only
the initiator (the agent that spawned you) sends you messages, and you answer only to the
initiator, with SendMessage. Never message another teammate. Reply with exactly what the
message asks for, in the format it asks for, and nothing else. Skills in .claude/skills/ are
yours to load when a message calls for them." You, the initiator, do not read ./personas/ at
all.

Messaging rules. Where the skill says to send a message to an agent, send it with SendMessage
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
