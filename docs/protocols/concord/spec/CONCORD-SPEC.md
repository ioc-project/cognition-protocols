# CONCORD Protocol Specification

Version 0.1.0

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT). If a message that an agent is owed never arrives, the run ends in error (ABORTED).

## Overview

Every agent in a CONCORD run has its own criteria. The protocol looks for one outcome that every agent rates at or above a satisfaction threshold, and says so honestly when it cannot find one.

A run goes like this:

1. Any agent can start a run by sending one message, `<concord-init>`, with the problem, the two anchor outcomes and the candidate format.
2. Every participant answers with its anchors, which fix its personal scoring scale, and a seed candidate.
3. Every participant scores every candidate. The candidate whose weakest score is highest becomes the incumbent.
4. If the incumbent's weakest score is below the threshold and the budget allows, the participant that is least satisfied with it (the bottleneck) proposes a repair. Everyone scores the new candidate and the incumbent is evaluated again.
5. The run ends in one of three ways: `SUCCESS` (every agent clears the threshold), `BEST_EFFORT` (the budget of repair rounds is spent) or `ABORTED` (a message an agent was owed never arrived).
6. Each participant then sends its result to all the others and waits for theirs. Every agent checks that all of them ended the same way, and reports any difference.

There is no central decision-maker and no coordinator that the run depends on. Each participant runs the same decision engine over the same messages and reaches the same decision. The messages travel over a channel that every agent reads, or over several channels in which agents attached to more than one relay the messages (see [Transport](#transport)).

Activate CONCORD when:
- An outcome must satisfy agents whose criteria may conflict.
- A named negotiating set can be specified.
- A shared candidate schema exists.

Conflict may be confirmed, anticipated, or merely uncertain — all three cases warrant activation.

---

## Roles

All agents run the same protocol. The only thing that sets the initiator apart is that it sends the first message.

- **The initiator** is the agent that starts the run by sending `<concord-init>`. After that it is an ordinary agent. If it is listed in `<participants>`, it takes part like any other participant, with its own participant instance. If it is not listed, it does not negotiate: it only waits for the init replies and reads the participants' results to report them. Because it is the author of `<concord-init>`, it is also the only agent that can send it again (see [Timeouts and abort](#timeouts-and-abort)).
- **Participants** are the agents listed in `<participants>`, at least 2. Each holds a role-specific scoring perspective, maintains its own full picture of the run, and self-determines when to score, repair, or terminate.

The state machine is distributed. Each participant runs the same decision engine over the same messages and moves as soon as its own picture says it is its turn — it scores after all seeds have reached it, and repairs when every score is in and it is the bottleneck. Nobody requests scores or repairs. Each participant detects a gap in its own picture, asks the author of the missing message for it, and aborts if the request stays unanswered.

---

## Configuration Parameters

These are supplied once at run start and are immutable for the run. The second column gives the element that carries each one in `<concord-init>`.

| Parameter | Element in `<concord-init>` | Required | Default | Meaning |
|---|---|---|---|---|
| `agents` | `session/participants` | yes | — | Ordered list of at least 2 distinct participant agent identifiers |
| `satisfaction_threshold` (τ) | `session/params/satisfaction_threshold` | no | 0.6 | Normalized score every agent must clear for SUCCESS, in (0, 1]; set at the genuine bar, not an estimated reachable value |
| `eps` | `session/params/eps` | no | 0.05 | Minimum leximin improvement required to replace the incumbent; above 0 |
| `B` | `session/params/budget` | no | 8 | Maximum repair rounds before BEST_EFFORT termination; an integer, at least 1 |
| `timeout` | `session/params/timeout` | no | 300 | Seconds an agent waits for a message it is owed before asking for it again; above 0. Every agent uses this value |
| `retries` | `session/params/retries` | no | 3 | How many times an agent asks again before it aborts the run; an integer, at least 0 (0 aborts at the first expiry) |
| `schema` | `proposal/candidate_schema` | yes | — | Candidate schema (a JSON Schema, see [Candidate schema](#candidate-schema)) |
| `context` | `context` | yes | — | Problem statement |
| `x_reject` | `anchors/x_reject` | yes | — | Outcome description anchoring the bottom (worst) of each participant's scale |
| `x_ideal` | `anchors/x_ideal` | yes | — | Outcome description anchoring the top (best) of each participant's scale |

`x_reject` and `x_ideal` must describe outcome states ("error rate returns to 0"), not proposals or mechanisms, as an anchor that names an action biases the scoring scale.

---

## Protocol Phases

### State Machine

```
START
 └─ INIT   (the initiator sends <concord-init> to all participants)
      every participant answers with one init reply (its anchors and its seed candidate)
      all init replies received
      └─ each participant scores all seeds independently (no explicit request)
           all scores received
           each participant evaluates
           ├─ floor(x*) ≥ τ, no absent scores  →  RUN COMPLETE (SUCCESS)
           ├─ t ≥ B                              →  RUN COMPLETE (BEST_EFFORT)
           └─ bottleneck self-identifies; sends one repair message (candidate + own score)
                received by all participants; others score independently
                └─ (repeat evaluate)
```

At any point where an agent waits for a message it is owed, the run can also end `ABORTED` (see [Timeouts and abort](#timeouts-and-abort)). When a participant has computed its outcome, with any outcome, it sends its `<concord-result>`. It then runs a final round: it waits for the result of every other participant and compares each with its own. The run is over for an agent only when that round is complete.

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| INIT | Protocol start |
| SCORE | Each participant independently scores once every init reply, or the repair candidate, has reached it |
| REPAIR | Each participant's own picture shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes without an explicit request |
| COMPLETE | Each participant independently computes the outcome once its own picture holds every rating, then sends its `<concord-result>` |
| CONFIRM | Each agent has its own outcome and holds the `<concord-result>` of every participant; the run ends for it when they all match |

The initiator opens the run with `<concord-init>`. After that the only communication is the participants' own messages (`<concord-init-reply>`, `<concord-score>`, `<concord-repair>` and `<concord-result>`) and the control messages `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. Nothing requests a score or a repair: each participant determines its own obligations from the messages it has received.

### Messages owed

An agent is owed a message when its own picture shows that the state machine requires that message from another agent. The timers and the lost-message rule work on this. A message becomes owed when the step before it completes in the agent's picture, and the wait for it starts then.

| When | The agent is owed |
|---|---|
| After `<concord-init>` | An init reply from every participant |
| Once every init reply is in | A score from every participant, on every candidate in the pool |
| Once the incumbent's weakest score is below τ and `t < B` | A repair from the bottleneck (it contains the bottleneck's own score), then a score on the new candidate from every other participant |
| Once the agent has computed its own outcome | A result from every other participant |

An agent is never owed its own messages. An initiator that is not a participant is owed the init replies and the results, and the other agents are not owed anything from it. 

---

### One run at a time

An agent takes part in one run at a time. A run is active for an agent from the moment it accepts `<concord-init>` until the run ends for it, the final round included. Keeping the state of an ended run, and answering errors from it, does not count.

When an agent listed in `<participants>` receives a `<concord-init>` for a run it does not know:

| The agent is | It does |
|---|---|
| in no active run | Accepts the run and starts it |
| in an active run | Sends `<concord-error what="init" reason="busy">` to the author of the init |
| the initiator of a run that still waits for init replies | Compares the two run ids. If its own is lower, it sends `busy`. Otherwise it sends `<concord-cancel>` for its own run, naming the new one in `superseded_by`, and joins the new run |

Run ids are compared as UUID text in lower case, and the lower one continues. This settles two inits sent at about the same time: each initiator sees the other's init, and only the one with the higher id gives way. An agent that is not listed in `<participants>` ignores the init. An init for a run the agent already knows is a re-send and is not a new init: the agent answers it as described under `<concord-init>`.

A `busy` error does not end the wait of the initiator. It keeps its timer and re-sends `<concord-init>` as usual, so an agent that becomes free can still join, and the run ends `ABORTED` after `retries` if nobody does. An agent that refused a run as busy and is then released by a `<concord-cancel>` that names that run joins it at once.

This keeps every agent in at most one run, and every run ends. It does not promise that a run completes: in a race both runs can abort, and the initiators start again with new ids.

---

## Message Schemas

### Attributes of messages

Every message carries these attributes on its root element:

| Attribute | Presence | Meaning |
|---|---|---|
| `from` | always | The sender's own id. On `<concord-init>` it is the initiator's id; on a `<concord-resend>` it is the author of the message inside |
| `run_id` | always | Run id, a UUID. On `<concord-init>` it repeats `<run_id>` from the body, so that every message names its run in the same way |
| `uuid` | always | UUID of this transmission (see [Transport](#transport)) |
| `check` | `<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>`, `<concord-result>` | Integrity fingerprint of the document (see below) |

### Integrity check

`<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>` and `<concord-result>` each carry a `check` attribute: the first 8 hex characters of the SHA-256 of the document exactly as sent, in its serialized and escaped form and not a parsed one, with all whitespace removed and the `check` attribute itself left out. The agent that authors the document computes it; every agent that reads the document verifies it. A document that fails its check is not recorded. The control messages (`<concord-error>`, `<concord-resend>`, `<concord-cancel>`) carry none: a `<concord-resend>` carries a stored original whose own `check` protects it.

The check detects accidental change, such as text shortened or reworded while an agent copies it. It is not a signature: it uses no secret. Agent ids are self-declared. The protocol assumes cooperative agents and does not defend against an agent that lies about who it is.

| Failure | What happens |
|---|---|
| The text handed to an agent's own protocol implementation is wrong | The implementation records nothing and asks for the whole text again; no message is sent |
| A document arrived damaged | The agent sends `<concord-error reason="check">` to the author, who sends the message again in a `<concord-resend>` |

### Invalid messages

An author checks its own message before it sends it: a candidate must satisfy the candidate schema, and `raw_ideal` must exceed `raw_reject`. An agent that nevertheless finds a message invalid does not record it. It refuses it with `<concord-error reason="invalid">` to the author, and the text of the error says why. Nothing was recorded, so the author then sends a corrected message. This is the only case in which an author may replace a message it has already sent. Like any request, the error counts against `retries`.

---

### `<concord-init>`

Sent by the initiator to open the run. One message carries everything a participant needs: the session, the problem, the two anchor outcomes and the candidate format. Each participant answers with one `<concord-init-reply>`.

```xml
<concord-init from="alice" run_id="RUNID" uuid="1b4e28ba-2fa1-4d3c-9b0e-7a6f5e4d3c2b" check="a1b2c3d4">
  <session>
    <run_id>RUNID</run_id>
    <participants>
      <agent>cost</agent>
      <agent>reliability</agent>
      <agent>security</agent>
    </participants>
    <params>
      <satisfaction_threshold>0.6</satisfaction_threshold>
      <eps>0.05</eps>
      <budget>8</budget>
      <timeout>300</timeout>
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

`<run_id>` identifies the run, and the `run_id` attribute of the root repeats it. The initiator generates it as a UUID when it creates the run, so that it differs from the id of any other run on the same channel. `<participants>` lists the full negotiating set, in order. Participants use `<session>` to keep their picture of the run consistent; its values are immutable for the run. `<context>` is shared: it applies to both the anchors and the proposal.

The anchors establish each participant's personal raw scoring scale. Each participant proposes from its own role's perspective, without pre-compromising for other roles.

If init replies are missing when the `timeout` expires, the initiator sends `<concord-init>` again, unchanged, inside a `<concord-resend>` with no `reply_to`, up to `retries` times (see [Timeouts and abort](#timeouts-and-abort)). A participant that receives it again after answering sends its stored init reply again, in a `<concord-resend>` with no `reply_to`. A participant that has not answered yet answers now.

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

### `<concord-init-reply>`

Sent by each participant in answer to `<concord-init>`. One reply carries its anchors and its seed candidate.

```xml
<concord-init-reply from="cost" run_id="RUNID" uuid="6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51" check="e5f6a7b8">
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

### `<concord-repair>`

Sent by the bottleneck: a repair proposal, with its own score for the new candidate.

```xml
<concord-repair id="r1-cost" from="cost" run_id="RUNID" uuid="0d7a5e92-3c64-4f18-b2a9-84e1c6d7f305" check="c9d0e1f2">
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

### `<concord-score>`

Sent by each participant: its scores on the candidates it has not rated yet.

```xml
<concord-score from="cost" run_id="RUNID" uuid="9e2b7c40-1d85-4a36-8f0c-5b3a2d1e6f47" check="3a4b5c6d">
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

Every participant rates every candidate in the pool, its own included, on the same raw 0–100 scale used at anchoring. A score message covers the candidates the sender has not rated yet: once the init replies are in it holds one entry per seed, and after a repair it holds a single entry, for the new candidate. A repair message carries the proposer's own score for its new candidate. A candidate is rated once: a second score for the same candidate from the same author is ignored. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate.

---

### `<concord-result>`

Sent once by each participant as soon as it has computed its outcome, with any outcome. It is the final round of the run: every agent learns how every participant ended, so that all of them can be sure they reached the same conclusion.

```xml
<concord-result from="cost" run_id="RUNID" uuid="c5e81a3f-92d4-47b6-8e0a-1f7d3c5b9a26" check="d4e5f6a7">
  <outcome>SUCCESS</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
</concord-result>
```

`outcome` is `SUCCESS`, `BEST_EFFORT` or `ABORTED`. `winning_candidate_id` is `x*`: for `SUCCESS` the candidate that every agent rates at or above τ, and for `BEST_EFFORT` the best candidate found, which does not clear τ for everyone. It is omitted for `ABORTED`, which has no winner.

A result is owed by every participant to every other agent, and the usual timer applies (see [Messages owed](#messages-owed)). It is not a vote: a result never changes the outcome an agent has computed. A result that arrives before the agent has computed its own is recorded and compared later.

An agent compares each result it receives with its own. Two results differ when their outcomes differ, or when both outcomes are `SUCCESS` (or both `BEST_EFFORT`) and the winning candidates differ. Scores are not compared. When they differ, the agent sends `<concord-error reason="mismatch" what="result">` to the author. No re-send answers it: it only tells the author that another agent ended differently. Each agent records every mismatch in its report (see Result).

The run ends for an agent when it holds the result of every participant. If a result is still missing after `retries` requests, the agent aborts like for any other missing message.

---

### `<concord-error>`

Reports a problem with a message and asks for it to be sent again: it never arrived, it arrived damaged, or it is invalid. It is also how an agent refuses a `<concord-init>` because it is busy (see [One run at a time](#one-run-at-a-time)), and how it tells an agent that its result differs from another's, which no re-send answers. A missing, damaged or invalid result is asked for like any other message. Any agent that finds the problem sends it, the initiator included.

```xml
<concord-error run_id="RUNID" from="cost" to="security" uuid="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73" what="score" candidate="seed-cost" reason="missing">
  I am missing the score from security for seed-cost. If you are security, please send it again.
</concord-error>
```

| Attribute | Meaning |
|---|---|
| `to` | The author of the message that is wanted |
| `what` | `init-reply`, `score`, `repair` or `result`: what is wanted. `init` goes only with `reason="busy"`: the refused message is the init itself. `result` is the final round; with `reason="mismatch"` no re-send answers it |
| `candidate` | Required when `what` is `score` or `repair`: the id of the candidate whose score, or whose repair proposal, is missing, for example `r1-security`. Omitted for `init`, `init-reply` and `result` |
| `reason` | `missing` (never arrived), `check` (arrived, failed its check), `invalid` (arrived intact but breaks a rule, such as the schema; the text says why) `mismatch` (a result differs from the sender's) or `busy` (the sender is in another run and refuses this init) |

Besides the attributes above, an error carries the common attributes; its `uuid` is what the re-send names in `reply_to`. The text inside is optional and for readers only; for `reason="invalid"` it states why the message was refused. A protocol implementation produces this message; it is not written by hand.

Only the author answers an error, and every other agent ignores it. The author sends the stored original, word for word, wrapped in a `<concord-resend>` whose `reply_to` is the `uuid` of the error. Sending a message twice is harmless: a repeated rating or candidate is ignored by the decision engine.

An agent sends each message once and keeps it. It may re-send only that stored original, and never writes a new version of a message it has already sent, except one that was refused as invalid and so was never recorded. A receiver that has recorded a message ignores a different one from the same author for the same item (an init reply; a score for the same candidate; a repair with the same id): the first one recorded stands.

An agent keeps the state of a run, including the messages it sent and recorded, after the run has ended. If it receives a `<concord-error>` addressed to it for a run that is no longer active, it looks for the stored state of that run id and answers from it if it exists.

A re-send answers an error when it carries the message the error names: the same author, the same kind, and the same candidate. Its `reply_to` ends the wait of the agent that asked. An agent that already holds the message ignores the copy. A score message that covers several candidates is re-sent whole; it answers the error for each candidate it contains. A repair message also contains the proposer's own score, so it answers an error for that score too.

An agent sends `reason="missing"` when it has waited `timeout` seconds for a message it knows is owed, or when its picture has a gap that a later message reveals (see Lost messages below). It sends `reason="check"` when a document fails its check and `reason="invalid"` when one is invalid. Each request counts against `retries`.

#### Lost messages

An agent can lose a message without noticing, since nothing numbers the messages. It can still find out. The state machine says which messages are owed at each step (see [Messages owed](#messages-owed)), and a message that could only have been sent after other messages proves that those exist. An agent that receives such a message while lacking the earlier ones asks for them at once with `<concord-error reason="missing">`, without waiting for its timer.

For example, everyone sends a `<concord-score>` for a candidate, and participant P does not receive the one from X. Meanwhile participant B, whose picture is complete, finds it is the bottleneck and sends `<concord-repair id="r1-B">`. A repair in round N is sent only once every participant's score for every candidate before round N is in, so when P receives it, P knows its own picture is incomplete. P compares its picture with what the repair presupposes and asks the author of each message it lacks, here X:

```xml
<concord-error run_id="RUNID" from="P" to="X" uuid="7b2d5e91-4c08-4a63-9f1e-d3a6b8c07e25" what="score" candidate="seed-A" reason="missing"/>
```

P keeps processing what it has. It can score the new candidate, since that needs only the candidate text, but it decides nothing until its picture is complete.

The same holds the other way round: a `<concord-score>` that rates a candidate P never received shows that the proposal was lost, and P asks its author: `what="init-reply"` for a seed, which travels in the author's init reply, or `what="repair"` with the `candidate` id for a repair.

A request like this starts the timer for that message, as any other request does, and counts against `retries`.

---

### `<concord-resend>`

Sent by the author of a stored message to carry it again, word for word. It answers a `<concord-error>`, or it is sent without one: the initiator sends `<concord-init>` this way when init replies are missing, and a participant sends its stored init reply this way when it receives init again.

```xml
<concord-resend run_id="RUNID" from="security" uuid="f4d92b61-0a37-4c8e-9d15-6b2e7a3c8f04" reply_to="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73">
  <concord-score from="security" run_id="RUNID" uuid="51d0a7e3-96b2-4f84-a0c7-2e9d1b6f3a58" check="1b2c3d4e">
    <score candidate_id="seed-cost">
      <raw>44.0</raw>
      <reasoning>…</reasoning>
    </score>
  </concord-score>
</concord-resend>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `reply_to` | when it answers an error | The `uuid` of that error |

Besides `reply_to`, the wrapper carries the common attributes, with its own `uuid`, new for every re-send. The message inside is the stored original, untouched: it keeps its own `uuid` and `check`. A receiver unwraps it, verifies its `check`, and records it if it holds no message for that item; otherwise it ignores it. An agent that relays treats the wrapper like any other transmission, by its own `uuid`.

---

### `<concord-cancel>`

Sent by the initiator to end its own run before it has started: another run for the same participants has a lower run id (see [One run at a time](#one-run-at-a-time)).

```xml
<concord-cancel from="alice" run_id="RUNID" uuid="8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36" superseded_by="OTHER-RUNID"/>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `superseded_by` | always | The run id of the run that continues |

A participant that receives it ends the run as `ABORTED`, with the reason "cancelled", and sends no result: a cancelled run is not confirmed. A `<concord-cancel>` from any agent other than the author of the run's `<concord-init>` is ignored. It carries no `check`. If it is lost, the participants end the run on their timers, since the initiator sends nothing more.

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
| `ABORTED` | An agent still lacks a message it is owed, a result included, after `retries` requests, each after a `timeout` (see below) |

Each participant computes its outcome independently from its own picture, sends it in its `<concord-result>`, and then collects the results of the others. A reader that is not a participant learns how the run ended from those messages. A run is confirmed when every agent holds the same outcome and winner from every participant; a difference is reported, not resolved.

### Result

The report of a run is not a message: nothing is sent. It is complete once the final round is. It contains:

| Field | Content |
|---|---|
| outcome | `SUCCESS`, `BEST_EFFORT` or `ABORTED` |
| winning candidate | The id and text of `x*`. None for `ABORTED` |
| final scores | Each participant's normalized score on `x*` |
| shortfall | Each participant below τ, and by how much; empty for `SUCCESS` |
| rounds | The number of repair rounds used |
| reason | For `ABORTED` only: the agent and the message that was missing |
| other results | The results received from other agents, and whether each matches this one (see `<concord-result>`). `confirmed` when all of them match |

### Timeouts and abort

Every time an agent is owed a message (see [Messages owed](#messages-owed)) it starts a timer of `timeout` seconds, counted on its own clock. A message that arrives ends the wait.

- **Any agent.** When the timer expires, the agent sends `<concord-error reason="missing">` to the author and restarts the timer. When it expires after `retries` requests, the agent aborts: its run ends with outcome `ABORTED`, and a participant sends its `<concord-result>` (with outcome `ABORTED`, so that the others see it).
- **The initiator, for init replies.** An agent that never received `<concord-init>` does not know the run and cannot answer an error. So when the timer for an init reply expires, the initiator sends `<concord-init>` again, inside a `<concord-resend>`, instead of an error, and restarts the timer. When it expires after `retries` re-sends, the initiator aborts and reports `ABORTED`. For every other message the initiator follows the rule above, like any other agent.

An agent waits at most `(retries + 1) × timeout` seconds for one message. `timeout` must be longer than the slowest turn of any agent. Relaying adds only network delay, which is much shorter.

`ABORTED` is an error, not a result: no candidate is certified. The report (see Result) names the agent and the message that was missing, and has no winner. No message announces the abort: every other agent reaches the same conclusion with its own timer, or stops earlier because the run has ended. A data message that arrives after an abort is ignored, but the agent still answers errors for its own messages from its stored state.

A timer is a running process: an implementation must keep one running while the agent waits.

---

## Transport

CONCORD does not depend on a particular network. The messages are application messages, so the network of a run is made only of the agents that take part in it. An agent is attached to one or more channels. A channel is anything that carries a message from one agent to the others attached to it: a shared channel that every member reads, or a link between two agents. Every message an agent sends reaches every other agent of the run, directly or through the agents in between, for example in a line or a star.

Every message and every `<concord-resend>` carries a `uuid` attribute: a UUID that identifies that one transmission. A new transmission always gets a new `uuid`, a re-send included: the `<concord-resend>` that carries a stored message has its own.

An agent that is attached to more than one channel relays. When a transmission arrives on a channel, it sends it on every other channel it is attached to, unless it has already relayed that `uuid`. The protocol does not discover channels and no message describes them. An agent knows its channels from its configuration, the list of channels its deployment attaches it to, which exists before any run starts. This list is local: an agent knows only its own channels, not what is on the other side, who else is attached, or how many agents the run has. The rule needs no more than that. An agent attached to one channel, such as every member of a shared channel, has no other channel to relay to, so nothing is relayed. The centre of a star relays every message to all its spokes, and the middle agent of a line relays between its two sides.

Relaying is mechanical. It looks only at the `uuid` and the `run_id` of the outermost element, never changes the message, and does not wait for the model: an agent that is slow or busy still relays at once, or the agents behind it would time out. An agent relays only the messages of a run it has state for, plus `<concord-init>` and a `<concord-resend>` that carries it, since the agents behind it learn the run only from that message. It keeps the `uuids` it has relayed for at least `(retries + 1) × timeout` seconds. A receiver that gets the same message twice ignores the second copy, so a copy that arrives by two paths is harmless.

An agent ignores any message of a run it is not part of, and any message whose run id is not one it has state for. Several runs can therefore share a channel.

---

## Invariants

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*`, and a run is not confirmed until every participant's result is in.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are rated once.** An agent's anchors are the ones in its first init reply. A later, different init reply is ignored, since replacing anchors would invalidate every earlier comparison.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; nobody chooses it and nobody requests a repair. A repair from any other agent for that round is ignored.
5. **Messages are sent verbatim.** Every agent sends message content exactly as produced — no preamble, rewording, or added template.
6. **Run to complete.** Once a run has started it ends, for each agent, with one of the outcomes (`SUCCESS`, `BEST_EFFORT` or `ABORTED`); it never ends without one.
7. **Announcing is not acting.** Stating that an action will be taken is not a substitute for taking it.
8. **Termination is distributed.** Each participant computes its outcome independently from the same messages. Every participant's result reaches every agent, and a difference is reported, never negotiated.
9. **A damaged document is never used.** A document that fails its `check` is not recorded; it is asked for again.
10. **Silence ends the run.** An agent never continues on a partial picture: if a message it is owed has not arrived after `retries` requests, it aborts.
11. **One message per item.** An agent sends each message once and may re-send only the stored original, and only its author does. A different message from the same author for an item already recorded is ignored. The exception is a message refused as invalid, which its author may replace.
12. **One uuid per transmission.** Every send, a re-send included, has a new `uuid`, and an agent relays a `uuid` at most once.
13. **One run at a time.** An agent is in at most one active run. It refuses a second init as busy, and of two runs started together the one with the lower run id continues.
