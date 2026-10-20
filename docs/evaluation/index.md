---
icon: material/flask-outline
---

# Benchmarks

<p class="eyebrow">What was measured, and what it showed</p>

Both protocols were evaluated on four dimensions, chosen because they fail
independently: a protocol can converge reliably and buy nothing, or buy a great deal
at a price nobody would pay.

!!! abstract "The full write-up"
    This page reports the results. The method behind them — the benchmark, the
    overlays, the use cases and the per-run reporting contract — is set out in full
    in the extended version of the study.

    [:octicons-download-24: Agreeing on Purpose, Deciding Under Conflict — extended version (PDF, 26 pages)](../protocols/blogs/accord-concord-extended.pdf)

| Dimension | ACCORD | CONCORD |
|---|---|---|
| **Convergence / compliance** | Partial — 82.7% of runs lock a contract; failures occur early and at low cost | Met — 100% on two use cases, 88.9–95.7% on the others |
| **Correctness** | Met — 74.4% precision, 99.5% utilization | Met — 99.4% of agents satisfied, 99.3% of scores consistent |
| **Cost** | 379K prompt tokens per run, 86.5% cache-eligible | 876K tokens per run, 78% served from cache, against a published ceiling |
| **Benefit** | Not established — uptake +17.9 pp and utilization 99.5%, but goal success flat against baseline | Met — every agent above the floor on all four decisions |

CONCORD meets all four. ACCORD meets three; the benefit dimension is unresolved, in
that the contract is constructed and used but goal success is flat against the
baseline. Measuring dimension by dimension is what
[locates that gap](accord.md#benefit) rather than only reporting it, which an outcome-only
comparison would not do.

## What each run reports

| Dimension | ACCORD | CONCORD |
|---|---|---|
| Convergence / compliance | A valid locked contract was produced | Every protocol step executed, all candidates scored, run reached a decision |
| Correctness | Precision — locked terms that were genuinely contested; utilization — locked terms actually used afterwards | Outcome satisfaction per agent; score consistency against each agent's own stated reasoning |
| Cost | Tokens against the matched baseline, init plus carried context | Tokens and cost by phase and by token kind |
| Benefit | Goal success against expected remediation; glossary uptake; influence on reasoning | Worst-off agent's satisfaction; repair rounds actually run |

Certified and correct are reported separately wherever a ground truth exists.

!!! info "ACCORD evaluation"
    The full ACCORD measurement results — convergence, correctness, cost, and benefit
    against a matched baseline — are on the
    [ACCORD Evaluation page](accord.md).

!!! info "CONCORD evaluation"
    The full CONCORD measurement results — compliance and correctness, cost, threshold
    calibration and variation across decision types — are on the
    [CONCORD Evaluation page](concord.md).

## Source

**Agreeing on Purpose, Deciding Under Conflict: Two Protocols for the Agent
Cognitive Layer** — L9 + MAS-Lab T3s, Outshift by Cisco, October 2026.

- [Extended version (PDF, 26 pages)](../protocols/blogs/accord-concord-extended.pdf) — the
  full study: motivation, both protocols, the evaluation methodology, the use
  cases, and the results reported here.
