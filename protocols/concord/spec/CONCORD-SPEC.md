# CONCORD Protocol Specification

Version 0.1.0 <!-- x-release-please-version -->

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT). If a message that an agent is owed never arrives, the run ends in error (ABORTED).

## Overview

Every agent in a CONCORD run has its own criteria. The protocol looks for one outcome that every agent rates at or above a satisfaction threshold, and says so honestly when it cannot find one.

A run goes like this:

1. Any agent can start a run by sending one message, `<concord-init>`, that describes the problem and sets up the run.
2. Every participant answers with its anchors, which fix its personal scoring scale, and a seed candidate.
3. Every participant scores every candidate. The candidate whose weakest score is highest becomes the incumbent. A later candidate replaces it only if it is clearly better, or if it clears the threshold where the incumbent does not.
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

- **The initiator** is the agent that starts the run by sending `<concord-init>`. After that it is an ordinary agent. If it is listed in `<participants>`, it takes part like any other participant. If it is not listed, it does not negotiate. It keeps only one duty: sending init again when init replies are missing (see [Timeouts and abort](#timeouts-and-abort)). It also receives the participants' results, so it can report how the run ended.
- **Participants** are the agents listed in `<participants>`, at least 2. Each holds a role-specific scoring perspective, maintains its own copy of the run state, and self-determines when to score, repair, or terminate.

The state machine is distributed. Each participant moves as soon as its own copy of the run state says it is its turn — it scores after all seeds have reached it, and repairs when every score is in and it is the bottleneck. Each participant detects a gap in its own copy of the run state, asks the author of the missing message for it, and aborts if the request stays unanswered.

---

## Configuration Parameters

These are supplied once at run start and are immutable for the run. The second column gives the element that carries each one in `<concord-init>`.

| Parameter | Element in `<concord-init>` | Required | Default | Meaning |
|---|---|---|---|---|
| `participants` | `participants` | yes | — | Ordered list of at least 2 distinct participant agent identifiers |
| `satisfaction_threshold` (τ) | `params/satisfaction_threshold` | no | 0.6 | Normalized score every agent must clear for SUCCESS, in (0, 1]; set at the genuine bar, not an estimated reachable value |
| `eps` | `params/eps` | no | 0.05 | Minimum leximin improvement required to replace the incumbent; above 0 |
| `B` | `params/budget` | no | 8 | Maximum repair rounds before BEST_EFFORT termination; an integer, at least 1 |
| `timeout` | `params/timeout` | no | 300 | Seconds an agent waits for a message it is owed before asking for it again; above 0. Every agent uses this value |
| `retries` | `params/retries` | no | 3 | How many times an agent asks again before it aborts the run; an integer, at least 0 (0 aborts at the first expiry) |
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
      └─ each participant scores all seeds independently
           all scores received
           each participant evaluates
           ├─ floor(x*) ≥ τ, no absent scores  →  SUCCESS
           ├─ otherwise, t ≥ B                   →  BEST_EFFORT
           └─ otherwise, the bottleneck self-identifies and sends one repair message (candidate + own score)
                received by all participants; the others score independently
                └─ (repeat evaluate)

SUCCESS or BEST_EFFORT  →  each participant sends its <concord-result> and collects the others (CONFIRM)
a message that is owed never arrives, after retries requests  →  ABORTED
```

At any point where an agent waits for a message it is owed, the run can also end `ABORTED` (see [Timeouts and abort](#timeouts-and-abort)). When a participant has computed its outcome, with any outcome, it sends its `<concord-result>`. It then runs the confirmation: it waits for the result of every other participant and compares each with its own. The run is over for an agent only when the confirmation is complete, unless it aborts first.

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| INIT | Protocol start |
| SCORE | Each participant scores when its copy of the run state holds a candidate it has not rated yet |
| REPAIR | Each participant's own copy of the run state shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes |
| COMPLETE | Each participant independently computes the outcome once its own copy of the run state holds every rating, then sends its `<concord-result>` |
| CONFIRM | Each agent has its own outcome and holds the `<concord-result>` of every participant; the run ends for it when they all match |

The initiator opens the run with `<concord-init>`. After that the only communication is the participants' own messages (`<concord-init-reply>`, `<concord-score>`, `<concord-repair>` and `<concord-result>`) and the control messages `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. Nothing requests a score or a repair: each participant determines its own obligations from the messages it has received.

### Messages owed

An agent is owed a message when its copy of the run state shows that another agent should produce a message. The timer for that message starts as soon as the state machine enters the state that makes it owed.

| When | The agent is owed |
|---|---|
| After `<concord-init>` | An init reply from every participant |
| Once every init reply is in | A score from every participant, on every candidate in the pool |
| Once the incumbent's weakest score is below τ and `t < B` | A repair from the bottleneck (it contains the bottleneck's own score), then a score on the new candidate from every other participant |
| Once the agent has computed its own outcome | A result from every other participant |
| Once the first result has arrived, for an initiator that is not a participant | A result from every other participant |

An agent is never owed its own messages. An initiator that is not a participant is owed the init replies and the results, and the other agents are not owed anything from it. It computes no outcome of its own, so its wait for results starts when the first result arrives, not before: the negotiation can take longer than one timeout.

---

### One run at a time

An agent takes part in one run at a time. A run is active for an agent from the moment it accepts `<concord-init>` until it has reached its outcome (`SUCCESS`, `BEST_EFFORT` or `ABORTED`) and holds the result of every participant, the confirmation included. An aborted or cancelled run ends at once. Keeping the state of an ended run, and answering errors from it, does not count.

An agent that is not listed in `<participants>` ignores a `<concord-init>`. When a listed agent receives one for a run it does not know:

| The agent is | It does |
|---|---|
| in no active run | Accepts the run and starts it |
| in an active run | Sends `<concord-error what="init" reason="busy">` to the author of the init |
| the initiator of a run that still waits for init replies | Compares the two run ids. If its own is lower, it sends `busy`. Otherwise it sends `<concord-cancel>` for its own run, naming the new one in `superseded_by`. Its own run ends `ABORTED`, and it joins the new run |

Run ids are compared as UUID text in lower case, and the lower one continues. This settles two inits sent at about the same time: each initiator sees the other's init, and only the one with the higher id gives way. A `<concord-init>` for a run the agent already knows is ignored. The initiator's re-send of init arrives inside a `<concord-resend>` and is answered as described under `<concord-init>`; for a run the agent does not know, the init inside it is treated like any other init.

A `busy` error does not end the wait of the initiator. It keeps its timer and re-sends `<concord-init>` as usual, so an agent that becomes free can still join, and the run ends `ABORTED` after `retries` if nobody does.

This keeps every agent in at most one run, and every run ends. It does not promise that a run completes: in a race both runs can abort, and the initiators start again with new ids.

---

## Message Schemas

### Attributes of messages

Every message carries these attributes on its root element:

| Attribute | Presence | Meaning |
|---|---|---|
| `from` | always | The sender's own id. On `<concord-init>` it is the initiator's id; on a `<concord-resend>` it is the author of the message inside |
| `run_id` | always | Run id, a UUID. The initiator generates it for `<concord-init>`, and every other message repeats it |
| `uuid` | always | UUID of this transmission (see [Transport](#transport)) |
| `check` | `<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>`, `<concord-result>` | Integrity fingerprint of the document (see below) |

### Integrity check

`<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>` and `<concord-result>` each carry a `check` attribute: the first 8 hex characters of the SHA-256 of the document exactly as sent, in its serialized and escaped form and not a parsed one, with the `check` attribute itself left out, whitespace between elements removed, and each run of whitespace inside text replaced by one space, none left at either end of the text. Code computes and verifies it, never an agent by hand. The agent that authors the document computes it; every agent that reads the document verifies it. A document that fails its check is not recorded: the receiver sends `<concord-error reason="check">` to the author, who sends the stored original again in a `<concord-resend>`. A damaged `<concord-init>` is only not recorded, since the receiver may not know the run yet: the initiator's timer sends it again. The control messages (`<concord-error>`, `<concord-resend>`, `<concord-cancel>`) carry none: a `<concord-resend>` carries a stored original whose own `check` protects it.

The check detects accidental change, such as text shortened or reworded while an agent copies it. It is not a signature: it uses no secret. Agent ids are self-declared. The protocol assumes cooperative agents and does not defend against an agent that lies about who it is.

### Invalid messages

It is the author's responsibility to validate a message before it stores and sends it. A candidate must satisfy the candidate schema, `raw_ideal` must exceed `raw_reject`, and every raw score and anchor must be a number from 0 to 100. A message that fails validation is not stored and not sent: the author creates it again. If a message arrives changed, the receiver's `check` catches it (see [Integrity check](#integrity-check)).

The receiver validates what it receives as well, which costs little. A receiver that finds a message invalid, although it passed its check, does not record it and sends `<concord-error reason="invalid">` to the author, with the reason in the text of the error. Nobody answers this error: the author can only send the stored original again, which is the same message, so the error only informs the author. The message stays owed to the receiver, whose timer applies as for any missing message.

---

### `<concord-init>`

Sent by the initiator to open the run. One message carries everything a participant needs to start. Each participant answers with one `<concord-init-reply>`.

```xml
<concord-init from="alice" run_id="RUNID" uuid="1b4e28ba-2fa1-4d3c-9b0e-7a6f5e4d3c2b" check="a1b2c3d4">
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
  <context>problem statement</context>
  <anchors>
    <x_reject>outcome description anchoring the rejection end of the scale</x_reject>
    <x_ideal>outcome description anchoring the ideal end of the scale</x_ideal>
  </anchors>
  <proposal>
    <candidate_schema>{"type": "string", "description": "format specification"}</candidate_schema>
  </proposal>
</concord-init>
```

The `run_id` attribute identifies the run. The initiator generates it as a UUID when it creates the run, so that it differs from the id of any other run on the same channel. `<participants>` lists the full negotiating set, in order.

The anchors establish each participant's personal raw scoring scale. Each participant proposes from its own role's perspective, without pre-compromising for other roles.

If init replies are missing, the initiator sends `<concord-init>` again (see [Timeouts and abort](#timeouts-and-abort)). A participant that receives it again after answering sends its stored init reply again, in a `<concord-resend>` with no `reply_to`. A participant that has not answered yet answers now.

#### Candidate schema

`<candidate_schema>` holds a JSON Schema, as JSON text, that says what one candidate may look like. It is required, and it is the only description of the format: each receiver derives from it the wording it shows its agent. Authors enforce it on every seed and repair proposal, and receivers check it again: one that does not satisfy it is not sent, or not recorded (see Invalid messages). Two forms cover most runs:

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

The reply is valid as a whole or not at all: if either part is invalid, the author does not send it, and a receiver does not record it (see Invalid messages).

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

`id` is `r<N>-<agent>`. The `<score>` element has the same form as in `<concord-score>`, so a repair and its first score cannot be separated. The other participants score the new candidate in their own `<concord-score>` messages. A repair may propose a candidate that is already in the pool, for instance when the bottleneck holds that its earlier proposal is right and needs to argue it better. Its rationale then says so and gives the new arguments.

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

Every participant rates every candidate in the pool, its own included, on the same raw 0–100 scale used at anchoring. A score message covers the candidates the sender has not rated yet: once the init replies are in it holds one entry per seed, and after a repair it holds a single entry, for the new candidate. A repair message carries the proposer's own score for its new candidate. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate.

---

### `<concord-result>`

Sent once by each participant as soon as it has computed its outcome, with any outcome. It is the confirmation of the run: every agent learns how every participant ended, so that all of them can be sure they reached the same conclusion.

```xml
<concord-result from="cost" run_id="RUNID" uuid="c5e81a3f-92d4-47b6-8e0a-1f7d3c5b9a26" check="d4e5f6a7">
  <outcome>SUCCESS</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
</concord-result>
```

`outcome` is `SUCCESS`, `BEST_EFFORT` or `ABORTED`. `winning_candidate_id` is `x*`: for `SUCCESS` the candidate that every agent rates at or above τ, and for `BEST_EFFORT` the incumbent, which does not clear τ for everyone. It is omitted for `ABORTED`, which has no winner.

A result is owed by every participant to every other agent, and the usual timer applies (see [Messages owed](#messages-owed)). It is not a vote: a result never changes the outcome an agent has computed. A result that arrives before the agent has computed its own is recorded and compared later.

An agent compares each result it receives with its own. Two results differ when their outcomes differ, or when both outcomes are `SUCCESS` (or both `BEST_EFFORT`) and the winning candidates differ. Scores are not compared. When they differ, the agent sends `<concord-error reason="mismatch" what="result">` to the author. No re-send answers it: it only tells the author that another agent ended differently. Each agent records every mismatch in its report (see Run report).

The run ends for an agent when it holds the result of every participant. If a result is still missing after `retries` requests, the agent aborts like for any other missing message.

---

### `<concord-error>`

Reports a problem with a message. It mostly asks for the message to be sent again: it never arrived, or it arrived damaged. It is also how an agent refuses a `<concord-init>` because it is busy (see [One run at a time](#one-run-at-a-time)), how it tells an agent that its result differs from another's, and how it tells an author that a message it sent is invalid. A re-send answers none of these three. A missing or damaged result is asked for like any other message. Any agent that finds the problem sends it, the initiator included.

```xml
<concord-error run_id="RUNID" from="cost" to="security" uuid="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73" what="score" candidate="seed-cost" reason="missing">
  I am missing the score from security for seed-cost. If you are security, please send it again.
</concord-error>
```

| Attribute | Meaning |
|---|---|
| `to` | The author of the message that is wanted |
| `what` | `init-reply`, `score`, `repair` or `result`: what is wanted. `init` goes only with `reason="busy"` or `reason="invalid"`: the message concerned is the init itself. `result` is the confirmation; with `reason="mismatch"` no re-send answers it |
| `candidate` | Required when `what` is `score` or `repair`, except with `reason="check"`: the id of the candidate whose score, or whose repair proposal, is missing, for example `r1-security`. Omitted for `init`, `init-reply` and `result` |
| `ref` | Required with `reason="check"`: the `uuid` of the damaged document itself, which is the inner message when it arrived inside a `<concord-resend>`, since only that one matches a stored original. Its content cannot be read to name the candidate. If that `uuid`, or the author, cannot be read, the receiver sends nothing and the missing message is asked for when its timer expires |
| `reason` | `missing` (never arrived), `check` (arrived, failed its check), `invalid` (arrived intact but breaks a rule, such as the schema; the text says why), `mismatch` (a result differs from the sender's) or `busy` (the sender is in another run and refuses this init) |

Besides the attributes above, an error carries the common attributes; its `uuid` is what the re-send names in `reply_to`. The text inside is optional and for readers only; for `reason="invalid"` it states why the message was not recorded.

Only the author answers an error, and every other agent ignores it. The author sends the stored original, word for word, wrapped in a `<concord-resend>` whose `reply_to` is the `uuid` of the error. The exceptions are `reason="mismatch"`, `reason="busy"` and `reason="invalid"`, which nobody answers: they only inform the author. Sending a message twice is harmless: a repeated rating or candidate is ignored by the decision engine.

An agent sends each message once and keeps it. It may re-send only that stored original and never writes a new version of a message it has already sent. A receiver that has recorded a message ignores a different one from the same author for the same item (an init reply; a score for the same candidate; a repair with the same id): the first one recorded stands.

An agent keeps the state of a run, including the messages it sent and recorded, after the run has ended. If it receives a `<concord-error>` addressed to it for a run that is no longer active, it looks for the stored state of that run id and answers from it if it exists.

A re-send answers an error when it carries the message the error names: the same author, the same kind, and the same candidate, or, for `reason="check"`, the message whose `uuid` is in `ref`. Its `reply_to` ends the wait of the agent that asked. An agent that already holds the message ignores the copy. A score message that covers several candidates is re-sent whole; it answers the error for each candidate it contains. A repair message also contains the proposer's own score, so it answers an error for that score too.

An agent sends `reason="missing"` when it has waited `timeout` seconds for a message it knows is owed, or when its copy of the run state has a gap that a later message reveals (see Lost messages below). It sends `reason="check"` when a document fails its check, and `reason="invalid"` when one is invalid. Each `missing` or `check` request counts against `retries`.

#### Lost messages

An agent can lose a message without noticing, since nothing numbers the messages. It can still find out. The state machine says which messages are owed at each step (see [Messages owed](#messages-owed)), and a message that could only have been sent after other messages proves that those exist. An agent that receives such a message while lacking the earlier ones asks for them at once with `<concord-error reason="missing">`, without waiting for its timer.

For example, everyone sends a `<concord-score>` for a candidate, and participant P does not receive the one from X. Meanwhile participant B, whose copy of the run state is complete, finds it is the bottleneck and sends `<concord-repair id="r1-B">`. A repair in round N is sent only once every participant's score for every candidate before round N is in, so when P receives it, P knows its own copy of the run state is incomplete. P compares its copy of the run state with what the repair presupposes and asks the author of each message it lacks, here X:

```xml
<concord-error run_id="RUNID" from="P" to="X" uuid="7b2d5e91-4c08-4a63-9f1e-d3a6b8c07e25" what="score" candidate="seed-A" reason="missing"/>
```

P keeps processing what it has. It can score the new candidate, since that needs only the candidate text, but it decides nothing until its copy of the run state is complete.

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

Sent by the initiator to end its own run while it still waits for init replies: another `<concord-init>` has arrived that wins by the run id rule (see [One run at a time](#one-run-at-a-time)).

```xml
<concord-cancel from="alice" run_id="RUNID" uuid="8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36" superseded_by="OTHER-RUNID"/>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `superseded_by` | always | The run id of the run that continues |

A participant that receives it ends the run as `ABORTED`, at whatever stage the run is, with the reason "cancelled". The initiator's own run ends the same way. A cancelled run is the one exception to the confirmation: nobody sends a `<concord-result>` for it. A cancel for a run the agent has no state for, for example one that arrives before the init, is ignored. A `<concord-cancel>` from any agent other than the author of the run's `<concord-init>` is ignored. It carries no `check`. If it is lost, the participants end the run on their timers, since the initiator sends nothing more.

---

## Scoring Model

The terms used throughout this section and the rest of the document: the pool is the set of candidates of the run, the seeds and every repair so far; `floor(x)` is the lowest normalized score any participant gave candidate `x` (see Raw vs Normalized); `t` is the number of repair rounds so far, which counts every repair proposal, whether or not it replaced the incumbent.

### Raw vs Normalized

Participants always report raw scores on a personal 0–100 scale anchored at `raw_reject` and `raw_ideal`. A score is normalized to [0,1] with the anchors of the agent that gave it, taken from that agent's init reply, so all participants derive the same normalized scores:

```
s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0)
```

Raw scores and anchors are numbers from 0 to 100; a message with a value outside that range is invalid (see [Invalid messages](#invalid-messages)). A normalized score can still fall outside [0,1] when a raw score lies beyond an anchor, and is then clamped. Only raw scores travel in messages. Participants must never send a normalized value as their own raw rating.

### Candidate Selection (SELECT)

SELECT picks `x_cand`, the candidate of the pool with the best leximin vector: the normalized scores of the candidate, sorted from the lowest, are compared position by position, and the larger value wins at the first position where they differ. The candidate with the highest floor therefore wins, a tie in floor is broken by the next score, and so on. If two candidates have exactly the same vector, the one that comes first in the pool wins: the earlier round first, and within a round the proposer that comes first in `<participants>`.

### The incumbent (x*)

`x*` is the incumbent. Each time every rating of the pool is in, each participant evaluates:

- With no incumbent yet, `x*` is `x_cand`.
- Otherwise `x*` becomes `x_cand` if ACCEPT holds, and stays as it is if not.

At the end of the run `x*` is the winner.

### Incumbent Replacement (ACCEPT)

`x_cand` replaces the incumbent `x*` if either of these holds:

```
floor(x_cand) >= τ and floor(x*) < τ

leximin(x_cand) beats leximin(x*) by >= eps at some position k,
and no position before k is lower
```

The first condition means that a candidate that every agent rates at or above τ is accepted even when its gain is below `eps`. `eps` only decides between candidates on the same side of τ.

### Bottleneck Identification

The participant with the lowest normalized score on `x*` is the bottleneck. Ties are broken by the participant's position in `<participants>` in `<concord-init>`. The bottleneck identifies itself from its own copy of the run state.

---

## Candidate ID Scheme

| Round | Pattern | Example |
|---|---|---|
| Seed (round 0) | `seed-<agent>` | `seed-cost` |
| Repair round N | `r<N>-<agent>` | `r1-reliability`, `r2-security` |

`N` is the number of the repair round, counted from 1: the repair that follows `t` earlier repairs has `N = t + 1`.

---

## Termination Conditions

| Outcome | Condition |
|---|---|
| `SUCCESS` | `floor(x*) >= τ` AND every participant has rated `x*` |
| `BEST_EFFORT` | `floor(x*) < τ` AND `t >= B` (budget exhausted) |
| `ABORTED` | An agent still lacks a message it is owed, a result included, after `retries` requests, each after a `timeout` (see below); or the run is cancelled (see `<concord-cancel>`) |

Each participant computes its outcome from its own copy of the run state, sends it in its `<concord-result>`, and then collects the results of the others. A reader that is not a participant learns how the run ended from those messages. A run is confirmed when every agent holds the same outcome and winner from every participant; a difference is reported, not resolved.

### Run report

The report of a run is not a message: nothing is sent. It is complete once the confirmation is. It contains:

| Field | Content |
|---|---|
| outcome | `SUCCESS`, `BEST_EFFORT` or `ABORTED` |
| winning candidate | The id and text of `x*`. None for `ABORTED` |
| final scores | Each participant's normalized score on `x*` |
| shortfall | Each participant below τ, and by how much; empty for `SUCCESS` |
| rounds | The number of repair rounds used |
| reason | For `ABORTED` only: the agent and the message that was missing, or "cancelled" |
| other results | The results received from other agents, and whether each matches this one (see `<concord-result>`). `confirmed` when all of them match |

### Timeouts and abort

Every time an agent is owed a message (see [Messages owed](#messages-owed)) it starts a timer of `timeout` seconds, counted on its own clock. A message that arrives ends the wait.

- **Any agent.** When the timer expires, the agent sends `<concord-error reason="missing">` to the author and restarts the timer. When it expires after `retries` requests, the agent aborts: its run ends with outcome `ABORTED`, and a participant sends its `<concord-result>` (with outcome `ABORTED`, so that the others see it), except for a cancelled run (see `<concord-cancel>`). An agent that aborts does not wait for the results of the others: its run has ended.
- **The initiator, for init replies.** An agent that never received `<concord-init>` does not know the run and cannot answer an error. So when the timer for an init reply expires, the initiator sends `<concord-init>` again, inside a `<concord-resend>`, instead of an error, and restarts the timer. When it expires after `retries` re-sends, the initiator aborts and reports `ABORTED`. For every other message the initiator follows the rule above, like any other agent.

An agent waits at most `(retries + 1) × timeout` seconds for one message. `timeout` must be longer than the slowest turn of any agent. Relaying adds only network delay, which is much shorter.

`ABORTED` certifies no candidate. The report (see Run report) names the agent and the message that was missing, and has no winner. An aborting participant announces it with its `<concord-result>`, and the other agents also reach the same conclusion with their own timers, or stop earlier because the run has ended. A data message that arrives after an abort is ignored.

A timer is a running process: an implementation must keep one running while the agent waits.

---

## Transport

CONCORD does not depend on a particular network. The messages are application messages, so the network of a run is made only of the agents that take part in it. An agent is attached to one or more channels. A channel is anything that carries a message from one agent to the others attached to it: a shared channel that every member reads, or a link between two agents. Every message an agent sends reaches every other agent of the run, directly or through the agents in between, for example in a line or a star.

Every message and every `<concord-resend>` carries a `uuid` attribute: a UUID that identifies that one transmission. A new transmission always gets a new `uuid`, a re-send included: the `<concord-resend>` that carries a stored message has its own.

An agent that is attached to more than one channel relays. When a transmission arrives on a channel, it sends it on every other channel it is attached to, unless it has already relayed that `uuid`. The protocol does not discover channels and no message describes them. An agent knows its channels from its configuration, the list of channels its deployment attaches it to, which exists before any run starts. The configuration may also say which agents can be reached through each channel. The agent then relays only on the channels that lead to an agent of the run, the participants and the initiator, so an agent with several channels, only one of which leads to the run, relays nothing. A channel for which the configuration says nothing is treated as leading to the run. An agent attached to one channel, such as every member of a shared channel, has no other channel to relay to, so nothing is relayed. The centre of a star relays every message to all its spokes, and the middle agent of a line relays between its two sides.

Relaying can be mechanical, and an agent should pass messages on as fast as possible, since the agents behind it have timers running. It looks only at the `uuid` and the `run_id` of the outermost element and never changes the message. An agent relays only the messages of a run it has state for, plus `<concord-init>`, including when it is re-sent inside a `<concord-resend>`, since the agents behind it learn the run only from that message. A run that has ended still counts, since the agent keeps its state (see `<concord-error>`): it goes on relaying the messages of that run, so agents behind it can still finish. It keeps the `uuids` it has relayed for as long as it keeps the state of the run. A receiver that gets the same message twice ignores the second copy, so a copy that arrives by two paths is harmless.

An agent ignores any message whose run id is not one it has state for, except `<concord-init>`, bare or inside a `<concord-resend>` (see [One run at a time](#one-run-at-a-time)). Several runs can therefore share a channel. Within a run, an agent also ignores any message whose `from` is not listed in `<participants>`. The only agent outside that list that sends messages is the initiator, when it did not list itself: it sends the init and its re-send, errors and a cancel.

---

## Invariants

These summarize rules stated in the sections above. Where wording differs, the section applies.

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*`, and a run is not confirmed until every participant's result is in.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are fixed.** An agent's anchors are the ones in its first init reply. A later, different init reply is ignored, since replacing anchors would invalidate every earlier comparison.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; nobody chooses it. A repair from any other agent for that round is ignored.
5. **Messages are sent verbatim.** Every agent sends message content exactly as produced — no preamble, rewording, or added template.
6. **Run to complete.** Once a run has started it ends, for each agent, with one of the outcomes (`SUCCESS`, `BEST_EFFORT` or `ABORTED`); it never ends without one.
7. **Termination is distributed.** Each participant computes its outcome independently from the same messages. Every participant's result reaches every agent, and a difference is reported, never negotiated.
8. **A damaged document is never used.** A document that fails its `check` is not recorded; it is asked for again.
9. **Silence ends the run.** An agent never continues on an incomplete copy of the run state: if a message it is owed has not arrived after `retries` requests, it aborts.
10. **One message per item.** An agent sends each message once and may re-send only the stored original, and only its author does. A different message from the same author for an item already recorded is ignored.
11. **One uuid per transmission.** Every send, a re-send included, has a new `uuid`, and an agent relays a `uuid` at most once.
12. **One run at a time.** An agent is in at most one active run and refuses a second init as busy. Two initiators that both receive the other's init settle it by run id, and the lower one continues.
