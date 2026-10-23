# Examples

<p class="eyebrow">Real prompts and roles for the AgentBridge tutorial</p>

The [tutorial](../index.md) describes the steps to run a protocol on a team of agents joined by
AgentBridge. An example is a complete implementation of it: a team, its task, the role of each
specialist and the real prompt, ready to run.

<div class="grid cards" markdown>

-   **[Coding Agent Team](coding-agent-team/index.md)**

    ---

    A team of coding agents settling an architecture decision: three specialists that must
    decide how to build a notification service.

</div>

## Anatomy of an example

An example page describes the task and the agents, and publishes these files next to it:

| File | Content |
|---|---|
| `roles/<id>.txt` | The role definition of each specialist, one file per agent id. It is saved as the instruction file of the agent's harness |
| `prompt.txt` | The initiator's prompt: the tutorial's template, filled in. It is saved as `prompt.md` |
| `team.env` | `SPECIALISTS`, the agent ids separated by spaces. Optionally `SLIM_HUMAN_SEED` and `SLIM_MEMBER_DIDS`, the identities of the agents, which skips deriving them |
