---
icon: material/play-circle-outline
---

# Tutorials and Demos

<p class="eyebrow">Running ACCORD and CONCORD on a multi-agent system</p>

!!! info "In preparation"
    The guided walkthroughs below are being written.

## What insertion requires

Both protocols are distributed as skills: an initiator skill, a receiver skill and
the scripts holding the deterministic half of the protocol. Adding them to a system
under your own control requires no change to the application.

In the measured SRE triage integration, a single overlay file adds the initiator
skill to the lead agent and the receiver skill to the five specialists. No framework
is rewritten and no transport is replaced, and the baseline remains runnable as the
control arm.

[:octicons-arrow-right-24: Installation](../installation.md)

## Demos

Recordings of the protocols running on real applications. Each is one decision, from
the opening proposals to the committed outcome.

!!! note "Where the recordings are hosted"
    The files are attached to a [repository release](https://github.com/cisco-eti/IoC-BHAG/releases/tag/demos-2026-10)
    rather than served from this site, because they are too large to publish with it.
    Each demo page links to its own recording; access is the same as for this site.

<div class="grid cards" markdown>

-   **[Coding Architect Team](coding-architect-team.md)**

    ---

    Architect agents settling a design question, recorded under two agent runtimes —
    one agent per architect, and Claude Agent Teams.

-   **[Hiddenbench](hiddenbench.md)**

    ---

    A hidden-profile task: the facts needed for the correct answer are split across
    the participants, and no agent holds enough alone.

-   **[E-Commerce](e-commerce.md)**

    ---

    A promotion decided across specialist systems with incompatible objectives — the
    one use case where the repair loop runs in most runs.

-   **[SRE Triage](sre-triage.md)**

    ---

    Four subsystem owners converging on one remediation, each able to see only its
    own subsystem. The application the benchmark is built on.

-   **[Customer renewal discount](customer-renewal-discount.md)**

    ---

    A commercial negotiation across roles pulling in different directions, included
    to test whether the protocol travels off incident response.

</div>

## Walkthroughs

| Walkthrough | Status |
|---|---|
| Adding the protocol skills to an existing multi-agent system | In preparation |
| Running ACCORD and inspecting the Intent Contract it locks | In preparation |
| Running CONCORD and reading the certificate | In preparation |
| Choosing a satisfaction floor for a workload | In preparation |
| Reproducing the measured results in MAS-Lab | In preparation |

## Related

- [ACCORD](../accord/index.md) — phases, guarantees and measured results
- [CONCORD](../concord/index.md) — phases, guarantees and measured results
- [Installation](../installation.md) — insertion, packaging and transport bindings
