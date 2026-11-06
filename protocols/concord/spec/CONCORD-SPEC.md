# CONCORD Protocol Specification

Version 0.1.0 <!-- x-release-please-version -->

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT). A participant that stops answering is marked missing and, while at least two participants remain, the run goes on without it; the run can then end at best in BEST_EFFORT, since one agent was not taken into account. Only if too few participants remain, or the run is cancelled, does it end in error (ABORTED).

## Overview

Every agent in a CONCORD run has its own criteria. The protocol looks for one outcome that every agent rates at or above a satisfaction threshold, and says so honestly when it cannot find one.

A run goes like this:

1. Any agent can start a run by sending one message, `<concord-init>`, that describes the problem and sets up the run.
2. Every participant answers with its anchors, which fix its personal scoring scale, a seed candidate and its own score for it.
3. Every participant scores every candidate. The candidate whose weakest score is highest becomes the incumbent. A later candidate replaces it only if it is clearly better, or if it clears the threshold where the incumbent does not.
4. If the incumbent's weakest score is below the threshold and the budget allows, the participant that is least satisfied with it (the bottleneck) proposes a repair. Everyone scores the new candidate and the incumbent is evaluated again.
5. The run ends with `SUCCESS` when every participant clears the threshold, or with `BEST_EFFORT` when the budget of repair rounds is spent. A run that cannot complete ends with `ABORTED`.
6. One participant then sends its result to all the others. Every other participant compares that result with the outcome it computed, and reports any difference.

There is no central decision-maker and no coordinator that the run depends on. Each participant runs the same decision engine over the same messages and reaches the same decision.

Activate CONCORD when:
- An outcome must satisfy agents whose criteria may conflict.
- A named negotiating set can be specified.
- A shared candidate schema exists.

Conflict may be confirmed, anticipated, or merely uncertain — all three cases warrant activation.

---

## Roles

All agents run the same protocol.

