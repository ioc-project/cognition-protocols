---
name: concord-initiator
description: >
  Use when you need a single outcome that satisfies multiple agents with
  different, potentially conflicting criteria — a unilateral choice is not
  enough. Activate on confirmed conflict (agents returned divergent outputs),
  anticipated conflict (distinct roles on a shared decision make divergence
  structurally likely), or when you cannot tell whether roles will diverge —
  uncertainty is itself the signal. Also activate when you would relay outputs
  from other agents to a downstream agent whose criteria bear on the decision:
  include that agent directly instead of forwarding a pre-digested summary.
  Apparent agreement upstream is not a reason to skip — a downstream relay
  recipient may still hold different criteria. Also activate when an upstream
  protocol (e.g. ACCORD) has handed you a pre-verified conflict frame.
  Do NOT activate when a single role owns the decision or agents work on
  separate sub-problems with no shared decision variable.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.1.1" # x-release-please-version
---

# CONCORD — Initiator Skill

Drive the CONCORD protocol to converge on a single outcome that clears
`satisfaction_threshold` for **every** agent in a named set — not a majority, not an average.
[`scripts/concord_initiator.py`](scripts/concord_initiator.py) makes every selection and decision; your role is to
route messages and record replies. The result is either `SUCCESS`
(proven, every agent at or above `satisfaction_threshold`) or `BEST_EFFORT` (which agent is
short, and by how much).

**[`scripts/concord_initiator.py`](scripts/concord_initiator.py) drives every decision — it selects the incumbent,
identifies the bottleneck, and decides COMMIT vs BEST_EFFORT.** Do not
compute scores, floors, leximin comparisons or the bottleneck yourself — the
certificate is only worth something if the algebra behind it is exact. Your
job is to send the messages the tool tells you to send and record the
replies — "send" means a real message actually delivered to that named
agent (e.g. via the agentbridge-delegate skill, if you're running as an
agentbridge adapter) and "record" means its real reply. Never author a
role's proposal, score, or repair on its behalf yourself, under any
framing ("seed", "represent", "play the role of") — every `record-reply` call's
content must trace back to a reply that agent actually sent. If you cannot
reach an agent, that's a failure to report, not a gap to fill in yourself.

Every `concord_initiator.py` invocation below is written as a bare relative path,
**relative to this skill's own directory** — the directory this SKILL.md
file itself lives in — not necessarily your current working directory. If
your cwd differs (e.g. you loaded this skill via a `.claude/skills/`
symlink into a working directory elsewhere), resolve the path against where
you actually found this file — e.g.
`.claude/skills/concord-initiator/scripts/concord_initiator.py` — instead of
assuming a bare `scripts/concord_initiator.py` from your cwd will find it. If it
404s, that's the reason: don't reimplement the algorithm yourself as a
fallback, find the real path instead.

---

## When to activate

All three must be true:

1. You need an outcome that must satisfy agents with **different, potentially
   conflicting criteria** — a unilateral choice by you or any one agent is
   inadequate.
2. You can name the **negotiating set `A`**: the specific sub-agents whose
   satisfaction counts (e.g. `["security", "cost", "reliability"]`).
3. You have or can establish a **candidate schema** — a shared format for the
   options you are choosing between.

**Conflict may be confirmed, anticipated, or uncertain — activate in all three
cases.** Confirmed: agents have already returned divergent outputs. Anticipated:
their distinct role criteria applied to a shared decision variable make divergence
structurally certain by design (security vs. cost, performance vs. reliability).
Uncertain: you cannot tell whether their roles will diverge. Uncertainty is itself
the signal to activate — do not poll agents first to check whether conflict exists.

Apparent agreement among upstream agents is not a reason to skip. A downstream
agent in a relay chain may hold different criteria even when the agents feeding it
seem aligned.

Also activate when an upstream protocol (e.g. ACCORD) has handed you a
pre-verified conflict frame and asked you to converge.

Do not activate when:
- A single role already owns the decision
- Agents are working on separate sub-problems — no shared decision variable

