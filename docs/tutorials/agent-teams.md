# Tutorial: running a protocol with Claude Code agent teams

<p class="eyebrow">Agents in one local Claude Code session</p>

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

## Set up the working directory

Create an empty directory outside any repository, with one persona file per teammate in
`personas/` (the same role descriptions you would put in each AgentBridge `CLAUDE.md`) and an
empty `run/` for the lead's files, and install the protocol's plugin there with project scope:

```bash
PROTOCOL=<protocol>   # a protocol plugin, e.g. accord or concord
WORK=/tmp/teams-$PROTOCOL
mkdir -p $WORK/personas $WORK/run
(cd $WORK && claude plugin install $PROTOCOL@cognition-protocols --scope project)
# write personas/microservices.md, personas/monolith.md, personas/pragmatist.md
```

## Kick off the lead

Start Claude Code in that directory with a prompt that has three parts:

1. **The task**: the decision the team must make, stated once and verbatim.
2. **The protocol**: tell the lead to use the `<protocol>-initiator` skill and its script
   (`--state ./run/...` on every call), with the receivers named exactly as the teammates.
3. **Team setup and messaging rules**: tell the lead to create a team with one teammate per
   role, each told to read its own persona file, to answer only the lead with `SendMessage`, and
   never to message another teammate. Wherever the skill says to send a message to an agent,
   the lead sends it with `SendMessage`, one teammate at a time, waits for the reply, and
   uses that reply text as the agent's answer. It must never write a teammate's reply itself.

```bash
cd $WORK && CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude "$(cat kickoff.md)"
```

The session stays open, so you can follow the team while the protocol runs. The lead ends with
the protocol's final output, and its files, including the script's state file, are in `run/`. `/tmp` can be cleared on a
reboot, so copy `run/` somewhere safe if you want to keep it.

!!! tip "Reference setup"
    A `setup.sh` that builds this directory, the three personas and the exact kickoff prompts
    (including a no-protocol baseline) are in the
    `concord/testing/agent-teams` folder of `ioc-core-l9`, next to the AgentBridge setup.