- **The initiator** is the agent that starts the run by sending `<concord-init>`. If it is listed in `<participants>`, it is also a participant; otherwise it does not negotiate. It sends `<concord-init>` again when init replies are missing (see [Timeouts and missing agents](#timeouts-and-missing-agents)), and it receives the result of the run so that it can report how the run ended.
- **Participants** are the agents listed in `<participants>`, at least two. Each holds a role-specific scoring perspective, maintains its own copy of the run state, and decides for itself when to score, repair or compute its outcome.

The state machine is distributed. Each participant moves as soon as its own copy of the run state says it is its turn. It scores once the seeds have reached it, and repairs when every score is in and it is the bottleneck. Each participant detects a gap in its own copy of the run state and asks the author of the missing message for it. If the author stays silent, the participant marks that author missing and goes on without it (see [Timeouts and missing agents](#timeouts-and-missing-agents)).

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
| `retries` | `params/retries` | no | 3 | How many times an agent asks again before it marks the silent agent missing; an integer, at least 0 (0 marks it at the first expiry) |
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
      every participant answers with one init reply
      all init replies received (or their authors marked missing)
      └─ each participant scores the seeds of the others independently
           all scores received (from the participants not missing)
           each participant evaluates
           ├─ floor(x*) ≥ τ, nobody missing    →  SUCCESS
           ├─ floor(x*) ≥ τ, somebody missing  →  BEST_EFFORT
           ├─ otherwise, t ≥ B                 →  BEST_EFFORT
           └─ otherwise, the bottleneck self-identifies and sends one repair message (proposal + own score)
                received by all participants; the others score independently
                └─ (repeat evaluate)

SUCCESS or BEST_EFFORT  →  the reporter (the first participant in `<participants>` that is not missing) sends its <concord-result>;
                           the others wait for it and compare it with their own outcome
an agent stays silent after retries requests  →  it is marked missing, and the evaluation is made again without it
fewer than two participants left, or the run is cancelled  →  ABORTED
```

At any point where an agent waits for a message it is owed, the author of that message can be marked missing, and the run can end `ABORTED` if too few participants are left (see [Timeouts and missing agents](#timeouts-and-missing-agents)). When a participant has computed its outcome, with any outcome, it works out who the reporter is (see [`<concord-result>`](#concord-result)). The reporter sends its `<concord-result>`. Every other participant runs the confirmation: it waits for the result of the reporter and compares it with its own. The run is over for an agent only when the confirmation is complete, unless it aborts first.

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| INIT | Protocol start |
| SCORE | Each participant scores the seeds of the others once every init reply is in, or its author is marked missing, in one `<concord-score>`. Seeds in the pool are scored (see [Scoring Model](#scoring-model)). After the seeds it scores each repair as soon as it has it. A participant rates its own candidates inside the proposal |
| REPAIR | Each participant's own copy of the run state shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes |
| COMPLETE | Each participant independently computes the outcome once its own copy of the run state holds every rating of the participants that are not missing; the reporter then sends its `<concord-result>` |
| CONFIRM | Each participant that is not the reporter has its own outcome and holds the reporter's `<concord-result>`; the run ends for it when it has compared them |

The initiator opens the run with `<concord-init>`. After that the only communication is the participants' own messages (`<concord-init-reply>`, `<concord-score>`, `<concord-repair>` and `<concord-result>`) and the control messages `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. Nothing requests a score or a repair: each participant determines its own obligations from the messages it has received.

### Missing participants

A participant that stays silent after repeated requests is marked missing. The mark is local to the agent that makes it, and the run goes on without that participant as long as at least two participants remain. Everything in the sections below that counts participants counts only those that are not missing. The rules for marking, and what follows from it, are in [Timeouts and missing agents](#timeouts-and-missing-agents).

---

## Scoring Model

The terms used throughout this section and the rest of the document: the pool is the set of candidates of the run, the seeds and every repair so far; `floor(x)` is the lowest normalized score any participant gave candidate `x` (see [Raw vs Normalized](#raw-vs-normalized)); `t` is the number of repair rounds so far, which counts every repair proposal, whether or not it replaced the incumbent. A participant that an agent has marked missing is left out of everything in this section for that agent: `floor(x)`, the leximin vector, the bottleneck and the ratings that must be in all count only the participants that are not missing (see [Timeouts and missing agents](#timeouts-and-missing-agents)).

A seed is in the pool once its init reply has arrived, and stays there even if its author is marked missing afterwards. A participant marked missing before its init reply arrived has no seed.

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

At the end of the run `x*` is the winner. When an agent marks a participant missing, it goes through the evaluations again from the first: for the seeds, and then for each repair in the order of the pool, with only the ratings of participants that are not missing. The candidates of the pool stay, the missing participant's included. This gives `x*` again, which may be a different candidate, and every participant that holds the same messages and the same missing set gets the same one.

### Incumbent Replacement (ACCEPT)

`x_cand` replaces the incumbent `x*` if either of these holds:

```
floor(x_cand) >= τ and floor(x*) < τ

there is a position k with c[k] >= i[k] + eps,
and c[j] >= i[j] for every position j before k
```

where `c` and `i` are the leximin vectors of `x_cand` and `x*` (the normalized scores of the participants that are not missing, sorted from the lowest).

The first condition means that a candidate that every agent rates at or above τ is accepted even when its gain is below `eps`. `eps` only decides between candidates on the same side of τ.

### Bottleneck Identification

The participant, among those that are not missing, with the lowest normalized score on `x*` is the bottleneck. Ties are broken by the participant's position in `<participants>` in `<concord-init>`. The bottleneck identifies itself from its own copy of the run state.

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
| `SUCCESS` | `floor(x*) >= τ` AND every participant has rated `x*` AND no participant was marked missing before the outcome was computed |
| `BEST_EFFORT` | `floor(x*) < τ` AND `t >= B` (budget exhausted) |
| `BEST_EFFORT` | At least one participant is missing, AND `floor(x*) >= τ` for all the others |
| `ABORTED` | Fewer than two participants are left that are not missing (see [Timeouts and missing agents](#timeouts-and-missing-agents)); or the run is cancelled (see `<concord-cancel>`) |

The second `BEST_EFFORT` row applies as soon as it holds, without waiting for the budget to run out: everyone still in the run is satisfied, so a repair would gain nothing. The outcome is not `SUCCESS`, because the missing participant never had its say on the winner.

Each participant computes its outcome from its own copy of the run state, and the reporter's `<concord-result>` confirms it (see [`<concord-result>`](#concord-result)). A reader that is not a participant learns how the run ended from that message.

### Run report

The report of a run is not a message: nothing is sent. It is complete once the confirmation is. It contains:

| Field | Content |
|---|---|
| outcome | `SUCCESS`, `BEST_EFFORT` or `ABORTED` |
| winning candidate | The id and text of `x*`. None for `ABORTED` |
| final scores | Each participant's normalized score on `x*`, for the participants that are not missing |
| shortfall | Each participant below τ, and by how much; empty for `SUCCESS` and when every remaining participant clears τ |
| rounds | The number of repair rounds used |
| missing | Each participant marked missing, and the message that was missing; empty if nobody was |
| reason | For `ABORTED` only: "too few participants" or "cancelled" |
| results compared | The reporter's result, and any other result received, and whether each matches this one (see `<concord-result>`); for the reporter, the mismatch errors it received |
| status | `confirmed`, `unverified` or `disputed`, as below |


The status is `confirmed` when the results match and nobody is missing. It is `unverified` for an initiator that is not a participant, which has no outcome of its own to compare. It is `disputed` when a mismatch was found or received, and then no result of the run is to be relied on.

---

## Message Schemas

The messages of the negotiation are described here, in the order of a run. They assume that every message arrives intact; [Reliability and Liveness](#reliability-and-liveness) says what the agents do when one does not.

### Attributes of messages

Every message carries these attributes on its root element:

| Attribute | Presence | Meaning |
|---|---|---|
| `version` | always | The protocol version, `0.3.0` (see [Version](#version)) |
| `from` | always | The sender's own id. On `<concord-init>` it is the initiator's id; on a `<concord-resend>` it is the author of the message inside |
| `run_id` | always | Run id, a UUID. The initiator generates it for `<concord-init>`, and every other message repeats it |
| `uuid` | always | UUID of this transmission. Every transmission gets a new one, a re-send included: the `<concord-resend>` that carries a stored message has its own |
| `check` | `<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>`, `<concord-result>` | Integrity fingerprint of the document (see [Integrity check](#integrity-check)) |

### `<concord-init>`

Sent by the initiator to open the run. One message carries everything a participant needs to start. Each participant answers with one `<concord-init-reply>`.

```xml
<concord-init version="0.3.0" from="alice" run_id="RUNID" uuid="1b4e28ba-2fa1-4d3c-9b0e-7a6f5e4d3c2b" check="a1b2c3d4">
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

The `run_id` attribute identifies the run. The initiator generates it as a UUID when it creates the run, so that it differs from the id of any other run on the same network. `<participants>` lists the full negotiating set, in order. The order matters: the participants listed first win ties in the decision rules, and the first one that is not missing is the reporter. The initiator should order the list with this in mind.

The anchors establish each participant's personal raw scoring scale. Each participant proposes from its own role's perspective, without pre-compromising for other roles.

If init replies are missing, the initiator sends `<concord-init>` again (see [Timeouts and missing agents](#timeouts-and-missing-agents)). A participant that receives it again after answering sends its stored init reply again, in a `<concord-resend>` with no `reply_to`. A participant that has not answered yet answers now.

#### Candidate schema

`<candidate_schema>` holds a JSON Schema, as JSON text, that says what one candidate may look like. It is required, and it is the only description of the format: each receiver derives from it the wording it shows its agent. Authors enforce it on every seed and repair proposal, and receivers check it again: one that does not satisfy it is not sent, or not recorded (see [Invalid messages](#invalid-messages)). Two forms cover most runs:

- **Open format** (the default). The description tells the agents what one candidate is. Nothing else is enforced, and agents may propose combinations.

  ```json
  {"type": "string", "description": "a remediation plan: the actions to take, each with its service and version"}
  ```

- **Closed list.** Every proposal, seeds and repairs alike, must be exactly one of the listed options (a repair may re-propose one already in the run). Added wording is refused, so detail goes in the rationale. Use it only when the options were handed to the initiator, and copy each exactly as given: a list the initiator invents shuts out better answers that nobody listed.

  ```json
  {"enum": ["option A", "option B", "option C"]}
  ```

A schema needs a `description` or an `enum`. The supported keywords are `type` (only `"string"`), `enum`, `description`, `title`, `pattern` (a regular expression the candidate must match), `minLength` and `maxLength`. Any other keyword is refused when the run is created, so a rule is never silently left unenforced. The JSON is ordinary XML text: `<`, `>` and `&` inside it are escaped, a `"` is not.

### `<concord-init-reply>`

Sent by each participant in answer to `<concord-init>`. One reply carries its anchors, its seed candidate and its own score for it.

```xml
<concord-init-reply version="0.3.0" from="cost" run_id="RUNID" uuid="6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51" check="e5f6a7b8">
  <anchors>
    <raw_reject>8.0</raw_reject>
    <raw_ideal>95.0</raw_ideal>
  </anchors>
  <proposal id="seed-cost">
    <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
    <rationale>evidence and reasoning</rationale>
    <score>
      <raw>82.0</raw>
      <reasoning>full reasoning text</reasoning>
    </score>
  </proposal>
</concord-init-reply>
```

`raw_reject` should be low (participant's satisfaction if the rejection outcome occurred); `raw_ideal` should be high. `raw_ideal` must strictly exceed `raw_reject`. These are satisfaction ratings of hypothetical outcomes, not measurements of current state.

`id` is `seed-<agent>`. The `<proposal>` element has the same form in an init reply and in a repair: the candidate, the rationale and the proposer's own score for it, in the same `<score>` form as in `<concord-score>` but without `candidate_id`, since it rates the candidate of the proposal. The candidate must satisfy the candidate schema. The rationale should state the facts that support the candidate and the domain knowledge that argues against the other options in the schema.

The reply is valid as a whole or not at all: if either part is invalid, the author does not send it, and a receiver does not record it (see [Invalid messages](#invalid-messages)).

### `<concord-repair>`

Sent by the bottleneck: a repair proposal, with its own score for the new candidate, in the same `<proposal>` form as in an init reply.

```xml
<concord-repair version="0.3.0" from="cost" run_id="RUNID" uuid="0d7a5e92-3c64-4f18-b2a9-84e1c6d7f305" check="c9d0e1f2">
  <proposal id="r1-cost">
    <candidate>strategy: local-memory, ttl_seconds: 600</candidate>
    <rationale>evidence and reasoning</rationale>
    <score>
      <raw>82.0</raw>
      <reasoning>full reasoning text</reasoning>
    </score>
  </proposal>
</concord-repair>
```

`id` is `r<N>-<agent>`. The proposal and the proposer's score travel together and cannot be separated. The other participants score the new candidate in their own `<concord-score>` messages. A repair may propose a candidate that is already in the pool, for instance when the bottleneck holds that its earlier proposal is right and needs to argue it better. Its rationale then says so and gives the new arguments.

### `<concord-score>`

Sent by each participant: its scores on the candidates of the others that it has not rated yet.

```xml
<concord-score version="0.3.0" from="cost" run_id="RUNID" uuid="9e2b7c40-1d85-4a36-8f0c-5b3a2d1e6f47" check="3a4b5c6d">
  <score candidate_id="seed-reliability">
    <raw>82.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
  <score candidate_id="seed-security">
    <raw>41.0</raw>
    <reasoning>full reasoning text</reasoning>
  </score>
</concord-score>
```

Every participant rates every candidate in the pool on the same raw 0–100 scale used at anchoring. It rates its own candidates inside the proposal, in the init reply or the repair, and the others' in `<concord-score>`. A score message covers the candidates of the others that the sender has not rated yet: once the init replies are in it holds one entry per seed of another participant, and after a repair it holds a single entry, for the new candidate. A `<concord-score>` never covers a candidate its sender proposed: such a message is invalid. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate. For the same reason, everything a participant writes in a message is read by every agent of the run, so a participant must not write what it needs to keep private.

### `<concord-result>`

Sent once, by the reporter, as soon as it has computed its outcome, with any outcome. It is the confirmation of the run: every agent learns how the reporter ended, and the participants compare it with their own outcome, so that all of them can be sure they reached the same conclusion.

The reporter is the first participant in `<participants>` that the sender has not marked missing. This is the same order that breaks ties in the decision rules. Every agent works it out for itself, from the `<participants>` list and its own list of missing participants. Normally all agents get the same one. If the reporter is marked missing, the next participant in the list takes its place.

```xml
<concord-result version="0.3.0" from="cost" run_id="RUNID" uuid="c5e81a3f-92d4-47b6-8e0a-1f7d3c5b9a26" check="d4e5f6a7">
  <outcome>SUCCESS</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
</concord-result>
```

With a missing participant the result looks like this:

```xml
<concord-result version="0.3.0" from="cost" run_id="RUNID" uuid="9d2f4c60-18a7-4e35-b3c9-5a0e7d1f6b82" check="0f1e2d3c">
  <outcome>BEST_EFFORT</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
  <missing>
    <agent>security</agent>
  </missing>
</concord-result>
```

`outcome` is `SUCCESS`, `BEST_EFFORT` or `ABORTED`. `winning_candidate_id` is `x*`: for `SUCCESS` the candidate that every participant rates at or above τ, and for `BEST_EFFORT` the incumbent, which either does not clear τ for everyone or clears it only for the participants that were not missing. It is omitted for `ABORTED`, which has no winner.

`missing` lists the participants the sender had marked missing when it computed its outcome, each in an `<agent>` element, in the order of `<participants>`. The element is omitted when nobody was missing. Participants marked missing later, for instance a reporter that stayed silent, are not in it.

How the other participants compare the result with their own, and what happens when it is lost, is described under [Confirmation](#confirmation).

---

## Reliability and Liveness

The core protocol above works when every message arrives, intact and once, and every participant answers. This part says what the agents do when that is not so: how a message is checked, asked for again and sent again, how a silent participant is left out of the run, and how one agent is kept in one run. It adds three control messages, `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. The rules of the core still decide the outcome; the rules here decide which messages and which participants reach the core.

The sections follow the path of a message and of a missing one: first the order in which an agent handles a message, then liveness (what is owed, the timers, lost messages), then the validity of a message (check, invalid, version, duplicates), then recovery and exclusivity (error, resend, cancel, one run at a time, confirmation).

### Order of checks

An agent handles a message in this order, and stops at the first step that rejects it:

1. The run is known, and the sender is a participant of the run, or its initiator. A data message from an agent marked missing is rejected here; its `<concord-error>` is not. A `<concord-init>` for a run the agent does not know is not rejected here: it goes through steps 3 to 5 first, and the agent accepts the run only if it passes (see [One run at a time](#one-run-at-a-time)).
2. The `uuid` is not one the agent has accepted.
3. The `version` is its own.
4. The `check` is correct, for the messages that carry one.
5. The message is valid (see [Invalid messages](#invalid-messages)).
6. The message is recorded and its `uuid` kept, and the agent acts on it.

For a `<concord-resend>`, steps 2 to 4 apply to the wrapper by its `uuid`, and steps 3 to 5 apply again to the message inside, by its own `version` and `check`.

### Messages owed

An agent is owed a message when its copy of the run state shows that another agent should produce a message. The timer for that message starts as soon as the state machine enters the state that makes it owed.

| When | The agent is owed |
|---|---|
| After `<concord-init>` | An init reply from every participant |
| Once, for every participant, its init reply is in or the participant is marked missing | A score from every participant that is not missing, on every seed in the pool other than its own (its score on its own seed came with the init reply) |
| Once the incumbent's weakest score is below τ and `t < B` | A repair from the bottleneck (its proposal contains the bottleneck's own score), then a score on the new candidate from every other participant |
| Once the agent has computed its own outcome, and it is not the reporter | The result of the reporter |
| For an initiator that is not a participant | The first result of the run, from any participant. It starts no timer for it |

An agent is never owed its own messages, nor any message from an agent it has marked missing. A row above that waits for a message is also met when the author of that message is marked missing. An initiator that is not a participant is owed the init replies and the result, and the other agents are not owed anything from it. It computes no outcome of its own and cannot tell when the negotiation ends, since the negotiation can take longer than one timeout, so it keeps no timer for the result: it takes the first result that arrives.

### Timeouts and missing agents

Every time an agent is owed a message (see [Messages owed](#messages-owed)) it starts a timer of `timeout` seconds, counted on its own clock. A message that arrives ends the wait.

- **Any agent.** When the timer expires, the agent sends `<concord-error reason="missing">` to the author and restarts the timer. When it expires after `retries` requests, the agent marks the author missing.
- **The initiator, for init replies.** An agent that never received `<concord-init>` does not know the run and cannot answer an error. So when the timer for an init reply expires, the initiator sends `<concord-init>` again, inside a `<concord-resend>`, instead of an error, and restarts the timer. When it expires after `retries` re-sends, the initiator marks the participant missing.

An agent waits at most `(retries + 1) × timeout` seconds for one message. `timeout` must be longer than the slowest turn of any agent. The network adds only a delay, which is much shorter.

**Marking an agent missing.** The mark is local: it is made by one agent, from its own timers, and nothing announces it. It is final for the run. From then on the agent:

- stops all its timers for messages owed by that participant, and is owed nothing more from it;
- ignores every data message from it that arrives later (its `<concord-error>` is still answered), and no longer counts the ratings it has already recorded from it, while the candidates it proposed stay in the pool;
- evaluates the run without it, going through the evaluations again as described under [The incumbent](#the-incumbent-x), as long as it has not computed its outcome. If the missing participant was the bottleneck whose repair was owed, the bottleneck is now another participant, and the repair round keeps its number;
- keeps its outcome and winner as they are, once it has computed them, since they were computed with that participant's scores. The participant is only added to the report as missing, which is then not `confirmed`. This is how a reporter that stays silent at the confirmation is treated;
- lists it as missing in its `<concord-result>`, if it sends one and it was marked before the outcome was computed, and in its report;
- works out the reporter again, without it.

A participant that is marked missing is not told, and goes on as before. Its own run ends by its own rules, and its result is ignored by the agents that marked it.

If fewer than two participants are left that are not missing, the agent aborts: its run ends with outcome `ABORTED`, and, if it is a participant, being then the only one it has not marked missing, it is its own reporter and sends its `<concord-result>` (with outcome `ABORTED`, so that the others see it). A cancelled run sends none. An agent that aborts does not wait for a result: its run has ended. An initiator that is not a participant counts the participants that have not been marked missing, and sends nothing.

The agents may not agree on who is missing: a message can reach one agent and not another, and each agent has its own timers. This is not resolved. Each agent reports what it saw, the `missing` list in the reporter's result shows the difference, and the participant that finds it records it as a mismatch. When the network fails, for instance when the centre of a star stops, the agents are cut off from each other: each marks the agents it no longer hears missing, and an agent left with fewer than two participants aborts.

`ABORTED` certifies no candidate. A data message that arrives after an abort is ignored.

A timer is a running process: an implementation must keep one running while the agent waits.

### Lost messages

An agent can lose a message without noticing, since nothing numbers the messages. It can still find out. The state machine says which messages are owed at each step (see [Messages owed](#messages-owed)), and a message that could only have been sent after other messages proves that those exist. An agent that receives such a message while lacking the earlier ones asks for them at once with `<concord-error reason="missing">`, without waiting for its timer.

For example, everyone sends a `<concord-score>` for a candidate, and participant P does not receive the one from X. Meanwhile participant B, whose copy of the run state is complete, finds it is the bottleneck and sends a `<concord-repair>` whose proposal has the id `r1-B`. A repair in round N is sent only once every participant's score for every candidate before round N is in, so when P receives it, P knows its own copy of the run state is incomplete. P compares its copy of the run state with what the repair presupposes and asks the author of each message it lacks, here X:

```xml
<concord-error version="0.3.0" run_id="RUNID" from="P" to="X" uuid="7b2d5e91-4c08-4a63-9f1e-d3a6b8c07e25" what="score" candidate="seed-A" reason="missing"/>
```

P keeps processing what it has. It can score the new candidate, since that needs only the candidate text, but it decides nothing until its copy of the run state is complete.

The same holds the other way round: a `<concord-score>` that rates a candidate P never received shows that the proposal was lost, and P asks its author: `what="init-reply"` for a seed, which travels in the author's init reply, or `what="repair"` with the `candidate` id for a repair.

A request like this starts the timer for that message, as any other request does, and counts against `retries`.

### Integrity check

`<concord-init>`, `<concord-init-reply>`, `<concord-repair>`, `<concord-score>` and `<concord-result>` each carry a `check` attribute: the first 8 lowercase hex characters of the SHA-256 of the canonical text of the document. The canonical text is the text of the document exactly as it was sent, with these changes and no others:

1. The `check` attribute is removed, `check="…"` with its double quotes, together with the space before it.
2. Each stretch of text between a `>` and the next `<` is changed: if it holds only whitespace it is removed, otherwise each run of whitespace in it becomes one space and none is left at either end.
3. The result is encoded as UTF-8.

Whitespace is space, tab, carriage return and line feed. Nothing else is changed: attribute order, quotes and entities stay as sent. Neither the author nor a receiver may parse the document and write it again before computing the check, since both work on the text. A message holds no comments, CDATA sections or processing instructions, and writes `<`, `>` and `&` as `&lt;`, `&gt;` and `&amp;` in text and in attribute values, and `"` as `&quot;` in attribute values only; a `"` in text stays as it is. For a document inside a `<concord-resend>`, the text is the inner element, from its first `<` to its last `>`.

Code computes and verifies the check, never an agent by hand. The agent that authors the document computes it; every agent that reads the document verifies it. A document that fails its check is not recorded: the receiver sends `<concord-error reason="check">` to the author, who sends the stored original again in a `<concord-resend>`. A damaged `<concord-init>` is only not recorded, since the receiver may not know the run yet: the initiator's timer sends it again. The control messages (`<concord-error>`, `<concord-resend>`, `<concord-cancel>`) carry none: a `<concord-resend>` carries a stored original whose own `check` protects it.

The check detects accidental change, such as text shortened or reworded while an agent copies it. It is not a signature: it uses no secret. Agent ids are self-declared. The protocol assumes cooperative agents and does not defend against an agent that lies about who it is.

### Invalid messages

It is the author's responsibility to validate a message before it stores and sends it. A candidate must satisfy the candidate schema, `raw_ideal` must exceed `raw_reject`, and every raw score and anchor must be a number from 0 to 100. A `<proposal>` is invalid when its `id` is not `seed-<from>` in an init reply, or not `r<N>-<from>` in a repair with `N` the number of the repair round (see [Candidate ID Scheme](#candidate-id-scheme)); a receiver whose copy of the run state is incomplete first asks for the messages it lacks before it judges a repair `id`. A `<concord-score>` is invalid when it rates a candidate its sender proposed or rates the same candidate twice. A rating of a candidate the receiver does not hold is not invalid: the proposal was lost (see [Lost messages](#lost-messages)). A `<concord-init>` is invalid when it breaks a rule of [Configuration Parameters](#configuration-parameters) or the candidate schema rules: fewer than 2 participants or a repeated one, a value out of its range, a missing required element, or an unsupported schema keyword. A message that fails validation is not stored and not sent: the author creates it again. If a message arrives changed, the receiver's `check` catches it (see [Integrity check](#integrity-check)).

The receiver validates what it receives as well, which costs little. A receiver that finds a message invalid, although it passed its check, does not record it (an invalid init does not make it join the run) and sends `<concord-error reason="invalid">` to the author, with the reason in the text of the error. Nobody answers this error: the author can only send the stored original again, which is the same message, so the error only informs the author. The message stays owed to the receiver, whose timer applies as for any missing message.

### Version

Every message carries `version="0.3.0"` on its root element, and the one inside a `<concord-resend>` carries it too. The value is the version of the protocol on the wire. It is not the release number of this document, which is in the header, and the two change independently: the wire version changes only when the messages or the rules change. All agents of a run must use the same one.

An agent checks the `version` after the filters of [Order of checks](#order-of-checks) and before the integrity check and any other validation, since another version may define those differently. A message whose `version` is not exactly the agent's own, or that has none, is dropped: it is not recorded, and nothing is sent in answer, since the agent cannot read it. What follows depends on the message:

- A `<concord-init>`, bare or inside a `<concord-resend>`: the agent does not join the run. If every participant uses another version, the run ends on the initiator's timers.
- Any other message of a run the agent is in: the sender goes unheard, and the agent's timers mark it missing like any silent participant (see [Timeouts and missing agents](#timeouts-and-missing-agents)). One agent with another version therefore costs the run that agent, not the run.

A version never aborts a run. For a `<concord-resend>`, the wrapper and the message inside are both checked.

### Duplicates

An agent keeps the `uuid` of every transmission it has accepted, for as long as it keeps the state of the run. When a message arrives whose `uuid` is one of them, the agent drops it at that point of the [Order of checks](#order-of-checks): no version check, no integrity check, no answer and no error. This covers a message that the network delivers twice, so that it is acted on once. Only the `uuid` of the outermost element counts. A `<concord-resend>` is checked by its own `uuid`, and the stored message inside it keeps its original `uuid`, so it is not taken for a duplicate of a damaged copy.

A transmission is accepted, and its `uuid` kept, only when the agent has processed it: it passed the version and the integrity check and was valid. A damaged or invalid copy is not kept, so an intact copy with the same `uuid` that arrives later is still processed. A transmission that is ignored for another reason, such as an unknown run or a sender that is not a participant, is not kept either, since the agent holds no state for it.

### `<concord-error>`

Reports a problem with a message. It mostly asks for the message to be sent again: it never arrived, or it arrived damaged. It is also how an agent refuses a `<concord-init>` because it is busy (see [One run at a time](#one-run-at-a-time)), how it tells an agent that its result differs from another's, and how it tells an author that a message it sent is invalid. A missing or damaged result is asked for like any other message. Any agent that finds the problem sends it, the initiator included.

```xml
<concord-error version="0.3.0" run_id="RUNID" from="cost" to="security" uuid="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73" what="score" candidate="seed-cost" reason="missing">
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

Only the author answers an error, and every other agent ignores it. The author sends the stored original, word for word, wrapped in a `<concord-resend>` whose `reply_to` is the `uuid` of the error. The exceptions are `reason="mismatch"`, `reason="busy"` and `reason="invalid"`, which nobody answers: they only inform the author. In the other direction, a `<concord-result>` that its author has not sent yet is sent now, as there is no stored original (see [`<concord-result>`](#concord-result)).

An agent sends each message once and keeps it. It may re-send only that stored original and never writes a new version of a message it has already sent. A receiver that has recorded a message ignores a different one from the same author for the same item (an init reply; a score for the same candidate; a repair with the same id): the first one recorded stands.

An agent keeps the state of a run, including the messages it sent and recorded, after the run has ended. If it receives a `<concord-error>` addressed to it for a run that is no longer active, it looks for the stored state of that run id and answers from it if it exists.

A re-send answers an error when it carries the message the error names: the same author, the same kind, and the same candidate, or, for `reason="check"`, the message whose `uuid` is in `ref`. Its `reply_to` ends the wait of the agent that asked. An agent that already holds the message ignores the copy. A score message that covers several candidates is re-sent whole; it answers the error for each candidate it contains. An init reply and a repair also contain the proposer's own score, so each answers an error for that score too.

An agent sends `reason="missing"` when it has waited `timeout` seconds for a message it knows is owed, or when its copy of the run state has a gap that a later message reveals (see [Lost messages](#lost-messages)). It sends `reason="check"` when a document fails its check, and `reason="invalid"` when one is invalid. Each `missing` or `check` request counts against `retries`.

### `<concord-resend>`

Sent by the author of a stored message to carry it again, word for word. It answers a `<concord-error>`, or it is sent without one: the initiator sends `<concord-init>` this way when init replies are missing, and a participant sends its stored init reply this way when it receives init again.

```xml
<concord-resend version="0.3.0" run_id="RUNID" from="security" uuid="f4d92b61-0a37-4c8e-9d15-6b2e7a3c8f04" reply_to="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73">
  <concord-score version="0.3.0" from="security" run_id="RUNID" uuid="51d0a7e3-96b2-4f84-a0c7-2e9d1b6f3a58" check="1b2c3d4e">
    <score candidate_id="seed-cost">
      <raw>44.0</raw>
      <reasoning>…</reasoning>
    </score>
    <score candidate_id="seed-reliability">
      <raw>71.0</raw>
      <reasoning>…</reasoning>
    </score>
  </concord-score>
</concord-resend>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `reply_to` | when it answers an error | The `uuid` of that error |

Besides `reply_to`, the wrapper carries the common attributes, with its own `uuid`, new for every re-send. The message inside is the stored original, untouched: it keeps its own `uuid` and `check`. A receiver unwraps it, verifies its `check`, and records it if it holds no message for that item; otherwise it ignores it.

### `<concord-cancel>`

Sent by the initiator to end its own run while it still waits for init replies: another `<concord-init>` has arrived that wins by the run id rule (see [One run at a time](#one-run-at-a-time)).

```xml
<concord-cancel version="0.3.0" from="alice" run_id="RUNID" uuid="8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36" superseded_by="OTHER-RUNID"/>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `superseded_by` | always | The run id of the run that continues |

A participant that receives it ends the run as `ABORTED`, at whatever stage the run is, with the reason "cancelled". The initiator's own run ends the same way. A cancelled run is the one exception to the confirmation: nobody sends a `<concord-result>` for it. A cancel for a run the agent has no state for, for example one that arrives before the init, is ignored. A `<concord-cancel>` from any agent other than the author of the run's `<concord-init>` is ignored. It carries no `check`. If it is lost, the participants end the run on their timers, since the initiator sends nothing more.

### One run at a time

An agent takes part in one run at a time. A run is active for an agent from the moment it accepts `<concord-init>` until it has reached its outcome (`SUCCESS`, `BEST_EFFORT` or `ABORTED`) and, unless it is the reporter, holds the result of the reporter, the confirmation included. An aborted or cancelled run ends at once. Keeping the state of an ended run, and answering errors from it, does not count.

An agent that is not listed in `<participants>` ignores a `<concord-init>`. When a listed agent receives one for a run it does not know:

| The agent is | It does |
|---|---|
| in no active run | Accepts the run and starts it |
| in an active run | Sends `<concord-error what="init" reason="busy">` to the author of the init |
| the initiator of a run that still waits for init replies | Compares the two run ids. If its own is lower, it sends `busy`. Otherwise it sends `<concord-cancel>` for its own run, naming the new one in `superseded_by`. Its own run ends `ABORTED`, and it joins the new run |

Run ids are compared as UUID text in lower case, and the lower one continues. This settles two inits sent at about the same time: each initiator sees the other's init, and only the one with the higher id gives way. A `<concord-init>` for a run the agent already knows is ignored. The initiator's re-send of init arrives inside a `<concord-resend>` and is answered as described under `<concord-init>`; for a run the agent does not know, the init inside it is treated like any other init.

A `busy` error does not end the wait of the initiator. It keeps its timer and re-sends `<concord-init>` as usual, so an agent that becomes free can still join. An agent that stays busy is marked missing after `retries`, like any silent agent.

This keeps every agent in at most one run, and every run ends. It does not promise that a run completes: in a race both runs can abort, and the initiators start again with new ids. The run id rule needs an initiator that is a participant. Two initiators that are not participants do not arbitrate; each participant takes the first init it sees and refuses the other as busy, so the runs may split the participants and both end `ABORTED`.

### Confirmation

The reporter's `<concord-result>` is what confirms a run (see [`<concord-result>`](#concord-result)). These rules say how it is waited for and compared.

The result of the reporter is owed to every other agent, and the usual timer applies (see [Messages owed](#messages-owed)). A participant that is asked for a result it has not sent, with `<concord-error reason="missing" what="result">`, sends its own at once if it has computed its outcome, or as soon as it has. An agent whose reporter has changed asks the new reporter this way, so the new reporter does not have to know that it has become one. It is not a vote: a result never changes the outcome an agent has computed. A result that arrives before the agent has computed its own is recorded and compared later.

An agent compares each result it receives with its own. Two results differ when their outcomes differ, when both outcomes are `SUCCESS` (or both `BEST_EFFORT`) and the winning candidates differ, or when their `missing` lists differ; the agent compares the list in the result with the one it had when it computed its own outcome. Scores are not compared. When they differ, the agent sends `<concord-error reason="mismatch" what="result">` to the author. No re-send answers it: it only tells the author that another agent ended differently. Each agent records every mismatch in its report (see [Run report](#run-report)). A run in which any agent finds or receives a mismatch is not trusted: no result of it is authoritative, and it is known that the protocol did not run correctly.

The run ends for a participant that is not the reporter when it holds the result of its reporter. The reporter itself ends when it has sent its result; a mismatch error that reaches it later is added to its report. A result that comes from another participant, for instance from one that sent it because it saw the first reporter missing, is compared and recorded as well, and does not end the run alone. If the result of the reporter is still missing after `retries` requests, the agent marks it missing and the next participant becomes the reporter for it, with the effects described in [Timeouts and missing agents](#timeouts-and-missing-agents): its own outcome is not recomputed.

---

## Transport

CONCORD does not depend on a particular network, but it asks the network below it for one thing: every message an agent sends reaches every other agent of the run, and no message travels in a loop. The protocol does not discover the network, does not relay and does not describe it in any message. The network belongs to the run: it is made of the agents that take part in it and, in a star, of the centre. One of these three shapes is enough:

- **A broadcast channel.** Every agent sends to the channel and reads from it. Nothing relays.
- **A full mesh.** Every agent has a link to every other agent and sends each message on all its links. Nothing relays.
- **A star.** Every agent, a spoke, has a link to one central agent, the centre. A spoke sends each message to the centre only. The centre sends the messages of the runs it knows on to every spoke except the one it came from. As the one relay, it cannot create a loop. The centre can also be a participant, or the initiator, of the run. It then does two separate things with each message: it passes it on, and it processes it like any other agent, and its own messages go to every spoke. Passing on is mechanical: it never changes the message, and it does not depend on what the centre does with it as an agent. The centre passes a message on even when it drops it as a duplicate, ignores it, has marked its sender missing, or has ended or aborted the run, since the spokes behind it still need it.

  The centre knows a run once it has passed on that run's `<concord-init>`, bare or inside a `<concord-resend>`, since the spokes learn the run only from that message. It passes on a message of any other run only if it knows it, so traffic of runs it has never seen goes nowhere. To decide, it reads only the name and the `run_id` of the outermost element, and, for a `<concord-resend>`, the name of the element inside. It keeps the run ids it knows for as long as it keeps the state of the run, ended runs included, so that errors and re-sends after the end still reach the spokes.

The centre should pass messages on as fast as possible, since the agents behind it have timers running.

Several runs can share a network, since an agent ignores the messages of a run it does not know, and, within a run, those of an agent that is not listed in `<participants>` (see [Order of checks](#order-of-checks)). The only agent outside that list that sends messages is the initiator, when it did not list itself: it sends the init and its re-send, errors and a cancel.

---

## Invariants

These summarize rules stated in the sections above. Where wording differs, the section applies.

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*` or was marked missing before the outcome was computed. A participant marked missing later is listed in the report, and the run is not reported as confirmed while a participant is missing.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are fixed.** An agent's anchors are the ones in its first init reply. A later, different init reply is ignored, since replacing anchors would invalidate every earlier comparison.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; nobody chooses it. A repair from any other agent for that round is ignored.
5. **Messages are sent verbatim.** Every agent sends message content exactly as produced — no preamble, rewording, or added template.
6. **Run to complete.** Once a run has started it ends, for each agent, with one of the outcomes (`SUCCESS`, `BEST_EFFORT` or `ABORTED`); it never ends without one.
7. **Termination is distributed.** Each participant computes its outcome independently from the same messages and compares it with the reporter's result. A difference is reported, never negotiated.
8. **A damaged document is never used.** A document that fails its `check` is not recorded; it is asked for again.
9. **Silence removes the agent.** An agent never continues on an incomplete copy of the run state: if a message it is owed has not arrived after `retries` requests, it marks the author missing and goes on without it, never declares `SUCCESS`, and aborts only when fewer than two participants are left.
10. **One message per item.** An agent sends each message once and may re-send only the stored original, and only its author does. A different message from the same author for an item already recorded is ignored.
11. **One uuid per transmission.** Every send, a re-send included, has a new `uuid`, and an agent processes an accepted one at most once.
12. **One run at a time.** An agent is in at most one active run and refuses a second init as busy. Two initiators that both receive the other's init settle it by run id, and the lower one continues.
13. **One version.** Every message carries the protocol version. An agent drops a message of another version, and does not join a run whose init has one.
