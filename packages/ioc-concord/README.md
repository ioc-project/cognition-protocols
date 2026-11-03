# ioc-concord

The CONCORD protocol as a library: the decision engine, and (as they are added) the documents, validation and state
persistence. Stdlib only, Python 3.11 or later.

The skill scripts under `protocols/concord/skills/` carry a generated copy of the modules they use, so each stays a single
self-contained file. Edit the library, then run `just sync` from the repository root; CI runs `just check-sync`.

    uv run --project packages/ioc-concord pytest
