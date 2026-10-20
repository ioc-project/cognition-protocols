# Cognition Protocols

All Cognition Protocols share a common set of [design requirements](design-requirements.md)
— convergence, certified benefit, and bounded cost — that each protocol satisfies by
construction rather than by observation.

Adding a protocol to an existing multi-agent system requires no application changes.
See the [installation guide](installation.md) for how skills are packaged and deployed
across different agent harnesses.

## Current protocols

Two protocols are currently defined, addressing the two cognitive failure classes
that design iteration cannot remove. Further protocols addressing other failure
classes are in development.

<div class="grid cards" markdown>

-   **[ACCORD — intent alignment](accord/index.md)**

    ---

    Runs before the task. Consolidates divergent understandings into one shared
    model by coverage rather than negotiation: who is participating, what the
    problem is, what the terms mean, and how completion is verified. Produces the
    locked **Intent Contract**.

-   **[CONCORD — deciding under conflict](concord/index.md)**

    ---

    Runs during the task, taking the locked contract as input. Selects an outcome
    acceptable to every agent from a set of options that are divergent but each
    correct from its own role, and issues a certificate recording the result — or
    identifies the agent whose floor was not met and the size of the shortfall.

</div>
