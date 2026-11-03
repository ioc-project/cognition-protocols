# CONCORD initiator: full reference

The long form of the skill: every phase, the relay, acks, the watchdog and diagnostics. Not needed to run the protocol: the script reminds you of the next step at every call. Read it when the script's output is not enough.

## Initiator skill (long form)

Drive the CONCORD protocol to converge on a single outcome that clears
`satisfaction_threshold` for **every** agent in a named set — not a majority, not an average.
[`scripts/concord_initiator.py`](../scripts/concord_initiator.py) makes every selection and decision; your role is to
pass it what arrives and send what it tells you to send. The result is either `SUCCESS`
(proven, every agent at or above `satisfaction_threshold`) or `BEST_EFFORT` (which agent is
short, and by how much).

**[`scripts/concord_initiator.py`](../scripts/concord_initiator.py) drives every decision — it selects the incumbent,
identifies the bottleneck, and decides SUCCESS vs BEST_EFFORT.** Do not
compute scores, floors, leximin comparisons or the bottleneck yourself — the
certificate is only worth something if the algebra behind it is exact. Never author a
role's proposal, score, or repair on its behalf yourself, under any
framing ("seed", "represent", "play the role of") — everything you pass to the script
must be a reply that agent actually sent. If you cannot
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

## The loop: one command

```
concord_initiator.py init … --quiet                       once: creates the run (prints the state path)
concord_initiator.py --state <path> handle                first call, no input: prints the first message to send
concord_initiator.py --state <path> handle <<'EOF'        every later call: pass what arrived
<the reply or replies, exactly as received>
EOF
```

`<path>` is whatever `init` reported — the same value on every call in this run, never one you chose or
reconstructed yourself. `handle` is the only command you need: it records what you give it and ends its output
with one of three states on the first line:

| First line | Meaning | You |
|---|---|---|
| `STATUS: SEND` | the rest is what to send: a message, and the replies to relay | send it exactly as printed, to the agents it names |
| `STATUS: NONE` | there is nothing to send | wait for the next reply. Send nothing. |
| `STATUS: ASK` | a decision is yours (reserved: the text says what) | decide, then act as it says |

Your whole part in a run:

1. Run `handle` with no input: it prints the **Anchor** message. Send it to everyone, then, if you can run a command
   in the background, start the watchdog there (see "Nudging, and the watchdog").
2. Every time an agent replies, pass the reply, exactly as received, to `handle`. When it prints a `SEND`, send it.
   Once every anchor is in, it prints the **Seed** message: send it to everyone.
3. After the Seed message you only record. The agents rate the proposals and make the repairs among themselves
   (see "Who does what" below); `handle` prints `NONE`, and you wait and keep passing replies in.
4. The run is over when `handle` prints `STATUS: NONE` followed by `RUN COMPLETE: outcome …`: the outcome, the winning
   candidate, the final scores and any shortfall. **That is your result: report it to whoever asked for the run.**
   There is no commit message to send. Every agent reaches the same outcome by itself, from the same ratings it has
   seen, and has nothing to do once the run is over. If the last `handle` also printed a `SEND` (the last replies to
   relay), send that first; the report is on its error output.

**You never ask for ratings and never ask for a repair, and you never write a message of your own.** Do not run
any other command to find out what to send: if `handle` printed `NONE`, nothing is to be sent.
If replies stop arriving, do not guess how long to wait: the watchdog (see "Nudging, and the watchdog" below)
returns the nudge for you.

### What `handle` prints

- **Replies to relay.** Every reply `handle` records is passed on, as received and naming its author, to every
  other agent, so each agent builds the same picture of the run without any group communication. Relayed replies
  come out together with the next message, if there is one. Use `init --no-relay` only if the agents already see
  each other's replies.
