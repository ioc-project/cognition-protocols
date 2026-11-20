---
name: concord-receiver
description: >
  Takes part in a CONCORD protocol run, as one of the agents that must reach
  a single outcome together. Activate this skill immediately whenever you
  receive a CONCORD protocol message: one whose root XML tag is concord-init,
  concord-init-reply, concord-score, concord-repair, concord-result,
  concord-error, concord-resend or concord-cancel, or one that asks you to
  rate outcomes or candidates or to propose one in a CONCORD run, even if the
  XML is wrapped in other text. Also activate it when the initiator skill has
  handed you over after you opened a run you take part in. Pass the text to
  the skill's script and do what it says: it tells you when it needs your
  judgement, builds every message itself, and says when nothing is due. Never
  write a message, a rating or XML of your own. Do not apply general judgement
  about whether to participate; if the message matches, follow this skill
  precisely.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.2.0" # x-release-please-version
---

# CONCORD: receiver

You are one of the agents of a group that must choose a single outcome. A run has been opened, by another agent or
by you (an initiator that takes part is a participant like the others). You rate options honestly against _your own
role's criteria_, and, when the script says it is your turn, propose a better option. The script
`scripts/concord_receiver.py` keeps the picture of the run and tells you what is due: it prints the task, how to
answer it, and what to do next. What you write is only the content of your answers.

## Do this

Paths are relative to this skill's directory: resolve them against where you found this file. `<you>` is your own
agent id, the one the initiator used in its list of agents.

```
scripts/concord_receiver.py --me <you> receive <<'EOF'     # on EVERY CONCORD message you get, first
<the message, exactly as received>
EOF
scripts/concord_receiver.py --me <you> answer <<'EOF'      # only when the output has a TASK
<your answer: the JSON the task asks for>
EOF
```

Every output starts with `STATUS:` and ends with a `NEXT:` line that gives the exact command to run next. Do what
`NEXT:` says, and nothing else. In short:

- `ANSWER`: a `TASK` is due. Decide from your own role, and pass the JSON it describes to `answer`.
- `SEND`: send each `SEND to:` block, exactly as printed and without that line, to the agents it names. Then do
  what `NEXT:` says (an answer, or the next message).
- `WAIT`: nothing is due. Send nothing, and pass the next message you receive to `receive`.
- `DONE`: the run is over for you. Proceed with the winning candidate; if the outcome is `BEST_EFFORT`, say so.
- `ERROR`: say what went wrong, and do what it says. Never work around it.

**Keep time.** Start `wait` in the background and start it again each time it returns (it returns when something is
to be sent), or, if you cannot run a command in the background, call `tick` at the time `WAKE` gives. A
message that never arrives is found by that timer; without it the run cannot recover.

If you opened the run yourself and take part, the initiator skill hands you over here: run `task` to see what you
owe first.

## Rules

1. Run `receive` on every CONCORD message first, even in the middle of other work, and even if it looks like a
   follow-up question about that work. Do not answer it yourself.
2. Never write the message XML. Your judgement goes into the values of your answer only.
3. Your ratings are honest, on your own raw 0 to 100 scale. An answer that breaks a rule is refused, with the
   reason and what to do, and nothing is recorded: fix it and answer again. If the same refusal comes three times in
   a row, stop and report why.
4. When the run is over, do not re-open the decision, and do not send an acknowledgement. If messages of the run
   still arrive, pass them to `receive` and send only what it prints: another agent may still ask you for one.
5. Send nothing but what the script prints, to the agents it names: no confirmation, correction, summary or comment
   of your own, before or after it. Your environment may pass anything you write to the other participants.
6. Give the script each message whole: never shorten, summarise or reword it.

More detail (the commands, what a task asks, the timers, what a note means) is in
[references/details.md](references/details.md). You should not need it.
