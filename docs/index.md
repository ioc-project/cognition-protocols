---
title: IoC Cognition Protocols
hide:
  - navigation
  - toc
---

# IoC Cognition Protocols


Cognition Protocols occupy the agent cognitive layer, **L9**, above the L8 agent
transports. L8 standardises message envelopes, performatives and interaction patterns.
It does not define a verifiable shared meaning for the content those messages carry,
nor primitives for coordination and consensus across a population of agents.

That is the function of L9. Cognition Protocols are open, measurable specifications
that occupy it. Each one satisfies three requirements by construction: the exchange
**converges** provably, emits a **certified record** of what was achieved, and runs
within a **cost bound** declared at the start.

<div class="grid cards" markdown>

-   :material-handshake:{ .lg .middle } **Protocols**

    ---

    ACCORD establishes shared intent before work begins. CONCORD reconciles
    divergent-but-correct specialist outputs into a verifiable decision.

    [:octicons-arrow-right-24: Cognition Protocols](protocols/index.md)

-   :material-chart-bar:{ .lg .middle } **Evaluation**

    ---

    Both protocols evaluated across 500 runs on four independent dimensions:
    convergence, correctness, cost, and benefit.

    [:octicons-arrow-right-24: Evaluation overview](evaluation/index.md)

-   :material-play-circle:{ .lg .middle } **Tutorials and Demos**

    ---

    Recorded end-to-end demos across SRE triage, e-commerce, and multi-agent
    coding teams — with and without each protocol.

    [:octicons-arrow-right-24: Tutorials and Demos](protocols/tutorials/index.md)

-   :material-github:{ .lg .middle } **Get Involved**

    ---

    Specifications, skill packages, evaluation harness, and demo recordings
    are all open. Issues and contributions welcome.

    [:octicons-arrow-right-24: GitHub](https://github.com/ioc-project/cognition-protocols)

</div>

## Background reading

- **[Cognitive challenges in multi-agent systems](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems)**
  — the silent-failure problem that motivates L9: why coordination failures
  are invisible, persistent, and not fixed by stronger models.

- **[Agreeing on Purpose, Deciding Under Conflict](protocols/blogs/accord-concord-extended.pdf)**
  — the extended paper (PDF, 26 pages) covering the protocol designs,
  guarantees, and full evaluation results.
