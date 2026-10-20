# ACCORD — Installation

<p class="eyebrow">Adding intent alignment to an existing multi-agent system</p>

ACCORD is designed to be added to a multi-agent system that already exists, without
modifying the application.

## Coding harnesses, Claw-like agents, and Agntcy AgentBridge

### Skills

ACCORD ships as two skills from
[`ioc-core-l9-skills`](https://github.com/cisco-eti/ioc-core-l9-skills): an
**initiator** and a **receiver**. Install the initiator on any agent that may trigger
the protocol and the receiver on every agent that may be asked to take part. An agent
can carry both.

| Skill | Role | Function |
|---|---|---|
| `l9-accord-initiator` | initiator | Runs the five phases (CONVENE, FRAME, GROUND, CLARIFY, VERIFY, LOCK) via `accord_initiator.py`. |
| `l9-accord-receiver` | receiver | Answers each phase request (`CONVENE_REQUEST`, `FRAME_REQUEST`, `GROUND_REQUEST`, `CLARIFY_REQUEST`, `VERIFY_REQUEST`, `EXECUTION_REQUEST`) with the expected JSON. |

Decisions are made by `accord_initiator.py` — a stdlib-only Python script — not by
the LLM. `python3` must be available on the agent's path.

#### Get the skills

```bash
git clone git@github.com:cisco-eti/ioc-core-l9-skills.git
cd ioc-core-l9-skills
```

Or download individual skill zips from the
[GitHub releases page](https://github.com/cisco-eti/ioc-core-l9-skills/releases).

#### Install

Copy (or symlink) each skill into the harness's skills directory.

| Harness | User-wide | Project only |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Codex CLI | `~/.codex/skills/` | `.agents/skills/` |
| Other agentskills-compatible | see harness docs | same |

```bash
# initiator — on the triggering agent
cp -r l9-accord-initiator ~/.claude/skills/

# receiver — on every participating agent
cp -r l9-accord-receiver ~/.claude/skills/
```

Symlinks stay updated on `git pull`:

```bash
ln -s $PWD/l9-accord-initiator ~/.claude/skills/
ln -s $PWD/l9-accord-receiver ~/.claude/skills/
```

Agents running in separate processes or containers each need their own copy of their
skill, including its `scripts/` folder.

#### Parametrization

ACCORD derives everything it needs from the task itself — there are no required
parameters. The initiator and receiver read no settings from `AGENTS.md` or
`CLAUDE.md`.

## Agents whose code you control

### Skills

Frameworks such as LangChain, Google ADK, and others support agentskills-compatible
skill loading. If your agent framework supports skills, use the same skills above.

### Native implementation

If skills are not available, implement the protocol directly against the
specification (to be published).

## Chaining with CONCORD

ACCORD and CONCORD chain: ACCORD runs first, aligns scope and terms, then hands
CONCORD a pre-verified conflict frame via the locked Intent Contract. Install
[CONCORD](../concord/installation.md) alongside ACCORD to use both in sequence.
