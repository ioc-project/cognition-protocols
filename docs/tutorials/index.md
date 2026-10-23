# Tutorials

<p class="eyebrow">Running a Cognition Protocol end to end</p>

Step-by-step walkthroughs that run a Cognition Protocol on a team of agents, using the skills
from the [Getting Started](../getting-started.md) page. Each tutorial comes with examples: a
team of agents, the role of each one, and a task for them to run the protocol on. You can
follow a tutorial with an example of your own.

<div class="grid cards" markdown>

-   **[Running Cognition Protocols with Claude Agent Teams](agent-teams/index.md)**

    ---

    The quickest way to try a protocol: an initiator and its teammates in your local Claude
    Code, with no message bus. [Examples](agent-teams/examples/index.md)

-   **[Running Cognition Protocols over AgentBridge](agentbridge/index.md)**

    ---

    Independent Claude Code processes joined by AgentBridge (A2A, here over a SLIM message bus),
    each sandboxed. Agents can live in separate processes, containers or harnesses.
    [Examples](agentbridge/examples/index.md)

-   **[Running Cognition Protocols with MAS Lab](mas-lab/index.md)**

    ---

    Agents defined as YAML manifests (`kind: Agent`, `kind: MAS`) and run with `mas-ctl`.
    No coding harness required.

</div>
