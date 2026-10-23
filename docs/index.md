---
title: Cognition Protocols
---

# Cognition Protocols


Cognition Protocols occupy the agent cognitive layer, **L9**, above the L8 agent transports.

L8 standardises message envelopes, performatives and interaction patterns, but does not define
a verifiable shared meaning for the content those messages carry, nor primitives for coordination
and consensus across a population of agents.

Cognition Protocols are open, measurable specifications that fill that gap — each one satisfies
three requirements by construction: the exchange **converges** provably, emits a **certified
record** of what was achieved, and runs within a **cost bound** declared at the start.

## Common Principles

Each Cognition Protocol satisfies the same three guarantees by construction — convergence,
certified benefit, and bounded cost — rather than leaving them as emergent properties to be
observed. See [Common Principles](common-principles.md) for the full requirements.

## Getting Started

Adding a protocol to an existing multi-agent system requires no application code changes.
See the [getting started guide](getting-started.md) for how skills are packaged and deployed
across different agent harnesses.

## Protocols

The current protocols are:

- **ACCORD** — addresses *absence of shared intent*: misaligned terms, incompatible task models,
  and outputs produced without an agreed verification step. Establishes who is participating,
  what the problem is, what the terms mean, and how correctness is checked — before work begins.
- **CONCORD** — addresses *unreconciled disagreement*: divergent-but-correct specialist outputs
  that converge on an action without consensus. Reconciles differing positions into a certified
  decision.

For full design details see the [Protocols](protocols/index.md) section.

## Evaluation

A protocol specification is only as useful as its evidence. Each Cognition Protocol is
evaluated against independent dimensions — convergence, correctness, cost, and benefit —
so claims about behaviour are measurable, not asserted. See the [evaluation overview](evaluation/index.md)
for results and methodology.

## Demos

Recorded end-to-end demos across SRE triage, e-commerce, and multi-agent
coding teams — with and without cognition protocols. See the [demos](demos/index.md) section.

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
