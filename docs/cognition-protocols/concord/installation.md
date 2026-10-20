# CONCORD — Installation

<p class="eyebrow">Adding conflict resolution to an existing multi-agent system</p>

CONCORD is designed to be added to a multi-agent system that already exists, without
modifying the application.

## Coding harnesses, Claw-like agents, and Agntcy AgentBridge

### Skills

CONCORD ships as two skills from
[`ioc-core-l9-skills`](https://github.com/cisco-eti/ioc-core-l9-skills): an
**initiator** and a **receiver**. Install the initiator on any agent that may trigger
the protocol and the receiver on every agent that may be asked to take part. An agent
can carry both.

| Skill | Role | Function |
|---|---|---|
| `l9-concord-initiator` | initiator | Drives the loop via `concord_initiator.py`: sends messages, records replies, and hands back the certificate. |
| `l9-concord-receiver` | receiver | Answers CONCORD XML messages (`concord-anchor`, `-propose-seed`, `-score`, `-repair`, `-commit`) via `concord_receiver.py forge`. |

Decisions are made by `concord_initiator.py` — a stdlib-only Python script — not by
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
cp -r l9-concord-initiator ~/.claude/skills/

# receiver — on every participating agent
cp -r l9-concord-receiver ~/.claude/skills/
```

Symlinks stay updated on `git pull`:

```bash
ln -s $PWD/l9-concord-initiator ~/.claude/skills/
ln -s $PWD/l9-concord-receiver ~/.claude/skills/
```

Agents running in separate processes or containers each need their own copy of their
skill, including its `scripts/` folder.

#### Parametrization

CONCORD takes two user-level inputs:

| Input | `init` flag | Default |
|---|---|---|
| Satisfaction threshold | `--satisfaction-threshold` | `0.6` |
| Round budget | `-B` / `--budget` | `10` |

Everything else — agents, schema, context, anchors — the initiator derives from the
task. Receivers need no parameters.

Two ways to set non-default values:

**Persistent — in `AGENTS.md` or `CLAUDE.md`:**

```markdown
## L9-CONCORD
- Use `--satisfaction-threshold 0.8` and `-B 6`.
```

**Per task — in the prompt:**

```
Use l9-concord-initiator with threshold 0.8 and budget 6.
```

## Agents whose code you control

### Skills

Frameworks such as LangChain, Google ADK, and others support agentskills-compatible
skill loading. If your agent framework supports skills, use the same skills above.

### Native implementation

If skills are not available, implement the protocol directly against the
[specification](specification.md).

## Chaining with ACCORD

ACCORD and CONCORD chain: ACCORD runs first, aligns scope and terms, then hands
CONCORD a pre-verified conflict frame via the locked Intent Contract. Install
[ACCORD](../accord/installation.md) alongside CONCORD to use both in sequence.
