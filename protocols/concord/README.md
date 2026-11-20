# CONCORD

Skills (`skills/`) and metrics (`mce-metrics/`) for the CONCORD protocol.

## Shared code

The protocol's code lives in the Rust crate [`ioc-concord-rs`](../../packages/ioc-concord-rs), which has an API for other languages
(feature `uniffi`); the Python library [`ioc-concord`](../../packages/ioc-concord-python) is built on it. A skill must stay self-contained, so `concord_initiator.py` and
`concord_receiver.py` each carry a generated copy of the modules of the Python library, at the release they are pinned to (`ref` in
[`tools/bundle.toml`](../../tools/bundle.toml)), between `BUNDLED ioc_concord` markers. That library is no longer on main: the pin
reads it from its git tag. CI runs `just check-sync` and fails if a script has drifted from the revision it is pinned to.

`just test-concord` runs all of it: the bundler's tests, the library's tests and the script tests ([`tests/`](tests)).
