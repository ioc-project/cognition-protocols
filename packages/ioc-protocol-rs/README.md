# ioc-protocol (Rust)

What the cognition protocol libraries share: [`ioc-concord`](../ioc-concord-rs) and [`ioc-accord`](../ioc-accord-rs) both depend on it.
It holds the mechanics of the message format and of the state storage, and knows no schema: each protocol keeps its own messages, its
own state and its own wording, and plugs them in through the traits and functions here.

## Message format

A model hands a message over inside a markdown fence, or with prose around it, or cut off. The reader finds the message and returns
the sender's own text, byte for byte, or a `MessageError` whose `ErrorKind` says why not: `NotFound`, `CutOff`, `Malformed`, `Invalid`,
`CheckMismatch`. A protocol converts it into its own error (`ConcordError::InvalidMessage`, `AccordError`).

* `text`: the whitespace of the protocols (`WS`, `trim_ws`, `squeeze`), a fence around a text (`strip_fence`, with the caller's
  whitespace: Python's for ACCORD), the object inside prose (`outermost`)
* `xml`: escaping (`esc_text`, `esc_attr`), the root start tag read as an XML parser reads it (`start_tag`), the XML message of a
  protocol in a text (`Locator`: CONCORD's is `Locator::new("concord-", "CONCORD", "xml")`), and the integrity check of a document,
  computed on its canonical text (`compute_check`, `with_check`, `verify_check`)
* `check`: the short fingerprint (`short_digest`, the first 8 hex characters of a SHA-256) and the text without whitespace it is often
  computed over (`without`)
* `message`: `MessageError`, and the traits a protocol implements for its own types: `Decode` (with a context, such as the schema of a
  run) and `Encode`; `read` locates a message in a text, then decodes it

```rust
use ioc_protocol::message::read;
use ioc_protocol::xml::Locator;

let locator = Locator::new("concord-", "CONCORD", "xml");
let message: ioc_concord::wire::Message = read(reply, |t| locator.locate(t), Some(&spec))?;
```

## State storage

A protocol asks a store for `load` and `save` only. The `Store` trait is the backend interface; each backend is a module of `store`:

| Backend | Module | Keeps |
|---|---|---|
| `FileStore<C>` | `store::file` | the state in one file, as the text its `Codec` writes; a save is atomic and durable (a temporary file, synced, renamed over the old one, the folder synced) |
| `MemoryStore<S>` | `store::memory` | the state value in memory, shared by the clones of the store; `MemoryStore::refusing` fails chosen saves, for tests |

A `Codec` turns a state into text and back: the format and the defaults a loaded state is merged over. `JsonCodec` is `serde_json`
with an indent of two spaces and a final line break (CONCORD's); ACCORD's `state::StateCodec` writes Python's `json.dumps(st, indent=2)`
and loads over the blank state.

Errors are a `StoreError` with a `StoreErrorKind`. `Missing` (nothing has been saved) is the only one that means "start empty"; every
other kind is a fault.

More backends (a database, a key-value service) are added as new modules of `store`, or as types in any crate, that implement `Store`:
the protocol crates take any `Store`, so they need no change. A backend must fail `load` with `Missing` when nothing has been saved, make
a `save` all-or-nothing, and give every handle on the same storage what the last `save` saved. `store::conformance::run` checks these,
and every backend calls it in its tests:

```rust
let store = MemoryStore::<serde_json::Value>::refusing(|st| st["poison"] == true);
ioc_protocol::store::conformance::run(|| store.clone(), json!({"v": 1}), json!({"v": 2}), json!({"poison": true}));
```

Around the store:

* `path`: the path of a state file. `unique_path` is the given path when it is free, else one with a random tag (CONCORD's initiator);
  `keyed_path` is a path of its own under a run key, the same on every call (ACCORD's `$ACCORD_RUN_KEY`, written as Python's `pathlib`
  writes it).
* `lock`: `FileLock`, an exclusive lock on a file of its own (`lock_path`: `<state>.lock`), to serialise the processes that load,
  change and save one state. It is never taken on the state file, which a save replaces.

## Formatting and tests

Formatted with `cargo fmt` from the repository (`rustfmt.toml`: 140 columns). The tests are in the modules:

    cargo test -p ioc-protocol
