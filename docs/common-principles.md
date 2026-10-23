---
icon: material/list-box-outline
---

# Common Principles

<p class="eyebrow">What makes a specification a protocol</p>

A layer designates a position in an architecture. A protocol specifies an exchange
that can be implemented, executed and measured. That distinction imposes three
requirements that an architectural specification does not. Each Cognition Protocol
satisfies all three by construction rather than by observation.

| Principle | What it requires |
|---|---|
| **Convergence** | The exchange provably reaches a fixed point, or terminates within a stated bound. Neither cycling nor unbounded deliberation is admissible, and a valid result exists at every step — the **anytime** property — so a deadline can truncate a run without invalidating what it has already produced |
| **Benefit, certified rather than promised** | The protocol emits a machine-readable artifact recording what was achieved and, where the objective was not met, the precise shortfall |
| **Cost, bounded a priori** | An exact ceiling on rounds, candidate outcomes, agent evaluations and model calls — reported when the run opens, reconciled against actual spend when it closes |

## Portability

A protocol is portable only if it is specified independently of the transport it
runs over. Each Cognition Protocol is a self-contained definition together with one
**transport binding** per L8 transport it supports.

| Concern | For A2A |
|---|---|
| **Field mapping** | Protocol URI in `message.extensions`, phase and role in `message.metadata`, content in `message.parts` |
| **Discovery** | The agent card's extension list and the skill's declared media types |
| **Session correlation** | The existing `contextId` |

Where all agents are under common control, uniform support can be asserted by
construction and discovery omitted. Discovery becomes mandatory once third-party
agents participate.

## Scope boundary

Cognition Protocols maintain shared state within a coordination session only. The
following are out of scope:

- **Governance and policy enforcement** — owned by the Cognition Engines area.
- **Persistent memory and knowledge graphs** — owned by the Cognition Fabric.
