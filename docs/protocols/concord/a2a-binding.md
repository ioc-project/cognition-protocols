---
icon: material/transit-connection-variant
---

# CONCORD — A2A Transport Binding

<p class="eyebrow">Running CONCORD over the Agent-to-Agent protocol</p>

This document specifies how to run CONCORD over the [Agent-to-Agent (A2A) protocol](https://a2a-protocol.org).
It covers how a CONCORD-capable agent declares its capability in an AgentCard, how CONCORD XML messages
are carried in A2A task turns, and how the multi-turn CONCORD session maps onto A2A task lifecycle.

Protocol semantics — phase sequence, scoring model, termination conditions, and XML message schemas —
are unchanged and defined in the [CONCORD Specification](specification.md).

---

## Media type

All CONCORD messages carried over A2A use the media type:

```
application/vnd.ioc-project.concord+xml
```

A Part carrying a CONCORD message uses the `text` field (or `raw` for binary-safe transport) with
`mediaType` set to this value. The content is verbatim CONCORD XML as defined in the
[CONCORD Specification](specification.md) — no wrapper, no reformatting.

```json
{
  "text": "<concord-anchor>\n  <satisfaction_threshold>0.6</satisfaction_threshold>\n  <x_reject>error rate stays above 40%</x_reject>\n  <x_ideal>error rate returns to 0</x_ideal>\n  <instructions>…</instructions>\n</concord-anchor>",
  "mediaType": "application/vnd.ioc-project.concord+xml"
}
```

---

## AgentCard skill declaration

An agent that implements the **CONCORD participant** role MUST advertise the following skill in its
AgentCard at `/.well-known/agent.json`:

```json
{
  "id": "concord-participant",
  "name": "CONCORD Participant",
  "description": "Accepts CONCORD negotiation messages as a participant. Handles anchor, seed, and scoring/repair rounds driven by the relay delivery stream.",
  "tags": ["concord", "negotiation", "consensus"],
  "inputModes": ["application/vnd.ioc-project.concord+xml"],
  "outputModes": ["application/vnd.ioc-project.concord+xml"]
}
```

An agent that acts only as an initiator and never as a participant need not advertise this skill.

### Complete AgentCard example

```json
{
  "name": "Security Specialist",
  "description": "Security-domain specialist agent; participates in CONCORD negotiation rounds.",
  "url": "https://example.com/agents/security",
  "version": "1.0.0",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "concord-participant",
      "name": "CONCORD Participant",
      "description": "Accepts CONCORD negotiation messages as a participant. Handles anchor, seed, and scoring/repair rounds driven by the relay delivery stream.",
      "tags": ["concord", "negotiation", "consensus"],
      "inputModes": ["application/vnd.ioc-project.concord+xml"],
      "outputModes": ["application/vnd.ioc-project.concord+xml"]
    }
  ]
}
```

---

## Session model

A CONCORD run maps onto A2A as **multi-turn exchanges within a task**: each CONCORD message is an
additional turn on an existing task, not a new task per message. The initiator sends each CONCORD
phase message via `SendMessage` and the participant replies in a `TaskStatusUpdate` with
`state: INPUT_REQUIRED` and the CONCORD reply in the status `message`.

### Task and context reuse

The initiator may already have an active A2A task with each participant — for example, the work
that triggered the CONCORD run. In that case:

- The initiator **SHOULD** send CONCORD messages as additional turns on the existing task by setting
  `taskId` (and `contextId` if known) on the `SendMessage` request.
- The initiator **MUST NOT** create a new task when an active task with that participant already exists
  for the same work.

When no existing task exists with a participant, the initiator creates one per participant with the
first `SendMessage` (omitting `taskId`). The server assigns a `taskId` and `contextId`; the initiator
MUST record both per participant and reuse them in all subsequent turns.

!!! note "contextId is server-assigned"
    `contextId` is assigned by the participant's A2A server, not by the initiator. The initiator
    learns it from the first task response and tracks it per participant. The CONCORD session id
    (the `<state_id>` carried inside every CONCORD XML message) is a separate identifier; it is NOT
    used as the A2A `contextId`.

---

## Message flow

The sequence below covers a two-participant run (security + cost) from ANCHOR through one repair round.
Security is the bottleneck in this example; the actual bottleneck is determined algorithmically.

After the SEED phase the initiator sends only deliveries — relay wrappers carrying the other
participants' replies. There are no explicit score requests, repair requests, or commit messages:
each participant self-determines what is due from the relay stream.

```mermaid
sequenceDiagram
    participant I as Initiator
    participant S as Security agent
    participant C as Cost agent

    Note over I,C: ANCHOR phase (broadcast)
    I->>S: SendMessage(parts=[concord-anchor])
    I->>C: SendMessage(parts=[concord-anchor])
    S-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-anchor-reply])
    C-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-anchor-reply])

    Note over I,C: SEED phase (broadcast)
    I->>S: SendMessage(taskId=S-task, parts=[concord-propose-seed])
    I->>C: SendMessage(taskId=C-task, parts=[concord-propose-seed])
    S-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-candidate-reply])
    C-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-candidate-reply])

    Note over I,C: Relay deliveries — participants self-trigger scoring
    I->>S: SendMessage(taskId=S-task, parts=[concord-delivery + C's candidate-reply])
    I->>C: SendMessage(taskId=C-task, parts=[concord-delivery + S's candidate-reply])
    S-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-score-reply])
    C-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-score-reply])

    Note over I,C: Relay score replies — Security self-identifies as bottleneck and repairs
    I->>S: SendMessage(taskId=S-task, parts=[concord-delivery + C's score-reply])
    I->>C: SendMessage(taskId=C-task, parts=[concord-delivery + S's score-reply])
    S-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-candidate-reply + concord-score-reply])
    C-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-ack])

    Note over I,C: Relay repair — others score; run completes from participants' own picture
    I->>S: SendMessage(taskId=S-task, parts=[concord-delivery + S's repair])
    I->>C: SendMessage(taskId=C-task, parts=[concord-delivery + S's repair])
    S-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-ack])
    C-->>I: TaskStatusUpdate(state=INPUT_REQUIRED, message.parts=[concord-score-reply])
```

### Delivery Part format

After SEED, each `SendMessage` carries a single Part whose content is a `<concord-delivery>`
header followed by any relay documents, all as one text value. The delivery header is a
self-closing element; the relay documents follow as peer XML elements in the same Part.

```json
{
  "text": "<concord-delivery seq=\"3\" state=\"RUNID\" check=\"a1b2c3d4\" />\n\n<concord-score-reply agent=\"cost\" state=\"RUNID\" ack=\"3\">\n  <score candidate_id=\"seed-cost\"><raw>72.0</raw><reasoning>…</reasoning></score>\n</concord-score-reply>",
  "mediaType": "application/vnd.ioc-project.concord+xml"
}
```

The participant passes the entire Part content to its CONCORD implementation verbatim — the
delivery header and all enclosed documents together, exactly as received.

### Phase-by-phase mapping

| CONCORD phase | A2A operation | Direction | `state` in response |
|---|---|---|---|
| ANCHOR | `SendMessage` (new or existing task) | Initiator → each participant | — |
| ANCHOR reply | `TaskStatusUpdate` | Participant → initiator | `INPUT_REQUIRED` |
| SEED | `SendMessage` (same task) | Initiator → each participant | — |
| SEED reply | `TaskStatusUpdate` | Participant → initiator | `INPUT_REQUIRED` |
| DELIVERY (relay) | `SendMessage` (same task) | Initiator → each participant | — |
| Score reply (self-triggered) | `TaskStatusUpdate` | Participant → initiator | `INPUT_REQUIRED` |
| Repair candidate + own score (self-triggered, bottleneck only) | `TaskStatusUpdate` | Bottleneck → initiator | `INPUT_REQUIRED` |
| ACK (nothing due on this delivery) | `TaskStatusUpdate` | Participant → initiator | `INPUT_REQUIRED` |

After SEED, the initiator only ever sends deliveries. It never explicitly requests a score, a
repair, or a commit. Participants self-determine their obligations from the relay stream; the run
ends when every participant's picture of the run reaches a terminal state.

---

## Conformance

The following requirements apply to any implementation of this binding.

### Initiator

- MUST send each CONCORD message as a `SendMessage` with exactly one Part carrying
  `mediaType: "application/vnd.ioc-project.concord+xml"`.
- MUST reuse an existing `taskId` and `contextId` when an active task already exists
  for a participant in the same work context.
- MUST record the server-assigned `taskId` and `contextId` from the first task response
  and supply them on all subsequent `SendMessage` calls to that participant in the run.
- After SEED, MUST send only deliveries. MUST NOT send explicit score requests, repair requests,
  or commit messages.
- MUST send each participant's delivery as a single Part containing the delivery header and any
  relay documents verbatim.
- A run is complete when the initiator's CONCORD state reaches a terminal outcome. The initiator
  reports that result and stops sending messages to participants.
- MUST NOT treat a `TaskStatus.message` that contains no `application/vnd.ioc-project.concord+xml`
  Part as a CONCORD reply. Prose is not a reply; the initiator must re-send the delivery.

### Participant

- MUST advertise the `concord-participant` skill in its AgentCard as specified above when it
  implements the participant role.
- MUST accept `SendMessage` requests whose Parts include `mediaType: "application/vnd.ioc-project.concord+xml"`.
- MUST respond to each CONCORD message (ANCHOR, SEED, and all deliveries) with a
  `TaskStatusUpdate(state=INPUT_REQUIRED)` whose `message.parts` contain the CONCORD reply XML
  with `mediaType: "application/vnd.ioc-project.concord+xml"`.
- MUST respond to every delivery, even when no substantive reply is due. When a delivery
  requires no action, the participant MUST send a `<concord-ack>` as a
  `TaskStatusUpdate(state=INPUT_REQUIRED)`.
- **MUST NOT transition an A2A task to a terminal state (`COMPLETED`, `FAILED`, `CANCELED`) while
  a CONCORD session is open on that task.** A CONCORD session is open from the receipt of a
  `concord-anchor` message until the participant's CONCORD state reaches a terminal outcome.
  There is no `concord-commit` message in the broadcast model.
- MAY transition the task to any appropriate state once the participant's CONCORD state is
  terminal; this binding does not mandate `COMPLETED`.

### Both roles

- The `text` (or `raw`) content of every `application/vnd.ioc-project.concord+xml` Part MUST be
  verbatim CONCORD XML as defined in the [CONCORD Specification](specification.md).
  The XML MUST NOT be paraphrased, reformatted, or reconstructed by hand.

---

## Related

- [Specification](specification.md) — formal CONCORD protocol definition
- [Configuration](configuration.md) — satisfaction threshold and round budget
- [Tutorial: AgentBridge](../../tutorials/agentbridge/index.md) — running CONCORD over A2A via AgentBridge and SLIM
