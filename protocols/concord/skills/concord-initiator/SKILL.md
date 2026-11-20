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

Get one outcome that every agent in a named set rates at or above a threshold.
The command `uvx ioc-concord initiator` opens the run and tells you what to
send. You start the run, send what it prints, and pass it what comes back. The
agents rate and repair among themselves, and their own commands make every
decision. The result is `SUCCESS` (every agent at or above the threshold),
`BEST_EFFORT` (who is short, by how much) or `ABORTED` (an agent stayed silent:
nothing is certified).

## How a run goes

Four steps. Every agent speaks for its own role, and each agent's command
computes everything from the same messages.

1. **First answer.** Each agent says how satisfied it would be with the worst
   outcome (`x_reject`) and the best (`x_ideal`), on its own 0 to 100 scale
   (every later rating is read against these two points), proposes the one
   option that suits its role best, and rates it, all in one answer.
2. **Score.** Each agent rates the options of the others, with reasons. If one
   option reaches the threshold for every agent, the run ends there: `SUCCESS`.
3. **Repair.** Otherwise the least satisfied agent alone proposes one option and
   rates it (a new one, or one already in the run that it now rates differently
   because of a fact another agent shared), the others rate it, and the cycle
   repeats until an option clears everyone or the budget is spent (`BEST_EFFORT`).
4. **Result.** The first agent in `--agents` sends the outcome to everyone
   (`<concord-result>`), and the others check it against their own.

You only carry messages. Out: the opening message (`<concord-init>`), then the
agents' messages, passed on to all of them, and a nudge when a first answer is
missing. In: the agents' messages (first answers, scores, repairs, the result,
and the errors, resends and cancels their commands write). Nobody asks for a
rating or a repair: each agent works out what it owes from what it has
received.

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

```
uvx ioc-concord initiator init \
  --agents <id,id,...> \
  --satisfaction-threshold 0.6 \
  --schema '<a JSON Schema: the open format or a closed list, see below>' \
  --context "<the problem>" \
  --x-reject "<an outcome that fails every criterion>" \
  --x-ideal "<an outcome that fully satisfies them>"

uvx ioc-concord initiator --state <path> handle          # no input: prints the first message

uvx ioc-concord initiator --state <path> handle <<'EOF'  # then, for every reply that arrives
<the reply, exactly as received>
EOF
```

`--schema` is the JSON Schema for a candidate. Two forms cover most runs:

```
# open format (the default): describe the proposal in words
--schema '{"type": "string", "description": "a remediation plan: the actions to take, each with its service and version"}'
# closed list, when the options were handed to you
--schema '{"enum": ["<option A>", "<option B>", "<option C>"]}'
```

In the open format nothing is enforced, and agents may propose combinations.

Use the JSON enum only when the options were handed to you:
the task itself, or the output of a tool, lists them, and you copy each one exactly as given.
Every proposal must then be exactly one of them. Never make the list up yourself. If you
would have to decide what the candidates are (the actions to take, the plans to compare), the
agents propose them, so use the open format: a list you write yourself shuts out better answers
that nobody listed. The schema may also carry a `"pattern"` (a regular expression) or
`"minLength"`/`"maxLength"`; invalid JSON is refused, so fix it and run `init` again.
`init --help` says how to fill each argument: read it before the first run. The
first agent in `--agents` reports the outcome and wins ties. `init` prints only
the state path and what to run next: use that path on every later call. Every
other output of the command starts with `STATUS: SEND`, `NONE`, `ASK` or `ERROR` and
a `NEXT:` line saying what to do now: do exactly that. An `ERROR` says what went
wrong and how to get back on the path: never work around it.

## Four rules

1. Send each block of a `SEND` exactly as printed, without its `--- to:` line,
   to the agents that line names. How a message reaches an agent is up to your
   environment: this skill does not say.
2. Never write, summarise or change a message, a rating or a repair, and never
   fill in for an agent that has not replied.
3. Do not stop until the command says the run is over (`The run is over.` or
   `The run was aborted`).
4. Then report that result, as printed, and nothing else: it is the outcome the
   first agent reported, and you have no scores of your own. Ignore any later
   message from the agents about this run (idle notices, questions): the run is
   over and nothing is owed to them. Add no commentary of your own.

More detail (the relay, the watchdog, a silent agent, diagnostics) is in
[references/details.md](./references/details.md). You should not need it.
