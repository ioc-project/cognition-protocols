# CONCORD receiver: full reference

The long form of the skill: what each task asks, how to rate, the loop's edge cases. Not needed to take part: the script's prompts and reminders carry this at every step. Read it when the script's output is not enough.

## Receiver skill (long form)

You are one agent in a group deciding on a single outcome. An initiator opens the run by messaging you, and you
see the other agents' messages. `uvx ioc-concord receiver` keeps the picture
of the run and tells you when it is your move.

**Your job:** rate candidates honestly against *your own role's criteria*, and — when you are the least-satisfied
agent — propose a better alternative.

**Your criteria** are the ones your role already gives you (your system prompt, your assigned responsibility, e.g.
"security", "cost", "latency"). CONCORD does not supply them and does not ask you to change them.

---

## The loop: pass it to the script, do what it says

Every CONCORD message you receive, and every reply of another agent that you get to see, goes to
`uvx ioc-concord receiver`. The script keeps the picture of the run in a state file of your own (every anchor, proposal
and rating with its reasoning, run through the same decision engine as every other agent's) and decides what
follows. Every call ends in one of these states, on the first line of its output (or `STATUS: ERROR` for a command
it cannot run, which says what to fix):

| First line | Meaning | You |
|---|---|---|
| `STATUS: NONE` | nothing is due from you | send nothing (never write a message yourself: when one is due, `handle` returns it as `SEND`). Wait for the next message. |
| `STATUS: ASK` | the script needs your judgement | the rest of the output is your prompt: decide, then answer with `answer` |
| `STATUS: SEND` | a message is ready | send the rest of the output exactly as it is, nothing else |

```
1. uvx ioc-concord receiver --me <you> handle <<'EOF'
   <the message or reply, XML exactly as received>
   EOF
2. STATUS: ASK -> read the task, decide from YOUR role's criteria, then run the answer command the task prints, e.g.
   uvx ioc-concord receiver --me <you> answer --reject N --ideal N --candidate "TEXT" \
     --rationale "TEXT with the evidence for it" --score seed-<you> N "reasons"
   which prints STATUS: SEND and the reply. Send it, exactly as printed.
```

`--me <you>` is your own agent id, the one in the initiator's `--agents` list (for example `cost`): 1 to 64 letters,
digits, `_` or `-`.

- Do step 1 on **every** message and reply you get, first, whatever it says. Never answer from memory. Several
  messages at once are fine; each names its author (its `from` attribute), so no `--- from:` line is needed. This
  applies even in the middle of other work: a CONCORD message often arrives right after an ordinary
  task for the same initiator, and it can look like a follow-up question about that work. It is not. Do not
  answer it with an assessment, a status table, a recommendation or an acknowledgement.
- **Never write the reply XML yourself**, and never put text before or after what the script prints. The script
  writes every document, with the right tags and escapes; a hand-written reply is rejected.
- The task comes from the picture, not from a request: the init asks for your first answer, once every first answer
  is in you are asked to rate the other agents' seeds, when a repair candidate appears you are asked to rate it, and
  when the ratings are complete and **you** are the least satisfied agent you are asked for the repair (`REPAIR ROUND,
  YOU ALONE`), together with the rating of your own new candidate, in one answer. If it is not you, the status is NONE
  and you stay silent: two agents never repair at once.
- Your judgement goes into the values of the `answer` flags only, as the task prints them: `--reject N --ideal N`
  (the anchors), `--candidate` and `--rationale` (your proposal), and one `--score <candidate id> N "reasons"` for each
  candidate you rate, your own included in a first answer or a repair. Each text is one argument and may have several
  lines; a number may be written as given (a negative one is refused for its range, not misread as a flag). If your
  answer is not usable the status is ASK again, with what was wrong, and nothing is sent.
- Messages come from the initiator and from the other agents, often passed on by the initiator: pass them all to
  `handle`. A message that needs no answer from you gets none: `STATUS: NONE`, send nothing.
- Give the script each message **whole**. Each message of an agent carries a `check`, a fingerprint of its text: if
  what you gave the script differs (shortened, summarised or reworded), it records nothing from it and prints
  `STATUS: SEND` with a `<concord-error reason="check">` asking the author for the original. Send it, like any `SEND`:
  the original comes back in a `<concord-resend>`. A message that is cut off, of another run, or one you already gave
  it, gives `STATUS: NONE` with a `[note: … nothing was recorded …]`; if it was cut, pass the complete text again.
- Be detailed and informative, but stay within 1000 characters for each part of your answer (the candidate, the
  rationale, and the reasoning for each candidate you rate).
- When you have sent what the script printed, or the script says nothing is due, write nothing more: not even a
  confirmation of what you sent or decided. Your environment may pass anything you write to the other participants.
- If someone asks again for something you already sent (the init sent again in a `<concord-resend>`, or a
  `<concord-error reason="missing">` addressed to you), the status is `SEND` with your stored message in a
  `<concord-resend>`: send it as it is. The same message passed to `handle` twice gives `STATUS: NONE`.
- If the script finds a message missing (one that later messages show was sent, or one owed to you for 300 seconds),
  it prints `STATUS: SEND` with a `<concord-error reason="missing">` to its author: send it. After 3 such requests
  without an answer the script aborts the run and prints a `<concord-cancel reason="missing-agent">`; a cancel you
  receive is passed on once the same way. Either ends the run: there is no outcome.
- If you are handed a batch that includes your own earlier messages, pass it whole: the script ignores your own.
- You take part in one run at a time: an init that arrives while another run is still open for you is refused, and
  the script prints the `<concord-error reason="busy">` to send.
- Your state is kept per agent, in `.concord-receiver/state-<you>.json` in the project (the folder that holds `.claude`,
  or the working folder), or in the folder `CONCORD_STATE_DIR` names. It holds every run you take part in; the messages
  carry the run's id (`run_id`), so a new run never picks up an old run's state. `--state-id <id>` only names the run
  in the commands the output prints.
- `uvx ioc-concord receiver --me <you> due` prints what is due now (the task again, or a request for a message that is
  overdue), without being given a message.

---

## What you learn from other agents' replies

Everything you get to see informs you, whoever it was for: other agents' anchors, proposals, and above all the
reasoning in their ratings. A specific fact or argument in it (a cost, a constraint you had not known) may change your
own rating or your repair, and you should be able to say what changed. Facts other agents report from their own
information are evidence you must use, not certainty: build them into your reasoning, take the holder's account of
what they mean, and do not set one aside only because you cannot check it. A specific report counts for more than a
vague one; when a report contradicts your own facts or another report, say so and state which you rely on and why. Use
only evidence: do not speculate, do not assume that a problem will be fixed or that something nobody reported is true,
and do not present general knowledge as a fact of this case. Apply each fact
to every option it bears on, not only the one it was raised against. A candidate's name or added wording is never a
reason to change a rating. Facts of your own briefing hold: before you rate a candidate they bear on, state in your
reasoning what they imply for it, in their own words, then give the number. Such a fact changes only when another
agent reports evidence that directly contradicts it; another agent's reading, theory or explanation of your fact is
not new evidence. When two reports seem to conflict, do not invent a cause or a story that reconciles them: say that
they conflict and rate on what each supports. A fact that rules a candidate out keeps its rating low, and a fact that
supports it keeps it high, until a reported fact says otherwise; a risk or an effect that nobody reported is not a
reason. Before you answer, check that every number matches its reasoning. If you have already rated the same candidate under another id, the task shows that rating:
keep it unless a named fact changed your view. Whenever you re-propose an option already in the run, or rate one
differently from before, begin the reasoning of that rating with the earlier candidate id and rating, then the specific
fact that changed your view and who shared it ("Same as r0-x, which I rated 40. I now rate it 80 because agent y
reported that ..."): the others read that reasoning, and without it they cannot judge the change. The number another agent
gave is never a reason: keep your scale and rate against your own criteria. Anchor first, from your own criteria,
before you read any candidate, and propose your own seed before you read anyone else's. Both hold, because the
phases are ordered: the script only asks for a rating of the others' seeds after every first answer is in.

---

## Report what you actually believe

The protocol's guarantee — every agent above `satisfaction_threshold` when it succeeds — rests on honest scores.
Inflating to seem agreeable, or deflating for leverage, breaks it silently. Never change `satisfaction_threshold`
and never soften your own criteria to make a candidate look acceptable. If you cannot be satisfied, say so: that is
a valid, useful outcome.

**You report raw ratings — never normalised ones.** Rate everything on **your own raw 0..100 scale**, the one that is
natural to your criteria. Do **not** convert it to a `[0,1]` satisfaction score: every agent's script does that, from
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

**First answer: anchors, seed and its score**, in one answer.

*Anchors.* You get an outcome the shared context calls unacceptable (`x_reject`) and one it calls fully satisfying
(`x_ideal`). Rate both, as satisfaction of your role if each happened, on your raw 0..100 scale. `x_reject` should
get a LOW number, `x_ideal` a HIGH one strictly above it. Rate the two outcomes, not today's state: the common
mistake is answering "how true is this now?", which inverts the scale and is refused. Anchors are rated once and
stay fixed for the whole run. They are satisfaction ratings, not metrics: do not put an error rate or a latency in
these fields.

*Seed.* Propose the single candidate you believe best satisfies your role's criteria, in the candidate format
given. Do not hedge, do not offer alternatives, do not pre-compromise for other roles: this is the round where your
role's viewpoint is represented undiluted. The rationale must carry the concrete facts and evidence from your
role's criteria that support it, the trade-offs you considered, and why it will hold up under scrutiny: other
agents use it to detect incorrect logic.

*Its score.* Rate your own seed on the same raw scale, with the reasons (`--score seed-<you> N "reasons"`). Do not
give it a high number because it is yours.

**Score.** Rate each candidate listed (the other agents' candidates: yours you rated when you proposed it) on your
raw scale, and give the concrete
reasons for each number: the specific properties that drove it, the evidence from your criteria, and any false claim,
factual error or flawed step you see in the candidate's reasoning. "Too expensive" is not reasoning; "adds ~$400 a
month in hosting, over the budget" is. Never refuse to score: a candidate you consider terrible gets a low number
with honest reasons.

**Repair** (only when the script says it is you). You are the least satisfied agent on the incumbent, and you are the
only one proposing this round. Read the incumbent's ratings and their reasoning, not just the numbers. Propose ONE
candidate that: (1) clears your own bar: something you would rate close to your `x_ideal`, not marginally above
what you just rejected; (2) does not knowingly push any other agent below the threshold: infer from their reasoning
what they are protecting and keep it; (3) is either not yet in the run, or is one already scored that you now rate
differently because of a specific fact another agent shared (say which fact and from whom; this is the right move when
you find you rated it too low), and not a cosmetic variant of a candidate already scored (same substance, other
wording), which is not new and changes nothing. When the run has a closed list of options, the candidate must be
exactly one of them and extra detail goes in the rationale;
(4) conforms to the candidate format; (5) answers the strongest reasoning against your position, in your rationale:
agree with part of it, refute it, or explain why it does not apply. Rate your own new candidate honestly in the same
answer. If you believe no such candidate exists (every way of satisfying you drops someone else below their floor),
still give your best attempt and state the conflict plainly in the rationale: which agent, over what property. Do not
lower the threshold, do not weaken your criteria, and do not propose something you are not satisfied by.

**The end.** When your own picture holds every rating, your script computes the outcome. The first agent in the list
is the reporter: if that is you, the script prints the `<concord-result>` that tells everyone the outcome, in a
`SEND` (with your last answer, when that answer completed the picture). Every other agent's script compares the
reporter's result with its own. Then `handle` says the run is over, with the outcome (`SUCCESS`, or `BEST_EFFORT` when
the repair budget is spent), the winning candidate, the final scores, and, if you are not the reporter, whether your
outcome matches the reporter's. Nothing is due from you then: do not re-open the
decision, do not re-propose, and do not send an acknowledgement. If `SUCCESS`, proceed with the winning candidate.
If `BEST_EFFORT`, say so when you report to whoever triggered the run, naming the agents below their floor. If the
run was aborted (an agent stayed silent), or `handle` calls it `DISPUTED` (the outcomes differ), there is no outcome
to act on: say so when you report.