- **Acknowledgements (relay mode only).** Each delivery you send an agent carries a number (`<concord-delivery seq=…>`).
  An agent that has nothing to say about a delivery sends you a small `<concord-ack>` for it, and an agent that has
  something to say sends that directly (its reply carries the number it processed). Pass acks to `handle` like any
  reply: they are recorded, never relayed, and count as progress for the watchdog. The summary line lists the agents
  that have **not yet acknowledged** their latest delivery, which is how you tell "it never arrived" from "nothing to
  say". With `--no-relay` there are no numbers and no acks. An ack vouches for a contiguous run: "I received deliveries
  1 to N", so a lost delivery is never hidden by a later one, and a nudge (which carries everything recorded, marked as
  a full delivery) closes any gap.
- **Several messages.** When the output carries several messages it is split in blocks: a line `--- to: NAME[, NAME]`
  introduces a message, and its text (up to the next `--- to:` line) goes to the agents named. A single message with
  no such line goes to everyone awaited. Send each exactly as printed: add no introduction, no summary, no reply
  template of your own.
- **Send once, to everyone it is for, before waiting for any reply.** Never send to one agent, wait for its reply, and
  only then send to the next.
- **Replies may arrive in any order**, and before you expected them. A rating that comes before its proposal is kept
  and applied later; a repeated one is ignored. A reply names its author (its `agent` attribute); for one that does
  not, put a line `--- from: NAME` before it.
- **`NOT RECORDED: …`** (on the error output) means a reply was not recorded: it was malformed, from another run, or out
  of turn. Never edit the reply or its numbers and never write one for the agent: if the reason is the agent's, send it
  the reason and ask it to answer again.

### Who does what

Each agent keeps its own picture of the run (every anchor, proposal and rating it has seen, run through the same
decision engine as yours; its script works it out) and moves as soon as it says it is its turn: it rates once every
proposal is in, and when every rating is in, the agent that is least satisfied makes the repair on its own and rates
its own new candidate in the same reply. Nobody needs to ask. So replies can arrive before you have sent anything for
that step. Record them and carry on.

### Nudging, and the watchdog

When a message is lost or an agent has stopped, nothing arrives, so nothing wakes you. If you can run a command in the
background, start a watchdog there as soon as the first message is out, and re-arm it every time it returns. If you
cannot, do not run it in the foreground (it blocks until the run moves or ends): skip it, and when a reply you expect
does not come, run `handle` with no input by hand, which sends the same nudge.

```
concord_initiator.py --state <path> watchdog [--timeout 60] [--max-nudges 3]
```

It blocks while the run makes progress. It returns when:

- **the run has not moved for `--timeout` seconds.** It prints what a nudge is, as a `STATUS: SEND`: a delivery that
  makes each agent check what it has to do next, carrying the replies of the others it has not acknowledged (an agent
  that missed one would otherwise wait forever). In the Anchor and Seed phases the delivery also carries the request
  again, as that is the task itself; in Score and Repair the agents work out what they owe from the relay, so no rating
  or repair request is ever sent. In relay mode it goes only to the agents that owe a reply or have not
  acknowledged their latest delivery; the others are up to date and are left alone. Send it as printed, and re-arm. Agents already hold what they hold, so
  a repeat does no harm. This is the same as running `handle` with no input by hand.
- **`--max-nudges` nudges in a row did nothing.** It prints `STATUS: ASK`: the run is stuck and the decision is yours.
  Wait longer (re-arm), or, in the Score phase, give up on the silent agent with `mark-missing`, or stop and report
  that the run could not be completed.
- **the run is complete.** It prints `STATUS: NONE` and the report, which is your result.

Progress resets the count: any recorded reply, a new proposal, a new rating. Run the watchdog instead of guessing
how long to wait.

---

## Starting a run

