# CONCORD initiator: reference

Every output says what to do next in its `NEXT:` line. This file has the additional information on how to use the CONCORD
initiator skill.

Paths are relative to the skill directory. `<you>` is your agent id.

## Commands

Flags before the command: `--me <you>` (required), `--state-dir DIR` (where the state is kept; default the current directory) and
`--relay a,b,...` (the centre of a star only). `scripts/concord_initiator.py --help` lists the commands, and `start --help` explains
each argument of `start`.

| Command | Reads | Does |
|---|---|---|
| `start --participants a,b --context T --x-reject T --x-ideal T (--option A ... \| --format T \| --schema JSON)` | | opens the run and prints the first message. Optional: `--satisfaction-threshold`, `--timeout S`, `--retries N`, `--budget B`. A text may be `@path` of a file |
| `receive` | a message, on standard input | records it, prints what to send, if anything |
| `tick` | | asks again for what is late, or gives up on a silent agent |
| `wait [--poll S]` | | blocks until the next timer is due, then does a `tick` and returns its output |
| `report` | | how the run stands or ended |
| `forward [--from NAME]` | a message | (with `--relay`) what to pass on to the other spokes |

An initiator that takes part in the run uses this script for `start` and, if it is the centre of a star, for `forward`, and
`concord_receiver.py` for the rest: this script's other commands refuse it, and say so.

## Reading an output

```
STATUS: SEND | WAIT | DONE | ERROR
--- <section>
...
NEXT: what to do now
```

| `STATUS` | Meaning |
|---|---|
| `SEND` | there is something to send now |
| `WAIT` | nothing is due: pass the next message to `receive` |
| `DONE` | the run is over for you |
| `ERROR` | the call failed and changed nothing; the output says what to do. Exit code 2 (also for a wrong command line) |

| Section | Holds |
|---|---|
| `SEND to: a, b` | one message to send, exactly as printed and without this line, to the agents named. One section for each message |
| `FORWARD to: a, b` | (`forward` only) a message to pass on, as it came |
| `READ` | the messages of others that the script took |
| `END` | how the run ended, or that it is not over |
| `NOTE` | `not used: <reason>`: the message did nothing (see below) |
| `WAKE` | when to come back (`tick`), with how long from now |

## The timers

Every message the run is waiting for has a timer of `timeout` seconds (default 300). When it expires the script asks the author
again; after `retries` requests (default 3) the author is taken to be missing. So an agent waits at most
`(retries + 1) × timeout` for one message.

Nothing runs on its own. `wait` blocks until the next timer is due, ticks, and returns what the tick produced (a request, the init
sent again, or the abort); start it in the background and again each time it returns, or call `tick` at the `WAKE` time. While `wait`
sleeps, `receive` and `report` work. When no timer runs, `wait` returns at once with `WAIT` and no `WAKE`: do not start it again
until a call prints a `WAKE`. An initiator that does not take part has no timer for the result, because the negotiation can take
as long as it takes: after the init replies are in it only listens, so keep passing messages to `receive`.

## A silent agent

If an agent stays silent after `retries` requests, the one that noticed ends its run `ABORTED` ("missing-agent", naming it) and sends
a cancel. Every agent that receives a cancel does the same, once, so the notice reaches everybody. An aborted run certifies no
outcome and has no winner. An agent whose run had already ended keeps its outcome, and its report is `disputed`.

## The status at the end

| Status | Means |
|---|---|
| `unverified` | you have no outcome of your own to compare: you take it from the reporter's result (the normal end for an initiator that does not take part) |
| `confirmed` | the reporter's result matches the agent's own outcome |
| `disputed` | a mismatch was found or received, or a cancel reached an agent whose run had ended: do not rely on the result |
| `aborted` | the run was cancelled or given up |
| `pending` | not over yet |

## Choosing the arguments

- `--participants`: every agent whose criteria bear on the outcome, as they are called in your environment. The first reports the
  result and wins ties: list yourself first if you take part (and only then are you a participant).
- What an answer looks like: `--option`, once for each, when the task gives a closed list of possible answers (they become an
  enum, and nothing else is accepted); `--format` for the open format; `--schema` for a full JSON Schema (a `pattern`, a `minLength`,
  a `maxLength`). Exactly one of the three.
- `--x-reject` and `--x-ideal`: outcome states ("error rate returns to 0"), never proposals or mechanisms.
- `--satisfaction-threshold`: the bar the outcome must genuinely meet, not a level you are sure is reachable. If it is lower than
  what the agents reach on their own proposals, the run ends with no negotiation at all. It never changes during the run.

## The centre of a star

When the agents reach each other only through you, add `--relay a,b,...` (the spokes) to every call. A spoke sends to you alone.
For each message from a spoke: first `forward --from <spoke>`, and deliver what it prints (`FORWARD to:`) to the other spokes
exactly as printed; then pass the message to `receive`. What you send yourself (`SEND to:`) goes to every spoke. If you also take
part, `forward` is in this script and `receive` in the receiver script. A centre that does not forward leaves the spokes without each
other's messages, and the run does not end.

## What a note means

`not used: <reason>` says why a message changed nothing. It is not an error.

| Reason | Meaning |
|---|---|
| `not a CONCORD message` | no CONCORD element in the text |
| `own message` | you heard yourself, on a broadcast channel |
| `unknown run`, `sender is not in the run` | a message of another run, or from an agent that is not in this one |
| `duplicate`, `already recorded`, `already counted` | received before |
| `version` | another protocol version; an init of one is refused with an error to its initiator |
| `check` | damaged on the way: it is asked for again |
| `invalid` | it breaks a rule of the protocol: not recorded, not answered; the timer asks again |
| `run over` | a data message after the end |
| others (`not handled`, `not for the initiator`, `no answer expected`, ...) | not for this agent at this point of the run |

## Errors you may see

- `the current directory ... is inside the folder of this skill`: you went into the skill's folder. Run from your own working directory and call the
  script by its full path, or add `--state-dir DIR` to every call.
- `there is no run for <you>`: nothing has opened the run yet. Run `start`.
- `<you> is in a run already`: `start` is refused while a run is active; `report` says how it stands. Use another `--me` or
  `--state-dir` for a second run.
- `you take part in this run`: use the receiver script, with the same command.
- `the run is not valid: ...`: `start` refused an argument and started nothing; fix the one it names.
- `... got an empty standard input`: pass the message exactly as received.

## State, restarts

The state of the agent is `concord-state-<you>.json` in `--state-dir`; `concord-script-<you>.json` holds what only the script
keeps, and `concord-relay-<you>.json` the runs a centre knows. Each call takes a lock, so two calls never run together. Nothing is
lost if the process stops: run `report` to see where the run stands, and start `wait` again. Do not delete the files while the run is
active, and do not `start` again.

## If you cannot run the script

Say so to whoever asked, and stop. Do not write a CONCORD message, a rating or a repair yourself.
