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
| `prompt.md` | The initiator's prompt: the tutorial's template, filled in |
| `team.env` | `SPECIALISTS`, the agent ids separated by spaces. Optionally `SLIM_HUMAN_SEED` and `SLIM_MEMBER_DIDS`, the identities of the agents, which skips deriving them |

The initiator is not a specialist: it needs no role file, and is the same in every example.

## Adding an example

1. Create `docs/tutorials/agentbridge/examples/<name>/` with `index.md`, `prompt.md`, `team.env`
   and a role file for each specialist.
2. To publish the identities, derive those of `avatar`, `driver`, `initiator` and each specialist
   from a seed, in this order, and list them in `SLIM_MEMBER_DIDS`:

    ```bash
    shadictl derive-agent-identity --source seed --in <seed file> -n <agent id>
    ```

    On macOS, this also stores the derived keys in the keychain.
3. Add the page to the navigation in `mkdocs.yml` and to the cards above.