**Who should participate.** Every agent whose criteria shape the outcome should be
included — including agents that would otherwise receive a synthesised relay
downstream. If your plan is to collect from agents B and C and forward a digest to
D, include D alongside B and C: D's criteria bear on the decision and it should hold
a seat during consensus rounds, not wait for a pre-digested summary. A relay chain
where you would synthesise for another agent is a signal that agent was left out.

---

## The loop

```
concord_initiator.py init …                       once — prints state path and first messages to send
  … send them; collect replies …
concord_initiator.py --state <path> record-reply <agent> <xml>   one call per reply
concord_initiator.py --state <path> mark-missing <agent> <cid>   only after one retry with no response
```

If `record-reply` prints `REJECTED`, follow the instructions it prints (re-send the message it
shows; never edit the reply or its numbers). See "When `record-reply` rejects a reply" below.

The tool is self-driving. After each `record-reply`, it checks whether all
expected replies for the current phase are in. When they are, it automatically
advances — running SELECT/ACCEPT/REPAIR/COMMIT internally and printing the next
set of messages to send. You do not call a separate step command.

### How to send a message

Every message the tool prints sits between `----- BEGIN MESSAGE (to: …) -----` and
`----- END MESSAGE -----`. The text between those two lines, and nothing else, is the
whole message you send to that agent, however your environment delivers messages to other
agents (a delegation tool, a messaging call, a chat reply). The XML already contains the
instructions and the exact reply command the recipient needs, so you add nothing:

- no introduction or summary ("CONCORD Anchor Phase. Please rate…"), no restating of the context
- no "Respond with…" or "Reply with…" template of your own: the recipient's reply format comes from
  its own skill, and a different template makes it answer in a format the tool rejects
- no BEGIN/END lines, no re-indenting, no shortening

Right: send the text between the markers, unchanged.

```
<concord-anchor>
  <satisfaction_threshold>0.7</satisfaction_threshold>
  …
</concord-anchor>
```

Wrong, because it replaces the tool's message with your own wording and reply schema:

```
Rate these outcomes 0-100 … Respond with <concord-anchor-reply><x_reject_score>…
```

A Repair message goes only to the one agent named in its BEGIN line. All other messages go to every
agent listed under `Awaiting replies`.

**Check that every message was really delivered.** Some environments limit how much you can produce in one
step, and sending the same long message to several agents at once can exceed it: the last send is then cut off or
arrives empty. If a send fails, comes back empty or is reported as truncated, that message was not delivered:
send exactly the same message again, never a shortened one. To avoid it, send long messages to a few agents at a
time (two is safe) instead of all at once.

When the protocol terminates, `record-reply` prints:

```
Phase: Complete — SUCCESS

Messages to Send:

<concord-commit>…</concord-commit>

Awaiting replies:

(none — broadcast to all participants)
```

Broadcast the commit message to all agents. The run is complete.

**You must run this loop to completion.** Do not stop before the tool prints
`Phase: Complete — SUCCESS` or `Phase: Complete — BEST_EFFORT`.

`<path>` is whatever `init` reported — the same value on every remaining
call in this run, never one you chose or reconstructed yourself.

Use `status` at any time to re-read the current phase without changing state:

```
concord_initiator.py --state <path> status
```

### 1. init

```
scripts/concord_initiator.py init \
  --agents security,cost,reliability \
  --satisfaction-threshold 0.6 --eps 0.05 -B 10 \
  --schema  "the shared candidate format, inline — multi-line is fine" \
  --context "the problem statement, inline" \
  --x-reject "an outcome the shared context calls unacceptable" \
  --x-ideal  "an outcome the shared context calls fully satisfying" \
  --known-proposals "a position an agent has actually stated, quoted verbatim, one per line or paragraph; omit if none"
```

Pass each of these as a plain argument value — multi-line text is fine,
there's no shell involved when you invoke this through a tool call rather
than a literal shell command. If you are typing this as a literal shell
command and already have the content in a file, `@path` reads it from there
instead (e.g. `--schema @schema.txt`) — but that's a convenience for that one
case, never a requirement: don't write a file just to satisfy this flag.

`init` always mints its own state path — even if you pass `--state`, or
don't — and prints the exact path it actually used as `State:`. Concurrent
negotiations that happened to reuse the same `--state` value would otherwise
silently corrupt each other's state, so never guess or reconstruct this path
yourself: copy `init`'s own reported value verbatim into every remaining
`record-reply` / `mark-missing` / `status` call in this run.

