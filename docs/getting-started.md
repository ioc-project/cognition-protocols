---
icon: material/rocket-launch-outline
---

# Getting Started

<p class="eyebrow">Adding a Cognition Protocol to an existing multi-agent system</p>

A Cognition Protocol is designed to be added to a multi-agent system that already
exists, without modifying the application. The design assumption is minimal: agents
communicate using some messaging mechanism, whether native to the framework or an
external CLI. No further assumption is made about the application.

The messaging mechanism can be anything that lets one agent send a message to another
and read its reply. The [tutorials](tutorials/index.md) show concrete setups, such as
[running a protocol with Claude Agent Teams](tutorials/coding-agent-team/agent-teams.md),
which is the quickest way to try a protocol in your local Claude Code, and
[running a protocol over AgentBridge](tutorials/coding-agent-team/agentbridge.md), which makes independent coding
harnesses talk over a message bus.

## Coding harnesses, Claw-like agents, and Agntcy AgentBridge

Each protocol is distributed as **skills** — an initiator skill, a receiver skill,
and the scripts that implement the deterministic portion of the protocol. Install the
initiator on any agent that may trigger the protocol and the receiver on every agent
that may be asked to take part. If any agent in the system can trigger the protocol,
every agent should carry both skills: each one can then start a run, and each one can
answer when another starts it.

### Getting the skills

=== "Claude Code marketplace"

    For Claude Code, the recommended install path is the plugin marketplace. One command
    registers this repository as a marketplace, and one more installs each protocol.

    **Install both skills (both roles):**

    ```bash
    claude plugin marketplace add ioc-project/cognition-protocols
    claude plugin install concord@cognition-protocols
    claude plugin install accord@cognition-protocols
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
    install only the skill you need via the skills directory — see [Installing a skill](#installing-a-skill) below.

=== "AI Catalog"

    Skills are published to an [AI Catalog](https://ioc-project.github.io/cognition-protocols/ai-catalog.json) and can be installed with the [`ai-catalog` CLI](https://github.com/Agent-Card/ai-catalog-cli).

    Register the catalog once:

    ```bash
    ai-catalog catalog add ioc-protocols https://ioc-project.github.io/cognition-protocols/ai-catalog.json
    ```

    Then pull and install a skill. Skills are distributed as zip archives, so unzip after pulling:

    ```bash
    ai-catalog pull urn:air:ioc-project:skill:concord-initiator -o /tmp/skill-download/
    unzip /tmp/skill-download/concord-initiator.zip -d /tmp/skill-download/
    mv /tmp/skill-download/concord-initiator ~/.claude/skills/
    ```

    Repeat for each skill you need (`concord-receiver`, `accord-initiator`, `accord-receiver`).

=== "GitHub releases"

    Download individual skill zips from the
    [GitHub releases page](https://github.com/ioc-project/cognition-protocols/releases), then unzip and copy the skill folder into the harness's skills directory.

### Installing a skill

Copy (or symlink) each skill folder into the harness's skills directory.

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

## Next steps

Run a protocol end to end with the [tutorials](tutorials/index.md).
