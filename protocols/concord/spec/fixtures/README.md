# CONCORD fixtures

Fixed inputs and the results every implementation of [CONCORD](../CONCORD-SPEC.md) must give for them. They are language neutral: the
Rust crate (`packages/ioc-concord-rs/tests/fixtures.rs`) makes and reads them, and so can any other implementation. When two implementations agree on these
files, they read each other's messages the same way.

| Path | What it holds | What an implementation checks |
|---|---|---|
| `wire/*.xml` | One message of every kind, with a fixed `run_id`, `uuid` and `check`; and two inits that leave the parameters out (`init-defaults`, all of them; `init-with-a-parameter-left-out`, one) | Find it in the text (`locate`), read the root attributes (`peek`), verify the `check`; a parameter that is left out takes its default |
| `wire/tricky/*.xml` | Valid messages that a careless reader gets wrong: a `from=` or `check=` inside the quoted value of another attribute, single quotes, `>` in a value, a character reference in `from` (`&#97;lice` is `alice`), non-ASCII spaces | The attributes read before parsing are those of the parsed document, and the `check` is the real one |
| `wire/valid.json` | For each valid message: `kind`, `from`, `run_id`, `uuid`, `check`, and `parsed`, the fields a reader must get | The same values (numbers in millionths, as in the spec's exact arithmetic) |
| `wire/refused/*.xml`, `wire/refused.json` | Messages that break a rule of the spec, and why: a data message with a correct `check`, or an error, a resend or a cancel, which carry none | Refused as invalid: not recorded, nobody answers, the state does not change |
| `check.json` | Documents, their canonical text and their `check` | The canonical text and the first 8 hex characters of its SHA-256 |
| `patterns.json` | Patterns of the subset with texts that match and that do not, and patterns outside the subset | The same matches, and a refusal of the others |
| `state/` | The saved state of the node `cost` after the init and both init replies, the next message, and what happens | Load the state, receive the message, reach `expected.json` |

## What is in and what is not

- Only rules of the spec are here. A limit of one implementation (the longest integer it reads, the size of a pattern it compiles,
  how many messages it holds) is not a fixture.
- The field names of `parsed` and `state/` are those of the Rust crate (`parsed_json` in `tests/fixtures/recipes.rs` writes them out). The
  values are the contract; another implementation maps the names. `state/` is the saved state of the Rust node: an implementation with
  another state format can only check `expected.json` by replaying the messages.
- The messages carry no real secrets: every id and uuid is made up.

## Changing them

The Rust crate is the reference that makes these files, and the diff of a change is the change that every implementation has to follow:

```
cd packages/ioc-concord-rs
UPDATE_FIXTURES=1 cargo test --test fixtures
```

The test builds every file in memory from `tests/fixtures/recipes.rs`, with fixed ids, uuids and clock. In a normal run it compares them
with these files, and with `UPDATE_FIXTURES=1` it writes them. What the spec decides is written by hand in the recipes and asserted, so
that the crate cannot make its own mistakes the fixtures: who sends each tricky message, which messages are refused, what each pattern
matches, and the canonical text of each document with its `check`, which is hashed in the test and not by the library. The generation
fails if the library disagrees with any of it.

A change to these files is reviewed as a change to the spec. Do not regenerate them to make a test pass.
