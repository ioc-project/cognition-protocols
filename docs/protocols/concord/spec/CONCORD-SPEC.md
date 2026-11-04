# CONCORD Protocol Specification

Version 0.1.0

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT). If a message that an agent is owed never arrives, the run ends in error (ABORTED).

## Overview

Every agent in a CONCORD run has its own criteria. The protocol looks for one outcome that every agent rates at or above a satisfaction threshold, and says so honestly when it cannot find one.

A run goes like this:

1. The initiator sends one message, `<concord-init>`, with the problem, the two anchor outcomes and the candidate format.
2. Every participant answers with its anchors, which fix its personal scoring scale, and a seed candidate.
3. Every participant scores every candidate. The candidate whose weakest score is highest becomes the incumbent.
4. If the incumbent's weakest score is below the threshold and the budget allows, the participant that is least satisfied with it (the bottleneck) proposes a repair. Everyone scores the new candidate and the incumbent is evaluated again.
5. The run ends in one of three ways: `SUCCESS` (every agent clears the threshold), `BEST_EFFORT` (the budget of repair rounds is spent) or `ABORTED` (a message an agent was owed never arrived).

There is no central decision-maker. Each participant runs the same decision engine over the same messages and reaches the same decision. The messages travel either through the initiator (relay mode) or on a channel that every agent reads (broadcast mode).

Activate CONCORD when:
- An outcome must satisfy agents whose criteria may conflict.
- A named negotiating set can be specified.
- A shared candidate schema exists.

Conflict may be confirmed, anticipated, or merely uncertain — all three cases warrant activation.

---

## Roles

| Role | Count | Responsibilities |
|---|---|---|
| **Initiator** | 1 | Sends `<concord-init>`. In relay mode it also relays replies to all other agents, nudges agents that owe one, and reports the final result. In broadcast mode it re-sends `<concord-init>` until every init reply has arrived, then does nothing more |
| **Participant** | N ≥ 2 | Each holds a role-specific scoring perspective; maintains its own full picture of the run; self-determines when to score, repair, or terminate |

The initiator never authors replies on behalf of participants. Participants send replies verbatim — no preamble or rewording.

