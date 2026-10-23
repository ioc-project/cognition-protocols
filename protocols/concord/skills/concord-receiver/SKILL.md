---
name: concord-receiver
description: >
  Handles incoming CONCORD protocol messages for a sub-agent participating
  in a multi-agent consensus round. Activate this skill immediately whenever you
  receive a CONCORD protocol message: one whose root XML tag is concord-anchor,
  concord-propose-seed, concord-score, concord-repair or concord-commit, or one
  that names a CONCORD anchor, seed, score or repair round and asks you to rate
  outcomes or candidates or to propose one, even if the XML is wrapped in other
  text. Each message type requires a specific XML response: this skill tells you
  exactly what to return. Run the forge script and reply with its output, never
  with your own prose or hand-written XML. Do not apply general judgement about
  whether to participate; if the message matches, follow this skill precisely.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.1.0" # x-release-please-version
---

# CONCORD — Receiver Skill

You are one agent in a group deciding on a single outcome. An orchestrator drives
the protocol; you reply only when it messages you.

**Your job:** rate candidates honestly against *your own role's criteria*, and —
when you are the least-satisfied agent — propose a better alternative.

**Your criteria** are the ones your role already gives you (your system prompt,
your assigned responsibility, e.g. "security", "cost", "latency"). CONCORD does
not supply them and does not ask you to change them.

**Your whole final message is the stdout of `concord_receiver.py forge …`, and nothing else.** Do your analysis
first, put it in the `--reasoning` / `--rationale` arguments, then run forge and return its output exactly as
printed. Never write the reply XML yourself, and never put text before or after it.

---

## When to activate

Your incoming message is an XML document whose root tag is one of:

```
<concord-anchor>
<concord-propose-seed>
<concord-score>
<concord-repair>
<concord-commit>
```

Match on the root tag, then follow the matching handler below. The XML may arrive wrapped in other text
(for example an introduction from the orchestrator): use the tag inside it. If there is no XML at all but the
message names a CONCORD anchor, seed, score or repair round and asks you to rate or propose, treat it as that
message type. In every case the reply is still the output of forge, never your own wording.

**This applies even in the middle of other work.** The first CONCORD message often arrives right after you
finished an ordinary task for the same orchestrator, such as an investigation or a report, and it can look like a
follow-up question about that work. It is not. Do not answer it with an assessment, a status table, a
recommendation, next steps or an acknowledgement. The only valid reply is the forge output for that message type:

| Incoming message | Your reply (root tag) | Forge command |
|---|---|---|
| `<concord-anchor>` | `<concord-anchor-reply>` | `forge anchor` |
| `<concord-propose-seed>` | `<concord-candidate-reply>` | `forge seed` |
| `<concord-score>` | `<concord-score-reply>` | `forge score` |
| `<concord-repair>` | `<concord-candidate-reply>` | `forge repair` |
| `<concord-commit>` | none | `forge commit` (optional) |

There is no `<concord-repair-reply>`, `<concord-seed-reply>` or `<concord-anchor-ack>`: seed and repair replies both
use `<concord-candidate-reply>`. Every message that expects a reply also carries an `<instructions>` element
restating the row above, so follow it. A reply that is not forge output is rejected, and the orchestrator has to
send you the same message again.

---

## Handling any CONCORD message: four steps

For every message you receive, follow these steps in order:

1. **Identify the message type.** Normally this is the root tag; match it against
   the handlers below (see "When to activate" for wrapped or tag-less messages).
   If it is none of these, do not reply.
2. **Process the message** per the matching handler — rate candidates, read the
   context, re-ground your scale from your anchor exchange in your context window.
   Every value you need is either in the message or in your prior context; your
   anchor calibration is not re-sent, so read it from the
   `<concord-anchor>` / `<concord-anchor-reply>` exchange earlier in this session.
   Never guess an anchor value you do not have in context.
3. **Call `concord_receiver.py forge <subcommand>`** with the arguments shown in the handler.
   This skill ships a `scripts/concord_receiver.py` helper; invoke it using whatever
   script-execution tool your environment provides. Always call it, for every message
   type, even when you think you know the format: hand-written replies are the most common
   cause of rejected replies. If you genuinely cannot run scripts, say so plainly instead
   of writing the XML yourself.
