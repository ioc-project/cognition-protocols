# CONCORD initiator: full reference

The long form of the skill: every phase, the relay, the watchdog and diagnostics. Not needed to run the protocol: the command reminds you of the next step at every call. Read it when the command's output is not enough.

## Initiator skill (long form)

Drive the CONCORD protocol to converge on a single outcome that clears
`satisfaction_threshold` for **every** agent in a named set — not a majority, not an average.
`uvx ioc-concord initiator` opens the run and says what to send; your role is to
pass it what arrives and send what it tells you to send. The result is `SUCCESS`
(every agent at or above `satisfaction_threshold`), `BEST_EFFORT` (which agent is
short, and by how much) or `ABORTED` (an agent stayed silent, and nothing is certified).

**The agents' commands drive every decision — each one selects the incumbent,
identifies the bottleneck, and decides SUCCESS vs BEST_EFFORT from the same messages.** Do not
compute scores, floors, leximin comparisons or the bottleneck yourself — the
certificate is only worth something if the algebra behind it is exact. Never author a
role's proposal, score, or repair on its behalf yourself, under any
framing ("seed", "represent", "play the role of") — everything you pass to the command
must be a message that agent actually sent. If you cannot
reach an agent, that's a failure to report, not a gap to fill in yourself.

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
uvx ioc-concord initiator init …                               once: creates the run (prints the state path)
uvx ioc-concord initiator --state <path> handle                first call, no input: prints the first message to send
uvx ioc-concord initiator --state <path> handle <<'EOF'        every later call: pass what arrived
<the reply or replies, exactly as received>
EOF
```

`<path>` is whatever `init` reported — the same value on every call in this run, never one you chose or
reconstructed yourself. `handle` is the only command you need: it records what you give it and ends its output
with one of three states on the first line:

| First line | Meaning | You |
|---|---|---|
| `STATUS: SEND` | the rest is what to send: the messages to pass on, or a nudge | send it exactly as printed, to the agents it names |
| `STATUS: NONE` | there is nothing to send | wait for the next reply. Send nothing. |
| `STATUS: ASK` | a decision is yours (only from `watchdog`: the text says what) | decide, then act as it says |

Your whole part in a run:

1. Run `handle` with no input: it prints the opening message (`<concord-init>`). Send it to everyone, then, if you
   can run a command in the background, start the watchdog there (see "Nudging, and the watchdog").
2. Every time an agent replies, pass the reply, exactly as received, to `handle`. When it prints a `SEND`, send it.
3. That is all, to the end. The agents give their first answers, rate the proposals and make the repairs among
   themselves (see "Who does what" below); you keep passing their messages in and sending what `handle` prints.
4. The run is over when `handle` prints `The run is over.` (the outcome and the winning candidate, as the first agent
   in `--agents` reported them in its `<concord-result>`) or `The run was aborted` (an agent stayed silent: there is no
   outcome). **That is your result: report it to whoever asked for the run.** You are not a participant, so you have no
   scores of your own: the report gives no final scores or shortfall, and its status is `unverified` (the result of
   the reporter, with nothing of your own to compare it with). Every agent reaches the same outcome by itself, from
   the same ratings it has seen, and has nothing to do once the run is over. If the last `handle` printed a `SEND`
   (the last messages to pass on), send that first; the report is then on its error output.

**You never ask for ratings and never ask for a repair, and you never write a message of your own.** Do not run
any other command to find out what to send: if `handle` printed `NONE`, nothing is to be sent.
If replies stop arriving, do not guess how long to wait: the watchdog (see "Nudging, and the watchdog" below)
returns the nudge for you.

### What `handle` prints

- **Messages to pass on.** Every message `handle` records is passed on, as received, to every agent (the `--- to:`
  line names them all, and an agent ignores its own), so each agent builds the same picture of the run without any
  group communication. That includes an exact repeat of a message: it is passed on again. Use `init --no-relay` only
  if the agents already see each other's messages: `handle` then passes nothing on, and prints `STATUS: NONE` with
  `[recorded; nothing to send for it yet]` for a message it recorded.
- **Every message of an agent carries a `check`**, a short fingerprint of its text (whitespace aside). An agent whose
  command finds that a copy was shortened or reworded on the way records nothing from it and answers with a
  `<concord-error reason="check">` to its author, whose command sends the original again in a `<concord-resend>`. To
  you these are messages like any other: pass them to `handle`, and send what it prints.
- **A nudge is the opening message again.** You are not a participant: the only messages owed to you are the first
  answers and the result. When a first answer is missing, a nudge sends `<concord-init>` again (inside a
  `<concord-resend>`), and an agent that has answered already sends its first answer again. From the Score step on,
  the agents' own commands ask each other for what is missing (`<concord-error reason="missing">`), and there is
  nothing for you to nudge.
- **`handle` with no input nudges only when a timer of the run has expired** (300 seconds after the last send), and
  never within 30 seconds of a send: it then prints `STATUS: NONE`, says how long ago it sent, and tells you to wait and
  keep passing replies as they arrive. `handle --force` nudges now, without waiting for the timer. The 30 seconds is the
  environment variable `CONCORD_MIN_NUDGE` (in seconds; 0 turns the guard off). The watchdog is not held back: it has
  its own timeout (60 seconds by default) and nudges when that ends, as `--force` does.
- **One block.** A `SEND` holds one block: a line `--- to: NAME[, NAME]` naming every agent, then the messages. Send
  it exactly as printed, without that line: add no introduction, no summary, no reply template of your own.
- **Send once, to everyone it is for, before waiting for any reply.** Never send to one agent, wait for its reply, and
  only then send to the next.
- **Replies may arrive in any order**, and before you expected them: pass them in as they come, several in one call if
  you like. Each message names its author (its `from` attribute), so no `--- from:` line is needed, and `--from` is
  accepted and ignored.
- **`[not recorded: …]`** after a `STATUS: NONE` means what you passed holds a CONCORD message that could not be used:
  it was cut off or damaged, of another run or version, or already known. If it was cut, pass it again as it arrived;
  else wait for the agent's next message. Never edit a reply or its numbers and never write one for the agent. Text
  that holds no CONCORD message (prose) is ignored without a note.

### Who does what

Each agent keeps its own picture of the run (every anchor, proposal and rating it has seen, run through the same
decision engine; its command works it out) and moves as soon as it says it is its turn: it rates once every
first answer is in, and when every rating is in, the agent that is least satisfied makes the repair on its own and rates
its own new candidate in the same message. Nobody needs to ask. When the run ends, the first agent in `--agents` sends
the outcome to everyone in a `<concord-result>`, and each other agent compares it with its own outcome.

### Nudging, and the watchdog

When a message is lost or an agent has stopped, nothing arrives, so nothing wakes you. If you can run a command in the
background, start a watchdog there as soon as the first message is out, and re-arm it every time it returns. If you
cannot, do not run it in the foreground (it blocks until the run moves or ends): skip it, and when a first answer you
expect does not come, run `handle --force` with no input by hand, which sends the same nudge.

```
uvx ioc-concord initiator --state <path> watchdog [--timeout 60] [--max-nudges 3]
```

It blocks while the run makes progress. It returns when:

- **the run has not moved for `--timeout` seconds.** It prints the nudge (see above), as a `STATUS: SEND`, or a
  `STATUS: NONE` when there is nothing to nudge. Send what it prints, as printed, and re-arm. Agents already hold what
  they hold, so a repeat does no harm. This is the same as running `handle --force` with no input by hand.
- **`--max-nudges` nudges in a row did nothing.** It prints `STATUS: ASK`: the run is stuck and the decision is yours.
  Wait longer (re-arm), or stop and report that the run could not be completed.
- **the run is over.** It prints `STATUS: NONE` and the report, which is your result.

Each nudge asks a silent agent once more, and the run allows 3 such requests (not settable): when they are used up,
the next nudge is a `<concord-cancel reason="missing-agent">`, which aborts the run. So keep `--max-nudges` at 3 or
less, unless you mean the run to abort. Progress resets the count: any recorded message. Run the watchdog instead of
guessing how long to wait.

---

## Starting a run

```
uvx ioc-concord initiator init \
  --agents security,cost,reliability \
  --satisfaction-threshold 0.6 --eps 0.05 -B 8 \
  --schema  '{"type": "string", "description": "what one option is, in plain words"}' \
  --context "the problem statement, inline" \
  --x-reject "an outcome the shared context calls unacceptable" \
  --x-ideal  "an outcome the shared context calls fully satisfying"
