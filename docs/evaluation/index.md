
# Evaluation

<p class="eyebrow">What was measured, and what it showed</p>

Each Cognition Protocol makes three guarantees by construction — convergence, certified
benefit, and bounded cost — as defined in the [Common Principles](../common-principles.md).
A specification that asserts these properties without measuring them is not a protocol;
it is a claim. Evaluation exists to close that gap.

Each protocol is evaluated on four independent dimensions. They are independent because
they fail independently: a protocol can converge reliably and buy nothing, or buy a great
deal at a price nobody would pay.

| Dimension | What it measures |
|---|---|
| **Convergence / compliance** | Whether the exchange reaches a valid fixed point within its stated bound |
| **Correctness** | Whether the output is accurate relative to a ground truth |
| **Cost** | Tokens and rounds against the declared ceiling and a matched baseline |
| **Benefit** | Whether the outcome measurably improves on what would have happened without the protocol |

!!! abstract "The full write-up"
    The method behind the results — the benchmark, the overlays, the use cases and the
    per-run reporting contract — is set out in full in the extended version of the study.

    [:octicons-download-24: Agreeing on Purpose, Deciding Under Conflict — extended version (PDF, 26 pages)](../protocols/blogs/accord-concord-extended.pdf)

!!! tip "Run the same method yourself"
    The evaluation runs on [MAS Lab](https://ioc-project.github.io/mas-lab/): each use case is an
    experiment that compares the same application with and without ACCORD and CONCORD, with the
    protocols enabled by overlays. The applications and the experiments are published in the
    [`ioc-eval-labs`](https://huggingface.co/datasets/ioc-project/ioc-eval-labs) dataset. The
    [SRE Triage example](../tutorials/mas-lab/examples/sre-triage/index.md) of the MAS Lab tutorial
    runs one of them step by step.

## How this is measured per protocol

| Dimension | ACCORD | CONCORD |
|---|---|---|
| Convergence / compliance | A valid locked contract was produced | Every protocol step executed, all candidates scored, run reached a decision |
| Correctness | Precision — locked terms that were genuinely contested; utilization — locked terms actually used afterwards | Outcome satisfaction per agent; score consistency against each agent's own stated reasoning |
| Cost | Tokens against the matched baseline, init plus carried context | Tokens and cost by phase and by token kind |
| Benefit | Goal success against expected remediation; glossary uptake; influence on reasoning | Worst-off agent's satisfaction; repair rounds actually run |

Certified and correct are reported separately wherever a ground truth exists.

## Results

| Dimension | ACCORD | CONCORD |
|---|---|---|
| **Convergence / compliance** | Partial — 82.7% of runs lock a contract; failures occur early and at low cost | Met — 100% on two use cases, 88.9–95.7% on the others |
| **Correctness** | Met — 74.4% precision, 99.5% utilization | Met — 99.4% of agents satisfied, 99.3% of scores consistent |
| **Cost** | 379K prompt tokens per run, 86.5% cache-eligible | 876K tokens per run, 78% served from cache, against a published ceiling |
| **Benefit** | Not established — uptake +17.9 pp and utilization 99.5%, but goal success flat against baseline | Met — every agent above the floor on all four decisions |

CONCORD meets all four. ACCORD meets three; the benefit dimension is unresolved, in
that the contract is constructed and used but goal success is flat against the
baseline. Measuring dimension by dimension is what [locates that gap](accord.md#benefit)
rather than only reporting it, which an outcome-only comparison would not do.

## Full methodology and results

- [ACCORD](accord.md)
- [CONCORD](concord.md)

## Source

**Agreeing on Purpose, Deciding Under Conflict: Two Protocols for the Agent
Cognitive Layer** — L9 + MAS-Lab T3s, Outshift by Cisco, October 2026.

- [Extended version (PDF, 26 pages)](../protocols/blogs/accord-concord-extended.pdf) — the
  full study: motivation, both protocols, the evaluation methodology, the use
  cases, and the results reported here.
