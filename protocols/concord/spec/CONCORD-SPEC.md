# CONCORD Protocol Specification

Version 0.1.0 <!-- x-release-please-version -->

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
| **Initiator** | 1 | Routes messages; relays replies to all other agents; reports the final result |
| **Participant** | N ≥ 2 | Each holds a role-specific scoring perspective; maintains its own full picture of the run; self-determines when to score, repair, or terminate |

The initiator never authors replies on behalf of participants. Participants send replies verbatim — no preamble or rewording.

The state machine is distributed. Each participant runs the same decision engine over the same relayed replies and moves as soon as its own picture says it is its turn — it scores after all seeds are relayed, and repairs when every score is in and it is the bottleneck. The initiator does not request scores or repairs; it only relays.

---

## Configuration Parameters

These are supplied once at run start and are immutable for the run.

| Parameter | Required | Default | Meaning |
|---|---|---|---|
| `agents` | yes | — | Ordered list of participant agent identifiers |
| `satisfaction_threshold` (τ) | no | 0.6 | Normalized score every agent must clear for SUCCESS, in (0, 1]; set at the genuine bar, not an estimated reachable value |
| `eps` | no | 0.05 | Minimum leximin improvement required to replace the incumbent |
| `B` | no | 10 | Maximum repair rounds before BEST_EFFORT termination |
| `schema` | yes | — | Candidate format specification broadcast to all participants |
| `context` | yes | — | Problem statement broadcast to all participants |
| `x_reject` | yes | — | Outcome description anchoring the bottom (worst) of each participant's scale |
| `x_ideal` | yes | — | Outcome description anchoring the top (best) of each participant's scale |

`x_reject` and `x_ideal` must describe outcome states ("error rate returns to 0"), not proposals or mechanisms, as an anchor that names an action biases the scoring scale.

---

## Protocol Phases

### State Machine

```
START
 └─ ANCHOR   (broadcast → all participants)
      all anchor replies received and relayed
      └─ SEED   (broadcast → all participants)
           all seed replies received and relayed
           └─ each participant scores all seeds independently (no explicit request)
                all score replies received and relayed
                each participant evaluates
                ├─ floor(x*) ≥ τ, no absent scores  →  RUN COMPLETE (SUCCESS; no commit message)
                ├─ t ≥ B                              →  RUN COMPLETE (BEST_EFFORT)
                └─ bottleneck self-identifies; sends candidate + own score in one reply
                     relayed to all participants; others score independently
                     └─ (repeat evaluate)
```

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| ANCHOR | Protocol start |
| SEED | All participants have submitted anchor replies |
| SCORE | Each participant independently scores once all seed or repair candidates have been relayed to it |
| REPAIR | Each participant's own picture shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes without an explicit request |
| COMPLETE | Each participant independently computes the outcome once its own picture holds every rating; no message is sent |

The initiator broadcasts ANCHOR and SEED. After SEED, all communication flows as relayed replies inside `<concord-delivery>` wrappers. There is no explicit score request, no explicit repair request, and no commit message: each participant determines its own obligations from the full relay stream.

**Relay mode (default):** The initiator delivers other agents' replies via numbered `<concord-delivery>` wrappers and tracks acknowledgements to detect lost messages.

**No-relay mode (`init --no-relay`):** Use only when agents already share a broadcast channel and see each other's replies directly. No delivery wrappers, no acknowledgements.

---

## Message Schemas

### Routing elements (all initiator-sent messages)

Every message the initiator sends includes the following elements, injected after the message-specific content:

```xml
<state_id>RUNID</state_id>
<participants>
  <agent>cost</agent>
  <agent>reliability</agent>
  <agent>security</agent>
</participants>
<params>
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <eps>0.05</eps>
  <budget>10</budget>
</params>
<addressed_to>
  <agent>cost</agent>   <!-- one or more; the agents this specific copy is for -->
</addressed_to>
```

`<participants>` lists the full negotiating set. `<addressed_to>` names only the recipients of this particular copy (all agents for broadcast messages; one agent for `<concord-repair>`). Participants use these to keep their picture of the run consistent.

---

### Reply attributes (all participant-sent messages)

Every reply a participant sends carries three attributes on the root element:

| Attribute | Meaning |
|---|---|
| `agent` | Sender's own id (from `--me`) |
| `state` | Run id (from `<state_id>` in the message) |
| `ack` | Delivery sequence number being acknowledged (relay mode only) |

---

### `<concord-anchor>` — Initiator → all participants

Establishes each participant's personal raw scoring scale.

```xml
<concord-anchor>
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <x_reject>outcome description anchoring the rejection end of the scale</x_reject>
  <x_ideal>outcome description anchoring the ideal end of the scale</x_ideal>
  <!-- routing elements: state_id, participants, params, addressed_to -->
  <instructions>Rate the two outcomes above on your own raw 0-100 scale …</instructions>
</concord-anchor>
```

### `<concord-anchor-reply>` — Participant → Initiator

```xml
<concord-anchor-reply agent="cost" state="RUNID" ack="1">
  <raw_reject>8.0</raw_reject>
  <raw_ideal>95.0</raw_ideal>
  <!-- optional: <note>free text</note> -->
</concord-anchor-reply>
```

