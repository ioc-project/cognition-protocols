# ioc-accord

The ACCORD protocol as a library. Stdlib only, Python 3.11 or later. It never prints, exits or writes outside the state
file it is given: it raises typed errors (all `AccordError`, each with a `code`).

| Module | Contents |
|---|---|
| `errors` | `AccordError` and its subclasses |
| `similarity` | text normalisation and the similarity score every merge is built on |
| `ingest` | reading specialist replies: raw text or JSON in, plain records out |
| `engine` | the deterministic merges: clustering, the glossary, set cover, the locked contract |
| `wire` | the requests a specialist receives, and the payload envelopes an orchestrator reads |
| `state` | the shape of a session's state, and which file an invocation belongs to |
| `store` | loading and saving that state, with an exclusive lock for concurrent replies |
| `flow` | the self-driving loop: the current phase, who still owes a reply, what to send next |

Similarity uses MiniLM cosine when `sentence-transformers` is installed with the model already on disk, and difflib's
ratio otherwise; `ACCORD_EMBEDDING=off` forces the string comparison.

The skill script under `protocols/accord/skills/` carries a generated copy of the code it uses from these modules, so it
stays a single self-contained file. Edit the library, then run `just sync` from the repository root; CI runs
`just check-sync`.

    uv run --project packages/ioc-accord pytest