```

The ids in `--agents` are 1 to 64 letters, digits, `_` or `-`, at least 2 of them. Their order matters: the first
agent wins ties and reports the outcome to the others.

`--schema` is the JSON Schema for a candidate, which a receiver enforces: it refuses any seed or repair that does
not satisfy it, and says why. Two forms cover most runs:

- **Open format**, the default: `{"type": "string", "description": "a remediation plan"}`. The description is what the
  agents read as the format. Nothing else is enforced, and agents may propose hybrids.
- **Closed list**: `{"enum": ["A", "B", "C"]}`. Every proposal, seeds and repairs alike, must be exactly one of them (a
  repair may re-propose one already in the run), and extra wording is refused. Use it only when the options were
  handed to you (the task or a tool output lists them) and copy each exactly as given; never write the list yourself,
  because a list you invent shuts out better answers that nobody listed.

Also supported: `"pattern"` (a regular expression the candidate must match), `"minLength"` and `"maxLength"`, `"title"`.
A schema needs a `"description"` or an `"enum"`. Any other keyword, or invalid JSON, is refused by `init`, so a rule is
never silently left unenforced.

Pass each of these as a plain argument value — multi-line text is fine,
there's no shell involved when you invoke this through a tool call rather
than a literal shell command. If you are typing this as a literal shell
command and already have the content in a file, `@path` reads it from there
instead (e.g. `--schema @schema.txt`) — but that's a convenience for that one
case, never a requirement: don't write a file just to satisfy this flag.

`init` prints the exact state path it used as `State:`, and what to run next; nothing else. It uses the `--state`
value (by default `concord-state.json`) when no file is there yet, and otherwise adds a random suffix to it, so that
two runs never share a state. Never guess or reconstruct this path yourself: copy `init`'s own reported value
verbatim into every remaining call in this run. The first message comes from `handle`. Every message carries the
run's id (`run_id`), and `handle` does not record a message of another run.

**Choosing `satisfaction_threshold` is the decision that determines whether CONCORD does anything
at all.** The seeds are what each agent proposes unilaterally, from its own
viewpoint. If `satisfaction_threshold` sits below the best seed's floor, that seed is feasible
immediately, the run commits at setup, no repair round ever executes, and the
protocol has reduced to "pick the best unilateral proposal" at the cost of a
scoring matrix. The floor only does work *above* the level the agents reach on
their own.

So set `satisfaction_threshold` at the standard the outcome really has to meet, not at a level
you are confident is reachable. `0.6` is a starting point, not a safe default —
on a well-aligned roster the seeds often clear it. A run that commits at `t = 0`
is one where no `<concord-repair>` ever passed through `handle`: the report does not flag it, so check for it
yourself. If seeds clear the threshold without any negotiation,
`satisfaction_threshold` was set below what the agents naturally agree on and
should be raised, in a new run: the threshold of a run never changes.

**`--x-reject`/`--x-ideal` describe the objective, never the means.** Set them
to the outcome `--context` is actually trying to produce, as a plain
before/after symptom state in its own numbers — e.g. error rate > 40% →
`x-ideal` "error rate returns to 0", `x-reject` "stays above 40% or rises."
Never a proposal: no naming an action, and no language about *how* it's
reached (confidence, verification, reversibility) — that's the usual way one
creeps back in. An anchor that pre-names an action biases every score against
it before any real candidate exists, since one agent's own unilateral pick
becomes the reference point the whole run is measured against.

Self-check: if an anchor could pass as something an agent might actually
propose, rewrite it one level up. (A decision with almost no real alternatives
can still make the answer easy to infer even worded this way — an inherent
limit, not a sign to add more words.)

---

## An agent that stays silent

The run never goes on without an agent: there is no way to skip one. A silent agent is
asked again by the timers: by your nudges for a first answer (`<concord-resend>` of the init), and by the other agents'
commands for a score, a repair or the result (`<concord-error reason="missing">`). When the 3 requests of the run are
used up, the agent that asked aborts the run and sends `<concord-cancel reason="missing-agent">`; every agent that
gets it passes it on and ends the run too. Pass a cancel to `handle` like any message, and send what it prints. Your
only decision is to wait (`watchdog`) or to stop: if you stop, report that the run could not be completed and is not
certified, and quote the last output.

`--format json` gives `init` and `status` structured output.

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
protocol that the receiver's command enforces: it refuses an anchor or a rating
outside this range, since that usually means the agent used a different scale
entirely.

**Every agent holds the same picture of the run.** Each agent's own command runs the same decision engine over the
same messages (anchors, proposals, ratings with their reasoning), so each one knows when its turn comes, and each
reaches the same outcome. The first agent in `--agents` sends its outcome to the others in a `<concord-result>`; each
compares it with its own and reports a difference, which makes the run `disputed`.

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

**Stopping.** `SUCCESS` the moment every agent clears `satisfaction_threshold`; `BEST_EFFORT` when the round cap
`B` is spent; `ABORTED` when an agent stays silent after its requests, which certifies no candidate. If an external
deadline cuts the run short, there is no outcome: report that the run could not be completed.

---

## Rules you must not break

1. **Never certify a floor over an agent you did not hear from.** A silent agent is asked again by the timers, and
   when it stays silent the run is aborted: nothing is certified. Never substitute a guess for a missing score.
2. **`satisfaction_threshold` never moves.** Not by you, not by a sub-agent, not to rescue a run. A floor that moves
   when it binds is not a floor. If nothing feasible exists, `BEST_EFFORT` plus the shortfall is the correct output,
   and relaxing the bar is a decision for the human or the upstream protocol.
3. **Anchors are rated once**, in each agent's first answer, and stay fixed for the run: a different one sent later
   is ignored. Miscalibrated anchors call for a new run.
4. **One proposer per round**, chosen by the tool, never by volunteering.
5. **Send what `handle` prints, as printed, and nothing else.** Never write a message of your own, never add a reply
   template, never ask an agent for a rating or a repair. Paraphrasing or reformatting is how a run turns into
   unparseable replies.
6. **Run to the end.** Do not stop before `handle` reports that the run is over (`The run is over.`) or aborted.
   Until then there is no outcome: do not carry out the action the run was meant to decide, and do not write a
   summary that calls a result "certified" or says the protocol "converged". An aborted run has no outcome either.
   If you cannot get there (an agent will not reply and the tool offers no way forward), stop, say that the run is
   not complete, and quote the last tool output.
7. **Announcing an action is not taking it.** Once `handle` prints a message to send, your turn is not over until you
   have actually sent it.

---

## If you cannot run the command

The tool is authoritative. Only if you have no execution environment, run the
protocol by hand — and say in your final answer that the result was computed
manually and is not a verified certificate.

```
setup:  FIRST ANSWER all (anchors, seed, own score) → SCORE the others' seeds, by all agents
        s = clamp((raw - raw_reject) / (raw_ideal - raw_reject), 0, 1)
        x_star = SELECT(pool)
loop:   if min_j s_j(x_star) >= satisfaction_threshold     → SUCCESS (the outcome)
        if t == B                                        → BEST_EFFORT (the outcome)
        t += 1
        b = argmin_j s_j(x_star)          # ties: lowest index in A
        REPAIR(b) → x_new with b's own score → SCORE x_new by the other agents
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
| A repair scores below `satisfaction_threshold` for its own proposer | That agent's anchors are miscalibrated | Anchors are fixed for the run: report it, and sharpen `--x-reject`/`--x-ideal` for a new run |
| `handle` keeps printing `NONE` and nothing arrives | Agents are waiting for something they did not receive | Run `watchdog` (or `handle --force` with no input) |
| One agent's scores are erratic across similar candidates | Criteria drift mid-run | Report it; a new run with sharper anchors may help |
| An agent replies in prose or plain text | The message was paraphrased, truncated, or not sent as printed | `handle` ignores prose: send the last block again exactly as `handle` printed it (`handle --resend` prints it again) |
