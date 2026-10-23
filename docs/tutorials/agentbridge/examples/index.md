# Examples

<p class="eyebrow">What the AgentBridge tutorial runs</p>

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
| `scenario.env` | `SPECIALISTS`, the agent ids separated by spaces, and `SLIM_HUMAN_SEED` and `SLIM_MEMBER_DIDS`, the identities of the agents |

The initiator is not a specialist: it needs no role file, and is the same in every example.

## Adding an example

1. Create `docs/tutorials/agentbridge/examples/<name>/` with `index.md`, `task.txt`,
   `scenario.env` and a role file for each specialist.
2. Derive the identity of `avatar`, `driver`, `initiator` and each specialist from a seed and
   list them in `SLIM_MEMBER_DIDS`, in this order:

    ```bash
    shadictl derive-agent-identity --source seed --in <seed file> -n <agent id>
    ```

    On macOS, this also stores the derived keys in the keychain.
3. Add the page to the navigation in `mkdocs.yml` and to the cards above.
