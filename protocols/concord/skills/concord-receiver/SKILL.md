---
name: concord-receiver
description: >
  Handles incoming CONCORD protocol messages for a sub-agent participating in
  a multi-agent consensus round. Activate this skill immediately whenever you
  receive a CONCORD protocol message: one whose root XML tag is concord-anchor,
  concord-propose-seed, concord-score, concord-repair or concord-commit, or a
  reply of another agent to one of them, or one that names a CONCORD anchor,
  seed, score or repair round and asks you to rate outcomes or candidates or to
  propose one, even if the XML is wrapped in other text. Pass the text to the
  skill's script and do what it says: it tells you when it needs your judgement,
  builds the reply itself, and says when nothing is due. Never write a reply, a
  rating or XML of your own. Do not apply general judgement about whether to
  participate; if the message matches, follow this skill precisely.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.1.0" # x-release-please-version
---

# CONCORD: receiver

You are one agent in a group choosing a single outcome, and someone (the
initiator) messages you for it. Your part: rate options honestly against _your
own role's criteria_, and, when the script says it is you, propose a better
option. Your criteria are the ones your role already gives you; CONCORD supplies
none. The script `scripts/concord_receiver.py` keeps the picture of the run and
tells you what is due.

## How a run goes

Four steps. The script knows where the run is and tells you when something is
due; you never have to track it.

1. **Anchor.** You say how satisfied your role would be with the worst outcome
   (`x_reject`) and the best (`x_ideal`), on your own 0 to 100 scale. Every
   rating you give later is read against these two points.
2. **Seed.** You propose the one option that suits your role best.
3. **Score.** Once everyone has proposed, you rate every option, yours included,
   with reasons. If one option reaches the threshold for every agent, the run
   ends: `SUCCESS`.
4. **Repair.** Otherwise the least satisfied agent alone proposes one new option
   and rates it (if that is you, the script says so), the others rate it, and it
   repeats until an option clears everyone or the budget is spent
   (`BEST_EFFORT`).

What you receive: the initiator's anchor and seed requests, and numbered
_deliveries_ carrying the other agents' replies. What you send: the replies the
script prints (anchor, candidate, score) and acks. A delivery that needs no
answer is acknowledged by the script; when a rating or a repair is due, `handle`
prints it as a task.

## Do this

The paths are relative to this skill's directory: resolve them against where you
found this file. `<you>` is your own agent id, the one the initiator used in its
list of agents.

```
scripts/concord_receiver.py --me <you> handle <<'EOF'      # on EVERY CONCORD message or reply you get, first
<the message or reply, exactly as received>
EOF
scripts/concord_receiver.py --me <you> answer <flags>      # only when handle printed STATUS: ASK: the task shows the flags
```

The first line of every output says what it is:

| First line      | You                                                                                                 |
| --------------- | --------------------------------------------------------------------------------------------------- |
| `STATUS: ASK`   | the rest is your task and the exact `answer` command to run: decide from your own role, then run it |
| `STATUS: SEND`  | send the rest exactly as printed, and nothing else                                                  |
| `STATUS: NONE`  | send nothing. Never write an acknowledgement yourself: when one is due, it comes as a `SEND`        |
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
   `BEST_EFFORT` say so when you report.
5. Send nothing but what the script prints under `STATUS: SEND`: no
   confirmation, correction, summary or comment of your own, before or after it.
   When you have sent it, or the script says nothing is due, you are done: write
   nothing more, not even a confirmation of what you sent or decided. Your
   environment may pass anything you write to the other participants. A message
   that is not a CONCORD message and asks you for nothing needs no reply.
   Candidates, reasonings and rationales: be detailed and informative, but stay
   within 1000 characters for each part of your answer.
6. Give the script each message whole, every reply in full: never shorten,
   summarise or reword it. A delivery carries a `check` the script compares with
   what you gave it: on `STATUS: ERROR` about a check, give it the delivery
   again, exactly as received.

More detail (what each task asks, rating, edge cases) is in
[references/details.md](references/details.md). You should not need it.