`raw_reject` should be low (participant's satisfaction if the rejection outcome occurred); `raw_ideal` should be high. `raw_ideal` must strictly exceed `raw_reject`. These are satisfaction ratings of hypothetical outcomes, not measurements of current state.

---

### `<concord-propose-seed>` — Initiator → all participants

Each participant proposes one candidate independently.

```xml
<concord-propose-seed>
  <candidate_schema>format specification</candidate_schema>
  <schema_json>{"type": "string", …}</schema_json>   <!-- optional; present when a JSON Schema was given -->
  <satisfaction_threshold>0.6</satisfaction_threshold>
  <context>problem statement</context>
  <!-- routing elements: state_id, participants, params, addressed_to -->
  <instructions>Propose the single candidate that best satisfies your own role …</instructions>
</concord-propose-seed>
```

Each participant proposes from its own role's perspective, without pre-compromising for other roles. The rationale should state the facts that support the candidate and the domain knowledge that argues against the other options in the schema.

### `<concord-candidate-reply>` — Participant → Initiator

Used for both seed and repair proposals.

```xml
<concord-candidate-reply id="seed-cost" agent="cost" state="RUNID" ack="2">
  <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
  <rationale>evidence and reasoning</rationale>
</concord-candidate-reply>
```

`id` is `seed-<agent>` for seed proposals and `r<N>-<agent>` for repair proposals.

In a repair round the proposer's own score arrives as a separate `<concord-score-reply>` document in the same reply block, immediately following the candidate reply.

---

### `<concord-score-reply>` — Participant → Initiator

```xml
<concord-score-reply agent="cost" state="RUNID" ack="3">
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

Participants must rate every candidate they have been sent on the same raw 0–100 scale used at anchoring. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate.

---

### `<concord-delivery>` — Initiator → one participant (relay mode)

A self-closing header element that precedes the relay payload. The relayed reply documents follow as peer XML elements in the same message, not nested inside the delivery element.

```xml
<concord-delivery seq="3" state="RUNID" check="a1b2c3d4" />

<concord-anchor-reply agent="reliability" state="RUNID" ack="2">
  <raw_reject>12.0</raw_reject>
  <raw_ideal>91.0</raw_ideal>
</concord-anchor-reply>

<concord-score-reply agent="security" state="RUNID" ack="2">
  <score candidate_id="seed-cost">
    <raw>44.0</raw>
    <reasoning>…</reasoning>
  </score>
</concord-score-reply>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `seq` | always | Monotonically increasing per recipient |
| `state` | always | Run id |
| `check` | when payload is non-empty | 8-hex SHA-256 fingerprint of the enclosed reply documents; participant's script compares it to detect relay corruption |
| `full="1"` | nudge only | Re-sends all replies recorded so far, not just new ones |
| `awaiting="A,B"` | nudge only | Comma-separated agents still awaited |

When a delivery also carries a new request (Anchor and Seed phases), the request element appears before the delivery header.

---

### `<concord-ack>` — Participant → Initiator (relay mode)

Sent by a participant when a delivery requires no substantive reply. The initiator uses acks to confirm delivery and distinguish "nothing to say" from "message lost".

```xml
<concord-ack agent="cost" state="RUNID" seq="3"></concord-ack>
```

When a participant does have a substantive reply, the ack is appended as a second document in the same reply block, after the reply itself.

---

### `<concord-missing>` — Initiator → all other participants

Sent by the initiator when it marks an agent missing (after a retry in the Score phase). Allows each participant's own picture to reach the same outcome as the initiator's.

```xml
<concord-missing agent="reliability" candidate="seed-reliability" state="RUNID"></concord-missing>
```

A run with any MISSING entry can only end `BEST_EFFORT`.

---

### `<concord-error>` — Participant → Initiator

Sent by a participant when a delivery cannot be read intact. The initiator logs the reason and re-sends the delivery.

```xml
<concord-error agent="cost" state="RUNID" seq="3">the delivery could not be copied intact (its replies do not match its check): send it again</concord-error>
```

The initiator re-sends the delivery unchanged on receipt. Participants never author this message manually; it is produced by the participant script.

---

**Note on `<concord-commit>`:** In the broadcast model there is no commit message. Each participant computes the outcome independently from its own picture once it holds every rating, and the run ends. A `<concord-commit>` from an older initiator is still understood and settles the outcome.

---

## Scoring Model

### Raw vs Normalized

Participants always report raw scores on a personal 0–100 scale anchored at `raw_reject` and `raw_ideal`. The initiator normalizes to [0,1] using each participant's own anchors:

```
s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0)
```

Normalized scores appear in the repair task the participant's script surfaces (`your_score`, `<scores>` in the incumbent). Participants must never echo normalized values back as their own raw rating.

### Candidate Selection (SELECT)

```
feasible = { x in pool : every participant rated x AND floor(x) >= τ }
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

The participant with the lowest normalized score on `x_star` is the bottleneck. Ties are broken by the participant's position in the ordered `agents` list. The bottleneck self-identifies from its own picture of the run without an explicit request from the initiator.

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
| `SUCCESS` | `floor(x*) >= τ` AND every participant has rated `x*` AND no MISSING entries in the score matrix |
| `BEST_EFFORT` | `t >= B` (budget exhausted) OR any MISSING entry exists at the time of termination |

Each participant computes the outcome independently from its own picture. The initiator reports the result; no commit message is sent.

A score is marked MISSING for a participant/candidate pair when the participant fails to respond after a retry in the Score phase. Any MISSING entry forces BEST_EFFORT regardless of other scores.

---

## Invariants

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*`.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are rated once.** Replacing an anchor reply invalidates all prior comparisons and the run integrity.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; the initiator does not choose and sends no repair request.
5. **Messages are sent verbatim.** The initiator sends message content exactly as produced — no preamble, rewording, or added reply template.
6. **Run to complete.** The protocol runs until every participant's picture reaches a terminal state; no partial outcomes.
7. **Announcing is not acting.** Stating that an action will be taken is not a substitute for taking it.
8. **Termination is distributed.** Each participant computes the outcome independently from the same relay stream. The initiator reports the result but sends no commit message.
