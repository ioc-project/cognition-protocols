# Script tests

Tests of the `ioc-concord` command (`ioc-concord initiator` and `ioc-concord receiver`, the command of the Rust crate
[`packages/ioc-concord-rs`](../../../packages/ioc-concord-rs), installed by the wheel of
[`packages/ioc-concord-python`](../../../packages/ioc-concord-python)) at the level of what a model does with it: it reads what a call
prints, and makes the next call. The command replaces the skill scripts `concord_initiator.py` and `concord_receiver.py`, and these tests
were written for them: the transcripts were recorded with the scripts, and the command must print what they printed. The crate replays
the same transcripts through its own function (`packages/ioc-concord-rs/tests/transcripts.rs`).

    just wheel && just test-transcripts      # these, with the command of the wheel, in an environment with nothing else
    just test-concord                        # the crate, the wheel, and these
    CONCORD_COMMAND="..." python3 -m pytest protocols/concord/tests      # with another command (split as a shell splits it)

The command prints its own name (`uvx ioc-concord initiator`) where a script printed its file name: what is recorded writes it as the
name of the script (`concord_initiator.py`), so the transcripts are those of the scripts, byte for byte.

## Transcripts (`test_scripts.py`, `scenarios.py`, `driver.py`)

Scripted runs through the command. The driver is the transport and the model: it delivers each message to the agents the `SEND`
(or `FORWARD`) section names, reads the `TASK` a script printed, and answers it from a fixed policy (tables of anchors, seeds, scores
and repairs). Every call is recorded (arguments, stdin, exit code, stdout, stderr) as text, one block per call, with each stream line
prefixed (`in|`, `out|`, `err|`) and wrapped to 120 columns. A line ending in `⏎` was wrapped for display only and continues on the next
line.

What a run holds that changes from one execution to the next is normalised in the transcript, never in what the calls pass to each
other: every UUID becomes `<UUID-n>` in the order it first appears, every `check` becomes `<CHECK>`, a time to come back becomes
`<WAKE>`, and the directory `<TMP>`. So the transcripts are byte-stable.

    UPDATE_TRANSCRIPTS=1 CONCORD_COMMAND=... uv run --no-project --with pytest python -m pytest protocols/concord/tests    # re-record; review the diff

A refactor that must not change behavior leaves every transcript untouched. A behavior change updates only the transcripts it affects,
and the diff shows exactly what changed. A change of the wording in `guidance` changes every transcript that prints it: that is the point,
and the diff is where to read it. Do not re-record to make a test pass.

| Scenario | What it covers |
|---|---|
| `success_at_score`, `three_agents_success` | a run that ends at the first scores, with two and with three agents; the initiator learns the result and reports `unverified` |
| `repair_then_success`, `best_effort_budget_spent` | a repair round; a budget that is spent: `BEST_EFFORT` and who is short |
| `open_schema_with_pattern` | the open format with a pattern and a length, and options that match them |
| `initiator_takes_part` | `start` as a participant, the hand-off to the receiver script (`task`), and the guard that refuses the initiator script afterwards |
| `star_with_relay`, `star_centre_takes_part` | the centre of a star: `--relay`, `forward`, then `receive`; the centre as a pure initiator and as a participant |
| `silent_agent_aborts` | an agent that never answers: `wait` blocks until the timer is due, then the initiator gives up, cancels, and the others end the run |
| `initiator_errors` | no run, no way (or two ways) to say what an option is, a threshold or a set of agents that is not valid, a second `start`, an empty message |
| `receiver_errors` | every kind of refused answer, the stop at the third identical refusal, an answer that is not due |
| `receiver_schema_enforcement` | a candidate that the pattern, the length or the list of options does not allow is refused, and one that it allows is taken |

`test_scripts.py` also says in assertions what the transcripts record, so that a wrong transcript cannot be re-recorded unnoticed, and
adds what a transcript cannot show: a negative control for the star (the same run, with a centre that does not `forward`, never ends),
the lock (messages that arrive at the same time are all recorded), `--help`, and a wrong command line.

## Inputs the command does not expect (`test_scripts_inputs.py`)

What the command is given that is wrong, and that it must answer with a clean error and not a traceback: a model that changes its bad
answer each time (it is stopped after six refusals of one task, besides the stop at three identical ones), a `--relay` list that leaves
a participant out, `forward --from` an agent that is not a spoke, a file that is not UTF-8, a text that starts with `@`, a closed
standard input, and `wait` on a run that is over. (The scripts also refused a call made from inside their skill's folder; the command
has no such folder.)

## Fixtures (`test_fixtures_scripts.py`)

The files of `protocols/concord/spec/fixtures` say what every implementation must do with a message; the library is tested against them as
imported. Here each is handed to `receive`, to see what the library tests cannot: that the command reads any message from
standard input and never crashes on it; that a message that breaks a rule of the spec changes nothing (the state file is byte for
byte the same) and is not answered, except an invalid init, which is answered with an error; that a valid message is never taken for a
wrong one; and that a message with a character reference, a quoted `from=`, or a no-break space comes out as it went in, under any
locale. The `state/` fixture is not used: it is the saved state of the Rust crate.
