# CONCORD receiver: reference

What the script does not print, for when `NEXT:` is not enough. You do not need it to take part: every output says what to do next, and
every task says what it asks and how to answer it. The protocol itself is in the
[specification](https://github.com/ioc-project/cognition-protocols/blob/main/protocols/concord/spec/CONCORD-SPEC.md).

Paths are relative to the skill directory. `<you>` is your agent id.

## Commands

Flags before the command: `--me <you>` (required) and `--state-dir DIR` (where the state is kept; default the current directory).
`scripts/concord_receiver.py --help` lists the commands.

| Command | Reads | Does |
|---|---|---|
| `receive` | a message, on standard input | records it, and prints what to send and the task that is due, if any |
| `answer` | your answer, as one JSON object | checks it, builds the message, and prints it to send |
| `task` | | prints what is due now (the task and the timer) without changing anything: use it when you do not know what is next |
| `tick` | | asks again for what is late, or gives up on a silent agent |
| `wait [--poll S]` | | blocks until the next timer is due, then does a `tick` and returns its output |
| `status` | | the view of the engine, for debugging: round, who is the bottleneck, what is lacking, the timers |
| `report` | | how the run stands or ended |

## Reading an output

```
STATUS: SEND | ANSWER | WAIT | DONE | ERROR
--- <section>
...
NEXT: what to do now
```

`SEND`: something to send now. `ANSWER`: a task is due and nothing is to be sent yet. `WAIT`: nothing is due. `DONE`: the run is over for
you. `ERROR`: the call failed and changed nothing; the output says what to do (exit code 2).

| Section | Holds |
|---|---|
| `BRIEF` | how a run goes, the first time you meet it |
| `SEND to: a, b` | one message to send, exactly as printed and without this line, to the agents named. One section for each message. In a star, hand it to the centre alone: the centre passes it on |
| `READ` | the messages of the others, as they arrived: read them before you answer |
| `TASK` | what you owe now, then `ANSWER FORMAT`: the fields of your answer |
| `END` | how the run ended |
| `NOTE` | `not used: <reason>`: the message did nothing (see below) |
| `WAKE` | when to come back (`tick`), with how long from now |

## Your answer

One JSON object, with exactly the fields the task asks for. They are the same in every run:

| Task | Fields |
|---|---|
| first answer | `raw_reject`, `raw_ideal`, `candidate`, `rationale`, `raw`, `reasoning` |
| rating | `scores`: a list with one entry for each candidate the task lists, each `{"id", "raw", "reasoning"}` |
| repair | `candidate`, `rationale`, `raw`, `reasoning` |

- Scores (`raw_reject`, `raw_ideal`, `raw`) are numbers from 0 to 100 on your own scale, never a 0-1 score; `raw_ideal` is above `raw_reject`.
- `candidate` is in the format the task gives, or one of the options it lists; `rationale` and `reasoning` are concrete reasons, not a
  verdict ("too expensive" is not one).
- An answer that breaks a rule is refused: the output says what is wrong, shows what you sent next to what is expected, and says what
  to do. Nothing is recorded; fix it and answer again. The same refusal three times in a row stops you: report that you could not
  answer, and why. A run you leave unanswered ends for the others on their timers.
- An answer to a task that is not due, or already given, is refused and not counted.

## The timers

Every message the run is waiting for has a timer of `timeout` seconds. When it expires the script asks the author again; after
`retries` requests the author is taken to be missing and the run is aborted for everyone with a cancel. The values are the initiator's,
printed in the task. Nothing runs on its own: `wait` blocks until the next timer is due and returns what the tick produced (start it in
the background, and again each time it returns), or call `tick` at the `WAKE` time. When no timer runs, `wait` returns at once with no
`WAKE`: do not start it again until a call prints one. A task you answer slowly counts against the others' timers: answer it promptly.

## The status at the end

`confirmed`: the reporter's result matches your outcome. `disputed`: a mismatch was found or received, or a cancel reached you after your
run had ended: do not rely on the result. `aborted`: the run was cancelled, or an agent stayed silent. `pending`: not over yet. After the
end you keep your outcome; if messages of the run still arrive, pass them to `receive` and send only what it prints.

## What a note means

`not used: <reason>` says why a message changed nothing. It is not an error.

| Reason | Meaning |
|---|---|
| `not a CONCORD message` | no CONCORD element in the text |
| `own message`, `duplicate`, `already recorded` | you heard yourself, or received it before |
| `unknown run`, `sender is not in the run` | a message of another run, or from an agent that is not in this one |
| `version` | another protocol version |
| `check` | damaged on the way: it is asked for again |
| `invalid` | it breaks a rule of the protocol: not recorded, not answered |
| `busy` | you are in another run: the init is refused with an error |
| `run over` | a data message after the end |
| others (`not handled`, `not a participant`, `no answer expected`, ...) | not for you at this point of the run |

## Errors you may see

- `there is no run for <you>`: no message has opened it yet. Pass the opening message to `receive`.
- `refused: ...` under `answer`: see "Your answer".
- `... got an empty standard input`: pass the message exactly as received.
- `... is not an agent id`: `--me` is the id the initiator gave you, 1 to 64 letters, digits, `_` or `-`.

## State, restarts

The state of the agent is `concord-state-<you>.json` in `--state-dir`, and `concord-script-<you>.json` holds what only the script keeps (the
run you were briefed on, the refusal count). Each call takes a lock, so two calls never run together. Nothing is lost if the process
stops: run `task` to see what is due, or `report`, and start `wait` again. Do not delete the files while the run is active.

## If you cannot run the script

Say so to whoever asked, and stop. Do not write a CONCORD message, a rating or a repair yourself.
