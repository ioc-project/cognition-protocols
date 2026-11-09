---
icon: material/transit-connection-variant
---

# ACCORD — A2A Transport Binding

<p class="eyebrow">Running ACCORD over the Agent-to-Agent protocol</p>

This document specifies how to run ACCORD over the [Agent-to-Agent (A2A) protocol](https://a2a-protocol.org).
It covers how an ACCORD-capable agent declares its capability in an AgentCard, how ACCORD messages are
carried in A2A task turns, and how the multi-phase ACCORD session maps onto the A2A task lifecycle.

Protocol semantics — the phase sequence, the merge operators, the lock condition, and the request and
reply payloads — are unchanged and defined by the
[ACCORD overview](index.md) and the `accord-initiator` and `accord-receiver` skills.

---

## Media type

All ACCORD messages carried over A2A use the media type:

```
application/vnd.ioc-project.accord
```

A Part carrying an ACCORD message uses the `text` field (or `raw` for binary-safe transport) with
`mediaType` set to this value. The content is the verbatim ACCORD message — no wrapper, no
reformatting, no paraphrase.

Unlike CONCORD, ACCORD messages are not a single XML document. A request is a delivery header
followed by a request prefix and a set of labelled sections; a phase reply is a bare JSON object.
The prefix on the first non-header line is what tells the receiver which handler applies, so one
media type covers both directions.

```json
{
  "text": "<accord-delivery state=\"a1b2c3d4\"></accord-delivery>\n\nGROUND_REQUEST\n\nActivate your `accord-receiver` skill and follow its GROUND_REQUEST handler.\n\nKEY_TERMS: [\n  \"error_rate\",\n  \"rollback_target\"\n]",
  "mediaType": "application/vnd.ioc-project.accord"
}
```

!!! note "The delivery header carries the session identifier"
    Every phase request opens with `<accord-delivery state="…"></accord-delivery>`, where `state` is
    the ACCORD session id. It is a separate identifier from the A2A `taskId` and `contextId` and is
    transport-independent. `EXECUTION_REQUEST` is the one message that carries no delivery header,
    because the locked contract it contains already identifies the session.

---

## AgentCard skill declaration

An agent that implements the **ACCORD receiver** role MUST advertise the following skill in its
AgentCard at `/.well-known/agent.json`:

```json
{
  "id": "accord-receiver",
  "name": "ACCORD Receiver",
  "description": "Accepts ACCORD intent-alignment messages as a specialist. Handles the CONVENE, FRAME, GROUND, CLARIFY and VERIFY phase requests, and applies the locked Intent Contract on EXECUTION_REQUEST.",
  "tags": ["accord", "intent-alignment", "coordination"],
  "inputModes": ["application/vnd.ioc-project.accord"],
  "outputModes": ["application/vnd.ioc-project.accord"]
}
```

An agent that acts only as an initiator and never as a receiver need not advertise this skill.

### Complete AgentCard example

```json
{
  "name": "Telemetry Specialist",
  "description": "Telemetry-domain specialist agent; contributes to ACCORD intent alignment.",
  "url": "https://example.com/agents/telemetry",
  "version": "1.0.0",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "accord-receiver",
      "name": "ACCORD Receiver",
      "description": "Accepts ACCORD intent-alignment messages as a specialist. Handles the CONVENE, FRAME, GROUND, CLARIFY and VERIFY phase requests, and applies the locked Intent Contract on EXECUTION_REQUEST.",
      "tags": ["accord", "intent-alignment", "coordination"],
      "inputModes": ["application/vnd.ioc-project.accord"],
      "outputModes": ["application/vnd.ioc-project.accord"]
    }
  ]
}
```

---

## Session model

An ACCORD run maps onto A2A as **multi-turn exchanges within a task**: each phase request is an
additional turn on an existing task, not a new task per message. The initiator sends each request
via `SendMessage` and the specialist replies in a `TaskStatusUpdate` with `state: INPUT_REQUIRED`
and the ACCORD reply in the status `message`.

### Task and context reuse

The initiator may already have an active A2A task with each specialist — for example, the work that
triggered the ACCORD run. In that case:

- The initiator **SHOULD** send ACCORD messages as additional turns on the existing task by setting
  `taskId` (and `contextId` if known) on the `SendMessage` request.
- The initiator **MUST NOT** create a new task when an active task with that specialist already
  exists for the same work.

When no existing task exists with a specialist, the initiator creates one per specialist with the
first `SendMessage` (omitting `taskId`). The server assigns a `taskId` and `contextId`; the initiator
MUST record both per specialist and reuse them in all subsequent turns.

!!! note "ACCORD runs before the work, not alongside it"
    ACCORD is a pre-delegation protocol: the contract must be locked before any specialist does
    domain work or calls an action tool. When the initiator reuses an existing task, the phase
    requests therefore precede the assignment on that same task, and `EXECUTION_REQUEST` is the turn
    that releases the specialist to work.

---

## Transport topologies

ACCORD is strictly initiator-driven. There is no relay and no peer-to-peer traffic: specialists
never see each other's raw replies. What they see is the **merged artifact** the initiator computes
at each phase barrier — `CURRENT_FRAME`, `DEFINITIONS_SO_FAR`, `PROBLEM_FRAME` — which is a protocol
output, not a forwarded message.

### Point-to-point (default)

The initiator holds one A2A task per specialist and fans out each phase request in parallel,
collecting every reply before running the merge. This is the default and is described throughout
this document.

### Group communication

Three of the phase requests are byte-identical for every specialist and may therefore be broadcast
on a shared channel — for example via
[A2A collaborative task](https://github.com/a2aproject/experimental-cpb-slimrpc/blob/main/spec/v1/a2a-collaborative-task.md):

| Request | Identical for all specialists? | Why |
|---|---|---|
| `CONVENE_REQUEST` | Yes | Carries only `TASK` and `SPECIALISTS` |
| `GROUND_REQUEST` | Yes | Carries only `KEY_TERMS` |
| `VERIFY_REQUEST` | Yes | Carries `REQUIRED_KNOWLEDGE`, `OBJECTIVE_CLAUSES` and `PROBLEM_FRAME` |
| `FRAME_REQUEST` | No | Carries `YOUR_PREVIOUS_ATOMS` on revision rounds |
| `CLARIFY_REQUEST` | No | Carries the `AMBIGUOUS_PAIR` raised for that agent |
| `EXECUTION_REQUEST` | No | Carries that agent's `ASSIGNMENT` |

In this topology:

- The initiator broadcasts the identical requests to the shared channel; the channel delivers to all
  specialists.
- The per-agent requests MUST still be sent point-to-point, even when a channel is available.
- **Replies MUST NOT be broadcast.** Every reply goes to the initiator alone. A specialist that can
  read another's raw reply may revise its own contribution to match, which destroys the independence
  the merge depends on — the support count in FRAME and the split/link decision in GROUND are only
  meaningful over contributions made without sight of each other.
- The ACCORD `state` attribute remains the authoritative run identifier and is transport-independent.

---

## Message flow

The sequence below covers a two-specialist run (telemetry + database) in point-to-point mode, from
CONVENE through EXECUTION. The CLARIFY phase is conditional and is shown as an optional turn.

```mermaid
sequenceDiagram
    participant I as Initiator
    participant T as Telemetry agent
    participant D as Database agent

    Note over I,D: CONVENE — who is here, what can they do
    I->>T: SendMessage(parts=[CONVENE_REQUEST])
    I->>D: SendMessage(parts=[CONVENE_REQUEST])
    T-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[agent card JSON])
    D-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[agent card JSON])
    Note over I: merge -> Roster

    Note over I,D: FRAME — state the task from every perspective
    I->>T: SendMessage(taskId=T-task, parts=[FRAME_REQUEST + CURRENT_FRAME])
    I->>D: SendMessage(taskId=D-task, parts=[FRAME_REQUEST + CURRENT_FRAME])
    T-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[atoms JSON])
    D-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[atoms JSON])
    Note over I: merge -> Problem Frame (may repeat for a revision round)

    Note over I,D: GROUND — do we mean the same things
    I->>T: SendMessage(taskId=T-task, parts=[GROUND_REQUEST + KEY_TERMS])
    I->>D: SendMessage(taskId=D-task, parts=[GROUND_REQUEST + KEY_TERMS])
    T-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[units JSON])
    D-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[units JSON])
    Note over I: merge -> Glossary; near-threshold pairs trigger CLARIFY

    opt a term pair is near the split threshold
        I->>T: SendMessage(taskId=T-task, parts=[CLARIFY_REQUEST + AMBIGUOUS_PAIR])
        T-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[extended definition JSON])
        Note over I: re-merge -> Glossary (extended)
    end

    Note over I,D: VERIFY — who holds what, how is done checked
    I->>T: SendMessage(taskId=T-task, parts=[VERIFY_REQUEST])
    I->>D: SendMessage(taskId=D-task, parts=[VERIFY_REQUEST])
    T-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[holds + checks JSON])
    D-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, parts=[holds + checks JSON])
    Note over I: merge -> Verifiable Objective, then LOCK (no message is sent)

    Note over I,D: EXECUTION — the contract is delivered with the assignment
    I->>T: SendMessage(taskId=T-task, parts=[EXECUTION_REQUEST + LOCKED_INTENT_CONTRACT])
    I->>D: SendMessage(taskId=D-task, parts=[EXECUTION_REQUEST + LOCKED_INTENT_CONTRACT])
    T-->>I: TaskStatusUpdate(parts=[LOCK_STATUS: RECEIVED + analysis])
    D-->>I: TaskStatusUpdate(parts=[LOCK_STATUS: RECEIVED + analysis + VERIFICATION])
```

### Phase-by-phase mapping

| ACCORD phase | A2A operation | `state` in response |
|---|---|---|
| CONVENE request | `SendMessage` (new or existing task) — fan-out from initiator | — |
| CONVENE reply | `TaskStatusUpdate` — agent card | `INPUT_REQUIRED` |
| FRAME request | `SendMessage` (same task) — fan-out, may repeat per revision round | — |
| FRAME reply | `TaskStatusUpdate` — task atoms | `INPUT_REQUIRED` |
| GROUND request | `SendMessage` (same task) — fan-out | — |
| GROUND reply | `TaskStatusUpdate` — term units | `INPUT_REQUIRED` |
| CLARIFY request *(conditional)* | `SendMessage` (same task) — to the agents in the ambiguous pair only | — |
| CLARIFY reply | `TaskStatusUpdate` — extended definition | `INPUT_REQUIRED` |
| VERIFY request | `SendMessage` (same task) — fan-out | — |
| VERIFY reply | `TaskStatusUpdate` — holdings and proposed checks | `INPUT_REQUIRED` |
| LOCK | *no message* — the initiator assembles the contract locally | — |
| EXECUTION request | `SendMessage` (same task) — per-agent assignment plus the contract | — |
| EXECUTION reply | `TaskStatusUpdate` — `LOCK_STATUS` line, analysis, optional `VERIFICATION` | implementation's choice |

LOCK is deliberately absent from the wire. The contract becomes visible to specialists only when it
arrives inside `EXECUTION_REQUEST`, which is also the point at which they are released to use tools.

---

## Conformance

The following requirements apply to any implementation of this binding.

### Initiator

- MUST send each ACCORD message as a `SendMessage` with exactly one Part carrying
  `mediaType: "application/vnd.ioc-project.accord"`.
- MUST reuse an existing `taskId` and `contextId` when an active task already exists for a
  specialist in the same work context.
- MUST record the server-assigned `taskId` and `contextId` from the first task response and supply
  them on all subsequent `SendMessage` calls to that specialist in the run.
- MUST collect every specialist's reply for a phase before running that phase's merge. A phase is a
  barrier; partial input produces a partial artifact.
- MUST NOT forward a specialist's raw reply to another specialist. Only merged artifacts
  (`CURRENT_FRAME`, `DEFINITIONS_SO_FAR`, `PROBLEM_FRAME`) are shared.
- MUST NOT send `EXECUTION_REQUEST` before the contract reaches `INTENT-LOCKED`, and MUST NOT call
  an action tool before every `EXECUTION_REQUEST` has been delivered.
- MUST NOT treat a `TaskStatus.message` that contains no
  `application/vnd.ioc-project.accord` Part as an ACCORD reply. Prose is not a reply; the initiator
  must re-send the request.

### Receiver

- MUST advertise the `accord-receiver` skill in its AgentCard as specified above when it implements
  the receiver role.
- MUST dispatch on the request prefix (`CONVENE_REQUEST`, `FRAME_REQUEST`, `GROUND_REQUEST`,
  `CLARIFY_REQUEST`, `VERIFY_REQUEST`, `EXECUTION_REQUEST`) and apply the handler for that prefix in
  preference to any other role instruction for that message.
- MUST respond with the reply payload for that handler in a Part with
  `mediaType: "application/vnd.ioc-project.accord"`. Phase replies are a bare JSON object with no
  preamble and no code fences.
- MUST NOT call data or action tools while handling a phase request. Tools belong to
  `EXECUTION_REQUEST` turns only.
- MUST begin an `EXECUTION_REQUEST` reply with `LOCK_STATUS: RECEIVED` when a
  `LOCKED_INTENT_CONTRACT` was delivered, or `LOCK_STATUS: MISSING` when none was.
- **MUST NOT transition an A2A task to a terminal state (`COMPLETED`, `FAILED`, `CANCELED`) while an
  ACCORD session is open on that task.** A session is open from the receipt of a `CONVENE_REQUEST`
  until the specialist has answered an `EXECUTION_REQUEST` for that session.
- MAY transition the task to any appropriate state once it has answered `EXECUTION_REQUEST`; this
  binding does not mandate `COMPLETED`.

### Both roles

- The `text` (or `raw`) content of every `application/vnd.ioc-project.accord` Part MUST be the
  verbatim ACCORD message. It MUST NOT be paraphrased, reformatted, or reconstructed by hand, and the
  `<accord-delivery>` header MUST be preserved where the message carries one.

---

## Related

- [Overview](index.md) — what ACCORD establishes and why
- [Evaluation](../../evaluation/accord.md) — measured results across 150 scenarios
- [CONCORD A2A Binding](../concord/a2a-binding.md) — the equivalent binding for the selection protocol
- [Tutorial: AgentBridge](../../tutorials/agentbridge/index.md) — running the protocols over A2A via AgentBridge and SLIM
