# CONCORD Protocol Specification

Version 0.1.0

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT).

## Overview

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT).

Activate CONCORD when:
- An outcome must satisfy agents whose criteria may conflict.
- A named negotiating set can be specified.
- A shared candidate schema exists.

Conflict may be confirmed, anticipated, or merely uncertain — all three cases warrant activation.

---

## Roles

| Role | Count | Responsibilities |
|---|---|---|
| **Initiator** | 1 | Routes messages; records replies; drives the state machine |
| **Participant** | N ≥ 2 | Each holds a role-specific scoring perspective; produces replies |

The initiator never authors replies on behalf of participants. Participants send replies verbatim — no preamble or rewording.

---

## Configuration Parameters

These are supplied once at run start and are immutable for the run.

| Parameter | Required | Default | Meaning |
|---|---|---|---|
| `agents` | yes | — | Ordered list of participant agent identifiers |
| `satisfaction_threshold` (τ) | no | 0.6 | Normalized score every agent must clear for SUCCESS, in (0, 1]; set at the genuine bar, not an estimated reachable value |
| `eps` | no | 0.05 | Minimum leximin improvement required to replace the incumbent |
| `B` | no | 10 | Maximum repair rounds before BEST_EFFORT termination |
| `schema` | yes | — | Candidate format specification broadcast to agents |
| `context` | yes | — | Problem statement broadcast to agents |
| `x_reject` | yes | — | Outcome description anchoring the bottom (worst) of each agent's scale |
| `x_ideal` | yes | — | Outcome description anchoring the top (best) of each agent's scale |

`x_reject` and `x_ideal` must describe outcome states ("error rate returns to 0"), not proposals or mechanisms, as an anchor that names an action biases the scoring scale.

---

## Protocol Phases

### State Machine

```
START
 └─ ANCHOR   (broadcast → all agents)
      all anchor replies received
      └─ SEED   (broadcast → all agents)
           all seed replies received
           └─ SCORE   (broadcast → all agents; full pool)
                evaluate
                ├─ floor(x*) ≥ τ, no absent scores, no MISSING  →  COMMIT SUCCESS
                ├─ t ≥ B                                          →  COMMIT BEST_EFFORT
                └─ t += 1, identify bottleneck b
                     └─ REPAIR   (unicast → b only)
                          b's candidate received
                          └─ SCORE   (broadcast → all agents; new candidate only)
                               evaluate
                               ├─ SUCCESS / BEST_EFFORT
                               └─ next REPAIR round …
```

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| ANCHOR | Protocol start |
| SEED | All agents have submitted anchor replies |
| SCORE | All agents have submitted seed replies; or a repair candidate was just received |
| REPAIR | Evaluation finds floor(x\*) < τ and t < B |
| COMMIT | Evaluation yields SUCCESS or BEST_EFFORT |

ANCHOR, SEED, and SCORE messages are broadcast (all recipients receive identical content). REPAIR is unicast to the bottleneck agent only. COMMIT is broadcast.

---

## Message Schemas

### `<concord-anchor>` — Initiator → all agents

Establishes each agent's personal raw scoring scale.

```xml
<concord-anchor>
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <x_reject>outcome description anchoring the rejection end of the scale</x_reject>
  <x_ideal>outcome description anchoring the ideal end of the scale</x_ideal>
  <instructions>Rate the two outcomes above on your own raw 0-100 scale …</instructions>
</concord-anchor>
```

### `<concord-anchor-reply>` — Agent → Initiator

```xml
<concord-anchor-reply>
  <raw_reject>8.0</raw_reject>
  <raw_ideal>95.0</raw_ideal>
  <!-- optional: <note>free text</note> -->
</concord-anchor-reply>
```

