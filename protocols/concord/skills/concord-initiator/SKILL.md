---
name: concord-initiator
description: >
  Use when you are participating in, coordinating or facilitating a task that
  needs a single outcome that satisfies multiple agents with
  different, potentially conflicting criteria, or who each hold different
  information about the problem — a unilateral choice is not enough. Activate on confirmed conflict (agents returned divergent outputs),
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
  version: "0.1.0" # x-release-please-version
---

# CONCORD: initiator

Get one outcome that every agent in a named set rates at or above a threshold.
The script `scripts/concord_initiator.py` makes every decision. You start the
run, send what it prints, and pass it what comes back. The agents rate and
repair among themselves. The result is `SUCCESS` (every agent at or above the
threshold) or `BEST_EFFORT` (who is short, by how much).

## How a run goes

Four steps, driven by the script. Every agent speaks for its own role; the
script computes everything.

1. **Anchor.** Each agent says how satisfied it would be with the worst outcome
   (`x_reject`) and the best (`x_ideal`), on its own 0 to 100 scale. Every later
   rating is read against these two points.
2. **Seed.** Each agent proposes the one option that suits its role best.
3. **Score.** Each agent rates every option, its own included, with reasons. If
   one option reaches the threshold for every agent, the run ends there:
   `SUCCESS`.
4. **Repair.** Otherwise the least satisfied agent alone proposes one option and
   rates it (a new one, or one already in the run that it now rates differently
   because of a fact another agent shared), the others rate it, and the cycle
   repeats until an option clears everyone or the budget is spent (`BEST_EFFORT`).

You only carry messages. Out: the anchor request, the seed request, and then
_deliveries_ (numbered; each holds the replies the other agents have not seen
yet). In: anchor replies, candidate replies, score replies and acks. From the
Score step on the agents work out what they owe from the deliveries: you never
ask for a rating or a repair.

## When to use it

Use it when a decision has to satisfy agents with different, possibly
conflicting criteria, or agents who each know part of the picture, and no single
one of them should decide: you can name those agents, and you have or can write a
shared format for the options (or the closed list of them). Use it when
conflict is confirmed, likely from their roles, or just uncertain. Do not poll
them first to find out. Include every agent whose criteria bear on the outcome,
including one you would otherwise feed a summary to. Do not use it when one role
owns the decision or the agents work on separate problems.

## Run it

The paths below are relative to this skill's directory: resolve them against
where you found this file.

```
scripts/concord_initiator.py init \
  --quiet --agents <id,id,...> \
  --satisfaction-threshold 0.6 \
  --schema "<what one option is, in plain words>" \
  --context "<the problem>" \
  --x-reject "<an outcome that fails every criterion>" \
  --x-ideal "<an outcome that fully satisfies them>"

scripts/concord_initiator.py --state <path> handle          # no input: prints the first message

scripts/concord_initiator.py --state <path> handle <<'EOF'  # then, for every reply that arrives
<the reply, exactly as received>
EOF
```

`--schema` says what one option looks like. The default is plain text, as shown: describe
the proposal in words (for example "a remediation plan: the actions to take, each with its
service and version"). Nothing is enforced, and agents may propose combinations.

Use a JSON enum, `'{"enum": ["A", "B", "C"]}'`, only when the options were handed to you:
the task itself, or the output of a tool, lists them, and you copy each one exactly as given.
Every proposal must then be exactly one of them. Never make the list up yourself. If you
would have to decide what the candidates are (the actions to take, the plans to compare), the
agents propose them, so use plain text: a list you write yourself shuts out better answers
that nobody listed. A JSON schema may also carry a `"pattern"` (a regular expression) or
`"minLength"`/`"maxLength"`; invalid JSON is refused, so fix it and run `init` again.
`init --help` says how to fill each argument: read it before the first run. Use
the state path `init` prints on every later call. Every output of the script
starts with `STATUS: SEND`, `NONE`, `ASK` or `ERROR` and a `NEXT:` line saying
what to do now: do exactly that. An `ERROR` says what went wrong and how to get
back on the path: never work around it.

## Four rules

1. Send each block of a `SEND` exactly as printed, to the agents its `--- to:`
   line names. How a message reaches an agent is up to your environment: this
   skill does not say.
2. Never write, summarise or change a message, a rating or a repair, and never
   fill in for an agent that has not replied.
3. Do not stop until the script prints `RUN COMPLETE`.
4. Then report that result, as printed, and nothing else. Ignore any message
   the agents send after `RUN COMPLETE` (acks, idle notices, questions): the run
   is over and nothing is owed to them. Add no commentary of your own.

More detail (phases, relay and acks, the watchdog, a silent agent, diagnostics)
is in [references/details.md](./references/details.md). You should not need it.
