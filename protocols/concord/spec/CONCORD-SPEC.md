# CONCORD Protocol Specification

Version 0.1.0 <!-- x-release-please-version -->

## Abstract

CONCORD is a multi-agent negotiation protocol that produces a single outcome satisfying agents with different, potentially conflicting criteria. Given a set of participant agents and a shared candidate schema, the protocol iteratively proposes, scores, and repairs candidates until either every agent clears a satisfaction threshold (SUCCESS) or a budget of repair rounds is exhausted (BEST_EFFORT). If a participant stops answering, a participant refuses the run because it speaks another protocol version, or the run is cancelled, it ends in error (ABORTED) for the agents that are still running.

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

- **The initiator** is the agent that starts the run by sending `<concord-init>`. If it is listed in `<participants>`, it is also a participant; otherwise it does not negotiate. It sends `<concord-init>` again when init replies are missing (see [Timeouts and abort](#timeouts-and-abort)), and it receives the result of the run so that it can report how the run ended.
- **Participants** are the agents listed in `<participants>`, at least two. Each holds a role-specific scoring perspective, maintains its own copy of the run state, and decides for itself when to score, repair or compute its outcome.

The state machine is distributed. Each participant moves as soon as its own copy of the run state says it is its turn. It scores once the seeds have reached it, and repairs when every score is in and it is the bottleneck. Participants detect gaps and errors in the protocol through their own copy of the run state. The protocol allows for resending lost messages, and aborts the run when a participant stops responding (see [Timeouts and abort](#timeouts-and-abort)).

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

`satisfaction_threshold` and `eps` are written as numbers in the form given in [Numbers](#numbers), and the range in the table is checked on the value read from that text.

`x_reject` and `x_ideal` must describe outcome states ("error rate returns to 0"), not proposals or mechanisms, as an anchor that names an action biases the scoring scale.

---

## Protocol Phases

### State Machine

```
START
 └─ INIT   (the initiator sends <concord-init> to all participants)
      every participant answers with one init reply
      all init replies received
      └─ each participant scores the seeds of the others independently
           all scores received
           each participant evaluates
           ├─ floor(x*) ≥ τ                    →  SUCCESS
           ├─ otherwise, t ≥ B                 →  BEST_EFFORT
           └─ otherwise, the bottleneck self-identifies and sends one repair message (proposal + own score)
                received by all participants; the others score independently
                └─ (repeat evaluate)

SUCCESS or BEST_EFFORT  →  the reporter (the first participant in `<participants>`) sends its <concord-result>;
                           the others wait for it and compare it with their own outcome
an agent stays silent after retries requests, an init is refused for its version, or the run is cancelled
                           →  every agent still running aborts (ABORTED), each telling the others with a <concord-cancel>
```

At any point where an agent waits for a message it is owed, a silent author ends the run `ABORTED` for every agent that has not ended (see [Timeouts and abort](#timeouts-and-abort)). When a participant has computed its outcome, with any outcome, it works out who the reporter is (see [`<concord-result>`](#concord-result)). The reporter sends its `<concord-result>`. Every other participant runs the confirmation: it waits for the result of the reporter and compares it with its own. The run is over for an agent only when the confirmation is complete, unless it aborts first.

### Phase Entry Conditions

| Phase | Entry condition |
|---|---|
| INIT | Protocol start |
| SCORE | Each participant scores the seeds of the others once every init reply is in, in one `<concord-score>`. After the seeds it scores each repair as soon as it has it. A participant rates its own candidates inside the proposal |
| REPAIR | Each participant's own copy of the run state shows floor(x\*) < τ and t < B; the bottleneck self-identifies and proposes |
| COMPLETE | Each participant independently computes the outcome once its own copy of the run state holds every rating; the reporter then sends its `<concord-result>` |
| CONFIRM | Each participant that is not the reporter has its own outcome and holds the reporter's `<concord-result>`; the run ends for it when it has compared them |

The initiator opens the run with `<concord-init>`. After that the only communication is the participants' own messages (`<concord-init-reply>`, `<concord-score>`, `<concord-repair>` and `<concord-result>`) and the control messages `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. Nothing requests a score or a repair: each participant determines its own obligations from the messages it has received.

---

## Scoring Model

The terms used throughout this section and the rest of the document: the pool is the set of candidates of the run, the seeds and every repair so far; `floor(x)` is the lowest normalized score any participant gave candidate `x` (see [Raw vs Normalized](#raw-vs-normalized)); `t` is the number of repair rounds so far, which counts every repair proposal, whether or not it replaced the incumbent.

### Raw vs Normalized

Participants always report raw scores on a personal 0–100 scale anchored at `raw_reject` and `raw_ideal`. A score is normalized to [0,1] with the anchors of the agent that gave it, taken from that agent's init reply, so all participants derive the same normalized scores:

```
s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0)
```

Raw scores and anchors are numbers from 0 to 100 in the form given in [Numbers](#numbers); a message with a value outside that range, or written in another form, is invalid (see [Invalid messages](#invalid-messages)). A normalized score can still fall outside [0,1] when a raw score lies beyond an anchor, and is then clamped. Only raw scores travel in messages. Participants must never send a normalized value as their own raw rating.

**Rounding.** Every agent must reach the same decisions, so the arithmetic is fixed. Each normalized score is computed exactly from the decimal values of the message, as a rational number, and then rounded to 6 decimal places, half up. `τ` and `eps` have at most 6 decimal places (see [Numbers](#numbers)), so they are exact. Every comparison below (floor, leximin vectors, ties, ACCEPT, SUCCESS and the bottleneck) uses the rounded values, and `i[k] + eps` is computed on them. An implementation compares them as integers in millionths, so that no floating-point error can change a decision.

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

there is a position k with c[k] >= i[k] + eps,
and c[j] >= i[j] for every position j before k
```

where `c` and `i` are the leximin vectors of `x_cand` and `x*` (the normalized scores of all participants, sorted from the lowest).

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
| `ABORTED` | An agent stays silent after `retries` requests (see [Timeouts and abort](#timeouts-and-abort)); a participant refuses the init because of its protocol version (see [Version](#version)); or the run is cancelled (see `<concord-cancel>`) |

Each participant computes its outcome from its own copy of the run state, and the reporter's `<concord-result>` confirms it (see [`<concord-result>`](#concord-result)). A reader that is not a participant learns how the run ended from that message. An aborted run has no outcome to confirm: an agent that aborts sends no result. A run has ended for a participant that is not the reporter when it has its outcome and holds the reporter's result, and for the reporter when it has sent its result. The outcome of an ended run is final: a later cancel never changes it. Before that an agent has an outcome but is still running, and if it gives up it ends `ABORTED`.

### Run report

The report of a run is not a message: nothing is sent. It is complete once the confirmation is. It contains:

| Field | Content |
|---|---|
| outcome | `SUCCESS`, `BEST_EFFORT` or `ABORTED` |
| winning candidate | The id and text of `x*`. None for `ABORTED` |
| final scores | Each participant's normalized score on `x*` |
| shortfall | Each participant below τ, and by how much; empty for `SUCCESS` |
| rounds | The number of repair rounds used |
| reason | For `ABORTED` only: "missing-agent" with the agent that was named, "superseded", or "version" (the initiator also gives the agent and the two versions) |
| results compared | The reporter's result and whether it matches this one (for the reporter, its own result); the mismatch errors sent and received (see `<concord-result>`) |
| status | `confirmed`, `unverified` or `disputed`, as below |

The status is `confirmed` when the result of the reporter matches the agent's own outcome. The reporter receives no result to compare, so its status is `confirmed` as long as no mismatch error or cancel reaches it. It is `unverified` for an initiator that is not a participant, which has no outcome of its own to compare, unless it receives a mismatch error. It is `disputed` when a mismatch was found, sent or received, or when an agent whose run has ended with an outcome receives a cancel; no result of a disputed run is to be relied on.

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

An agent id, in `from`, `to`, `agent` and `<participants>`, the initiator's included, is 1 to 64 ASCII characters, each a letter, a digit, `_` or `-`. `run_id` and `uuid` are UUIDs written in lowercase hexadecimal, with hyphens. In the examples, `RUNID` and `OTHER-RUNID` stand for a run id.

### Numbers

A number in a message (a raw score, an anchor, `satisfaction_threshold`, `eps`) is written as one to three digits, optionally followed by a decimal point and one to six digits: `[0-9]{1,3}(\.[0-9]{1,6})?`. The digits are the ASCII characters `0` to `9`. `82`, `82.0`, `0.5` and `007` are valid. `.5`, `82.`, `82.1234567`, `+5`, `-0`, `1e2`, `NaN`, a number with a space or an underscore, and non-ASCII digits are not. The value is read from this text exactly, as a decimal, and a range is checked on that value: 0 to 100 for a score or an anchor, above 0 and at most 1 for `τ`, above 0 for `eps`.

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

The `run_id` attribute identifies the run. The initiator generates it as a UUID when it creates the run, so that it differs from the id of any other run on the same network. `<participants>` lists the full negotiating set, in order. The order matters: the participants listed first win ties in the decision rules, and the first one is the reporter. The initiator should order the list with this in mind.

The anchors establish each participant's personal raw scoring scale. Each participant proposes from its own role's perspective, without pre-compromising for other roles.

If init replies are missing, the initiator sends `<concord-init>` again (see [Timeouts and abort](#timeouts-and-abort)). A participant that receives it again after answering sends its stored init reply again, in a `<concord-resend>` with no `reply_to`. A participant that has not answered yet answers now.

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

A schema needs a `description` or an `enum`. The supported keywords are `type` (only `"string"`), `enum`, `description`, `title`, `pattern` (a regular expression of the subset below), `minLength` and `maxLength` (counted in Unicode code points). Any other keyword is refused when the run is created, so a rule is never silently left unenforced. The JSON is ordinary XML text: `<`, `>` and `&` inside it are escaped, a `"` is not.

A `pattern` uses only this subset, so that every agent reads it the same way, whatever its language. It is made of literal characters; character classes `[…]` with literal characters and ranges, where a `^` right after the `[` negates the class; groups `( )` and `(?: )`; alternation `|`; the quantifiers `*`, `+`, `?`, `{n}`, `{n,}` and `{n,m}`; and the anchors `^` and `$`. A backslash either escapes one punctuation character so that it stands for itself (`\.`, `\\`, `\-`), or forms `\d` (`[0-9]`), `\w` (`[A-Za-z0-9_]`) or `\s` (space, tab, carriage return, line feed). A `.` is any character except carriage return and line feed. `^` is the start of the candidate and `$` is its end; `$` does not match before a final line feed. The candidate matches if the expression is found anywhere in it, unless it is anchored, and characters are Unicode code points. Anything else is outside the subset: lookahead and lookbehind, backreferences, named groups, `\b`, `\p{…}`, `\u` escapes, and flags of any kind. A pattern outside the subset is refused like an unsupported keyword.

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

Only the bottleneck of the round sends a repair, and only when one is owed: the incumbent's floor is below τ and `t < B`. A repair from any other agent, or when none is owed, is ignored (see [Invalid messages](#invalid-messages)).

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

Every participant rates every candidate in the pool on the same raw 0–100 scale used at anchoring. It rates its own candidates inside the proposal, in the init reply or the repair, and the others' in `<concord-score>`. A score message covers the candidates of the others that the sender has not rated yet: once the init replies are in it holds one entry per seed of another participant, and after a repair it holds a single entry, for the new candidate. A `<concord-score>` never covers a candidate its sender proposed: an entry for one is ignored. Reasoning and rationale text is the only channel through which a participant's private domain knowledge reaches the other participants, so participants should state everything relevant, including evidence against a candidate. For the same reason, everything a participant writes in a message is read by every agent of the run, so a participant must not write what it needs to keep private.

### `<concord-result>`

Sent once, by the reporter, as soon as it has computed its outcome. It is the confirmation of the run: every agent learns how the reporter ended, and the participants compare it with their own outcome, so that all of them can be sure they reached the same conclusion.

The reporter is the first participant in `<participants>`. This is the same order that breaks ties in the decision rules, so every agent knows who the reporter is from the init alone. If the reporter stays silent, the run is aborted like for any other silent agent (see [Timeouts and abort](#timeouts-and-abort)); nobody takes its place.

```xml
<concord-result version="0.3.0" from="cost" run_id="RUNID" uuid="c5e81a3f-92d4-47b6-8e0a-1f7d3c5b9a26" check="d4e5f6a7">
  <outcome>SUCCESS</outcome>
  <winning_candidate_id>seed-cost</winning_candidate_id>
</concord-result>
```

`outcome` is `SUCCESS` or `BEST_EFFORT`. `winning_candidate_id` is `x*`: for `SUCCESS` the candidate that every participant rates at or above τ, and for `BEST_EFFORT` the incumbent, which does not clear τ for everyone. An aborted run sends no result.

How the other participants compare the result with their own, and what happens when it is lost, is described under [Confirmation](#confirmation).

---

## Reliability and Liveness

The core protocol above works when every message arrives, intact and once, and every participant answers. This part says what the agents do when that is not so: how a message is checked, asked for again and sent again, how a silent participant ends the run for everyone, and how one agent is kept in one run. It adds three control messages, `<concord-error>`, `<concord-resend>` and `<concord-cancel>`. The rules of the core still decide the outcome; the rules here decide which messages and which participants reach the core.

The sections follow the path of a message and of a missing one: first the order in which an agent handles a message, then liveness (what is owed, the timers, lost messages), then the validity of a message (check, invalid, version, duplicates), then recovery and exclusivity (error, resend, cancel, one run at a time, confirmation).

### Order of checks

An agent handles a message in this order, and stops at the first step that rejects it:

1. The run is known, the sender is a participant of the run or its initiator, and the sender is not the agent itself: on a broadcast channel an agent hears its own messages, and ignores them. Anything that is not a CONCORD message, because it does not parse or its root element is not one of these, is ignored here too. A `<concord-init>` for a run the agent does not know is not rejected here: it goes through steps 3 to 5 first, and the agent accepts the run only if it passes (see [One run at a time](#one-run-at-a-time)).
2. The `uuid` is not one the agent has accepted.
3. The `version` is its own. The one exception is a `<concord-error reason="version">`, which is read whatever its version (see [Version](#version)).
4. The `check` is correct, for the messages that carry one.
5. The message is valid (see [Invalid messages](#invalid-messages)).
6. The message is recorded and its `uuid` kept, and the agent acts on it.

For a `<concord-resend>`, steps 2 and 3 apply to the wrapper by its `uuid` and `version`, since it carries no `check`, and steps 3 to 5 apply again to the message inside, by its own `version` and `check`.

### Messages owed

An agent is owed a message when its copy of the run state shows that another agent should produce a message. The timer for that message starts as soon as the state machine enters the state that makes it owed.

| When | The agent is owed |
|---|---|
| After `<concord-init>` | An init reply from every participant |
| Once every init reply is in | A score from every participant, on every seed other than its own (its score on its own seed came with the init reply) |
| Once the incumbent's weakest score is below τ and `t < B` | A repair from the bottleneck (its proposal contains the bottleneck's own score) |
| Once the repair is in | A score on the new candidate from every other participant |
| Once the agent has computed its own outcome, and it is not the reporter | The result of the reporter |
| For an initiator that is not a participant | The result of the reporter. It starts no timer for it |

An agent is never owed its own messages. An initiator that is not a participant is owed the init replies and the result, and the other agents are not owed anything from it. It computes no outcome of its own and cannot tell when the negotiation ends, since the negotiation can take longer than one timeout, so it keeps no timer for the result: it takes the result of the reporter when it arrives, and it also ends when it receives a `<concord-cancel>`.

### Timeouts and abort

Every time an agent is owed a message (see [Messages owed](#messages-owed)) it starts a timer of `timeout` seconds, counted on its own clock. A message that arrives ends the wait.

- **Any agent.** When the timer expires, the agent sends a new `<concord-error reason="missing">` to the author and restarts the timer. When it expires after `retries` requests, the author is missing and the agent aborts the run.
- **The initiator, for init replies.** An agent that never received `<concord-init>` does not know the run and cannot answer an error. So when the timer for an init reply expires, the initiator sends `<concord-init>` again, inside a `<concord-resend>`, instead of an error, and restarts the timer. When it expires after `retries` re-sends, the participant is missing and the initiator aborts the run.

An agent waits at most `(retries + 1) × timeout` seconds for one message. `timeout` must be longer than the slowest turn of any agent. The network adds only a delay, which is much shorter.

**Aborting.** The run does not go on without a missing agent: the agents could not tell who is missing in the same way, and would reach different conclusions. The agent that finds an agent missing ends its run with outcome `ABORTED` and reason "missing-agent", naming that agent. It stops all its timers and sends `<concord-cancel reason="missing-agent" agent="X">`, with the id of the missing agent, to the other agents. It sends nothing else: no result, and no answer to later messages of the run except errors that ask for a stored message.

Every agent that receives a cancel for its run and has not sent one yet does the same, whatever the reason and the agent that is named included: it ends its run with `ABORTED` and the same reason, and sends its own `<concord-cancel>` with the same reason and attributes, once. Each agent sends it at most once, so the notice does not circulate for ever, and every agent that learns of it passes it on. This gives the notice a good chance of reaching everybody when the network is partly cut: an agent that cannot be reached from the one that found the problem can still be reached by another agent. It does not matter which agent is named. If several agents find different agents missing at about the same time, each of them sends a cancel, and the report names the agent of the first cancel it learned of.

An agent whose run has already ended with an outcome, and that receives a cancel, keeps its outcome and sends its own cancel once, as above. Its report is `disputed` (see [Run report](#run-report)), since it cannot tell whether the other agents ended in the same way.

`ABORTED` certifies no candidate. A data message that arrives after the run has ended or aborted is ignored. When the network fails, for instance when the centre of a star stops, the agents are cut off from each other, and each ends its run on its own timers.

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

It is the author's responsibility to validate a message before it stores and sends it, so an invalid message should never reach a receiver. A candidate must satisfy the candidate schema, `raw_ideal` must exceed `raw_reject`, and every raw score and anchor must be a number from 0 to 100 in the form given in [Numbers](#numbers). Any message is invalid when its `uuid`, or a `reply_to`, `ref` or `superseded_by` it carries, is not a UUID in lowercase hexadecimal with hyphens, or when an agent id in its `to` or `agent` attribute does not follow the rule of [Attributes of messages](#attributes-of-messages). A message whose `run_id` is not such a UUID names no run the agent knows, and is ignored at the first step of the [Order of checks](#order-of-checks). A `<proposal>` is invalid when its `id` is not `seed-<from>` in an init reply, or not `r<N>-<from>` in a repair with `N` the number of the repair round (see [Candidate ID Scheme](#candidate-id-scheme)). A `<concord-repair>` is invalid when its sender is not the bottleneck of the round, or when no repair is owed (see [`<concord-repair>`](#concord-repair)). A receiver whose copy of the run state is incomplete first asks for the messages it lacks before it judges a repair. In a `<concord-score>`, an entry that rates a candidate its sender proposed, or a candidate the sender has already rated, is ignored, and the other entries are kept. A rating of a candidate the receiver does not hold is not invalid: the proposal was lost (see [Lost messages](#lost-messages)). A `<concord-init>` is invalid when it breaks a rule of [Configuration Parameters](#configuration-parameters) or the candidate schema rules: fewer than 2 participants or a repeated one, an agent id that does not follow the rule of [Attributes of messages](#attributes-of-messages), a number that is not in the form given in [Numbers](#numbers), a `pattern` outside the subset of [Candidate schema](#candidate-schema), a value out of its range, a missing required element, or an unsupported schema keyword. A `<concord-result>` is invalid when its sender is not the reporter, when its `outcome` is neither `SUCCESS` nor `BEST_EFFORT`, or when its `winning_candidate_id` is not a candidate of the run; a receiver whose copy of the run state is incomplete first asks for the messages it lacks before it judges the winner. A `<concord-error>` or `<concord-resend>` that does not follow its schema is dropped: it is not recorded, nobody answers it and it aborts nothing, and the message inside a `<concord-resend>` follows the rules of its own kind. A `<concord-cancel>` is invalid when its `reason` is not `superseded`, `version` or `missing-agent`, when its `reason` needs an attribute that is missing (`superseded_by` for `superseded`, `agent` for `missing-agent`), or when its `agent` is not a participant of the run. A message that fails validation is not stored and not sent: the author creates it again. If a message arrives changed, the receiver's `check` catches it (see [Integrity check](#integrity-check)).

The receiver validates what it receives as well, which costs little. A message that is invalid, although it passed its check, is ignored: it is not recorded, nobody answers it and it aborts nothing. The message stays owed to the receiver, whose timer asks for it again, and the run ends like for any silent agent if no valid message comes (see [Timeouts and abort](#timeouts-and-abort)). Since every author validates before it sends, this happens only with an agent that does not follow the protocol, or with agents that disagree on a rule. A receiver whose copy of the run state is incomplete first asks for the messages it lacks before it judges a repair or a result.

An invalid `<concord-init>` is the one invalid message that gets an answer: the receiver does not join, and sends `<concord-error what="init" reason="invalid">` to the author, with the reason in the text of the error. Nobody answers it, and the initiator does not act on it: its timer sends the init again, like for any participant that stays silent.

### Version

Every message carries `version="0.3.0"` on its root element, and the one inside a `<concord-resend>` carries it too. The value is the version of the protocol on the wire. All agents of a run must use the same one.

An agent checks the `version` after the filters of [Order of checks](#order-of-checks) and before the integrity check and any other validation, since another version may define those differently. A message whose `version` is not exactly the agent's own, or that has none, is not processed and not recorded. What follows depends on the message:

- **A `<concord-init>`, bare or inside a `<concord-resend>`.** The agent does not join the run. It sends `<concord-error what="init" reason="version">` to the author of the init, and the text of the error says which version the agent speaks and which one it received. When the initiator receives this error from a participant of its run, it ends its run as `ABORTED` with the reason "version", and sends `<concord-cancel reason="version">` so that the participants that already joined end it too.
- **Any other message of a run the agent is in.** The sender goes unheard, and the agent's timers end the run like for any silent participant (see [Timeouts and abort](#timeouts-and-abort)). Once an agent has joined, this should not happen, since an agent does not change version during a run.

For the error to be understood, `<concord-error reason="version">` is the one message that is read whatever its `version`. The receiver relies only on its root attributes `from`, `run_id` and `reason`, and on its text, so these attributes stay the same in every version of the protocol. The initiator accepts it only from a participant of the run. For a `<concord-resend>`, the wrapper and the message inside are both checked.

### Duplicates

An agent keeps the `uuid` of every transmission it has accepted, for as long as it keeps the state of the run. When a message arrives whose `uuid` is one of them, the agent drops it at that point of the [Order of checks](#order-of-checks): no version check, no integrity check, no answer and no error. This covers a message that the network delivers twice, so that it is acted on once. Only the `uuid` of the outermost element counts. A `<concord-resend>` is checked by its own `uuid`, and the stored message inside it keeps its original `uuid`, so it is not taken for a duplicate of a damaged copy.

A transmission is accepted, and its `uuid` kept, only when the agent has processed it: it passed the version and the integrity check and was valid. A damaged or invalid copy is not kept, so an intact copy with the same `uuid` that arrives later is still processed. A transmission that is ignored for another reason, such as an unknown run or a sender that is not a participant, is not kept either, since the agent holds no state for it.

### `<concord-error>`

Reports a problem with a message. It mostly asks for the message to be sent again: it never arrived, or it arrived damaged. It is also how an agent refuses a `<concord-init>` because it is busy (see [One run at a time](#one-run-at-a-time)) or because it speaks another version (see [Version](#version)), how it tells an agent that its result differs from another's, and how it tells an initiator that its init is invalid. A missing or damaged result is asked for like any other message. Any agent that finds the problem sends it, the initiator included.

```xml
<concord-error version="0.3.0" run_id="RUNID" from="cost" to="security" uuid="3a8c1f5d-7e24-4b90-a6d2-c1e0f9b84a73" what="score" candidate="seed-cost" reason="missing">
  I am missing the score from security for seed-cost. If you are security, please send it again.
</concord-error>
```

| Attribute | Meaning |
|---|---|
| `to` | The author of the message that is wanted; for `reason="mismatch"`, the author of the result that differs |
| `what` | `init-reply`, `score`, `repair` or `result`: what is wanted. `init` goes only with `reason="busy"`, `reason="invalid"` or `reason="version"`: the message concerned is the init itself. `result` is the confirmation; with `reason="mismatch"` no re-send answers it |
| `candidate` | Required when `what` is `score` or `repair`, except with `reason="check"`: the id of the candidate whose score, or whose repair proposal, is missing, for example `r1-security`. Omitted for `init`, `init-reply` and `result` |
| `ref` | Required with `reason="check"`: the `uuid` of the damaged document itself, which is the inner message when it arrived inside a `<concord-resend>`, since only that one matches a stored original. Its content cannot be read to name the candidate. If that `uuid`, or the author, cannot be read, the receiver sends nothing and the missing message is asked for when its timer expires |
| `reason` | `missing` (never arrived), `check` (arrived, failed its check), `invalid` (an init that arrived intact but breaks a rule; the text says why), `mismatch` (a result differs from the sender's), `busy` (the sender is in another run and refuses this init) or `version` (the sender speaks another protocol version and refuses this init) |

Besides the attributes above, an error carries the common attributes; its `uuid` is what the re-send names in `reply_to`. Each `<concord-error>` is a new transmission with its own new `uuid`, also when it repeats an earlier request after a timeout, since a receiver drops a `uuid` it has already accepted. The author answers each one with a `<concord-resend>` that names that `uuid` in `reply_to`. The text inside is optional and for readers only; for `reason="invalid"` it states why the message was not recorded, and for `reason="version"` it gives the two versions.

Only the author answers an error, and every other agent ignores it, except for `reason="mismatch"`, which every agent reads. The author sends the stored original, word for word, wrapped in a `<concord-resend>` whose `reply_to` is the `uuid` of the error. The exceptions are `reason="mismatch"`, `reason="busy"`, `reason="invalid"` and `reason="version"`, which nobody answers: they only inform the author (the initiator acts on `version`, see [Version](#version)). In the other direction, a `<concord-result>` that its author has not sent yet is sent now, as there is no stored original (see [`<concord-result>`](#concord-result)).

An agent sends each data message (init, init reply, repair, score, result) once and keeps it. It may re-send only that stored original and never writes a new version of a message it has already sent. A receiver that has recorded a message ignores a different one from the same author for the same item (an init reply; a score for the same candidate; a repair with the same id): the first one recorded stands.

An agent keeps the state of a run, including the messages it sent and recorded, after the run has ended. If it receives a `<concord-error>` addressed to it for a run that is no longer active, it looks for the stored state of that run id and answers from it if it exists.

A re-send answers an error when it carries the message the error names: the same author, the same kind, and the same candidate, or, for `reason="check"`, the message whose `uuid` is in `ref`. Its `reply_to` ends the wait of the agent that asked. An agent that already holds the message ignores the copy. A score message that covers several candidates is re-sent whole; it answers the error for each candidate it contains. An init reply and a repair also contain the proposer's own score, so each answers an error for that score too.

An agent sends `reason="missing"` when it has waited `timeout` seconds for a message it knows is owed, or when its copy of the run state has a gap that a later message reveals (see [Lost messages](#lost-messages)). It sends `reason="check"` when a document fails its check, and `reason="invalid"` when an init it has not joined is invalid. Each `missing` or `check` request counts against `retries`.

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

Ends the run for every agent. It has three reasons, which say where it starts:

- `superseded`: the initiator ends its own run while it still waits for init replies, because another `<concord-init>` has arrived that wins by the run id rule (see [One run at a time](#one-run-at-a-time)).
- `version`: the initiator ends its run because a participant has refused the init because of its protocol version (see [Version](#version)).
- `missing-agent`: an agent of the run has found an agent missing (see [Timeouts and abort](#timeouts-and-abort)).

Whatever the reason, an agent that receives a cancel for its run for the first time ends the run and sends its own cancel, once, with the same reason and the same attributes. This is how the notice reaches an agent that cannot be reached from the one that started it.

```xml
<concord-cancel version="0.3.0" from="alice" run_id="RUNID" uuid="8f3a1c52-6d07-4e9b-b4a8-2c5d7e0f1a36" reason="superseded" superseded_by="OTHER-RUNID"/>
```

```xml
<concord-cancel version="0.3.0" from="cost" run_id="RUNID" uuid="e1c4a7d2-3b58-4f09-8a6e-9d2b5c7f0a13" reason="missing-agent" agent="security"/>
```

| Attribute | Presence | Meaning |
|---|---|---|
| `reason` | always | `superseded` (another run continues), `version` (a participant refused the init because of its version), or `missing-agent` (an agent stayed silent) |
| `superseded_by` | with `reason="superseded"` | The run id of the run that continues |
| `agent` | with `reason="missing-agent"` | The id of the agent that was found missing |

A participant that receives it ends the run as `ABORTED`, at whatever stage the run is, with the reason of the cancel: `superseded`, `version` or `missing-agent`. The initiator's own run ends the same way. An agent whose run has already ended with an outcome keeps it (see [Timeouts and abort](#timeouts-and-abort)). An aborted run is the one exception to the confirmation: nobody sends a `<concord-result>` for it. A cancel is accepted from any participant of the run and from its initiator. A cancel for a run the agent has no state for, for example one that arrives before the init, is ignored. A cancel carries no `check`. If a cancel is lost, the agents that did not get it end the run on their own timers, since the agent that aborted sends nothing more.

### One run at a time

An agent takes part in one run at a time. A run is active for an agent from the moment it accepts `<concord-init>` until it has reached its outcome (`SUCCESS`, `BEST_EFFORT` or `ABORTED`) and, unless it is the reporter, holds the result of the reporter, the confirmation included. An aborted or cancelled run ends at once. Keeping the state of an ended run, and answering errors from it, does not count.

An agent that is not listed in `<participants>` ignores a `<concord-init>`. When a listed agent receives one for a run it does not know:

| The agent is | It does |
|---|---|
| in no active run | Accepts the run and starts it |
| in an active run | Sends `<concord-error what="init" reason="busy">` to the author of the init |
| the initiator of a run that still waits for init replies | Compares the two run ids. If its own is lower, it sends `busy`. Otherwise it sends `<concord-cancel reason="superseded">` for its own run, naming the new one in `superseded_by`. Its own run ends `ABORTED`, and it joins the new run |

Run ids are compared as UUID text in lower case, and the lower one continues. This settles two inits sent at about the same time: each initiator sees the other's init, and only the one with the higher id gives way. A `<concord-init>` for a run the agent already knows is ignored. The initiator's re-send of init arrives inside a `<concord-resend>` and is answered as described under `<concord-init>`; for a run the agent does not know, the init inside it is treated like any other init.

A `busy` error does not end the wait of the initiator. It keeps its timer and re-sends `<concord-init>` as usual, so an agent that becomes free can still join. An agent that stays busy ends the run after `retries`, like any silent agent.

This keeps every agent in at most one run, and every run ends. It does not promise that a run completes: in a race both runs can abort, and the initiators start again with new ids. The run id rule needs an initiator that is a participant. Two initiators that are not participants do not arbitrate; each participant takes the first init it sees and refuses the other as busy, so the runs may split the participants and both end `ABORTED`.

### Confirmation

The reporter's `<concord-result>` is what confirms a run (see [`<concord-result>`](#concord-result)). These rules say how it is waited for and compared.

The result of the reporter is owed to every other participant, and the usual timer applies (see [Messages owed](#messages-owed)). A reporter that is asked for the result, with `<concord-error reason="missing" what="result">`, sends it at once if it has computed its outcome, or as soon as it has. It is not a vote: a result never changes the outcome an agent has computed. A result that arrives before the agent has computed its own is recorded and compared later.

An agent compares the result it receives with its own. Two results differ when their outcomes differ, or when both outcomes are `SUCCESS` (or both `BEST_EFFORT`) and the winning candidates differ. Scores are not compared. When they differ, the agent sends `<concord-error reason="mismatch" what="result">`, addressed to the author of the result but read by every agent of the run, so that all of them know that the outcome is in doubt. No re-send answers it. Every agent that sends or receives a mismatch error records it in its report (see [Run report](#run-report)), also after its own run has ended. A run in which any agent finds or receives a mismatch is not trusted: no result of it is authoritative, and it is known that the protocol did not run correctly.

The run ends for a participant that is not the reporter when it holds the result of the reporter. The reporter itself ends when it has sent its result; a mismatch error that reaches it later is added to its report. If the result of the reporter is still missing after `retries` requests, the agent aborts the run like for any silent agent, even though it has computed its outcome (see [Timeouts and abort](#timeouts-and-abort)). The agents that already hold the result have ended with their outcome, and learn of the abort only afterwards, in which case their report is `disputed`.

---

## Transport

CONCORD does not depend on a particular network, but it asks the network below it for one thing: every message an agent sends reaches every other agent of the run, and no message travels in a loop. The protocol does not discover the network, does not relay and does not describe it in any message. The network belongs to the run: it is made of the agents that take part in it and, in a star, of the centre. One of these three shapes is enough:

- **A broadcast channel.** Every agent sends to the channel and reads from it. Nothing relays.
- **A full mesh.** Every agent has a link to every other agent and sends each message on all its links. Nothing relays.
- **A star.** Every agent, a spoke, has a link to one central agent, the centre. A spoke sends each message to the centre only. The centre sends the messages of the runs it knows on to every spoke except the one it came from. As the one relay, it cannot create a loop. The centre can also be a participant, or the initiator, of the run. It then does two separate things with each message: it passes it on, and it processes it like any other agent, and its own messages go to every spoke. Passing on is mechanical: it never changes the message, and it does not depend on what the centre does with it as an agent. The centre passes a message on even when it drops it as a duplicate, ignores it, or has ended or aborted the run, since the spokes behind it still need it.

  The centre knows a run once it has passed on that run's `<concord-init>`, bare or inside a `<concord-resend>`, since the spokes learn the run only from that message. It passes on a message of any other run only if it knows it, so traffic of runs it has never seen goes nowhere. To decide, it reads only the name and the `run_id` of the outermost element, and, for a `<concord-resend>`, the name of the element inside. It keeps the run ids it knows for as long as it keeps the state of the run, ended runs included, so that errors and re-sends after the end still reach the spokes.

The centre should pass messages on as fast as possible, since the agents behind it have timers running.

The protocol sets no limit on the size of a message, but an author should keep each one small enough for its network: a message that the network cannot carry is never delivered, and the run ends on the timers. A message of at most 256 KiB should pass on any common network.

Several runs can share a network, since an agent ignores the messages of a run it does not know, and, within a run, those of an agent that is not listed in `<participants>` (see [Order of checks](#order-of-checks)). The only agent outside that list that sends messages is the initiator, when it did not list itself: it sends the init and its re-send, errors and a cancel.

---

## Invariants

These summarize rules stated in the sections above. Where wording differs, the section applies.

1. **No certification over silence.** SUCCESS is never declared if any participant has not submitted scores for `x*`.
2. **Threshold is fixed.** `satisfaction_threshold` never changes during a run.
3. **Anchors are fixed.** An agent's anchors are the ones in its first init reply. A later, different init reply is ignored, since replacing anchors would invalidate every earlier comparison.
4. **One proposer per round.** The bottleneck is determined algorithmically by each participant's own decision engine; nobody chooses it. A repair from any other agent for that round is ignored (see [`<concord-repair>`](#concord-repair)).
5. **Messages are sent verbatim.** Every agent sends message content exactly as produced — no preamble, rewording, or added template.
6. **Run to complete.** Once a run has started it ends, for each agent, with one of the outcomes (`SUCCESS`, `BEST_EFFORT` or `ABORTED`); it never ends without one.
7. **Termination is distributed.** Each participant computes its outcome independently from the same messages and compares it with the reporter's result. A difference is reported, never negotiated.
8. **A damaged document is never used.** A document that fails its `check` is not recorded; it is asked for again.
9. **Silence ends the run.** An agent never continues on an incomplete copy of the run state: if a message it is owed has not arrived after `retries` requests, it aborts the run and tells every other agent with `<concord-cancel reason="missing-agent">`, and every agent that receives a cancel, whatever its reason, does the same, once; an agent whose run has ended keeps its outcome and only passes the cancel on.
10. **One message per item.** An agent sends each message once and may re-send only the stored original, and only its author does. A different message from the same author for an item already recorded is ignored.
11. **One uuid per transmission.** Every send, a re-send included, has a new `uuid`, and an agent processes an accepted one at most once.
12. **One run at a time.** An agent is in at most one active run and refuses a second init as busy. Two initiators that both receive the other's init settle it by run id, and the lower one continues.
13. **One version.** Every message carries the protocol version. An agent does not process a message of another version. If it is the init, the agent refuses it with an error and the run is aborted at once; otherwise the sender goes unheard and the timers end the run.
