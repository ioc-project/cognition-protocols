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

### What your model reads

The node decides; your model only writes content, and what it needs to see is in `guidance`, the reference wording of Appendix A of the
specification. You may use it as it is, or write your own with the same points.

```python
from ioc_concord.guidance import RUN_DESCRIPTION, INITIATOR_GUIDANCE, render_delivery, render_end, refusal

inbox = []                                             # the messages of the others that the model has not read yet
step = me.receive(text)
inbox += step.accepted                                 # the texts the node took: it joined the run, or recorded the message

system = RUN_DESCRIPTION                               # once: how a run goes, and what the model receives and sends
                                                       # (INITIATOR_GUIDANCE as well, for the agent that opens the run)
for task in me.due():
    prompt = render_delivery(inbox) + "\n\n" + me.instructions(task)    # the others' messages as they arrived, then the task
    inbox.clear()
    ...                                                # the model answers; `submit_*` raises InvalidMessage if it is wrong
    # on a refusal: refusal(error) says what is wrong and what to do about it (give it the error itself: its type says when an answer is already given)

# when the run is over: how it ended, for the model's context
print(render_end("cost", me.report()))
```

`instructions(task)` is the text of a task of `due()`. `check_answer` holds the checks that go beyond the specification (a reasoning too
short to be one, a 0-1 number sent as a raw score), and `hint_for`, `lacks` and `wrong_shape` build the refusals of an answer that is
wrong or incomplete. A model keeps its whole conversation for the run: the messages it was given, the tasks and its own answers.

`status()` says where a run stands for the agent, and `report()` is the run report of the spec: outcome, winner (id and text),
final scores, shortfall, rounds, reason and `status`.

### Relay: the centre of a star

On a broadcast channel and on a full mesh nothing relays. In a star, the centre passes the messages of the runs it knows on to every
spoke except the one a message came from, and sends its own messages to every spoke. `Relay` makes the passing-on decision and keeps
the run ids it needs, in its own store, and the spokes. A centre that takes part in no run is just a `Relay`; one that does gives it to
its agent. It never sends anything: you do, to the spokes that `targets` names.

The spokes are the links of the centre, which only your transport knows: the init lists the agents that negotiate, not the links. So you
register them, as any hashable handle you can send through (a connection, a socket id, a name):

```python
relay = Relay(store=FileStore("relay.json"))
relay.add_spoke(conn)                                  # when a spoke connects
relay.remove_spoke(conn)                               # when it goes

for out in relay.receive(text):                        # a pure relay: the text itself, unchanged, or nothing
    for spoke in relay.targets(origin):                # every spoke except the one it came from
        spoke.send(out)

hub = Agent("hub", store=FileStore("hub.json"), relay=relay)   # a centre that also takes part
step = hub.receive(text)
for out in step.forward:                               # pass on, as above, whatever the agent does with the message
    for spoke in relay.targets(origin):
        spoke.send(out)
for out in step.send:                                  # the agent's own messages go to every spoke
    for spoke in relay.targets():
        spoke.send(out)
```

An agent without a `relay` never fills `step.forward`. The relay knows a run once it has passed on its init, bare or inside a resend, and
it reads only the name and the `run_id` of a message: it passes on a message of another version, a damaged one, or one the agent drops,
and it keeps the run after the run has ended. Call `relay.forget(run_id)` when the state of a run is dropped. The run ids are saved; the
spokes are not, because they are live links: after a restart they connect again, and you register them again.

### State

The state is a plain JSON-able dict (`agent.state`), saved through the `Store` after every call that changes it. A `Store` is
anything with `load()` and `save(state)`; `FileStore` writes atomically. The clock (`clock=`) and the source of UUIDs
(`uuid_fn=`) are injected, with the standard library as default, so a run can be replayed.

### Numbers

Every number the library holds (a raw score, an anchor, `satisfaction_threshold`, `eps`) is an integer count of millionths, so that
no floating-point error can change a decision: `82.5` is `82_500_000`. Builders and `submit_*` take a number as text, an int or a
float (read from its shortest text, so at most 6 decimal places); the wire carries the text of the spec (`82.5`); parsers and
reports give millionths. The scores in a task are text, normalized between 0 and 1.

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
| `guidance` | what the model reads: the wording of the run and of each task, the messages of the others, the end, a refusal |
| `relay` | the centre of a star: which messages it passes on to the spokes |
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