4. **Reply with the exact stdout from forge — verbatim, the whole thing, nothing else.**
   No preamble. No explanation. No markdown fence. No trailing summary.
   The orchestrator parses your reply as raw XML; anything outside the tags breaks
   parsing. Treat the stdout string as an opaque blob: copy it, do not retype or
   reconstruct it. Retyping reintroduces the bugs the script exists to prevent —
   an unescaped `<` in your reasoning (e.g. "error <0.5%") silently breaks the
   XML, and reconstructing the schema from memory tends to produce structurally
   invalid output (e.g. a `<candidate-id>` child element instead of a
   `candidate_id` attribute). Keep the escapes exactly as printed: `&lt;`, `&gt;` and `&amp;` must stay as they are,
   never turn them back into `<`, `>` or `&`.

**Correct reply** (exactly what `concord_receiver.py forge anchor --raw-reject 8 --raw-ideal 95` prints, nothing more):
```
<concord-anchor-reply>
  <raw_reject>8.0</raw_reject>
  <raw_ideal>95.0</raw_ideal>
</concord-anchor-reply>
```

**Wrong — preamble before XML:**
```
Here is my anchor reply:
<concord-anchor-reply>
  <raw_reject>8</raw_reject>
  <raw_ideal>95</raw_ideal>
</concord-anchor-reply>
```

**Wrong — analysis in prose, then XML written by hand with invented tags (forge was never run):**
````
Based on my analysis, x_reject is a 5 and x_ideal is a 92.

```xml
<concord-anchor-reply><x_reject>5</x_reject><x_ideal>92</x_ideal></concord-anchor-reply>
```
````
*(The tags are wrong: the orchestrator needs `raw_reject` and `raw_ideal`. The analysis belongs in the `--note` / `--reasoning` arguments.)*

**Wrong — prose instead of XML:**
```
My raw_reject is 8 and my raw_ideal is 95.
```

**Wrong — markdown fence around XML:**
````
```xml
<concord-anchor-reply>
  <raw_reject>8</raw_reject>
  <raw_ideal>95</raw_ideal>
</concord-anchor-reply>
```
````

**Wrong — XML dissected and re-arranged by hand:**
```
<concord-anchor-reply><raw_reject>8</raw_reject><raw_ideal>95</raw_ideal></concord-anchor-reply>
```
*(Even if structurally valid, any hand-editing risks escaping errors; always use forge output verbatim.)*

---

**Report what you actually believe.** The protocol's guarantee — every agent
above `satisfaction_threshold` when it succeeds — rests on honest scores.
Inflating to seem agreeable, or deflating for leverage, breaks it silently.
Never change `satisfaction_threshold` and never soften your own criteria to make
a candidate look acceptable. If you cannot be satisfied, say so — that is a
valid, useful outcome.

---

**Your reasoning is your only voice in this protocol.** Every other agent —
and the repair agent in particular — can only see what you write in
`--rationale` and `--reasoning`. Private knowledge you hold but do not
surface in those fields is invisible to every other participant, including
the repair agent deciding how to address your concerns. If a fact from your
domain would change how another agent proposes or repairs, you must state it
explicitly: the protocol has no other channel for it.

---

## You report raw ratings — never normalised ones

Rate everything on **your own raw 0..100 scale**, the one that is natural to
your criteria. Report that number.

Do **not** convert it to a `[0,1]` satisfaction score. The orchestrator does
that, from the two anchor ratings you give in `<concord-anchor>`:

```
s = clamp( (raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0 )
```

That normalisation is what makes your `0.6` mean the same degree of
role-satisfaction as every other agent's `0.6` — which is why it is computed
once, in one place, and not by each agent separately.

Your one obligation is that your raw scale stays **consistent for the whole
run**: the same quality of outcome must get the same number in round 7 as it did
at anchoring time. To keep your scale stable, re-read your own `<concord-anchor>`
exchange from earlier in this session before placing any new candidate: recall
what you gave `x_reject` and `x_ideal` and why, then place the new candidate
between them. Score messages no longer re-send your calibration — use your
context window.

Consistent scale is not the same thing as a frozen score. If a later
candidate's rationale directly addresses a concern you raised earlier — a
real change, not a restatement of something you already rejected — your
rating on that dimension should move to reflect it, round over round.
Refusing to budge from an earlier number once a genuine counter-argument has
been made is not honesty to your criteria, it's just inertia. You should
still be able to say in one sentence what specifically changed your mind.

Normalised scores in `[0,1]` do appear in the `<scores>` block inside the
`<incumbent>` element of a `<concord-repair>` message. Those are the normalised
scores of all agents on the current best candidate, for you to read.
Never echo one back as your own rating.

---

## Handlers

### `<concord-anchor>`

You receive `<satisfaction_threshold>`, `<x_reject>` (an outcome the shared context calls
unacceptable) and `<x_ideal>` (one it calls fully satisfying).

