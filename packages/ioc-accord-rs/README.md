# ioc-accord (Rust)

The ACCORD protocol as a Rust library: the deterministic merges of the initiator, ported from
[`accord_initiator.py`](../../protocols/accord/skills/accord-initiator/scripts/accord_initiator.py). The initiator collects the
specialists' replies (raw model text or JSON) and merges them phase by phase: CONVENE (the roster), FRAME (the problem frame, in rounds),
GROUND and CLARIFY (the glossary of the key terms), VERIFY (who owns which knowledge, and the acceptance checks), then the locked intent
contract and the execution messages. The results, the messages and the state are the script's: the state each saves is the same JSON,
byte for byte, so a session can be continued by either.

The library does no I/O. It reads no file and no environment, never prints or exits, and takes no lock: the caller loads the state, runs
one command on a `Session`, and saves `session.dump()` when the command succeeded. Every fallible call returns `ioc_accord::Result`, whose
error, `AccordError`, has the text the script prints and a `refusal` flag; `hint_for` (or `error.hint()`) says what to do about it.

## A session

Self-driving: `init` with a task returns the CONVENE messages, then `handle` takes each reply as it arrives. When every specialist has
answered a phase, its merge runs and the messages of the next phase come back.

```rust
use ioc_accord::session::{InitArgs, Session};

let mut session = Session::new();
let out = session.init(&InitArgs {
    specialists: "ops,dev".into(),
    key_terms: "latency,error_rate".into(),
    task: "cut the p95 latency".into(),
    ..InitArgs::default()                       // state_id: None draws one
})?;
send_all(&out["messages"]);                     // {agent: message}

// a reply, exactly as the specialist returned it (with its <accord-delivery> line); VERIFY also takes the
// required knowledge and the objective clauses, as JSON
let step = session.handle("ops", &reply, None, None)?;
save(&session.dump());                          // after every command that succeeded
```

Phase by phase, as the script's other commands: `roster`, `frame` (once per round), `glossary` (again after CLARIFY), `verify`, `lock`,
`execution_message` and `status`, each given the replies of its phase as a JSON array of strings or objects.

A command that fails leaves the session as it was, with one exception: a refusal of `handle` (`error.is_refusal()`, a reply that
carried no delivery check) keeps the state as the refusal left it, because the attempt it counted has to survive it. Save the state then
too, as the script does.

`state::resolve_state_path(path, run_key, state_dir)` is the script's isolation of the state file under `$ACCORD_RUN_KEY`, as a pure
function: the caller reads the environment and creates the folder. To keep the state in a file, use the `FileStore` of
[`ioc-protocol`](../ioc-protocol-rs) with `state::StateCodec` (the script's text, loaded over the blank state), and its `FileLock` to
serialise the commands of one session.

## Differences from the script

The behaviour is the script's, quirks included. What differs:

* Similarity is the string similarity only (`difflib.SequenceMatcher` on the normalized texts, ported in `similarity`): the
  sentence-transformers embeddings the script uses when the package is installed are not ported. This is the script with
  `ACCORD_EMBEDDING=off`.
* `frame` returns `atoms_by_agent` in the order the agents replied; the script builds it from a set, so its order is Python's hash order.
* JSON is read with `serde_json`, which refuses what Python accepts and a reply never holds on purpose: `NaN`, `Infinity`, lone
  surrogates, nesting deeper than 128; an integer beyond 64 bits is read as a float.
* Where the script fails with a Python exception (a reply whose `holds` is a number, a hand-edited state), the error carries Python
  3.15's text for the common cases; for a state edited into another shape the text may differ.
* The `repr()` of a string inside a non-string field (`str(["a"])`) decides which characters are printable from the Unicode categories
  of control, separator and format characters, not from Python's full Unicode database.

## Modules

What the protocol libraries share is in [`ioc-protocol`](../ioc-protocol-rs): the fence and the prose around a reply, escaping, the
short fingerprint, the path under a run key, and the state backends. This crate keeps the messages, the state and the wording of the
script.

| Module | Contents |
|---|---|
| `errors` | `AccordError` and `hint_for` |
| `protocol` | the thresholds and limits |
| `py` | the Python semantics the merges rely on (truth, `str`, `==`, `in`, iteration, `strip`, `round`) over JSON values |
| `pyjson` | JSON read and written as Python's `json` does (`indent=2` with `ensure_ascii`, the canonical form, float `repr`) |
| `similarity` | `normalize_text`, `SequenceMatcher`, `similarity` |
| `ingest` | reading the replies of each phase |
| `algorithms` | the merges: roster, frame clusters and coverage (Good-Turing, Chao1), glossary, set covers, the contract |
| `state` | the blank state, loading with defaults, the state file and its `StateCodec`, `resolve_state_path` |
| `messages` | the request of each phase, the delivery check (`Delivery`, an `Encode`), the execution request |
| `session` | `Session` and the commands |

## Tests

    cargo test -p ioc-accord

`tests/differential.rs` checks the library against the script on the golden cases of `tests/fixtures` (see its README), byte for byte;
`ACCORD_DIFF_CASES=<file>` runs a larger file of the same shape too.
