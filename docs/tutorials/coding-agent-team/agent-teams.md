# Tutorial: running a protocol with Claude Agent Teams

<p class="eyebrow">Agents in one local Claude Code session</p>

To try a protocol on your own machine without a message bus, use the experimental
agent teams feature of Claude Code. A lead
agent creates a team of teammates and exchanges messages with them using `SendMessage`.
The lead plays the initiator, the teammates play the receivers, and the protocol is unchanged.
It works with any Cognition Protocol, and the example is the same notification-service decision as in the [AgentBridge tutorial](agentbridge.md).

Agent teams work only on a single machine, with no network between the agents, and only
with Claude Code. Use AgentBridge when agents must run in separate processes, containers or
harnesses.

## Prerequisites

- Claude Code, logged in, with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in the environment
  of the session that acts as the lead.
- The Cognition Protocols marketplace, added once:

    ```bash
    claude plugin marketplace add ioc-project/cognition-protocols
    ```

## 1. Create the project directory

Create an empty directory outside any repository, with a directory for the teammates' role
files and one for the lead's files:

```bash
mkdir -p ~/coding-agent-team-teams && cd ~/coding-agent-team-teams
mkdir -p personas run
```

Run every command from this directory.

## 2. Install the protocol

Install the protocol's plugin in this directory with project scope, so that it is enabled for the
lead and its teammates only:

```bash
PROTOCOL=concord   # or accord
claude plugin install $PROTOCOL@cognition-protocols --scope project
```

## 3. Write the role definitions

Save the text of each role from the [Coding Agent Team scenario](index.md#role-descriptions) as:

- `personas/microservices.md`
- `personas/monolith.md`
- `personas/pragmatist.md`

The initiator needs no file: its rules are in the kickoff message below.

### Resulting layout

```text
coding-agent-team-teams/
├── .claude/
│   └── settings.json         # enables the protocol plugin (project scope)
├── kickoff.md                # the lead's prompt, written in the next step
├── personas/
│   ├── microservices.md
│   ├── monolith.md
│   └── pragmatist.md
└── run/                      # the lead's files, including the protocol's state file
```

## 4. Write the kickoff message

The kickoff message has three parts: the task ([the example task](index.md#the-task)), the
protocol to use, and the team setup with the messaging rules. Wherever the protocol says to send
a message to an agent, the lead sends it with `SendMessage` to the teammate of that name, one
teammate at a time, waits for the reply, and uses that reply text as the agent's answer.

```bash
cat > kickoff.md <<EOF
You coordinate a team of three specialists (agent ids: microservices, monolith, pragmatist).
You cannot read files outside this directory; everything you need is below. Put every file
you write in ./run/.

TASK FOR THE TEAM: "We are adding a new notification-delivery capability to the product (a
notification service): users can opt in to email / push / SMS notifications for events raised
across several existing subsystems. Your team must develop it, and the team needs one decision
on how to build it that everyone can live with."

Use the $PROTOCOL-initiator skill and follow it to the end, with --state ./run/<name>.json on
every call of its script. The specialists are named exactly microservices, monolith and
pragmatist.

Team setup (do this first). You are the lead of an agent team. Create a team with three
teammates named exactly microservices, monolith and pragmatist. Spawn each one with this
instruction, replacing ROLE with its name: "You are the ROLE specialist of a team that must
decide how to build a feature. Read ./personas/ROLE.md first and act as that role. Do not open
any other file in ./personas/. Only the lead (the agent that spawned you) sends you messages,
and you answer only to the lead, with SendMessage. Never message another teammate. Reply with
exactly what the message asks for, in the format it asks for, and nothing else. Skills in
.claude/skills/ are yours to load when a message calls for them." You, the lead, do not read
./personas/ at all.

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

The session stays open, so you can follow the team while the protocol runs. The lead ends with
the protocol's final output, and its files, including the script's state file, are in `run/`.

!!! tip "Reference setup"
    A `setup.sh` that builds this directory, the three personas and the exact kickoff prompts
    (including a no-protocol baseline) are in the `concord/testing/agent-teams` folder of
    `ioc-core-l9`, next to the AgentBridge setup.