Rate **both** against your role criteria on your raw 0..100 scale. These two
numbers become your fixed reference points for the entire run.

- `raw_reject` is how satisfied your role would be **if the x_reject outcome happened**. It should be LOW.
- `raw_ideal` is how satisfied your role would be **if the x_ideal outcome happened**. It should be HIGH, above `raw_reject`.

They are satisfaction ratings, not measurements: do not put an error rate, a latency, a count or any other metric
quoted in the anchors into these fields.

You are rating two hypothetical outcomes, not the situation as it looks today. Whether the current state
resembles x_reject, or how bad things are right now, is not what these numbers measure. The common mistake is
answering "how true is this of the present state?": for example giving x_reject a 92 because things are
currently in that state. That inverts your scale and the orchestrator rejects it.

Forge your reply:
```
concord_receiver.py forge anchor --raw-reject NUMBER --raw-ideal NUMBER [--note TEXT]
```

`raw_ideal` must be strictly greater than `raw_reject`: the script refuses to forge the reply otherwise (equal
values are rejected too), and `--note` does not bypass that check. If it errors, you most likely rated the current
state or reported a metric: re-rate the two outcomes as hypotheticals, then run forge again.

**Example** (you are the "cost" agent, ratings 8 reject / 95 ideal):
```
concord_receiver.py forge anchor --raw-reject 8 --raw-ideal 95
```

**Example response:**
```
<concord-anchor-reply>
  <raw_reject>8.0</raw_reject>
  <raw_ideal>95.0</raw_ideal>
</concord-anchor-reply>
```

**Gotchas:**
- Report raw 0..100 — never a normalised `[0,1]` value.
- These numbers are fixed for the whole run; do not revise them in later rounds.
- `raw_ideal` must be strictly greater than `raw_reject`: `forge anchor` refuses an inverted or equal pair and `--note` does not bypass it. Re-rate the two hypothetical outcomes and run forge again — do not swap or fudge the values.

---

### `<concord-propose-seed>`

You receive the `<candidate_schema>`, `<satisfaction_threshold>`, and the problem `<context>`.
Always reply to this message with `concord_receiver.py forge seed`, never with `concord_receiver.py forge score`
— even if `<candidate_schema>` or `<context>` reads like it already lists two or more named
options to choose between. That reading means the schema is malformed, not that scoring is
now your job: this message type only ever has one valid reply. Pick or construct the single
candidate you believe best satisfies your own criteria and propose it in the schema's format;
do not shortcut to rating a candidate that hasn't been proposed by anyone yet.

Propose the single candidate you genuinely believe best satisfies your role's criteria. Conform strictly to the
schema. Do not hedge, do not offer alternatives, do not pre-compromise for other
roles — this is the round where your role's viewpoint is supposed to be
represented undiluted.

Forge your reply (`--rationale` is required):
```
concord_receiver.py forge seed --candidate "CANDIDATE IN SCHEMA FORMAT" --rationale "EVIDENCE AND REASONING"
```

`--rationale` must include:
- the concrete facts and evidence from your role's criteria that support this candidate
- why the other options in `<candidate_schema>` are less suitable from your
  role's perspective — any domain knowledge you hold that argues against
  them, even if none of them have been proposed yet
- any trade-offs you considered
- why you believe the candidate will hold up under scrutiny from other agents

This rationale is what scoring agents use to detect incorrect logic in your
proposal, and what the repair agent will read when deciding whether your
reasoning stands. Any domain knowledge you hold but do not state here is
invisible to every other participant — if a fact would change how others
propose or score, write it here.

**Example** (you are the "cost" agent, no prior proposals exist):
```
concord_receiver.py forge seed \
  --candidate "strategy: local-memory, ttl_seconds: 300" \
  --rationale "Local memory adds zero infrastructure cost, fits within the 512 MB process budget, and the 300 s TTL bounds stale-read risk to an acceptable window for this use case."
```

**Example response:**
```
<concord-candidate-reply>
  <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
  <rationale>Local memory adds zero infrastructure cost, fits within the 512 MB process budget, and the 300 s TTL bounds stale-read risk to an acceptable window for this use case.</rationale>
</concord-candidate-reply>
```

**Gotchas:**
- Always use `forge seed` — never `forge score`, even if `<context>` or `<candidate_schema>` lists multiple named options.
- Propose one candidate only — no alternatives, no hedging.
- Do not revise your position just because other proposals differ from yours; only revise if a specific argument genuinely changes your assessment.

