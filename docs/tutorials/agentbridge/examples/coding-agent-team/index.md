# Coding Agent Team

<p class="eyebrow">A team of coding agents settling an architecture decision</p>

A team of coding agents has to settle how to build a feature. Three specialists disagree, and
an initiator coordinates them with a Cognition Protocol. Run it with the
[AgentBridge tutorial](../../index.md).

## The task

> We are adding a new notification-delivery capability to the product (a notification
> service): users can opt in to email / push / SMS notifications for events raised across
> several existing subsystems. The team must develop it, and needs one decision on how to
> build it that everyone can live with.

## The agents

| Agent | Role |
|---|---|
| `initiator` | Starts the protocol and coordinates the specialists. It has no stake in the decision. |
| `microservices` | Advocates a standalone notifications service with its own datastore and deploy pipeline. |
| `monolith` | Advocates a notifications module inside the existing monolith. |
| `pragmatist` | Owns on-call load and infrastructure cost, with no architectural preference. |

```mermaid
flowchart LR
    you(["<b>You</b>"])

    subgraph init ["Initiator"]
        direction TB
        I["<b>initiator</b>"]
    end

    subgraph spec ["Specialists"]
        direction TB
        M["<b>microservices</b>"]
        O["<b>monolith</b>"]
        P["<b>pragmatist</b>"]
    end

    you -- "task" --> I
    I -- "protocol messages" --> M
    I --> O
    I --> P
    I -. "final outcome" .-> you
```

The three specialists also use the words *service*, *deployment* and *outage* in different
senses, which is what a protocol has to bring to the surface before it can reconcile them.

## Role definitions

Each specialist runs in its own process and reads its role definition from the instruction file
of its harness (`CLAUDE.md` for Claude Code). Each definition is a plain-text file you can
download, or copy from the tab. The initiator needs none: its rules are in the
[tutorial](../../index.md#4-write-the-role-definitions).

=== "microservices"

    [:material-download: Download](roles/microservices.txt){ download="microservices.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/microservices.txt"
    ```

=== "monolith"

    [:material-download: Download](roles/monolith.txt){ download="monolith.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/monolith.txt"
    ```

=== "pragmatist"

    [:material-download: Download](roles/pragmatist.txt){ download="pragmatist.txt" }

    ```markdown
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/roles/pragmatist.txt"
    ```

## The initiator's prompt

The prompt hands the initiator the task and tells it which protocol skill to use and how to reach
the specialists. It follows the [template](../../index.md#9-prompt-the-initiator) of the tutorial,
filled in for this example and for the `concord` protocol; for `accord`, replace
`concord-initiator` with `accord-initiator`.

[:material-download: Download prompt.md](prompt.md){ download="prompt.md" }

??? example "prompt.md"

    ```text
    --8<-- "tutorials/agentbridge/examples/coding-agent-team/prompt.md"
    ```

## Running it

Follow the [tutorial](../../index.md), with Claude Code for every agent. Where the tutorial
asks you to describe the team, write the roles, derive the identities and write the prompt, use
the files of this example instead:

```bash
mkdir -p ~/agent-team && cd ~/agent-team
EXAMPLE=https://ioc-project.github.io/cognition-protocols/tutorials/agentbridge/examples/coding-agent-team

# The team and its identities, instead of deriving them (steps 1 and 7)
curl -fsSL $EXAMPLE/team.env -o team.env && source team.env

# One directory per agent, and the role of each specialist (steps 2 and 4)
for id in initiator $(echo $SPECIALISTS); do mkdir -p agents/$id; done
for id in $(echo $SPECIALISTS); do
  curl -fsSL $EXAMPLE/roles/$id.txt -o agents/$id/CLAUDE.md
done

# The initiator's prompt (step 9)
curl -fsSL $EXAMPLE/prompt.md -o prompt.md

grep -qi '<html' prompt.md agents/*/CLAUDE.md && echo "download failed"
```

Then continue the tutorial with the steps that do not depend on the example: create the
`profiles/` and `.tmp/` directories, install the plugin in each agent directory, write the
initiator's rules, create the profiles, generate the certificates, write `env.sh` and
`register-agent.sh`, and start the mesh. Last, send the prompt:

```bash
agentbridge delegate "$(cat prompt.md)" \
  --to initiator --agent-id avatar --endpoint "$SLIM_ENDPOINT"
```
