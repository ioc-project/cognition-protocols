---
title: IoC Cognition Protocols
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

## Common Principles

All Cognition Protocols share a common set of [common principles](common-principles.md)
— convergence, certified benefit, and bounded cost — that each protocol satisfies by
construction rather than by observation.

## Getting Started

Adding a protocol to an existing multi-agent system requires no application changes.
See the [getting started guide](getting-started.md) for how skills are packaged and deployed
across different agent harnesses.

## Protocols

ACCORD establishes shared intent before work begins. CONCORD reconciles
divergent-but-correct specialist outputs into a verifiable decision.
See the [Cognition Protocols](protocols/index.md) section for full details.

## Evaluation

Both protocols evaluated across 500 runs on four independent dimensions:
convergence, correctness, cost, and benefit. See the [evaluation overview](evaluation/index.md).

## Demos

Recorded end-to-end demos across SRE triage, e-commerce, and multi-agent
coding teams — with and without each protocol. See the [demos](demos/index.md) section.

## Get Involved

Specifications, skill packages, evaluation harness, and demo recordings
are all open. Issues and contributions welcome on [GitHub](https://github.com/ioc-project/cognition-protocols).

## Background reading

- **[Cognitive challenges in multi-agent systems](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems)**
  — the silent-failure problem that motivates L9: why coordination failures
  are invisible, persistent, and not fixed by stronger models.

- **[Agreeing on Purpose, Deciding Under Conflict](protocols/blogs/accord-concord-extended.pdf)**
  — the extended paper (PDF, 26 pages) covering the protocol designs,
  guarantees, and full evaluation results.
