# CONCORD

Skills (`skills/`) and metrics (`mce-metrics/`) for the CONCORD protocol.

## Shared code

The protocol's code lives in the [`ioc-concord`](../../packages/ioc-concord) library (`packages/ioc-concord`). A skill must
stay self-contained, so `concord_initiator.py` and `concord_receiver.py` each carry a generated copy of the library modules
they use, between `BUNDLED ioc_concord` markers (which modules go where is set in [`tools/bundle.toml`](../../tools/bundle.toml)).
Only the code a script reaches is copied. Edit the library, run `just sync`, commit the library and the scripts. CI runs
`just check-sync` and fails if a script has drifted, and runs the library's tests against the library and against each
script's bundle as well.

`just test-concord` runs all of it: the bundler's tests, the library's tests and the script tests ([`tests/`](tests)).