```
scripts/concord_initiator.py init \
  --agents security,cost,reliability \
  --satisfaction-threshold 0.6 --eps 0.05 -B 10 \
  --schema  "the shared candidate format, inline — multi-line is fine" \
  --context "the problem statement, inline" \
  --x-reject "an outcome the shared context calls unacceptable" \
  --x-ideal  "an outcome the shared context calls fully satisfying" \
  --quiet
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
yourself: copy `init`'s own reported value verbatim into every remaining call
in this run. Add `--quiet` so that `init` prints nothing but the path; the first message comes from `handle`.
The random suffix of that path is the run's **state id**: every message carries it (`<state_id>`), the agents'
replies echo it, and `handle` skips a reply that belongs to another run.

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
is one where no `Repair` phase was ever printed: the report does not flag it, so check for it
yourself. If seeds clear the threshold without any negotiation,
`satisfaction_threshold` was set below what the agents naturally agree on and
should be raised. Raising it is cheap: the anchors
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

---

## An agent that stays silent

Retry it once (a nudge). If it still does not reply, in the Score phase mark it: `mark-missing <agent> <candidate-id>`
(per candidate it owes). A run with a MISSING entry can only end `BEST_EFFORT`. `mark-missing` prints a `SEND` of a small
notice (`<concord-missing …>`) addressed to the other agents: **send it**. Only you know the agent was marked missing,
and with the notice their own picture reaches the same outcome. There is no way to skip an agent that owes an anchor, a
seed or a repair: stop, report that the run could not be completed and is not certified, and quote the last output.

`--format json` is accepted by every command for structured output.

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

**Every agent holds the same picture of the run.** Each agent's own script runs the same decision engine over the
same replies (anchors, proposals, ratings with their reasoning), so each one knows when its turn comes. Prior
proposals in the Seed message are a flat anonymized list; each agent's own calibration is part of its own context.

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
deadline can cut the run short at any time — report what you have.

---

## Rules you must not break

1. **Never certify a floor over an agent you did not hear from.** Retry a silent agent once, then `mark-missing`.
   The tool will drop it from that candidate's floor, refuse to mark the run `SUCCESS`, and the final report
   will list it under `unknown`. Never substitute a guess for a missing score.
2. **`satisfaction_threshold` never moves.** Not by you, not by a sub-agent, not to rescue a run. A floor that moves
   when it binds is not a floor. If nothing feasible exists, `BEST_EFFORT` plus the shortfall is the correct output,
   and relaxing the bar is a decision for the human or the upstream protocol.
3. **Anchors are rated once**, at setup. `--overwrite` exists for genuine miscalibration and invalidates every
   comparison made before it.
4. **One proposer per round**, chosen by the tool, never by volunteering.
5. **Send what `handle` prints, as printed, and nothing else.** Never write a message of your own, never add a reply
   template, never ask an agent for a rating or a repair. Paraphrasing or reformatting is how a run turns into
   unparseable replies.
6. **Run to the end.** Do not stop before `handle` reports `RUN COMPLETE`. Until then there is no outcome: do not carry
   out the action the run was meant to decide, and do not write a summary that calls a result "certified" or says
   the protocol "converged". If you cannot get there (an agent will not reply and the tool offers no way forward),
   stop, say that the run is not complete, and quote the last tool output.
7. **Announcing an action is not taking it.** Once `handle` prints a message to send, your turn is not over until you
   have actually sent it.

---

## If you cannot run the script

The tool is authoritative. Only if you have no execution environment, run the
protocol by hand — and say in your final answer that the result was computed
manually and is not a verified certificate.

```
setup:  ANCHOR all → PROPOSE_SEED all → SCORE all seeds by all agents
        s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0, 1)
        x_star = SELECT(pool)
loop:   if min_j s_j(x_star) >= satisfaction_threshold and nobody is missing → SUCCESS (the outcome)
        if t == B                                        → BEST_EFFORT (the outcome)
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
| `handle` keeps printing `NONE` and nothing arrives | Agents are waiting for something they did not receive | Run `handle` with no input (nudge) |
| One agent's scores are erratic across similar candidates | Criteria drift mid-run | Re-anchor with `--overwrite` if budget allows |
| An agent replies in prose or plain text | The message was paraphrased, truncated, or not sent as printed | Send it again exactly as `handle` printed it |
