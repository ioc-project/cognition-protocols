# CONCORD script tests

No LLM involved: a stub plays the model. Each test takes the `protocols/concord` directory as its argument and exits non-zero on failure.

```bash
protocols/concord/tests/run_all.sh            # or: protocols/concord/tests/run_all.sh python3.11
```

CI runs the same script (job `script-tests`).

| Test | Covers |
|---|---|
| `test_shared_core.py` | both scripts embed the same engine, the source runs alone, no sibling imports |
| `test_relay_flow.py` | a whole run with point-to-point messages only, optional message loss |
| `test_ack.py` | numbered deliveries, acks, contiguous watermark, targeted nudge |
| `test_state_id.py` | per-run state, other runs' replies skipped, resend priority |
| `test_transport.py` | out-of-order and repeated replies, several documents in one reply |
| `test_watchdog.py` | nudge, then a decision for the initiator; progress resets the count |
| `test_missing.py` | silent agent marked missing, BEST_EFFORT reached locally |
| `test_next.py` | every initiator output carries a `NEXT:` line and every message a `--- to:` line; the end says to report as printed |
| `test_malformed.py` | malformed input is reported and skipped, never a crash |
| `test_receiver_next.py` | the receiver's task prompt carries the guidance; `NONE` says what to do; a `SEND` keeps its reminder off the message |
| `test_answer_rules.py` | a rating outside the agent's anchors, a reason that is no reason, an empty or repeated candidate and anchors outside the scale are refused with the reason, and nothing is recorded |
| `test_errors.py` | every failure prints `STATUS: ERROR`, what happened and a `NEXT:` line, with a non-zero exit and no traceback |
| `test_no_requests.py` | the initiator never sends a rating or repair request; a nudge only makes agents check what is due |
| `test_parallel_runs.py` | two runs share a state folder: the answer command names its run |
| `test_hints.py` | a wrong answer command is sent back to the flags the task shows, not to JSON |
| `test_lost_reply.py` | a reply that never arrived is sent again when the nudge names its author as awaited |
| `test_truncated_request.py` | a request that lost its last closing tags is read; other cut messages are not |
| `test_nudge_size.py` | a nudge is a bare delivery (no request) and carries only what the agent has not acknowledged |
| `test_unreadable.py` | a message an agent cannot read is reported to the initiator instead of acknowledged, and sent again to that agent only |
| `test_prose.py` | an agent that answers in words is reported, nothing is recorded and the request is sent again; a waiting initiator is told not to end its turn |
| `test_answer_flags.py` | `answer` takes plain flags (anchor, seed, scores); nothing given is told what to run; the prompt shows the command |
| `test_commands.py` | the initiator has init, handle, watchdog, mark-missing and a read-only status, and nothing else |
