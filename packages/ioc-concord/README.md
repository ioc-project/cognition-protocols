# ioc-concord

The CONCORD protocol as a library. Stdlib only, Python 3.11 or later. It never prints, exits or keeps module-level state:
it raises typed errors (all `ConcordError`, each with a `code`).

| Module | Contents |
|---|---|
| `errors` | `ConcordError` and its subclasses |
| `xmlutil` | escaping and element helpers, document regexes |
| `schema` | parse, validate and render a candidate schema; check a candidate against it |
| `engine` | the decision engine: candidates, ratings, leximin selection, repair rounds |
| `wire` | the protocol vocabulary, request builders, reply builders and parsers |
| `store` | the `Store` protocol and an atomic `FileStore` |
| `state` | constructors and checks for run and agent state |

The skill scripts under `protocols/concord/skills/` carry a generated copy of the code they use from these modules, so each stays a single
self-contained file. Edit the library, then run `just sync` from the repository root; CI runs `just check-sync`.

    uv run --project packages/ioc-concord pytest
