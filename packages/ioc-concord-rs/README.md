# ioc-concord (Rust)

The CONCORD protocol as a Rust library. The protocol is described in
[`protocols/concord/spec/CONCORD-SPEC.md`](../../protocols/concord/spec/CONCORD-SPEC.md).

The library does no I/O. It has no transport, no model and no threads, it never prints or exits, and it keeps no global state. You give
it the messages that arrive and the time that passes; it hands back the messages to send and what happened. Every fallible call
returns `ioc_concord::Result`, whose error, `ConcordError`, has a `code()`.

## The node

`Agent` is one agent of a run. Everything it sends is a broadcast: a message is never addressed to one agent, so your channel has to
deliver every message to every agent of the run.

```rust
use ioc_concord::agent::{Agent, Step};
use ioc_concord::store::FileStore;
use ioc_concord::tasks::Task;
use ioc_concord::wire::ScoreIn;

// the store is optional; without it the state stays in memory
let mut me = Agent::builder("cost").store(FileStore::new("cost.json")).build()?;

let handle = |step: Step| {
    for text in &step.send {            // messages to broadcast, as text
        broadcast(text);
    }
    for event in &step.events {         // what happened, for a log: event.event, event.at and its details
        log(event);
    }
};

handle(me.receive(&text, None)?);       // a message arrived: any text, what is not CONCORD is ignored

for task in me.due() {                  // what your model has to write now
    match task {
        Task::InitReply(t) => handle(me.submit_init_reply(&t.run_id, raw_reject, raw_ideal, candidate, rationale, raw, reasoning, None)?),
        Task::Score(t) => {
            let scores: Vec<ScoreIn> = t.candidates.iter().map(|c| ScoreIn::new(&c.id, raw, reasoning)).collect();
            handle(me.submit_score(&t.run_id, &scores, None)?)
        }
        Task::Repair(t) => handle(me.submit_repair(&t.run_id, candidate, rationale, raw, reasoning, None)?),
    }
}

let wake = me.next_wakeup();            // when to call tick next; None when nothing is awaited
handle(me.tick(None)?);                 // time has passed: ask again, or give up on a silent agent
```

An agent that opens a run calls `start`; it is a participant too when it is listed in `participants`:

```rust
use ioc_concord::wire::InitParams;
use serde_json::json;

let mut alice = Agent::new("alice")?;
let params = InitParams::default().satisfaction_threshold("0.6").budget(4);
let step = alice.start(&["cost", "security"], "the problem", "worst outcome", "best outcome",
                       &json!({"type": "string", "description": "what one option looks like"}), Some(&params), None, None)?;
```

What is mechanical is done by the node: the order of checks, duplicates, versions and integrity checks, resends and errors, the
decisions of the engine, the timers, the result and the cancel. What your model writes is only content: the anchors, a seed, the scores
with their reasoning, and a repair. A task is worked out from the state, not queued: `due()` gives the same answer twice, and after a
restart from the store it is still right. An invalid submission returns `ConcordError::InvalidMessage` and changes nothing, so you can
fix it and submit again.

Every call that takes the time takes an `Option<f64>`: `None` reads the clock of the agent (`Agent::builder(..).clock(..)`), which is
the system clock by default. The source of UUIDs (`.uuid_fn(..)`) is injected the same way, so a run can be replayed.

### What your model reads

What the other agents wrote goes into the text the model reads as it is, unescaped: the protocol assumes cooperative participants, and the
model is as exposed to them as it would be to any text a peer wrote. An integration that cannot assume that has to delimit it.

The node decides; your model only writes content, and what it needs to see is in `guidance`, the reference wording of Appendix A of the
specification. You may use it as it is, or write your own with the same points.

```rust
use ioc_concord::guidance::{render_delivery, render_end, refusal, INITIATOR_GUIDANCE, RUN_DESCRIPTION};

let mut inbox: Vec<String> = Vec::new();            // the messages of the others that the model has not read yet
let step = me.receive(&text, None)?;
inbox.extend(step.accepted);                        // the texts the node took: it joined the run, or recorded the message

let system = &*RUN_DESCRIPTION;                     // once: how a run goes, and what the model receives and sends
                                                    // (INITIATOR_GUIDANCE as well, for the agent that opens the run)
for task in me.due() {
    let prompt = format!("{}\n\n{}", render_delivery(&inbox), me.instructions(&task)?);
    inbox.clear();
    // the model answers; `submit_*` returns InvalidMessage if it is wrong
    // on a refusal: refusal(&error, repeats) says what is wrong and what to do about it
}

// when the run is over: how it ended, for the model's context
println!("{}", render_end("cost", &me.report(None).unwrap()));
```