`init` also prints the first set of messages to send (anchor phase) and
the list of agents awaiting a reply, so you can start delegating immediately.

**Choosing `satisfaction_threshold` is the decision that determines whether CONCORD does anything
at all.** The seeds are what each agent proposes unilaterally, from its own
viewpoint. If `satisfaction_threshold` sits below the best seed's floor, that seed is feasible
immediately, the run commits at setup, no repair round ever executes, and the
protocol has reduced to "pick the best unilateral proposal" at the cost of a
scoring matrix. The floor only does work *above* the level the agents reach on
their own.

So set `satisfaction_threshold` at the standard the outcome genuinely has to meet, not at a level
you are confident is reachable. `0.6` is a starting point, not a safe default —
on a well-aligned roster the seeds often clear it. A run that commits at `t = 0`
is one where no `Repair` phase was ever printed: the commit message does not flag it, so check for it
yourself. Whether that is a real success or a sign the bar
was too low depends on whether `--known-proposals` was used: if seeds were
informed by prior positions and one genuinely satisfied every agent, that is a
legitimate outcome; if no prior positions existed and seeds still cleared the
threshold without any negotiation, `satisfaction_threshold` was set below what the
agents naturally agree on and should be raised. Raising it is cheap: the anchors
and existing scores stay valid, so a rerun costs only the rounds it actually needs.

**`--x-reject`/`--x-ideal` describe the objective, never the means.** Set them
to the outcome `--context` is actually trying to produce, as a plain
before/after symptom state in its own numbers — e.g. error rate > 40% →
`x-ideal` "error rate returns to 0", `x-reject` "stays above 40% or rises."
Never a proposal: no naming an action, and no language about *how* it's
reached (confidence, verification, reversibility) — that's the usual way one
creeps back in. An anchor that pre-names an action biases every score against
it before any real candidate exists, since one agent's own unilateral pick
becomes the reference point the whole run is measured against.

Self-check: if an anchor could pass as something an agent might genuinely
propose, rewrite it one level up. (A decision with almost no real alternatives
can still make the answer easy to infer even worded this way — an inherent
limit, not a sign to add more words.)

**Only pass `--known-proposals` for positions an agent has really stated.** Each entry must be a reply you
actually received from that agent before this call (for example its answer to a question you asked it earlier),
quoted as closely as you can. Never write, summarise or infer a position for an agent that has not spoken, however
obvious it seems what a role would propose. That is answering on the agent's behalf, which this protocol forbids,
and it puts your guess in front of every other agent as if it were theirs. If no agent has spoken yet, leave the
flag out: the first Seed round then collects each role's view directly.

**`--known-proposals` is a mechanical backstop for that same rule.** If an
agent's position already exists as a real reply before this call (often the reason you're
invoking CONCORD at all), pass it — one distinct proposal per paragraph,
quoting each as closely as you can; leave it empty if nothing has been
proposed yet. When given, `init` rejects `--x-reject`/`--x-ideal` if either
reads as a description of one of these proposals rather than the state it's
chasing, naming which proposal it matched so you can rewrite and retry. It's
a similarity check on vocabulary, not a proof — it catches the obvious case,
not every paraphrase, so the self-check above still matters even when this
passes.

**Prefix each proposal with the agent's exact id** (as passed to `--agents`)
followed by a colon — e.g. `agent_1: <that agent's existing position,
verbatim>` — not a display name or role label like "Agent 1:" or "Telemetry:".
Write one proposal per paragraph, or one per line: every line that starts with
an agent id and a colon begins a new proposal, and any other line continues the
proposal above it. This prefix is used for the `--x-reject`/`--x-ideal` similarity check (to
reject anchors that read as a description of one of these proposals). In
`PROPOSE_SEED`, all prior proposals are stripped of their prefixes and sent to
every agent as an anonymized `<prior_proposals>` block — agents see the full
set of prior positions without knowing which belonged to whom. Check that block
in the first `Seed` message: it must hold one `<proposal>` per position and no agent id.
A paragraph whose prefix doesn't match any agent id is still passed through to
the similarity check above.

