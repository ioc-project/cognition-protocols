---
name: concord-initiator
description: >
  Use when you are participating in, coordinating or facilitating a task that
  needs a single outcome that satisfies multiple agents with
  different, conflicting criteria, or who each hold different
  information about the problem — a unilateral choice is not enough. Activate on confirmed conflict (agents returned divergent outputs),
  anticipated conflict (distinct roles on a shared decision make divergence
  likely), or when you cannot tell whether roles will diverge —
  uncertainty is itself the signal. Also activate when you would relay outputs
  from other agents to a downstream agent whose criteria bear on the decision:
  include that agent directly instead of forwarding a summary.
  Apparent agreement upstream is no reason to skip: a downstream recipient
  may still hold different criteria. Also activate when an upstream
  protocol (e.g. ACCORD) has handed you a pre-verified conflict frame.
  Do NOT activate when a single role owns the decision or agents work on
  separate sub-problems with no shared decision variable.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.2.0" # x-release-please-version
---

# CONCORD: initiator

Get one outcome that every agent in a named set rates at or above a threshold. You open the run
and follow it; the agents rate and repair among themselves, and the script `scripts/concord_initiator.py`
does everything mechanical. The result is `SUCCESS` (every agent at or above the threshold) or
`BEST_EFFORT` (who is short, by how much). You never rate or propose anything yourself.

## When to use it

Use it when a decision has to satisfy agents with different, possibly conflicting criteria, or agents
who each know part of the picture, and no single one of them should decide: you can name those agents,
and you have or can write a shared format for the options (or the closed list of them). Use it when
conflict is confirmed, likely from their roles, or just uncertain: do not poll them first to find out.
Include every agent whose criteria bear on the outcome, including one you would otherwise feed a summary
to. Do not use it when one role owns the decision or the agents work on separate problems.

## Open the run

Paths are relative to this skill's directory: resolve them against where you found this file. `<you>` is
your own agent id. Run `start --help` before the first run: it says how to fill each argument.

```
scripts/concord_initiator.py --me <you> start --participants <a,b,...> \
  --context "<the problem>" --x-reject "<an outcome that fails every criterion>" \
  --x-ideal "<an outcome that fully satisfies them>" \
  (--option "<A>" --option "<B>" ... | --format "<what one option looks like>") \
  [--satisfaction-threshold 0.6]
```

- Name the agents exactly as they are called in your environment.
- **Possible answers.** If the task gives you a closed list of possible answers
  pass each one with `--option`, exactly as written. The agents then have to propose exactly one of
  them; nothing else is accepted. If there is no such list, describe in words what an answer looks like with
  `--format`, and the agents propose their own. Never write the list yourself.
- **If you take part in the decision yourself**, list yourself in `--participants`, first. `start` then
  hands you over: load the `concord-receiver` skill and use only its script from then on.
- Agents that think slowly need a longer `--timeout` (seconds an agent waits before it asks again): it must
  exceed the slowest turn of any agent, or the run aborts.

## Follow the run

Every output starts with `STATUS:` and ends with a `NEXT:` line that gives the exact command to run
next. Do what `NEXT:` says, and nothing else. In short:

1. Send each `SEND to:` block, exactly as printed and without that line, to the agents it names.
2. Pass every message of the run that you receive, exactly as received, one per call, to `receive`.
3. Keep time: start `wait` in the background and start it again each time it returns (it returns when
   something is to be sent), or, if you cannot run a command in the background, call `tick` at the
   time `WAKE` gives. Silence is how a lost message is found: without it the run cannot recover.
4. At `STATUS: DONE`, report what `END` says and nothing more. Then, if messages of the run still
   arrive, keep passing them to `receive` and send only what it prints: an agent may still ask for a message.

A run can last as long as the agents need: there is no deadline for the result, so keep listening.

**If you are the centre of a star** (the agents reach each other only through you), add `--relay <a,b,...>`,
the names of the spokes, before the command on every call. For each message from a spoke, first run
`forward --from <spoke>` and deliver what it prints to the other spokes, then pass the message to `receive`.

## Rules

1. Send what the script prints, as printed, to the agents it names. Never write, summarise or change
   a message, a rating or a repair, and never fill in for an agent that has not answered.
2. Do not work around an `ERROR`: it says what went wrong and what to do.
3. Do not stop before `STATUS: DONE`. If you must stop earlier, say that the run is not over.
4. Report the result as `END` prints it, and add no commentary of your own.

More detail (commands, the timers, the star, a silent agent, what a note means) is in
[references/details.md](./references/details.md). You should not need it.
