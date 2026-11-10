# Script tests

Scripted runs through the real `accord_initiator.py`. ACCORD's receiver is a prompt rather than a script, so there is no
second process to drive: the specialists' judgement comes from a fixed policy in `driver.py` that returns the JSON a
receiver would have returned. `ACCORD_RUN_KEY` fixes the state id and the state path, `ACCORD_EMBEDDING=off` keeps
grounding on difflib instead of a model, and temp paths are replaced by `<TMP>`, so the transcripts in `transcripts/`
are byte-stable. They record every call (arguments, stdin, exit code, stdout, stderr) as text, one block per call, with
each stream line prefixed (`in|`, `out|`, `err|`) and wrapped to 120 columns. A line ending in `⏎` was wrapped for
display only and continues on the next line.

    just test-accord
    UPDATE_TRANSCRIPTS=1 uv run --no-project --with pytest python -m pytest protocols/accord/tests    # re-record; review the diff

A refactor that must not change behavior leaves every transcript untouched. A behavior change updates only the
transcripts it affects, and the diff shows exactly what changed.

## Scenarios

Both documented drive paths are covered: `handle` (self-driving) and the explicit per-phase merges.

| Scenario | What it pins down |
|---|---|
| `split_term_triggers_clarify` | The reference run. Two agents report different units for `error_rate`, GROUND flags it, CLARIFY follows, and the split survives into the locked Glossary |
| `agreed_terms_skip_clarify` | The same run with no split. CLARIFY is conditional, so GROUND goes straight to VERIFY |
| `three_agents_with_singleton` | Support counts across three specialists: the shared atom reaches support 2, everything only one agent knows is kept and flagged |
| `withdrawn_atoms` | `"atoms": []` on the revision round withdraws an agent's contributions, and the support count of the atom it shared drops with it |
| `step_by_step_phase_commands` | The explicit merge path, and the only one that can supply `REQUIRED_KNOWLEDGE` and `OBJECTIVE_CLAUSES` — so also where owners, checks and an uncovered gap are exercised |
| `execution_messages` | One `EXECUTION_REQUEST` per specialist after lock, carrying the contract, plus the unknown-agent path |
| `reply_check_refusals` | The delivery check: a wrong one is always refused, a missing one is refused once and then recorded with a defect |
| `reply_shape_refusals` | The shape check: prose, truncated JSON and a summary wearing the right delivery line are all refused, however often they are retried |
| `error_paths` | Bad input to every entry point: each must end in a readable error, never a traceback |

## Behavior these record but do not endorse

Following `protocols/concord/tests`, the transcripts capture what the script does today, including two things that
look like defects. Each has a test named `test_today_…` so the recording is not mistaken for approval; change the
assertion when the behavior changes.

- **`lock` has no precondition.** `cmd_lock` stores whatever is in state, so calling it on a session that has run no
  phases returns `status: INTENT-LOCKED` with an empty roster, frame, glossary and objective. That status is what the
  skill gates every app action on, so a premature call produces a green light over nothing — and the resulting empty
  contract then passes `execution-message`'s guard, which only checks that *a* contract exists.
- **A default self-driving run locks an empty Verifiable Objective.** `handle` does accept `--required-knowledge` and
  `--objective-clauses`, so the self-driving path *can* populate it. But nothing asks for them: `init` does not take
  them, no phase message mentions them, and no `next_command` hint names them. A run that does not know to pass them
  locks an empty objective, and every check an agent proposed is dropped for covering no clause.

A third — `handle` accepting prose and truncated JSON as an answer — is now fixed, by two checks that catch different
things. The **delivery check** says which message a reply answers, so a stale or invented one is caught; a missing
line is forgiven on the second attempt, with a defect, so a receiver that never echoes cannot stall the session. The
**shape check** says whether the reply is an answer to this phase at all, and is never forgiven — an orchestrator
holding the request can copy the delivery line onto anything, so the shape is the part a summary cannot fake. What
neither establishes is that the reply's content is what the specialist actually said: the initiator never saw it.
