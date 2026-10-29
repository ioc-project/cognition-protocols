# CONCORD

Skills (`skills/`) and metrics (`mce-metrics/`) for the CONCORD protocol.

## Shared code

The CONCORD decision engine (the same code runs in `concord_initiator.py` and `concord_receiver.py`, so that every
participant reaches the same decision) has a single source: [`shared/concord_core.py`](shared/concord_core.py). A skill
must stay self-contained, so the engine is copied into both scripts, between `SHARED CORE` markers, by
`python3 protocols/concord/tools/sync_core.py`. Edit the source, run the tool, commit the three files. CI runs
`python3 protocols/concord/tools/sync_core.py --check` and fails if a script has drifted.