### 2. record-reply

**Hand the agent's whole reply over, exactly as received, and let the tool parse it** (do not cut the XML out
yourself):

```
scripts/concord_initiator.py --state <path> record-reply cost '<concord-score-reply>…</concord-score-reply>'
… | scripts/concord_initiator.py --state <path> record-reply cost   # or on stdin
```

`record-reply` takes the agent name and the receiver's whole reply. The reply is
an XML document (`<concord-anchor-reply>`, `<concord-candidate-reply>`, or
`<concord-score-reply>`) that may sit inside a markdown fence or among surrounding
text: the tool finds it, unchanged, detects which kind of reply it is, stores it,
and prints the current protocol status. A message that contains no CONCORD XML at
all is rejected and nothing is recorded: prose is not a reply, so never pull numbers
or a candidate out of it and never write the XML yourself; follow the `REJECTED` block
and send the agent the message again. Use this rather than retyping
numbers: manual transcription is the one step in the loop where you can
silently corrupt the score matrix, and a corrupted matrix still produces a
confident result.

Pass the agent's reply **exactly as received** — it is the stdout of
`concord_receiver.py forge`; never edit, re-tag or retype it.

**Example — anchor reply** (cost agent replies to `<concord-anchor>`):
```
scripts/concord_initiator.py --state <path> record-reply cost '<concord-anchor-reply>
  <raw_reject>8.0</raw_reject>
  <raw_ideal>95.0</raw_ideal>
</concord-anchor-reply>'
```

**Example — candidate reply** (cost agent replies to `<concord-propose-seed>` or `<concord-repair>`):
```
scripts/concord_initiator.py --state <path> record-reply cost '<concord-candidate-reply>
  <candidate>strategy: local-memory, ttl_seconds: 300</candidate>
  <rationale>Local memory adds zero infrastructure cost, fits within the 512 MB process budget, and the 300 s TTL bounds stale-read risk to an acceptable window for this use case.</rationale>
</concord-candidate-reply>'
```

**Example — score reply** (cost agent replies to `<concord-score>`):
```
scripts/concord_initiator.py --state <path> record-reply cost '<concord-score-reply>
  <score candidate_id="r0-cost">
    <raw>82.0</raw>
    <reasoning>Local memory adds no infrastructure cost and requires no operational overhead.</reasoning>
  </score>
  <score candidate_id="r0-reliability">
    <raw>41.0</raw>
    <reasoning>Redis cluster adds ongoing hosting cost and operational overhead; the durability benefit does not justify the expense for this use case.</reasoning>
  </score>
</concord-score-reply>'
```

**Gotchas:**
- Do not strip `<reasoning>` from score replies before passing to `record-reply` — the initiator stores the full XML and embeds it verbatim in the `<concord-repair>` message. A repair agent with no reasoning to read cannot produce a valid repair.
- Do not strip `<rationale>` from candidate replies for the same reason.
- Do not retype or reconstruct XML — manual transcription silently corrupts the score matrix even when the output looks plausible.
- Do not fix malformed XML yourself — when `record-reply` prints a `REJECTED` block, send the agent the message printed in it, exactly as printed, and let the agent correct its reply.
- Never author a reply on behalf of an agent — every `record-reply` input must trace to a reply that agent actually sent.

Parallel calls are safe — `record-reply` holds an exclusive file lock for
the duration of each read-modify-write, so N agents replying simultaneously
will serialize without losing any reply.

After the last reply for a phase, `record-reply` automatically runs the
decision engine and prints the next phase's messages. You do not call
anything in between.

Pass `--overwrite` to correct a transcription error on an already-recorded
anchor or score. Use it only for genuine corrections.

### 3. mark-missing

```
scripts/concord_initiator.py --state <path> mark-missing cost r1-security
```

Use in the Score phase when an agent fails to reply after one retry. It only works on a
candidate already in the pool; there is no equivalent for a missing anchor or seed. Records
`MISSING` for that agent on that candidate, then re-evaluates. **Any run with a MISSING
entry can only terminate as BEST_EFFORT** — a candidate with an absent cell
cannot be certified as a SUCCESS.

---

**When `record-reply` rejects a reply — re-send, don't patch:**

