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
  version: "0.3.0" # x-release-please-version
---

# CONCORD: initiator

Get one outcome that every agent in a named set rates at or above a threshold. You open the run
and follow it; the agents rate and repair among themselves, and the script `uvx ioc-concord initiator`
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

The script is the command `uvx ioc-concord initiator` (it needs `uv`, nothing else installed). Run it from your own working
directory and never `cd` into the skill's directory: the state of the run is kept in the current directory, and every call of the run
must see the same one. `<you>` is your own agent id. Run `start --help` before the first run: it says how to fill each argument.

```
uvx ioc-concord initiator --me <you> start --participants <a,b,...> \
  --context "<the problem>" --x-reject "<an outcome that fails every criterion>" \
  --x-ideal "<an outcome that fully satisfies them>" \
  (--option "<A>" --option "<B>" ... | --format "<what one option looks like>") \
  [--satisfaction-threshold 0.6] [--budget 8] [--timeout 300] [--retries 3]
```

- Name the agents exactly as they are called in your environment.
- **Settings.** The flags in brackets are optional, with the defaults shown: the satisfaction threshold, the number of repair
  rounds (`--budget`), the seconds an agent waits for a message it is owed before it asks again (`--timeout`), and how many
  times it asks before the run is aborted (`--retries`). If the task gives a value for one, pass it; otherwise leave the default.
- **Possible answers.** If the task gives you a closed list of possible answers
  pass each one with `--option`, exactly as written. The agents then have to propose exactly one of
  them; nothing else is accepted. If there is no such list, describe in words what an answer looks like with
  `--format`, and the agents propose their own. Never write the list yourself.
- **If you take part in the decision yourself**, list yourself in `--participants`, first. `start` then
  hands you over: load the `concord-receiver` skill and use only its script from then on. The one exception
  is the centre of a star (you run `uvx ioc-concord initiator` with `--relay`): it keeps using that script,
  but only for `forward`, to pass the messages on (see below).

## Follow the run

Every output starts with `STATUS:` and ends with a `NEXT:` line that gives the exact command to run
next. Do what `NEXT:` says, and nothing else. In short:

1. Send each `SEND to:` block, exactly as printed and without that line, to the agents it names, before anything
   else: before you pass on a new message, and also when the block is a resend.
2. Pass every message of the run that you receive, exactly as received, one per call, to `receive`.
3. Keep time: start `wait` in the background and start it again each time it returns (it returns when
   something is to be sent), or, if you cannot run a command in the background, call `tick` at the
   time `WAKE` gives. Silence is how a lost message is found: without it the run cannot recover.
   Between those, when nothing is to be sent, end your turn: the next message, or `wait` returning, brings you back.
4. At `STATUS: DONE`, report what `END` says and nothing more (`report` prints it again). Then, if messages of the
   run still arrive, keep passing them to `receive` and send only what it prints: an agent may still ask for a message.

A run can last as long as the agents need: there is no deadline for the result. Do not poll for messages
(`task`, `tick`, `report`, `sleep`, reading files): end your turn, and pass each message to `receive` when it
reaches you.

**If you are the centre of a star** (the agents reach each other only through you), add `--relay <a,b,...>`,
the names of the spokes, before the command on every call of `uvx ioc-concord initiator` (the commands `NEXT:` prints for it carry it; the
receiver script has no such option). For each message from a spoke, first run
`forward --from <spoke>` and deliver what it prints to the other spokes, then pass the message to `receive`
(the receiver script's, if you also take part).

## Rules

1. Send what the script prints, as printed, to the agents it names, and send it before anything else: a block you do
   not send is a message the others wait for, until their timers give up on you. Never write, summarise or change
   a message, a rating or a repair, and never fill in for an agent that has not answered.
2. Do not work around an `ERROR`: it says what went wrong and what to do.
3. Do not stop before `STATUS: DONE`. If you must stop earlier, say that the run is not over.
4. Report the result as `END` prints it, and add no commentary of your own.

More detail (commands, the timers, the star, a silent agent, what a note means) is in
[references/details.md](./references/details.md).
