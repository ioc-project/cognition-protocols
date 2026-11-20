# CONCORD

Skills (`skills/`) and metrics (`mce-metrics/`) for the CONCORD protocol.

## Shared code

The protocol's code lives in the Rust crate [`ioc-concord-rs`](../../packages/ioc-concord-rs), which has an API for other languages
(feature `uniffi`). The Python package [`ioc-concord`](../../packages/ioc-concord-python) is built on it, and carries the command the
skills run: `uvx ioc-concord initiator ...` and `uvx ioc-concord receiver ...`. A skill is its `SKILL.md` and nothing else: it needs `uv`
and nothing installed beforehand.

`just test-concord` runs the tests of the crate (`packages/ioc-concord-rs/tests/cli.rs` drives a whole run through the command line) and of
the Python package (`packages/ioc-concord-python/tests`).