If `record-reply` exits with an error (wrong root tag, missing `<raw>` or
`<reasoning>`, missing `candidate_id`, inverted anchor, invalid XML), the state
file was not updated and the tool prints a `REJECTED` block after the error.
Do not repair, re-tag, re-order or re-type the reply, and never change, swap or
invent a number: a hand-corrected entry looks plausible but is no longer the
agent's own reply, and it silently corrupts the score matrix (for an anchor it
changes the agent's scale for the whole run). Instead:

1. Send the agent the message printed in the `REJECTED` block, exactly as printed
   between its BEGIN/END lines. It already contains the rejection reason followed by
   the original message, so you write nothing yourself.

2. Record the new reply once it arrives.

3. A rejection that says an agent "already rated" a candidate is not a non-reply: do not `mark-missing` for it.
   The Score message of a repair round lists only the candidate(s) still to be scored, and the tool keeps ratings
   already on record. Send the agent the message printed in the `REJECTED` block, as for any other rejection.

4. If the same agent is rejected a second time for the same round:
   - in the **Score** phase, treat it as a non-reply: `mark-missing <agent> <candidate-id>`
     for each candidate it owes (the run can then only end BEST_EFFORT);
   - in the **Anchor, Seed or Repair** phase there is no way to skip an agent
     (`mark-missing` only covers scores). Stop, report that the run could not be
     completed and is not certified, and quote the error. Do not fill in the reply yourself.

   One retry per agent per round is the limit.

---

**Flag reference:**

| Command | Flags |
|---|---|
| `init` | `--agents --satisfaction-threshold --eps -B/--B/--budget --schema --context --x-reject --x-ideal --known-proposals` |
| `record-reply` | `agent` (positional) `text` (positional, optional — stdin if omitted) `--overwrite` |
| `mark-missing` | `agent` (positional) `candidate` (positional) |
| `status` | no flags beyond `--state` |

`--state` may be given before or after the subcommand — both work.
`--format json` is accepted on all commands for structured output; XML
protocol message bodies remain XML strings inside the JSON values.

---

## What the protocol is doing, and why

You need this to write good `--context`, to judge whether a repair is
progressing, and to explain the result. The tool implements all of it.

**Anchoring makes scores comparable.** Each agent rates two shared reference
outcomes once — `x_reject` (what unacceptable looks like) and `x_ideal` (what
full satisfaction looks like) — and everything after is normalised into `[0,1]`
against those two points. This is the only thing that makes `satisfaction_threshold = 0.6` mean the
same degree of role-satisfaction to the security agent and the cost agent.
Choose the two anchors carefully; they set the meaning of every number in the
run.

Agents report raw scores on a **0..100 scale** — this is a fixed part of the
protocol that the receiver skill enforces. The tool warns when an anchor reply
arrives outside this range, since that usually means the agent used a different
scale entirely.

**Messages are broadcast-safe within each phase.** The Anchor, Seed, and Score
messages sent to all participants in a given round are identical — a single
message body can be multicast or broadcast to every eligible recipient rather
than composed per-agent. Each agent's own calibration numbers are part of its
prior context (from the anchor round), so they are not re-echoed; likewise,
prior proposals in the Seed phase are sent as a flat anonymized list. Only the
Repair message is unicast (to the one bottleneck agent).

**The worst-off agent proposes.** Round 0 seeds the pool with one candidate per
agent. After that, exactly one new candidate appears per round, from the agent
least satisfied by the current incumbent. It knows best what would satisfy it,
and under leximin it is the only agent whose improvement can move the objective.
This is why the pool is bounded at `N + B` and the cost is exact.

**SELECT ranks the whole pool.** Candidates clearing `satisfaction_threshold` for everyone are
ranked by leximin (best worst-off agent first); if none clears it, the pool is
ranked by highest minimum — the same direction repair pushes. The pool is
**never pruned**, because the winner is routinely a candidate that was nobody's
first choice. That is the entire point of the protocol.

**ACCEPT requires a real gain.** The incumbent is replaced only when the sorted
score vector gains at least `eps` at some position without degrading any
better-off position. That monotonicity is what guarantees termination.