The state machine is distributed. Each participant runs the same decision engine over the same replies, whether the initiator relays them (relay mode) or the participant reads them directly from a shared channel (broadcast mode), and moves as soon as its own picture says it is its turn — it scores after all seeds have reached it, and repairs when every score is in and it is the bottleneck. The initiator does not request scores or repairs. After `<concord-init>`, in relay mode, it only relays replies and nudges agents that owe one. In broadcast mode it listens only for init replies and sends `<concord-init>` again until all of them have arrived; after that it takes no further part: each participant detects a gap in its own picture and asks for the reply itself, and aborts if it stays unanswered (see [Timeouts and abort](#timeouts-and-abort)). An initiator that also wants to negotiate is a participant like the others, with its own participant instance.

---

## Configuration Parameters

These are supplied once at run start and are immutable for the run. The second column gives the element that carries each one in `<concord-init>`.

| Parameter | Element in `<concord-init>` | Required | Default | Meaning |
|---|---|---|---|---|
| `agents` | `session/participants` | yes | — | Ordered list of at least 2 distinct participant agent identifiers |
| `satisfaction_threshold` (τ) | `session/params/satisfaction_threshold` | no | 0.6 | Normalized score every agent must clear for SUCCESS, in (0, 1]; set at the genuine bar, not an estimated reachable value |
| `eps` | `session/params/eps` | no | 0.05 | Minimum leximin improvement required to replace the incumbent; above 0 |
| `B` | `session/params/budget` | no | 8 | Maximum repair rounds before BEST_EFFORT termination; an integer, at least 1 |
| `timeout` | `session/params/timeout`, broadcast mode only | no | 300 | Seconds an agent waits for a message it is owed before asking for it again; above 0 |
| `retries` | `session/params/retries`, broadcast mode only | no | 3 | How many times an agent asks again before it aborts the run; an integer, at least 0 (0 aborts at the first expiry) |
| `schema` | `proposal/candidate_schema` | yes | — | Candidate schema (a JSON Schema, see [Candidate schema](#candidate-schema)) |
| `context` | `context` | yes | — | Problem statement |
| `x_reject` | `anchors/x_reject` | yes | — | Outcome description anchoring the bottom (worst) of each participant's scale |
| `x_ideal` | `anchors/x_ideal` | yes | — | Outcome description anchoring the top (best) of each participant's scale |

`timeout` and `retries` can be set for a run in either mode. In broadcast mode the initiator sends them to all agents in `<concord-init>`, both together, so every waiting agent uses the same values, and their presence there is how a participant learns the run is in broadcast mode. In relay mode only the initiator waits, so it uses them itself and sends neither: a run whose `<concord-init>` does not carry them is a relay-mode run (see [Relay and broadcast mode](#relay-and-broadcast-mode)).

`x_reject` and `x_ideal` must describe outcome states ("error rate returns to 0"), not proposals or mechanisms, as an anchor that names an action biases the scoring scale.

---

## Protocol Phases

### State Machine

```
START
 └─ INIT   (sent to all participants)
      every participant answers with one init reply (its anchors and its seed candidate)
      all init replies received (relayed, in relay mode)
      └─ each participant scores all seeds independently (no explicit request)
           all score replies received (relayed, in relay mode)
           each participant evaluates
           ├─ floor(x*) ≥ τ, no absent scores  →  RUN COMPLETE (SUCCESS)
           ├─ t ≥ B                              →  RUN COMPLETE (BEST_EFFORT)
           └─ bottleneck self-identifies; sends one repair message (candidate + own score)
                received by all participants; others score independently
                └─ (repeat evaluate)
```

At any point where a participant waits for a message it is owed, the run can also end `ABORTED` (see [Timeouts and abort](#timeouts-and-abort)).

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| INIT | Protocol start |
| SCORE | Each participant independently scores once every init reply, or the repair candidate, has reached it |
| REPAIR | Each participant's own picture shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes without an explicit request |
| COMPLETE | Each participant independently computes the outcome once its own picture holds every rating; no message is sent |

The initiator opens the run with `<concord-init>`, and sends it again, unchanged, only to trigger init replies that are missing. All other communication is replies: relayed inside `<concord-delivery>` wrappers (relay mode) or read directly from the channel (broadcast mode). There is no explicit score request and no explicit repair request: each participant determines its own obligations from the replies it has received.

### Relay and broadcast mode

A participant learns the mode from `<params>` in `<concord-init>`: `timeout` and `retries` present means broadcast mode, absent means relay mode.

**Relay mode (default):** The initiator delivers other agents' replies via numbered `<concord-delivery>` wrappers and tracks acknowledgements to detect lost messages. The initiator sends `<concord-init>` to each participant as delivery 1, preceded by `<concord-delivery seq="1" state="RUNID" />` (no `check` on that header, since init carries its own), so replies to it carry `ack="1"`. The initiator's timer (see [Timeouts and abort](#timeouts-and-abort)) covers a reply that never arrives; participants run no timers.

**Broadcast mode:** Use only when agents already share a broadcast channel and see each other's replies directly. The initiator sends `<concord-init>` and listens for the init replies. If some are still missing after `timeout` seconds it sends `<concord-init>` again, unchanged, up to `retries` times. There are no delivery wrappers and no acknowledgements, and once every init reply has arrived the initiator takes no further part in the run. Each participant that finds a reply missing or damaged sends `<concord-error>` to the agent that must send it again. A participant whose requests stay unanswered aborts the run (see [Timeouts and abort](#timeouts-and-abort)).

### Messages owed

An agent is owed a message when its own picture shows that the state machine requires that message from another agent. The timers and the lost-message rule work on this. A message becomes owed when the step before it completes in the agent's picture, and the wait for it starts then.

| When | The agent is owed |
|---|---|
| After `<concord-init>` | An init reply from every participant |
| Once every init reply is in | A score from every participant, on every candidate in the pool |
| Once the incumbent's weakest score is below τ and `t < B` | A repair from the bottleneck (it contains the bottleneck's own score), then a score on the new candidate from every other participant |

An agent is never owed its own messages. In relay mode the initiator is the agent that is owed these messages. In broadcast mode every participant is, and the initiator is owed only the init replies.

---

## Message Schemas

### Attributes of participant messages

In relay mode a participant sends its replies to the initiator, who relays them. In broadcast mode it posts them to the channel, where every other participant reads them directly.

Every content message a participant sends (`<concord-init-reply>`, `<concord-repair>`, `<concord-score>`) carries these attributes on the root element. The control messages (`<concord-ack>`, `<concord-error>`) have their own, described below:

| Attribute | Presence | Meaning |
|---|---|---|
| `agent` | always | Sender's own id |
| `state` | always | Run id (from `<state_id>` in `<concord-init>`) |
| `ack` | relay mode only | The highest delivery number such that every delivery from 1 up to it has been received. A delivery that is missing therefore holds the number back. `<concord-init>` is delivery 1, so a reply to it carries `ack="1"` |
| `check` | always | Integrity fingerprint of the document (see below) |

An agent ignores any message whose run id (`state`, or `<state_id>` in `<concord-init>`) is not that of its own run, so several runs can share a channel.

### Integrity check

`<concord-init>`, `<concord-init-reply>`, `<concord-repair>` and `<concord-score>` each carry a `check` attribute: the first 8 hex characters of the SHA-256 of the document exactly as sent, in its serialized and escaped form and not a parsed one, with all whitespace removed and the `check` attribute itself left out. The agent that authors the document computes it; every agent that reads the document verifies it, in both modes. A document that fails its check is not recorded. The control messages (`<concord-ack>`, `<concord-error>`) are a few attributes and carry none.

The check detects accidental change, such as text shortened or reworded while an agent copies it. It is not a signature: it uses no secret. Agent ids are self-declared. The protocol assumes cooperative agents and does not defend against an agent that lies about who it is.

| Failure | What happens |
|---|---|
| The text handed to an agent's own protocol implementation is wrong | The implementation records nothing and asks for the whole text again; no message is sent |
| The document arrived damaged (relay mode) | The participant sends `<concord-error reason="check">` to the initiator, who re-sends the delivery |
| The document arrived damaged (broadcast mode) | The participant sends `<concord-error reason="check">` to the author, who sends the reply again |

### Invalid messages

An author checks its own message before it sends it: a candidate must satisfy the candidate schema, and `raw_ideal` must exceed `raw_reject`. A receiver that nevertheless finds a message invalid does not record it. It refuses it with `<concord-error reason="invalid">` to the author, and the text of the error says why. In relay mode the initiator sends this error, since it is the one that receives the participants' messages; in broadcast mode any participant that finds the message invalid does. Nothing was recorded, so the author then sends a corrected message. This is the only case in which an author may replace a message it has already sent. Like any request, the error counts against `retries`.

---

### `<concord-init>` — Initiator → all participants

Opens the run. One message carries everything a participant needs: the session, the problem, the two anchor outcomes and the candidate format. Each participant answers with one `<concord-init-reply>`.

```xml
<concord-init check="a1b2c3d4">
  <session>
    <state_id>RUNID</state_id>
    <participants>
      <agent>cost</agent>
      <agent>reliability</agent>
      <agent>security</agent>
    </participants>
    <params>
      <satisfaction_threshold>0.6</satisfaction_threshold>
      <eps>0.05</eps>
      <budget>8</budget>
      <timeout>300</timeout>   <!-- timeout and retries: broadcast mode only -->
      <retries>3</retries>
    </params>
  </session>
  <context>problem statement</context>
  <anchors>
    <x_reject>outcome description anchoring the rejection end of the scale</x_reject>
    <x_ideal>outcome description anchoring the ideal end of the scale</x_ideal>
  </anchors>
  <proposal>
    <candidate_schema>{"type": "string", "description": "format specification"}</candidate_schema>
  </proposal>
  <instructions>Rate the two anchor outcomes on your own raw 0-100 scale, then propose the single candidate that best satisfies your own role …</instructions>
</concord-init>
```

`<state_id>` identifies the run. The initiator generates it at random when it creates the run (for example 8 hex characters), so that it differs from the id of any other run on the same channel. `<participants>` lists the full negotiating set, in order. Participants use `<session>` to keep their picture of the run consistent; its values are immutable for the run. `<context>` is shared: it applies to both the anchors and the proposal.

The anchors establish each participant's personal raw scoring scale. Each participant proposes from its own role's perspective, without pre-compromising for other roles.

The initiator may send `<concord-init>` again, unchanged, to trigger init replies that are missing (see [Timeouts and abort](#timeouts-and-abort)). A participant that receives it again after answering sends its stored init reply again, word for word. A participant that has not answered yet answers now.

#### Candidate schema

`<candidate_schema>` holds a JSON Schema, as JSON text, that says what one candidate may look like. It is required, and it is the only description of the format: each receiver derives from it the wording it shows its agent. Receivers enforce it on every seed and repair proposal and refuse one that does not satisfy it (see Invalid messages). Two forms cover most runs:

- **Open format** (the default). The description tells the agents what one candidate is. Nothing else is enforced, and agents may propose combinations.

  ```json
  {"type": "string", "description": "a remediation plan: the actions to take, each with its service and version"}
  ```

- **Closed list.** Every proposal, seeds and repairs alike, must be exactly one of the listed options (a repair may re-propose one already in the run). Added wording is refused, so detail goes in the rationale. Use it only when the options were handed to the initiator, and copy each exactly as given: a list the initiator invents shuts out better answers that nobody listed.

  ```json
  {"enum": ["option A", "option B", "option C"]}
  ```

A schema needs a `description` or an `enum`. The supported keywords are `type` (only `"string"`), `enum`, `description`, `title`, `pattern` (a regular expression the candidate must match), `minLength` and `maxLength`. Any other keyword is refused when the run is created, so a rule is never silently left unenforced. The JSON is ordinary XML text: `<`, `>` and `&` inside it are escaped.

---

### `<concord-init-reply>` — Participant → Initiator (relay mode) or all participants (broadcast mode)

One reply carries the participant's anchors and its seed candidate.

```xml
<concord-init-reply agent="cost" state="RUNID" ack="1" check="e5f6a7b8">
  <anchors>
    <raw_reject>8.0</raw_reject>
    <raw_ideal>95.0</raw_ideal>
  </anchors>
  <proposal id="seed-cost">
    <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
    <rationale>evidence and reasoning</rationale>
  </proposal>
</concord-init-reply>
```

`raw_reject` should be low (participant's satisfaction if the rejection outcome occurred); `raw_ideal` should be high. `raw_ideal` must strictly exceed `raw_reject`. These are satisfaction ratings of hypothetical outcomes, not measurements of current state.

`id` is `seed-<agent>`. The candidate must satisfy the candidate schema. The rationale should state the facts that support the candidate and the domain knowledge that argues against the other options in the schema.

The reply is recorded as a whole or not at all: if either part is invalid, nothing is recorded (see Invalid messages).

---

### `<concord-repair>` — Participant → Initiator (relay mode) or all participants (broadcast mode)

The bottleneck's repair proposal, with its own score for the new candidate.

```xml
<concord-repair id="r1-cost" agent="cost" state="RUNID" ack="3" check="c9d0e1f2">
  <candidate>strategy: local-memory, ttl_seconds: 600</candidate>
  <rationale>evidence and reasoning</rationale>
  <score candidate_id="r1-cost">
    <raw>82.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
</concord-repair>
```

`id` is `r<N>-<agent>`. The `<score>` element has the same form as in `<concord-score>`, so a repair and its first score cannot be separated. The other participants score the new candidate in their own `<concord-score>` messages.

---

### `<concord-score>` — Participant → Initiator (relay mode) or all participants (broadcast mode)

```xml
<concord-score agent="cost" state="RUNID" ack="3" check="3a4b5c6d">
  <score candidate_id="seed-cost">
    <raw>82.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
  <score candidate_id="seed-reliability">
    <raw>41.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
</concord-score>
```

Every participant rates every candidate in the pool, its own included, on the same raw 0–100 scale used at anchoring. A repair message carries the proposer's own score for its new candidate. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate.

---

### `<concord-delivery>` — Initiator → one participant (relay mode)

A self-closing header element that precedes the relay payload. The relayed reply documents follow as peer XML elements in the same message, not nested inside the delivery element. Everything the initiator sends a participant in relay mode is a delivery: `<concord-init>`, relayed replies, and a `<concord-error>` for an invalid message, which follows the header like any other payload, so it has a `seq` and is acknowledged.

```xml
<concord-delivery seq="3" state="RUNID" check="a1b2c3d4" />

<concord-init-reply agent="reliability" state="RUNID" ack="1" check="7e8f9a0b">
  <anchors>
    <raw_reject>12.0</raw_reject>
    <raw_ideal>91.0</raw_ideal>
  </anchors>
  <proposal id="seed-reliability">
    <candidate>…</candidate>
    <rationale>…</rationale>
  </proposal>
</concord-init-reply>

<concord-score agent="security" state="RUNID" ack="2" check="1b2c3d4e">
  <score candidate_id="seed-cost">
    <raw>44.0</raw>
    <reasoning>…</reasoning>
  </score>
</concord-score>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `seq` | always | Monotonically increasing per recipient. `<concord-init>` is delivery 1, so the first relayed delivery is `seq="2"` |
| `state` | always | Run id |
| `check` | when payload is non-empty | 8-hex SHA-256 fingerprint of the enclosed reply documents as a whole; the participant compares it to detect relay corruption, including a message left out of the delivery (each message in it also carries its own `check`) |
| `full="1"` | nudge only | Re-sends all replies recorded so far, not just new ones |
| `awaiting="A,B"` | nudge only | Comma-separated agents still awaited |

---

### `<concord-ack>` — Participant → Initiator (relay mode)

Sent by a participant when a delivery requires no substantive reply. The initiator uses acks to confirm delivery and distinguish "nothing to say" from "message lost".

```xml
<concord-ack agent="cost" state="RUNID" seq="3"></concord-ack>
```

`seq` has the same meaning as `ack` in the other messages: every delivery up to `seq` has been received.

---

### `<concord-error>` — Participant (or the initiator, for an invalid message in relay mode) → one agent

Reports a problem with a reply or a delivery and asks for it to be sent again: it never arrived, it arrived damaged, or it is invalid. The same message is used in both modes. Only the addressee differs: in relay mode a participant talks only to the initiator, and in broadcast mode it reads the author's reply directly.

```xml
<concord-error state="RUNID" from="cost" to="security" what="score" candidate="seed-cost" reason="missing">
  I am missing the score from security for seed-cost. If you are security, please send it again.
</concord-error>

<concord-error state="RUNID" from="cost" to="initiator" what="delivery" seq="3" reason="check">
  delivery 3 could not be copied intact (its replies do not match its check): send it again
</concord-error>
```

| Attribute | Meaning |
|---|---|
| `state` | Run id |
| `from` | The sender's agent id, or `initiator` |
| `to` | The agent that must send again: the author of the reply, or `initiator` when `<concord-init>` or a delivery is wanted |
| `what` | `init`, `score`, `repair` or `delivery`: what is wanted. `init` is `<concord-init>` when `to` is `initiator`, and the addressed participant's `<concord-init-reply>` otherwise |
| `candidate` | Required when `what` is `score` or `repair`: the id of the candidate whose score, or whose repair proposal, is missing, for example `r1-security`. Omitted for `init` and `delivery` |
| `seq` | With `what="delivery"`: the delivery number wanted |
| `reason` | `missing` (never arrived, or a gap in the delivery numbers), `check` (arrived, failed its check) or `invalid` (arrived intact but breaks a rule, such as the schema; the text says why) |

The text inside is optional and for readers only; for `reason="invalid"` it states why the message was refused. The participant's protocol implementation produces this message; it is not written by hand.

The addressed agent sends the stored document again, word for word, with the same `check`. In relay mode that is the initiator re-sending the delivery unchanged; in broadcast mode it is the author re-sending its reply. Every other agent ignores the message. Sending a document twice is harmless: a repeated rating or candidate is ignored by the decision engine.

An agent sends each message once and keeps it. It may re-send only that stored original, and never writes a new version of a message it has already sent, except one that was refused as invalid and so was never recorded. A receiver that has recorded a message ignores a different one from the same author for the same item (an init reply; a score for the same candidate; a repair with the same id): the first one recorded stands.

A re-sent document answers an error when it is the message the error names: the same author, the same kind, and the same candidate (or the same delivery number). It ends the wait of every agent that asked. An agent that already holds the document ignores the copy. A score message that covers several candidates is re-sent whole; it answers the error for each candidate it contains. A repair message also contains the proposer's own score, so it answers an error for that score too.

In relay mode a participant sends it to the initiator when a delivery fails its check or the delivery numbers show a gap. The initiator sends it to a participant only when a message is invalid.

In broadcast mode the participant detects the problem itself and sends the message. It sends `reason="missing"` when it has waited `timeout` seconds for a reply it knows is owed, or when its picture has a gap that a later message reveals (see [Lost messages](#lost-messages-broadcast-mode)). It sends `reason="check"` when a document fails its check and `reason="invalid"` when one is invalid. Each request counts against `retries`.

#### Lost messages (broadcast mode)

In broadcast mode there are no delivery numbers and no acknowledgements, so an agent can lose a message without noticing. It can still find out. The state machine says which messages are owed at each step, and a message that could only have been sent after other messages proves that those exist. A participant that receives such a message while lacking the earlier ones asks for them at once with `<concord-error reason="missing">`, without waiting for its timer.

For example, everyone sends a `<concord-score>` for a candidate, and participant P does not receive the one from X. Meanwhile participant B, whose picture is complete, finds it is the bottleneck and sends `<concord-repair id="r1-B">`. A repair in round N is sent only once every participant's score for every candidate before round N is in, so when P receives it, P knows its own picture is incomplete. P compares its picture with what the repair presupposes and asks the author of each message it lacks, here X:

```xml
<concord-error state="RUNID" from="P" to="X" what="score" candidate="seed-A" reason="missing"/>
```

P keeps processing what it has. It can score the new candidate, since that needs only the candidate text, but it decides nothing until its picture is complete.

The same holds the other way round: a `<concord-score>` that rates a candidate P never received shows that the proposal was lost, and P asks its author: `what="init"` for a seed, which travels in the author's init reply, or `what="repair"` with the `candidate` id for a repair.

A request like this starts the timer for that message, as any other request does, and counts against `retries`.

---

## Scoring Model

### Raw vs Normalized

Participants always report raw scores on a personal 0–100 scale anchored at `raw_reject` and `raw_ideal`. Each participant normalizes to [0,1] using the anchors in the init replies it holds, so all participants derive the same normalized scores:

```
s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0)
```

Only raw scores travel in messages. Participants must never send a normalized value as their own raw rating.

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

The participant with the lowest normalized score on `x_star` is the bottleneck. Ties are broken by the participant's position in `<participants>` in `<concord-init>`. The bottleneck self-identifies from its own picture of the run without an explicit request from the initiator.

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
| `SUCCESS` | `floor(x*) >= τ` AND every participant has rated `x*` |
| `BEST_EFFORT` | `t >= B` (budget exhausted) |
| `ABORTED` | The initiator (relay mode, or broadcast mode for init replies) or a participant (broadcast mode) still lacks a message it is owed after `retries` requests, each after a `timeout` (see below) |

Each participant computes the outcome independently from its own picture. In relay mode the initiator reports the result; in broadcast mode the participants do, and the initiator reports an abort caused by missing init replies.

### Result

The report of a run is not a protocol message: nothing is sent. It contains:

| Field | Content |
|---|---|
| outcome | `SUCCESS`, `BEST_EFFORT` or `ABORTED` |
| winning candidate | The id and text of `x*`. For `ABORTED`, the best candidate so far if there is one, marked not certified |
| final scores | Each participant's normalized score on `x*` |
| shortfall | Each participant below τ, and by how much; empty for `SUCCESS` |
| rounds | The number of repair rounds used |
| reason | For `ABORTED` only: the agent and the message that was missing |

### Timeouts and abort

`timeout` and `retries` are set for the run, with defaults of 300 and 3. In broadcast mode they are sent to all agents in `<params>` in `<concord-init>`, so every waiting agent uses the same values. In relay mode only the initiator waits, so it uses them and does not send them. Each wait is counted on the waiting agent's own clock.

**Relay mode.** The initiator's timer is enough: it is the only agent that waits for replies, and participants only react to the deliveries they receive. When the initiator is owed a reply that has not arrived after `timeout` seconds, it nudges the agents that owe one (the current delivery again) and restarts the timer. When the timer expires after `retries` nudges, the initiator aborts: the run ends with outcome `ABORTED` and the initiator reports it. Participants run no timers; they simply stop being sent anything. A participant that sees a damaged delivery or a gap in the delivery numbers sends `<concord-error>` to the initiator at once. That is not a timer.

**Broadcast mode.** The initiator runs a timer for init replies only: when some are missing after `timeout` seconds it sends `<concord-init>` again, and when the timer expires after `retries` re-sends it aborts and reports `ABORTED`. After that, every participant runs the timer, since nobody else waits for it. An agent that is owed a message and does not have it starts a timer of `timeout` seconds. When it expires the agent sends `<concord-error>` to the author and restarts the timer. When the timer expires after `retries` requests, the agent aborts: its run ends with outcome `ABORTED`. A message that arrives ends the wait. No message announces the abort: every other agent reaches the same conclusion with its own timer, or stops earlier because the run has ended.

An agent waits at most `(retries + 1) × timeout` seconds for one message. `ABORTED` is an error, not a result: no candidate is certified. The report (see Result) names the agent and the message that was missing, and gives the best candidate so far, marked not certified. A message that arrives after an abort is ignored.

---

## Invariants

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*`.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are rated once.** An agent's anchors are the ones in its first init reply. A later, different init reply is ignored, since replacing anchors would invalidate every earlier comparison.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; the initiator does not choose and sends no repair request. A repair from any other agent for that round is ignored.
5. **Messages are sent verbatim.** The initiator and the participants send message content exactly as produced — no preamble, rewording, or added reply template.
6. **Run to complete.** Once a run has started it ends with one of the outcomes (`SUCCESS`, `BEST_EFFORT` or `ABORTED`); it never ends without one.
7. **Announcing is not acting.** Stating that an action will be taken is not a substitute for taking it.
8. **Termination is distributed.** Each participant computes the outcome independently from the same replies, relayed or read from the channel. The result is reported by the initiator in relay mode and by the participants in broadcast mode (the initiator also reports an abort caused by missing init replies).
9. **A damaged document is never used.** A document that fails its `check` is not recorded; it is asked for again.
10. **Silence ends the run.** An agent never continues on a partial picture: if a message it is owed has not arrived after `retries` requests, it aborts.
11. **One message per item.** An agent sends each message once and may re-send only the stored original. A different message from the same author for an item already recorded is ignored. The exception is a message refused as invalid, which its author may replace.