`check_answer` holds the checks that go beyond the specification (a reasoning too short to be one, a 0-1 number sent as a raw score),
and `hint_for`, `lacks` and `wrong_shape` build the refusals of an answer that is wrong or incomplete. `status()` says where a run
stands for the agent, and `report()` is the run report of the spec: outcome, winner (id and text), final scores, shortfall, rounds,
reason and `status`.

### Relay: the centre of a star

On a broadcast channel and on a full mesh nothing relays. In a star, the centre passes the messages of the runs it knows on to every
spoke except the one a message came from, and sends its own messages to every spoke. `Relay` makes the passing-on decision and keeps the
run ids it needs, in its own store, and the spokes. A centre that takes part in no run is just a `Relay`; one that does gives it to its
agent. It never sends anything: you do, to the spokes that `targets` names.

The spokes are the links of the centre, which only your transport knows: the init lists the agents that negotiate, not the links. So you
register them, as names you can send through (a connection id, a socket id):

```rust
use ioc_concord::relay::Relay;

let mut relay = Relay::with_store(FileStore::new("relay.json"))?;
relay.add_spoke("conn-1");                          // when a spoke connects
relay.remove_spoke("conn-1");                       // when it goes

for out in relay.receive(&text)? {                  // a pure relay: the text itself, unchanged, or nothing
    for spoke in relay.targets(Some(origin)) {      // every spoke except the one it came from
        send(&spoke, &out);
    }
}

let mut hub = Agent::builder("hub").store(FileStore::new("hub.json")).relay(relay).build()?;   // a centre that also takes part
let step = hub.receive(&text, None)?;
for out in &step.forward {                          // pass on, as above, whatever the agent does with the message
    for spoke in hub.relay().unwrap().targets(Some(origin)) { send(&spoke, out); }
}
for out in &step.send {                             // the agent's own messages go to every spoke
    for spoke in hub.relay().unwrap().targets(None) { send(&spoke, out); }
}
```

An agent without a relay never fills `step.forward`. The relay knows a run once it has passed on its init, bare or inside a resend, and
it reads only the name and the `run_id` of a message: it passes on a message of another version, a damaged one, or one the agent drops,
and it keeps the run after the run has ended. Call `relay.forget(run_id)` when the state of a run is dropped. The run ids are saved; the
spokes are not, because they are live links: after a restart they connect again, and you register them again.

### State

The state is plain data (`agent.state`, an `AgentState`), saved as JSON through the `Store` after every call that changes it. A `Store`
is anything with `load()` and `save(&Value)`; `FileStore` writes atomically, and `MemoryStore` keeps the state in memory (a clone shares
it, which is how a test restarts an agent).

### Numbers

Every number the library holds (a raw score, an anchor, `satisfaction_threshold`, `eps`) is an integer count of millionths, so that no
floating-point error can change a decision: `82.5` is `82_500_000`. Builders and `submit_*` take a number as a `Num`, which is made from
text, an integer or a float (read from its shortest text, so at most 6 decimal places); the wire carries the text of the spec
(`82.5`); parsers, reports and tasks give millionths, and `guidance` writes them as text. The scores in a task are normalized between 0
and 1.

## Modules

| Module | Contents |
|---|---|
| `errors` | `ConcordError` and its kinds |
| `protocol` | the version, the default parameters, the outcomes, and the forms of ids, UUIDs and numbers |
| `xmlutil` | escaping, and the integrity check of a document (computed on its text) |
| `schema` | parse, validate and render a candidate schema; the subset of patterns; check a candidate against it |
| `engine` | the decision engine: SELECT, ACCEPT, the bottleneck, the outcome, in integer millionths |
| `wire` | build and read every message, in the layers of the order of checks |
| `store` | the `Store` trait, an atomic `FileStore` and a `MemoryStore` |
| `state` | the state of an agent and of a run, and the keys of the items a run is made of |
| `liveness` | the messages an agent is owed and the timers that wait for them |
| `tasks` | what the model must write now |
| `report` | where a run stands, and the report of the run |
| `guidance` | what the model reads: the wording of the run and of each task, the messages of the others, the end, a refusal |
| `relay` | the centre of a star: which messages it passes on to the spokes |
| `agent` | the node |
