# ioc-concord

The CONCORD protocol as a library: the node that every participant of a run runs, and the pieces it is made of. Stdlib only,
Python 3.11 or later. The wire version is `0.3.0` (`ioc_concord.protocol.PROTOCOL_VERSION`); the protocol is in
[`protocols/concord/spec/CONCORD-SPEC.md`](../../protocols/concord/spec/CONCORD-SPEC.md).

The library does no I/O. It has no transport, no model and no threads, it never prints or exits, and it keeps no module-level
state. You give it the messages that arrive and the time that passes; it hands back the messages to send and what happened. It
raises typed errors (all `ConcordError`, each with a `code`).

## The node

`Agent` is one agent of a run. Everything it sends is a broadcast: a message is never addressed to one agent, so your channel
has to deliver every message to every agent of the run.

```python
from ioc_concord.agent import Agent
from ioc_concord.store import FileStore

me = Agent("cost", store=FileStore("cost.json"))       # the store is optional; without it the state stays in memory


def handle(step):
    for text in step.send:                             # messages to broadcast, as text
        broadcast(text)
    for event in step.events:                          # what happened, for a log: {"event": ..., "at": ..., ...}
        log(event)


handle(me.receive(text))                               # a message arrived: any text, what is not CONCORD is ignored

for task in me.due():                                  # what your model has to write now
    if task["kind"] == "init-reply":                   # run_id, context, x_reject, x_ideal, schema
        handle(me.submit_init_reply(task["run_id"], raw_reject, raw_ideal, candidate, rationale, raw, reasoning))
    elif task["kind"] == "score":                      # run_id, candidates: [{id, from, candidate, rationale, reasoning}]
        handle(me.submit_score(task["run_id"], [(c["id"], raw, reasoning) for c in task["candidates"]]))
    elif task["kind"] == "repair":                     # run_id, round, incumbent, scores, shortfall, ...
        handle(me.submit_repair(task["run_id"], candidate, rationale, raw, reasoning))

wake = me.next_wakeup()                                # when to call tick next; None when nothing is awaited
handle(me.tick())                                      # time has passed: ask again, or give up on a silent agent
```

An agent that opens a run calls `start`; it is a participant too when it is listed in `participants`:

```python
alice = Agent("alice")
handle(alice.start(["cost", "security"], "the problem", "worst outcome", "best outcome",
                   {"type": "string", "description": "what one option looks like"},
                   params={"satisfaction_threshold": "0.6", "B": 4}))
```

What is mechanical is done by the node: the order of checks, duplicates, versions and integrity checks, resends and errors, the
decisions of the engine, the timers, the result and the cancel. What your model writes is only content: the anchors, a seed, the
scores with their reasoning, and a repair. A task is worked out from the state, not queued: `due()` gives the same answer twice,
and after a restart from the store it is still right. An invalid submission raises `InvalidMessage` and changes nothing, so you can
fix it and submit again.

`status()` says where a run stands for the agent, and `report()` is the run report of the spec: outcome, winner (id and text),
final scores, shortfall, rounds, reason and `status`.

### State

The state is a plain JSON-able dict (`agent.state`), saved through the `Store` after every call that changes it. A `Store` is
anything with `load()` and `save(state)`; `FileStore` writes atomically. The clock (`clock=`) and the source of UUIDs
(`uuid_fn=`) are injected, with the standard library as default, so a run can be replayed.

### Numbers

Every number the library holds (a raw score, an anchor, `satisfaction_threshold`, `eps`) is an integer count of millionths, so that
no floating-point error can change a decision: `82.5` is `82_500_000`. Builders and `submit_*` take a number as text, an int or a
float (read from its shortest text, so at most 6 decimal places); the wire carries the text of the spec (`82.5`); parsers and
reports give millionths. The scores in a task are text, normalized between 0 and 1.

## Extensions that are not in the spec

- **`timeout = 0`** in the init switches the timers off: nobody asks again and nobody gives up on a clock, so a run never aborts on
  silence. The spec requires `timeout` above 0, so another implementation answers such an init as invalid.
- **Report statuses `aborted` and `pending`.** The spec lists `confirmed`, `unverified` and `disputed`. An aborted run reports
  `aborted`, and a run that has not ended reports `pending`.

## Modules

| Module | Contents |
|---|---|
| `errors` | `ConcordError` and its subclasses |
| `protocol` | the version, the default parameters, the outcomes, and the forms of ids, UUIDs and numbers |
| `xmlutil` | escaping, and the integrity check of a document (computed on its text) |
| `schema` | parse, validate and render a candidate schema; the subset of patterns; check a candidate against it |
| `engine` | the decision engine: SELECT, ACCEPT, the bottleneck, the outcome, in integer millionths |
| `wire` | build and read every message, in the layers of the order of checks |
| `store` | the `Store` protocol and an atomic `FileStore` |
| `state` | the state of an agent and of a run, and the keys of the items a run is made of |
| `liveness` | the messages an agent is owed and the timers that wait for them |
| `tasks` | what the model must write now |
| `report` | where a run stands, and the report of the run |
| `agent` | the node |

## Using it from a skill script

The skill scripts under `protocols/concord/skills/` carry a generated copy of the code they use from these modules, so each stays a
single self-contained file. The copy is of a library revision that is pinned in `tools/bundle.toml`, not of the files on disk, so
this library can change without the scripts. To adopt a newer library, change the pin and run `just sync` from the repository root;
CI runs `just check-sync`.

A module that is bundled must import only the standard library and its sibling modules as `from ioc_concord.<module> import <names>`,
and keep its top-level names unique across the bundled modules (see `tools/bundle.py`). A new module goes in the module list of the
tests (`MODULES` in `tests/conftest.py`) and, when a script is to carry it, in `tools/bundle.toml`.

## Tests

    just test-lib          # from the repository root: the library on disk, as imported and as bundled
    uv run --directory packages/ioc-concord pytest

Every module is tested twice: as imported, and as bundled into a script. The node is also run on a simulated network in virtual
time, with delayed, duplicated and lost messages, restarts from the saved state, silent agents, other versions and two runs at once.