---

### `<concord-score>`

You receive one or more `<candidate>` elements inside `<candidates>`. Before
rating anything, re-read your own `<concord-anchor>` / `<concord-anchor-reply>`
exchange from earlier in this session — recall what you gave `x_reject` and
`x_ideal` and why, then place each candidate between them on that same scale.
Your calibration is not re-sent in this message; your context window is the
authoritative source.

For **each** candidate: rate it on that raw 0..100 scale and write your full
reasoning for that number. This is required — not optional, not a summary.
Your reasoning must include:
- the specific properties of the candidate that drove the score
- any concrete facts or evidence from your role's criteria that support it
- any concrete facts or evidence from your role's criteria that oppose it
- any concern about a false claim, factual error, or flawed step in the
  candidate's reasoning

A score with no evidence behind it is not useful to the repair agent that
will read it. "Too expensive" is not reasoning; "adds ~$400/month in Redis
hosting based on current cluster pricing, which exceeds the cost budget" is.

**Write everything you know that is relevant, even if it seems obvious to
you.** The repair agent and other agents can only see what you write in
`--reasoning`. Any domain knowledge you hold but omit here is permanently
invisible to the rest of the protocol.

Forge your reply (repeat `--candidate-id / --raw / --reasoning` once per
candidate, in the order they appear in the message):
```
concord_receiver.py forge score \
  --candidate-id ID1 --raw N1 --reasoning "YOUR REASONING" \
  --candidate-id ID2 --raw N2 --reasoning "YOUR REASONING" \
  ...
```

`candidate_id` values must be copied verbatim from the input `id` attributes.
Include one triplet per candidate in the order given.

**Example** (you are the "cost" agent; two candidates):
```
concord_receiver.py forge score \
  --candidate-id r0-cost        --raw 82 --reasoning "Local memory adds no infrastructure cost and requires no operational overhead." \
  --candidate-id r0-reliability --raw 41 --reasoning "Redis cluster adds ongoing hosting cost and operational overhead; the durability benefit does not justify the expense for this use case."
```

**Example response:**
```
<concord-score-reply>
  <score candidate_id="r0-cost">
    <raw>82.0</raw>
    <reasoning>Local memory adds no infrastructure cost and requires no operational overhead.</reasoning>
  </score>
  <score candidate_id="r0-reliability">
    <raw>41.0</raw>
    <reasoning>Redis cluster adds ongoing hosting cost and operational overhead; the durability benefit does not justify the expense for this use case.</reasoning>
  </score>
</concord-score-reply>
```

Score every candidate you are given, including one you proposed yourself. Never
refuse to score; a candidate you consider terrible gets a low number with honest
reasoning, not a refusal.

**Gotchas:**
- `--reasoning` is required — do not strip or abbreviate it. The repair agent reads your full reasoning to decide what to address; "too expensive" alone is not reasoning.
- Score every candidate, including ones you proposed yourself — refusing to score is not valid.
- Copy `candidate_id` values verbatim from the input `id` attributes — do not rename or abbreviate them.
- Do not use the normalised `[0,1]` scores from the incumbent's `<scores>` block as your own ratings — report your own raw 0..100 assessment.

---

### `<concord-repair>`

You receive this **only** because you are the least-satisfied agent on the
current incumbent. It is not a request for an opinion — it is your turn to move
the group, and you are the only agent proposing this round.

