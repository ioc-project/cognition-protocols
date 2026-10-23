# Coding Agent Team

<p class="eyebrow">A team of coding agents settling an architecture decision</p>

A team of coding agents has to settle how to build a feature. This scenario is shared by the tutorials of this section, so the task and the roles are defined once, here: [Running a protocol with Claude Agent Teams](agent-teams.md).

## The task

> We are adding a new notification-delivery capability to the product (a notification
> service): users can opt in to email / push / SMS notifications for events raised across
> several existing subsystems. The team must develop it, and needs one decision on how to
> build it that everyone can live with.

Three specialists disagree on how to build it, and the initiator coordinates them:

| Agent | Role |
|---|---|
| `initiator` | Starts the protocol and coordinates the others. It has no stake in the decision. |
| `microservices` | Advocates a standalone notifications service with its own datastore and deploy pipeline. |
| `monolith` | Advocates a notifications module inside the existing monolith. |
| `pragmatist` | Owns on-call load and infrastructure cost, with no architectural preference. |

The three specialists also use the words *service*, *deployment* and *outage* in different
senses, which is what a protocol has to bring to the surface before it can reconcile them.

## Role descriptions

Each description is a plain-text file you can download, or copy from the tab. Save it as the
agent's role file: `personas/<role>.md` in the [Claude Agent Teams tutorial](agent-teams.md). The
downloaded file is named `CLAUDE.md`, so rename it.

=== "microservices"

    [:material-download: Download](roles/microservices.txt){ download="CLAUDE.md" }

    ```markdown
    --8<-- "tutorials/coding-agent-team/roles/microservices.txt"
    ```

=== "monolith"

    [:material-download: Download](roles/monolith.txt){ download="CLAUDE.md" }

    ```markdown
    --8<-- "tutorials/coding-agent-team/roles/monolith.txt"
    ```

=== "pragmatist"

    [:material-download: Download](roles/pragmatist.txt){ download="CLAUDE.md" }

    ```markdown
    --8<-- "tutorials/coding-agent-team/roles/pragmatist.txt"
    ```

=== "initiator"

    [:material-download: Download](roles/initiator.txt){ download="CLAUDE.md" }

    ```markdown
    --8<-- "tutorials/coding-agent-team/roles/initiator.txt"
    ```

The initiator has no stake in the decision, so its file only sets working rules.
