# ioc-concord (Python)

The CONCORD protocol as a Python library. It is the Rust crate [`ioc-concord`](../ioc-concord-rs) with its API for other languages (feature `uniffi`), and the Python bindings that
[UniFFI](https://mozilla.github.io/uniffi-rs/) generates for it, built into one wheel per platform: the code is the same as the Rust library's, so a run can have Python and Rust agents. The protocol is in
[`protocols/concord/spec/CONCORD-SPEC.md`](../../protocols/concord/spec/CONCORD-SPEC.md).

The library does no I/O: you give it the messages that arrive and the time that passes; it hands back the messages to send and what
happened. Everything it sends is a broadcast, so your channel has to deliver every message to every agent of the run.

## Use

```python
import json
import ioc_concord as ic            # the package is `ioc-concord`, the module `ioc_concord`

agent = ic.Agent("cost", "cost.json")       # the state file is optional; without it the state stays in memory
step = agent.start(["cost", "security"], "which plan?", "no plan", "the best plan",
                   json.dumps({"type": "string", "description": "what one option looks like"}))

for text in step.send:                      # messages to broadcast, as text
    broadcast(text)
for event in step.events:                   # what happened, for a log
    log(event.event, event.at, json.loads(event.details_json))

step = agent.receive(text)                  # a message arrived: any text, what is not CONCORD is ignored
for task in agent.due():                    # what your model has to write now
    if isinstance(task, ic.Task.INIT_REPLY):
        step = agent.submit_init_reply(task.run_id, ic.Number.INT(0), ic.Number.INT(100), "plan A", "the facts", ic.Number.INT(90), "why")
    elif isinstance(task, ic.Task.SCORE):
        scores = [ic.ScoreInput(id=c.id, raw=ic.Number.INT(80), reasoning="it holds") for c in task.candidates]
        step = agent.submit_score(task.run_id, scores)
    elif isinstance(task, ic.Task.REPAIR):
        step = agent.submit_repair(task.run_id, "plan B", "the facts", ic.Number.INT(90), "why")

wake = agent.next_wakeup()                  # when to call tick next; None when nothing is awaited
step = agent.tick()                         # time has passed: ask again, or give up on a silent agent
report = json.loads(agent.report_json())    # where the run ended
```

What is a JSON document in Rust is a JSON string here (`spec_json`, `Event.details_json`, `Task.INIT_REPLY.schema_json`, `status_json()`,
`report_json()`). `ConcordError` is an exception with one subclass per kind (`ConcordError.Schema`, `.InvalidRun`, `.InvalidMessage`,
`.State`, `.MissingState`). A number is a `Number` (`Number.TEXT`, `Number.INT` or `Number.FLOAT`). The API is the `ffi` module of
[`ioc-concord-rs`](../ioc-concord-rs/README.md#bindings-for-other-languages), which also describes the model it wraps.

## The `ioc-concord` command

The wheel also installs the `ioc-concord` command (`uvx ioc-concord ...`). It is the crate's `cli` module, which the
bindings export as `cli_run`: the arguments, the output and the state are all in Rust, and `ioc_concord/cli.py` only prints what comes back.

    ioc-concord initiator init --agents cost,security --context "..." --x-reject "..." --x-ideal "..." --schema '{...}'
    ioc-concord initiator --state <path> handle           # the first message; then every reply, on stdin
    ioc-concord receiver --me cost handle                 # every CONCORD message, on stdin
    ioc-concord receiver --me cost answer --reject N --ideal N --candidate "..." --rationale "..." --score seed-cost N "..."

Every output starts with `STATUS: SEND`, `ASK`, `NONE` or `ERROR` and says what to do next. The skills in
[`protocols/concord/skills`](../../protocols/concord/skills) describe how an agent uses it. The state is kept in
`.concord-receiver/` (receiver) and in the file `init` prints (initiator); `CONCORD_STATE_DIR` moves the receiver's.

## Build

The wheel is built with [maturin](https://www.maturin.rs/), which builds the crate with its `uniffi` feature, generates the bindings
and puts both in the wheel (see `pyproject.toml`). Rust is needed to build it, at the version `rust-version` of the crate says.

    uvx maturin build --release --out dist         # from packages/ioc-concord-python

## Tests

    uv run --no-project --with pytest --with dist/*.whl python -I -m pytest tests     # after the build

The Rust tests are in `packages/ioc-concord-rs`.
