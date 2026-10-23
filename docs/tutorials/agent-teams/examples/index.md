# Examples

<p class="eyebrow">Real prompts for the Claude Agent Teams tutorial</p>

The [tutorial](../index.md) describes the steps to run a protocol on a team of agents, with a
generic prompt template. An example is a complete implementation of it: a team, its task, and
the real prompt, ready to run.

<div class="grid cards" markdown>

-   **[Coding Agent Team](coding-agent-team/index.md)**

    ---

    A team of coding agents settling an architecture decision: three specialists that must
    decide how to build a notification service.

</div>

## Anatomy of an example

An example page describes the task and the agents, and publishes the prompt next to it as
`prompt.md`. The prompt is the tutorial's template, filled in with the task, the id and the role
description of each specialist, and the protocol skill.

## Adding an example

1. Create `docs/tutorials/agent-teams/examples/<name>/` with `index.md` and `prompt.md`.
2. Add the page to the navigation in `mkdocs.yml` and to the cards above.
