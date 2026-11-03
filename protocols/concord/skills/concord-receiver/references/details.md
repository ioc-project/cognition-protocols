# CONCORD receiver: full reference

The long form of the skill: what each task asks, how to rate, the loop's edge cases. Not needed to take part: the script's prompts and reminders carry this at every step. Read it when the script's output is not enough.

## Receiver skill (long form)

You are one agent in a group deciding on a single outcome. An initiator drives the protocol by messaging you.
You may also see other agents' replies. [`scripts/concord_receiver.py`](../scripts/concord_receiver.py) keeps the picture
of the run and tells you when it is your move.

**Your job:** rate candidates honestly against *your own role's criteria*, and — when you are the least-satisfied
agent — propose a better alternative.

**Your criteria** are the ones your role already gives you (your system prompt, your assigned responsibility, e.g.
"security", "cost", "latency"). CONCORD does not supply them and does not ask you to change them.

Every `concord_receiver.py` invocation below is written relative to this skill's own directory, not necessarily your
current working directory: resolve the path against where you found this file.

---

## The loop: pass it to the script, do what it says

Every CONCORD message you receive, and every reply of another agent that you get to see, goes to
`concord_receiver.py`. The script keeps the picture of the run in a state file of your own (every anchor, proposal
and rating with its reasoning, run through the same decision engine as the initiator's) and decides what
follows. Every call ends in exactly one of three states, on the first line of its output:

| First line | Meaning | You |
|---|---|---|
| `STATUS: NONE` | nothing is due from you | send nothing (never write an acknowledgement yourself: when one is due, `handle` returns it as `SEND`). Wait for the next message. |
| `STATUS: ASK` | the script needs your judgement | the rest of the output is your prompt: decide, then answer with `answer` |
| `STATUS: SEND` | a reply is ready | send the rest of the output exactly as it is, nothing else |

```
1. python3 <skill dir>/scripts/concord_receiver.py --me <you> handle <<'EOF'
   <the message or reply, XML exactly as received>
   EOF
2. STATUS: ASK -> read the task, decide from YOUR role's criteria, then:
   python3 <skill dir>/scripts/concord_receiver.py --me <you> answer <<'EOF'
   { …the JSON shape the prompt shows… }
   EOF
   which prints STATUS: SEND and the reply. Send it, exactly as printed.
```

`--me <you>` is your own agent id, the one in the initiator's `--agents` list (for example `cost`).

- Do step 1 on **every** message and reply you get, first, whatever it says. Never answer from memory. Several
  messages at once are fine; a reply names its author, and for one that does not, put a line `--- from: NAME`
  before it. This applies even in the middle of other work: a CONCORD message often arrives right after an ordinary
  task for the same initiator, and it can look like a follow-up question about that work. It is not. Do not
  answer it with an assessment, a status table, a recommendation or an acknowledgement.
- **Never write the reply XML yourself**, and never put text before or after what the script prints. The script
  writes every document, with the right tags and escapes; a hand-written reply is rejected.
- The task comes from the picture, not only from a request: after the seeds are in you are asked to rate them, when
  a repair candidate appears you are asked to rate it, and when the ratings are complete and **you** are the least
  satisfied agent you are asked for the repair (`REPAIR ROUND, YOU ALONE`), together with the rating of your own
  new candidate, in one answer. If it is not you, the status is NONE and you stay silent: two agents never repair
  at once.
- Your judgement goes into the JSON only: the anchor numbers, the proposal text, ratings and their reasoning. If
  your answer is not usable the status is ASK again, with what was wrong.
- Messages may carry other agents' replies, relayed by the initiator: pass them to `handle` like anything else.
  In that mode each delivery is numbered, and **you always answer a delivery**: if you have something to say, the
  status is ASK (then SEND) and that reply is the answer; if you have nothing to say, `handle` returns `STATUS: SEND`
  with a small acknowledgement. Send it exactly as printed, like any other `SEND`: it is how the initiator knows the
  delivery arrived. (Without numbered deliveries there is nothing to acknowledge, and `NONE` means send nothing.)
- Give the script each delivery **whole**. When it carries other agents' replies it also carries a `check`, a fingerprint
  of those replies: if what you gave the script differs (a reply shortened, summarised or reworded), it prints
  `STATUS: ERROR` and records nothing. Give it the delivery again, exactly as received. If that fails a second time the
  script prints a `concord-error` for you to send to the initiator instead, and the delivery comes again.
- Be detailed and informative, but stay within 1000 characters for each part of your answer (the candidate, the
  rationale, and the reasoning for each candidate you rate).
- When you have sent what the script printed, or the script says nothing is due, write nothing more: not even a
  confirmation of what you sent or decided. Your environment may pass anything you write to the other participants.
- If you are asked again for something you already answered (the same anchor, seed, rating or repair), the status is
  `SEND` with that same reply: it did not arrive, so send it again as it is.
- If you are handed a batch that includes your own earlier replies, pass it whole.
- Your state is kept per run, in `state-<state id>.<you>.json`: the id is read from the messages (`<state_id>`) and
  your replies carry it, so a new run never picks up an old run's state. Only if you take part in several runs at
  once, add `--state-id <id>` to `answer` and `due`.
- `concord_receiver.py --me <you> due` prints the current status without recording anything.

---

## What you learn from other agents' replies

Everything you get to see informs you, whoever it was for: other agents' anchors, proposals, and above all the
reasoning in their ratings. A specific, checkable fact or argument in it (a cost, a constraint you had not known)
may change your own rating or your repair, and you should be able to say what changed. The number another agent
gave is never a reason: keep your scale and rate against your own criteria. Anchor first, from your own criteria,
before you read any candidate, and propose your own seed before you read anyone else's. Both hold, because the
phases are ordered: the script only asks for a rating after every seed is in.

---

## Report what you actually believe

The protocol's guarantee — every agent above `satisfaction_threshold` when it succeeds — rests on honest scores.
Inflating to seem agreeable, or deflating for leverage, breaks it silently. Never change `satisfaction_threshold`
and never soften your own criteria to make a candidate look acceptable. If you cannot be satisfied, say so: that is
a valid, useful outcome.

**You report raw ratings — never normalised ones.** Rate everything on **your own raw 0..100 scale**, the one that is
natural to your criteria. Do **not** convert it to a `[0,1]` satisfaction score: the initiator does that, from
the two anchor ratings you give, which is what makes your `0.6` mean the same degree of role-satisfaction as every
other agent's `0.6`:

```
s = clamp( (raw - raw_reject) / (raw_ideal - raw_reject), 0.0, 1.0 )
```

Your one obligation is that your raw scale stays **consistent for the whole run**: the same quality of outcome must
get the same number in round 7 as it did at anchoring time. The task prompt repeats your anchor numbers; place each
new candidate between them. Consistent is not the same as frozen: if a later candidate's rationale directly
addresses a concern you raised earlier — a real change, not a restatement of something you already rejected — your
rating should move, and you should be able to say in one sentence what changed your mind. Refusing to budge from an
earlier number once a counter-argument has been made is not honesty to your criteria, it is inertia.

Normalised scores in `[0,1]` appear in the incumbent's ratings that a repair task shows you. Those are for you to
read. Never echo one back as your own rating.

---

## What each task asks of you

**Anchor.** You get an outcome the shared context calls unacceptable (`x_reject`) and one it calls fully satisfying
(`x_ideal`). Rate both, as satisfaction of your role if each happened, on your raw 0..100 scale. `x_reject` should
get a LOW number, `x_ideal` a HIGH one strictly above it. Rate the two outcomes, not today's state: the common
mistake is answering "how true is this now?", which inverts the scale and is refused. Anchors are rated once and
stay fixed for the whole run. They are satisfaction ratings, not metrics: do not put an error rate or a latency in
these fields.

**Seed.** Propose the single candidate you believe best satisfies your role's criteria, in the candidate format
given. Do not hedge, do not offer alternatives, do not pre-compromise for other roles: this is the round where your
role's viewpoint is represented undiluted. The rationale must carry the concrete facts and evidence from your
role's criteria that support it, the trade-offs you considered, and why it will hold up under scrutiny: other
agents use it to detect incorrect logic.

**Score.** Rate each candidate listed, including one you proposed yourself, on your raw scale, and give the concrete
reasons for each number: the specific properties that drove it, the evidence from your criteria, and any false claim,
factual error or flawed step you see in the candidate's reasoning. "Too expensive" is not reasoning; "adds ~$400 a
month in hosting, over the budget" is. Never refuse to score: a candidate you consider terrible gets a low number
with honest reasons.

**Repair** (only when the script says it is you). You are the least satisfied agent on the incumbent, and you are the
only one proposing this round. Read the incumbent's ratings and their reasoning, not just the numbers. Propose ONE
new candidate that: (1) clears your own bar: something you would rate close to your `x_ideal`, not marginally above
what you just rejected; (2) does not knowingly push any other agent below the threshold: infer from their reasoning
what they are protecting and keep it; (3) is genuinely new, not a cosmetic variant of a candidate already scored;
(4) conforms to the candidate format; (5) answers the strongest reasoning against your position, in your rationale:
agree with part of it, refute it, or explain why it does not apply. Rate your own new candidate honestly in the same
answer. If you believe no such candidate exists (every way of satisfying you drops someone else below their floor),
still give your best attempt and state the conflict plainly in the rationale: which agent, over what property. Do not
lower the threshold, do not weaken your criteria, and do not propose something you are not satisfied by.

**The end.** There is no message announcing the outcome. When your own picture holds every rating, `handle` says the
run is over, with the outcome (`SUCCESS`, or `BEST_EFFORT` when the repair budget is spent or an agent was marked
missing: the initiator passes that on to you) and the winning candidate. Nothing is due from you then: do not
re-open the decision, do not re-propose, and do not send an acknowledgement. If `SUCCESS`, proceed with the winning
candidate. If `BEST_EFFORT`, say so when you report to whoever triggered the run, naming the agents below their floor.
(A `<concord-commit>` from an older initiator is still understood and settles the outcome.)