`raw_reject` should be low (agent's satisfaction if the rejection outcome occurred); `raw_ideal` should be high. `raw_ideal` must strictly exceed `raw_reject`. These are satisfaction ratings of hypothetical outcomes, not measurements of current state.

---

### `<concord-propose-seed>` — Initiator → all agents

Each agent proposes one candidate independently.

```xml
<concord-propose-seed>
  <candidate_schema>format specification</candidate_schema>
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <context>problem statement</context>
  <instructions>Propose the single candidate that best satisfies your own role …</instructions>
</concord-propose-seed>
```

Each agent proposes from its own role's perspective, without pre-compromising for other roles. The rationale should state the facts that support the candidate and the domain knowledge that argues against the other options in the schema.

### `<concord-candidate-reply>` — Agent → Initiator

Used for both seed and repair proposals.

```xml
<concord-candidate-reply>
  <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
  <rationale>evidence and reasoning</rationale>
</concord-candidate-reply>
```

---

### `<concord-score>` — Initiator → all agents

Requests ratings for one or more candidates. After the initial seed round, only the newly added repair candidate appears.

```xml
<concord-score>
  <context>problem statement</context>
  <candidates>
    <candidate id="seed-cost">
      <text>strategy: local-memory, ttl_seconds: 300</text>
      <rationale>proposer's rationale</rationale>
    </candidate>
    <candidate id="seed-reliability">
      <text>strategy: redis-cluster</text>
      <rationale>proposer's rationale</rationale>
    </candidate>
  </candidates>
  <instructions>Rate every candidate on your own raw 0-100 scale …</instructions>
</concord-score>
```

### `<concord-score-reply>` — Agent → Initiator

```xml
<concord-score-reply>
  <score candidate_id="seed-cost">
    <raw>82.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
  <score candidate_id="seed-reliability">
    <raw>41.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
</concord-score-reply>
```

Agents must rate every candidate in the message on the same raw 0–100 scale used at anchoring. Reasoning and rationale text is the only channel through which an agent's private domain knowledge reaches the other participants and the repair agent, so agents should state everything relevant, including evidence against a candidate.

---

### `<concord-repair>` — Initiator → bottleneck agent only

Sent only to the agent with the lowest score on the current incumbent.

```xml
<concord-repair>
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <candidate_schema>format specification</candidate_schema>
  <context>problem statement</context>
  <incumbent id="seed-cost" your_score="0.43" shortfall="0.17">
    <text>strategy: local-memory, ttl_seconds: 300</text>
    <rationale>proposer's rationale</rationale>
    <scores>
      <score agent="cost" score="0.85">
        <reasoning>…</reasoning>
      </score>
      <score agent="reliability" score="0.43">
        <reasoning>…</reasoning>
      </score>
    </scores>
  </incumbent>
  <instructions>You are the least-satisfied agent on the incumbent …</instructions>
</concord-repair>
```

`your_score` and `score` attributes in `<scores>` are normalized [0,1] values for display only. The receiving agent must not echo these back as its own rating — it always reports raw 0–100.

The bottleneck agent must propose a candidate that: clears its own bar, does not knowingly push other agents below threshold, is genuinely new, conforms to schema, and engages with the strongest counter-evidence from the incumbent's scores.

Reply is a `<concord-candidate-reply>` (same schema as seed).

---

### `<concord-commit>` — Initiator → all agents

Terminates the run. No reply required from participants.

```xml
<concord-commit>
  <outcome>SUCCESS</outcome>                    <!-- or BEST_EFFORT -->
  <winning_candidate_id>r1-reliability</winning_candidate_id>
  <winning_candidate>strategy: redis-single, ttl_seconds: 600</winning_candidate>
  <final_scores>
    <score agent="cost" value="0.78" />
    <score agent="reliability" value="0.81" />
    <score agent="security" value="0.72" />
  </final_scores>
  <shortfall>                                   <!-- only present if BEST_EFFORT -->
    <agent name="cost" score="0.55" short_by="0.05" />
  </shortfall>
  <unknown>                                     <!-- only present if a score was marked missing -->
    <agent name="reliability" />
  </unknown>
</concord-commit>
```
---

## Scoring Model

### Raw vs Normalized

Agents always report raw scores on a personal 0–100 scale anchored at `raw_reject` and `raw_ideal`. The initiator normalizes to [0,1] using each agent's own anchors:

```
s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0)
```

Normalized scores appear in REPAIR messages (`your_score`, `<scores>`) and COMMIT messages (`<final_scores>`). Agents must never echo normalized values back as their own raw rating.

### Candidate Selection (SELECT)

```
feasible = { x in pool : every agent rated x AND floor(x) >= τ }
if feasible is non-empty:
    x* = leximin-best of feasible
else:
    x* = the candidate with the largest floor score across all pool members
```

Ties in floor score are broken by leximin comparison of the full score vector.

### Incumbent Replacement (ACCEPT)

A new candidate `x_new` replaces the incumbent `x_star` if:

```
leximin(x_new) beats leximin(x_star) by >= eps at some position k,
without degrading any position before k
```

### Bottleneck Identification

The agent with the lowest normalized score on `x_star` is the bottleneck. Ties are broken by the agent's position in the ordered `agents` list.

---

## Candidate ID Scheme

| Round | Pattern | Example |
|---|---|---|
| Seed (round 0) | `seed-<agent>` | `seed-cost` |
| Repair round N | `r<N>-<agent>` | `r1-reliability`, `r2-security` |

---

## Termination Conditions

| Outcome | Condition |
|---|---|
| `SUCCESS` | `floor(x*) >= τ` AND every agent has rated `x*` AND no MISSING entries in the score matrix |
| `BEST_EFFORT` | `t >= B` (budget exhausted) OR any MISSING entry exists at the time of termination |

A score is marked MISSING for an agent/candidate pair when the agent fails to respond after a retry in the Score phase. Any MISSING entry forces BEST_EFFORT regardless of other scores.

---

## Invariants

1. **No certification over silence.** SUCCESS is never declared if any agent has not submitted scores for `x*`.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are rated once.** Replacing an anchor reply invalidates all prior comparisons and the run integrity.
4. **One proposer per round.** The bottleneck is determined algorithmically; the initiator does not choose.
5. **Messages are sent verbatim.** The initiator sends message content exactly as produced — no preamble, rewording, or added reply template.
6. **Run to complete.** The protocol runs until COMMIT is reached; no partial outcomes.
7. **Announcing is not acting.** Stating that an action will be taken is not a substitute for taking it.
