# Installation

<p class="eyebrow">Adding a Cognition Protocol to an existing multi-agent system</p>

A Cognition Protocol is designed to be added to a multi-agent system that already
exists, without modifying the application. The design assumption is minimal: agents
communicate using some messaging mechanism, whether native to the framework or an
external CLI. No further assumption is made about the application.

## Coding harnesses, Claw-like agents, and Agntcy AgentBridge

Each protocol is distributed as **skills** — an initiator skill, a receiver skill,
and the scripts that implement the deterministic portion of the protocol. Install the
initiator on any agent that may trigger the protocol and the receiver on every agent
that may be asked to take part. An agent can carry both.

### Claude Code marketplace

For Claude Code, the recommended install path is the plugin marketplace. One command
registers this repository as a marketplace, and one more installs each protocol.

**Install both skills (both roles):**

```bash
claude plugin marketplace add ioc-project/ioc-cognition-protocols
claude plugin install concord@ioc-cognition-protocols
claude plugin install accord@ioc-cognition-protocols
```

Each protocol plugin contains two skills — `concord-initiator` and `concord-receiver`
(or `accord-initiator` and `accord-receiver`). Their activation conditions are precise:
the initiator skill only activates when the agent is orchestrating a protocol run;
the receiver skill only when the agent receives a protocol message. An agent carrying
both will activate the right skill automatically based on which messages it receives.

**Install for a single role:**

The plugin marketplace does not support per-skill enable/disable (Claude Code's
`skillOverrides` setting applies only to `.claude/skills/` directory skills, not to
plugin skills). If an agent plays only one role and you want to reduce context cost,
install only the needed skill via the skills directory instead:

```bash
git clone https://github.com/ioc-project/ioc-cognition-protocols.git

# Orchestrator only — receives no protocol messages, only initiates:
cp -r ioc-cognition-protocols/protocols/concord/skills/concord-initiator ~/.claude/skills/

# Participant only — receives and responds to protocol messages:
cp -r ioc-cognition-protocols/protocols/concord/skills/concord-receiver ~/.claude/skills/
```

Use a symlink instead of `cp` to stay current with updates:

```bash
ln -s "$(pwd)/ioc-cognition-protocols/protocols/concord/skills/concord-initiator" ~/.claude/skills/concord-initiator
```

### Getting the skills

All skills ship from [`ioc-core-l9-skills`](https://github.com/cisco-eti/ioc-core-l9-skills):

```bash
git clone git@github.com:cisco-eti/ioc-core-l9-skills.git
cd ioc-core-l9-skills
```

Or download individual skill zips from the
[GitHub releases page](https://github.com/cisco-eti/ioc-core-l9-skills/releases).

### Installing a skill

Copy (or symlink) each skill into the harness's skills directory.

| Harness | User-wide | Project only |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Codex CLI | `~/.codex/skills/` | `.agents/skills/` |
| Other agentskills-compatible | see harness docs | same |

Symlinks stay updated on `git pull`. Agents running in separate processes or
containers each need their own copy of their skill, including its `scripts/` folder.

## Agents whose code you control

Frameworks such as LangChain, Google ADK, and others support agentskills-compatible
skill loading. If your agent framework supports skills, use the same skills above.

If skills are not available, implement the protocols directly against their
specifications.