**Stopping.** `SUCCESS` the moment every agent clears `satisfaction_threshold` and no MISSING
entries exist; `BEST_EFFORT` when the round cap `B` is spent or any MISSING
entry exists. The incumbent is a valid answer at every point, so an external
deadline can cut the run short at any time — commit what you have.

---

## Rules you must not break

1. **Never certify a floor over an agent you did not hear from.** Retry a silent
   agent once, then `mark-missing`. The tool will drop it from that
   candidate's floor, refuse to mark the run `SUCCESS`, and the final commit
   message will list it under `unknown`. Never substitute a guess for a
   missing score.
2. **`satisfaction_threshold` never moves.** Not by you, not by a sub-agent, not to rescue a run. A
   floor that moves when it binds is not a floor. If nothing feasible exists,
   `BEST_EFFORT` plus the shortfall is the correct output, and relaxing the bar
   is a decision for the human or the upstream protocol.
3. **Anchors are rated once**, at setup. `--overwrite` exists for genuine
   miscalibration and invalidates every comparison made before it.
4. **One proposer per round**, chosen by the tool, never by volunteering.
5. **Send the message body as printed.** The text between the BEGIN/END lines
   is a self-contained XML document with its own reply instructions; pass it
   verbatim, as the entire message, to the named agent. Add nothing before or
   after it, and never add a reply template of your own. Paraphrasing or
   reformatting it is how a run turns into unparseable replies.
6. **Run to Complete.** Do not stop the loop before the tool prints
   `Phase: Complete — SUCCESS` or `Phase: Complete — BEST_EFFORT` and do not
   skip broadcasting the commit message — a run that ends at any earlier point
   has produced no certified outcome. Until then there is no outcome: do not
   carry out the action the run was meant to decide, and do not write a summary that calls a result "certified"
   or says the protocol "converged". If you cannot reach `Complete` (an agent
   will not reply and the tool offers no way forward), stop, say that the run is
   not complete, and quote the last tool output.
7. **Announcing an action is not taking it.** Once the tool names the message
   to send and who to send it to, your turn is not over until you have actually
   sent it and recorded every reply. Narrating the plan is not a substitute for
   actually sending the message.

---

## If you cannot run the script

The tool is authoritative. Only if you have no execution environment, run the
protocol by hand — and say in your final answer that the result was computed
manually and is not a verified certificate.

```
setup:  ANCHOR all → PROPOSE_SEED all → SCORE all seeds by all agents
        s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0, 1)
        x_star = SELECT(pool)
loop:   if min_j s_j(x_star) >= satisfaction_threshold and nobody is missing → COMMIT SUCCESS
        if t == B                                        → COMMIT BEST_EFFORT
        t += 1
        b = argmin_j s_j(x_star)          # ties: lowest index in A
        REPAIR(b) → x_new → SCORE x_new by all agents
        x_cand = SELECT(pool)
        if leximin(x_cand) beats leximin(x_star) by >= eps at some position
           without degrading an earlier one → x_star = x_cand

SELECT: feasible = {x : every agent rated it and min_j s_j(x) >= satisfaction_threshold}
        if feasible → leximin-best of feasible
        else        → the x with the largest min_j s_j(x)
leximin(x) = the score vector of x sorted ascending
```

---

## Diagnostics

| Symptom | Likely cause | Action |
|---|---|---|
| `BEST_EFFORT` every run | `B` too small, or `satisfaction_threshold` unreachable for this problem | Raise `B`; otherwise report the shortfall upstream — the bar may be wrong |
| Same bottleneck agent every round | Its criteria are structurally incompatible with the others' | Stop spending rounds; report the exact shortfall and escalate |
| A repair scores below `satisfaction_threshold` for its own proposer | That agent's anchors are miscalibrated | Re-record its anchor with `--overwrite`, then re-score its column |
| `record-reply` prints no phase advance after last reply | Protocol still awaiting a reply from another agent | Check `status` — the awaiting list shows exactly who is outstanding |
| One agent's scores are erratic across similar candidates | Criteria drift mid-run | Re-anchor with `--overwrite` if budget allows |
| An agent replies in prose or plain text | The message body was paraphrased, truncated, or not forwarded as XML | Re-send the exact XML content the tool printed |