You are given the problem `<context>` (including the full set of valid answers,
where the task has one — check it for an option nobody has proposed yet, not
just what's already in the pool), `<satisfaction_threshold>`, and the current
best candidate as `<incumbent>`. The incumbent carries `your_score` and
`shortfall` attributes (your normalised score and how far it falls short of
`satisfaction_threshold`), its `<text>`, its proposer's `<rationale>`, and a
`<scores>` block with one `<score agent="name" score="value">` per agent —
each score containing the scorer's required `<reasoning>`. The rest of the
candidate pool is in your context from the scoring round — you do not need it
re-sent. Your own calibration is not re-sent either — use your anchor exchange
from earlier in this session to re-ground your scale before proposing.

**Read the incumbent's scores before proposing anything** — not just the numbers. If any of it
contains an argument or evidence you had not considered, and it genuinely
changes your assessment, your new candidate should reflect that: this is not
about negotiating a compromise, it is about correcting a position now that
you can see something you couldn't before. You are not obligated to change
your position just because the pool disagrees with you, or because more of
the pool holds one view than another — only because a specific argument
actually persuades you.

Propose **one** new candidate that:

1. **Clears your own bar.** Primary obligation. You are told the size of your
   shortfall; do not close it with a cosmetic change. In your own terms: propose
   something you would rate close to how you rated `<x_ideal>`, not something
   marginally better than what you just rejected.
2. **Does not knowingly push any other agent below `satisfaction_threshold`.** Read the
   incumbent's `<scores>` before proposing: for each other agent, look at their
   score and rationale, and infer what they are protecting. Keep those properties
   intact. This is best-effort — you cannot predict their scores exactly, and
   the next scoring round will correct you if you miss.
3. **Is genuinely new.** Check your context for the candidates from the scoring
   round. A duplicate or cosmetic variant burns a round of the budget and moves
   nothing.
4. **Conforms to the candidate schema.**
5. **Directly addresses the strongest reasoning against it, if you still hold
   your prior position.** Your rationale must engage with the specific argument
   in the incumbent's `<scores>` that disagrees with you — agree with part of it,
   refute it, or explain why it doesn't apply — not merely restate your own
   case. A repeated or reworded candidate with no engagement with the
   counter-arguments is not an acceptable repair.

Forge your reply (`--rationale` is required):
```
concord_receiver.py forge repair \
  --candidate "CANDIDATE IN SCHEMA FORMAT" \
  --rationale "EVIDENCE, COUNTER-EVIDENCE ADDRESSED, AND REASONING"
```

`--rationale` must include:
- the concrete evidence from your role's criteria that supports this candidate
- explicit acknowledgement of the strongest counter-evidence from the
  incumbent's `<scores>` — the specific reasoning other agents gave for scoring
  it low — and why your new candidate addresses it or why that concern does not
  apply to the new proposal
- what specifically changed from any prior candidate you proposed and why
- any domain knowledge from your role that has not yet appeared in the message
  history and is relevant to why this candidate is the right choice — if you
  know something that argues for or against any option, this is your last
  reliable opportunity to make it visible

A rationale that ignores the scoring evidence already on record — treating it
as if the counter-arguments had not been made — is not an acceptable repair.
Equally, knowledge you hold but do not write here stays invisible to every
other participant for the remainder of the run.

**Example** (you are the "cost" agent; prior cluster proposal was scored low on cost):
```
concord_receiver.py forge repair \
  --candidate "strategy: redis-single, ttl_seconds: 600" \
  --rationale "Single-node Redis costs ~$45/month vs ~$380/month for the cluster (the cost concern in the incumbent scores), satisfying the cost budget. Reliability scored the cluster highly for durability; single-node Redis provides the same persistence guarantees (AOF enabled) so that property is preserved."
```

**Example response:**
```
<concord-candidate-reply>
  <candidate>strategy: redis-single, ttl_seconds: 600</candidate>
  <rationale>Single-node Redis costs ~$45/month vs ~$380/month for the cluster (the cost concern in the incumbent scores), satisfying the cost budget. Reliability scored the cluster highly for durability; single-node Redis provides the same persistence guarantees (AOF enabled) so that property is preserved.</rationale>
</concord-candidate-reply>
```

**Gotchas:**
- A cosmetic variant or duplicate of an already-scored candidate burns a repair budget slot and moves nothing — propose something genuinely new.
- Rationale must explicitly address the strongest counter-evidence from the incumbent's `<scores>` — a rationale that ignores on-record arguments is not accepted.
- Do not lower `satisfaction_threshold` or weaken your criteria to make the incumbent look acceptable.
- Clears your own bar first: propose something you would rate close to `x_ideal`, not something marginally above the incumbent.

**If you believe no such candidate exists** — every way of satisfying you drops
someone else below their floor — still return your best attempt, and state the
conflict plainly in `--rationale` (which agent, over what property). Do not
lower `satisfaction_threshold`, do not weaken your criteria, and do not propose something you are
not actually satisfied by. A clearly-named structural conflict is exactly what
the orchestrator needs to escalate; a manufactured compromise hides it.

---

### `<concord-commit>`

The run is over. Read `<outcome>`:

- **`SUCCESS`** — the winning candidate clears `satisfaction_threshold` for every agent, including
  you. Proceed with it as the group's decision.
- **`BEST_EFFORT`** — the budget ran out first. Check `<shortfall>`: those
  agents are below their floor. If you are among them, say so when you report
  back to whoever triggered the run; if an `<unknown>` element is present,
  those agents were never heard from and their satisfaction is not certified.

No reply is required. Optionally run `concord_receiver.py forge commit` (exits silently)
to confirm receipt. Do not re-open the decision or re-propose.
