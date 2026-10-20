---
title: IoC Cognition Protocols
hide:
  - navigation
  - toc
---

# IoC Cognition Protocols

<p class="eyebrow">L9 — the agent cognitive layer</p>

Multi-agent systems fail in ways that more capable models do not fix. Agents produce
individually correct outputs that contradict each other, route tasks against inaccurate
capability models, and reach decisions that are never verifiably shared. These are
**coordination failures**, not capability failures — and they live at a layer that
current infrastructure does not address.

[Agent-to-agent transports](https://google.github.io/A2A/) — **L8** — standardise
message envelopes, performatives, and interaction patterns. A2A, MCP, and SLIM operate
at that layer. What they do not define is a verifiable shared meaning for the content
those messages carry, or primitives for coordination and consensus across a population
of agents. That is **L9**, the agent cognitive layer — currently unpopulated.

Cognition Protocols are open, measurable specifications that occupy L9. Each one
satisfies three requirements by construction: the exchange **converges** provably,
emits a **certified record** of what was achieved, and runs within a **cost bound**
declared at the start.

<div class="grid cards" markdown>

-   :material-handshake:{ .lg .middle } **Protocols**

    ---

    ACCORD establishes shared intent before work begins. CONCORD reconciles
    divergent-but-correct specialist outputs into a verifiable decision.

    [:octicons-arrow-right-24: Cognition Protocols](cognition-protocols/index.md)

-   :material-chart-bar:{ .lg .middle } **Evaluation**

    ---

    Both protocols evaluated across 500 runs on four independent dimensions:
    convergence, correctness, cost, and benefit.

    [:octicons-arrow-right-24: Evaluation overview](evaluation/index.md)

-   :material-play-circle:{ .lg .middle } **Tutorials and Demos**

    ---

    Recorded end-to-end demos across SRE triage, e-commerce, and multi-agent
    coding teams — with and without each protocol.

    [:octicons-arrow-right-24: Tutorials and Demos](cognition-protocols/tutorials/index.md)

-   :material-github:{ .lg .middle } **Get Involved**

    ---

    Specifications, skill packages, evaluation harness, and demo recordings
    are all open. Issues and contributions welcome.

    [:octicons-arrow-right-24: GitHub](https://github.com/ioc-project/ioc-cognition-protocols)

</div>

## Background reading

- **[Cognitive challenges in multi-agent systems](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems)**
  — the silent-failure problem that motivates L9: why coordination failures
  are invisible, persistent, and not fixed by stronger models.

- **[Agreeing on Purpose, Deciding Under Conflict](cognition-protocols/blogs/accord-concord-extended.pdf)**
  — the extended paper (PDF, 26 pages) covering the protocol designs,
  guarantees, and full evaluation results.
