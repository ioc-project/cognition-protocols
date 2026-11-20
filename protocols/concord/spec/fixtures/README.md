# CONCORD fixtures

Fixed inputs and the results every implementation of [CONCORD](../CONCORD-SPEC.md) must give for them. They are language neutral: a
test of the Python library (`packages/ioc-concord/tests/test_fixtures.py`) reads them, and so can any other implementation. When two
implementations agree on these files, they read each other's messages the same way.

| Path | What it holds | What an implementation checks |
|---|---|---|
| `wire/*.xml` | One message of every kind, with a fixed `run_id`, `uuid` and `check` | Find it in the text (`locate`), read the root attributes (`peek`), verify the `check` |
| `wire/tricky/*.xml` | Valid messages that a careless reader gets wrong: a `from=` or `check=` inside the quoted value of another attribute, single quotes, `>` in a value, non-ASCII spaces | The attributes read before parsing are those of the parsed document, and the `check` is the real one |
| `wire/valid.json` | For each valid message: `kind`, `from`, `run_id`, `uuid`, `check`, and `parsed`, the fields a reader must get | The same values (numbers in millionths, as in the spec's exact arithmetic) |
| `wire/refused/*.xml`, `wire/refused.json` | Messages with a correct `check` that break a rule of the spec, and why | Refused as invalid: not recorded, nobody answers, the state does not change |
| `check.json` | Documents, their canonical text and their `check` | The canonical text and the first 8 hex characters of its SHA-256 |
| `patterns.json` | Patterns of the subset with texts that match and that do not, and patterns outside the subset | The same matches, and a refusal of the others |
| `state/` | The saved state of the node `cost` after the init and both init replies, the next message, and what happens | Load the state, receive the message, reach `expected.json` |

## What is in and what is not

- Only rules of the spec are here. A limit of one implementation (the longest integer it reads, the size of a pattern it compiles,
  how many messages it holds) is not a fixture.
- The field names of `parsed` and `state/` are those of the Python library (see `parse_message` in `wire.py`, and `state.py`). The values
  are the contract; another implementation maps the names. `state/` is the saved state of the Python node: an implementation with
  another state format can only check `expected.json` by replaying the messages.
- The messages carry no real secrets: every id and uuid is made up.

## Changing them

The files are generated, and the diff of a change is the change that every implementation has to follow:

```
cd packages/ioc-concord
uv run python tests/make_fixtures.py
```

`tests/test_fixtures.py` also checks that the builders of the library write the message files byte for byte.
