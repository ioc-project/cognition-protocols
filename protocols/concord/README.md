# CONCORD

Skills (`skills/`) and metrics (`mce-metrics/`) for the CONCORD protocol.

## Shared code

The protocol's code lives in the [`ioc-concord`](../../packages/ioc-concord) library (`packages/ioc-concord`). What the two skills
have in common on top of it, the command line (the lock, the output, and the commands `receive`, `tick`, `wait` and `report`), lives in
[`cli/`](cli). A skill must stay self-contained, so each of `concord_initiator.py` and `concord_receiver.py` is generated from three
parts:

1. a copy of the library modules it uses, between `BUNDLED ioc_concord` markers. The copy is of the library revision that is pinned in
   [`tools/bundle.toml`](../../tools/bundle.toml), not of the files on disk, and only the code the script reaches is copied;
2. a copy of the shared command line, between `BUNDLED concord_cli` markers (always of the files on disk, since it is hand-written here);
3. the script's own commands, written in the script file: `start` and `forward` for the initiator, `answer`, `task` and `status` for the
   receiver.

Edit the library or the command line, run `just sync`, commit the source and the scripts. CI runs `just check-sync` and fails if a
script has drifted. To adopt a newer library, change the pin and run `just sync` (see [`tools/bundle.py`](../../tools/bundle.py)).

`just test-concord` runs all of it: the bundler's tests, the library's tests (against the library and against its bundle), the shared
command line's tests ([`cli/tests`](cli/tests)) and the script tests ([`tests/`](tests), which also run the files of
[`spec/fixtures`](spec/fixtures) through the scripts).
