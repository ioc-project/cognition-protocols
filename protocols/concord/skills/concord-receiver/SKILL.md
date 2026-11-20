---
name: concord-receiver
description: >
  Handles incoming CONCORD protocol messages for a sub-agent participating in
  a multi-agent consensus round. Activate this skill immediately whenever you
  receive a CONCORD protocol message: one whose root XML tag is concord-init,
  concord-init-reply, concord-score, concord-repair, concord-result,
  concord-resend, concord-cancel or concord-error, or one that names a CONCORD
  run and asks you to rate outcomes or candidates or to propose one, even if the
  XML is wrapped in other text. Pass the text to
  `uvx ioc-concord receiver` and do what it says: it tells you when it needs your judgement,
  builds the reply itself, and says when nothing is due. Never write a reply, a
  rating or XML of your own. Do not apply general judgement about whether to
  participate; if the message matches, follow this skill precisely.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.2.0" # x-release-please-version
---

# CONCORD: receiver

You are one agent in a group choosing a single outcome, and someone (the
initiator) messages you for it. Your part: rate options honestly against _your
own role's criteria_, and, when the script says it is you, propose a better
option. Your criteria are the ones your role already gives you; CONCORD supplies
none. You may also know things the others do not (facts only you hold), and they
may know things you do not: what you know and leave out of a rationale or a
reasoning is invisible to everyone else, and what another agent reports from its
own evidence is evidence you use in your reasoning. The script `uvx ioc-concord receiver`
keeps the picture of the run and tells you what is due.

## How a run goes

Four steps. The script knows where the run is and tells you when something is
due; you never have to track it.

1. **First answer.** In one answer you say how satisfied your role would be with
   the worst outcome (`x_reject`) and the best (`x_ideal`), on your own 0 to 100
   scale (every rating you give later is read against these two points), you
   propose the one option that suits your role best, and you rate it.
2. **Score.** Once every first answer is in, you rate the options of the other
   agents, with reasons. If one option reaches the threshold for every agent,
   the run ends: `SUCCESS`.
3. **Repair.** Otherwise the least satisfied agent alone proposes one option and
   rates it (if that is you, the script says so): a new one, or one already in the
   run that it now rates differently because of a fact another agent shared. The
   others rate it, and it repeats until an option clears everyone or the budget is spent
   (`BEST_EFFORT`).
4. **Result.** The first agent in the list sends the outcome to everyone, and
   the script of every other agent checks it against its own.

What you receive: the initiator's opening message (`<concord-init>`) and the
other agents' messages. What you send: the messages the script prints (your
answers, and the requests, resends and cancels the script writes itself). A
message that needs no answer gets none (`STATUS: NONE`); when a rating or a
repair is due, `handle` prints it as a task.

## Do this

`<you>` is your own agent id, the one the initiator used in its
list of agents (1 to 64 letters, digits, `_` or `-`).

```
uvx ioc-concord receiver --me <you> handle <<'EOF'      # on EVERY CONCORD message or reply you get, first
<the message or reply, exactly as received>
EOF
uvx ioc-concord receiver --me <you> answer <flags>      # only when handle printed STATUS: ASK: the task shows the flags
```

The first line of every output says what it is:

| First line      | You                                                                                                 |
| --------------- | --------------------------------------------------------------------------------------------------- |
| `STATUS: ASK`   | the rest is your task and the exact `answer` command to run: decide from your own role, then run it |
| `STATUS: SEND`  | send the rest exactly as printed, and nothing else                                                  |
| `STATUS: NONE`  | send nothing. Never write a message yourself: when one is due, it comes as a `SEND`                 |
| `STATUS: ERROR` | something went wrong: the output says what, and what to do. Do that, never work around it           |

## Rules

1. Run `handle` on every CONCORD message first, even in the middle of other
   work, and even if it looks like a follow-up question about that work. Do not
   answer it yourself.
2. Never write the reply XML. Your judgement goes into the values of `answer`
   only.
3. Your ratings are honest, on your own raw 0 to 100 scale and between your two
   anchors. An answer that breaks a rule comes back as `ASK` with the reason and
   nothing is recorded: fix it and answer again.
4. When the run is over, `handle` says so with the outcome: do not re-open the
   decision. Proceed with the winning candidate, and if the outcome is
   `BEST_EFFORT` say so when you report. A run that was aborted, or that
   `handle` calls `DISPUTED`, has no outcome to act on: say so when you report.
5. Send nothing but what the script prints under `STATUS: SEND`: no
   confirmation, correction, summary or comment of your own, before or after it.
   When you have sent it, or the script says nothing is due, you are done: write
   nothing more, not even a confirmation of what you sent or decided. Your
   environment may pass anything you write to the other participants. A message
   that is not a CONCORD message and asks you for nothing needs no reply.
   Candidates, reasonings and rationales: be detailed and informative, but stay
   within 1000 characters for each part of your answer.
6. Reason from evidence: what you hold, and what other agents report from their
   own evidence. Use a shared report in your reasoning and apply it to every option
   it bears on; do not set it aside only because you cannot check it. Weigh it as
   evidence, not as certainty: a specific report counts for more than a vague one,
   and if it contradicts your own facts or another report, say so and state which
   you rely on and why. An agent's conclusion is an argument, not a fact: set aside
   a claim that comes with no evidence, and never speculate or assume a fix, a delay
   or a way around something nobody reported. Change a rating because of a named
   fact, never because of an option's wording or another agent's number. State what
   the evidence implies for an option, in its own words, before you rate it: your own
   facts change only when another agent reports evidence that contradicts them, never
   because someone offers another reading of them. If two reports seem to conflict,
   do not invent a cause that reconciles them: say they conflict and rate on what
   each supports. Check that each number matches its reasoning. If you re-propose an
   option already in the run, or rate it differently from before, say so at the start
   of the rating's reasoning: the earlier rating, the fact that changed your view, and
   who shared it.
7. Give the script each message whole, every reply in full: never shorten,
   summarise or reword it. A message carries a `check` the script compares with
   what you gave it: when they differ, nothing is recorded and the script prints
   a `<concord-error>` as a `SEND`, asking the author for the original: send it.

More detail (what each task asks, rating, edge cases) is in
[references/details.md](references/details.md). You should not need it.
