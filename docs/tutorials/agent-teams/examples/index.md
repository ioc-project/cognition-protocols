# Examples

<p class="eyebrow">What the Claude Agent Teams tutorial runs</p>

The [tutorial](../index.md) runs any example: a team of agents with a task. To run another
example, you only change the files below and the prompt that starts the run.

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
| `task.txt` | The task the initiator hands to the team |
| `roles/<id>.txt` | The role description of each specialist, one file per agent id |
| `scenario.env` | `SPECIALISTS`, the agent ids separated by spaces |

The initiator is not a specialist: it needs no role file, and is the same in every example.

## Adding an example

1. Create `docs/tutorials/agent-teams/examples/<name>/` with `index.md`, `task.txt`,
   `scenario.env` and a role file for each specialist.
2. Add the page to the navigation in `mkdocs.yml` and to the cards above.
