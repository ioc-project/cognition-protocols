# Script tests

Scripted runs through the real `concord_initiator.py` and `concord_receiver.py`. The agents' judgement comes from a fixed
policy, `CONCORD_RUN_KEY` fixes the state id, and temp paths are replaced by `<TMP>`, so the transcripts in `transcripts/`
are byte-stable. They record every call (arguments, stdin, exit code, stdout, stderr) as text, one block per call, with each stream line
prefixed (`in|`, `out|`, `err|`) and wrapped to 120 columns. A line ending in `⏎` was wrapped for display only and continues on the
next line.

    just test-concord
    UPDATE_TRANSCRIPTS=1 uv run --no-project --with pytest python -m pytest protocols/concord/tests    # re-record; review the diff

A refactor that must not change behavior leaves every transcript untouched. A behavior change updates only the transcripts it
affects, and the diff shows exactly what changed.

`receiver_schema_enforcement` records today's schema checks in the receiver, including that an invalid `pattern` is silently
ignored there (the initiator rejects it). The `ioc-concord` work changes this on purpose.
