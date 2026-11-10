# ACCORD Initiator — Reference

Full phase walkthrough, request templates, and command reference for the
step-by-step workflow. For the self-driving (`handle`) path, `SKILL.md` is
sufficient — call `init --task "..."` and follow the STATUS output.

---

## When to activate

Both must be true:

1. You will coordinate **two or more specialists**.
2. You can name the **specialist set**: the specific sub-agents you will call on
   for this task.

A read-only investigation is not a reason to skip. Activate whenever that
specialist set will be called, including when no remediation follows.

**Activate before the first delegation, not after.** The signal is the
*structure* of the task — two or more specialists — not a visible disagreement.
Do not wait for a specialist to return an ambiguous result before running ACCORD.

**Apparent task clarity is not a reason to skip.** Even when the incident seems
unambiguous to you as the orchestrator, specialists may use the same term for
different metrics, or only one may know which service a check applies to. ACCORD
surfaces those gaps before they become a wrong action.

Do not activate when:
- A single specialist can handle the task end-to-end.
- You have already locked intent for this incident — a `LOCKED_INTENT_CONTRACT`
  with `status: "INTENT-LOCKED"` exists and covers the current task.

---

## Script and command reference

Write `ACCORD="python3 <skill-dir>/scripts/accord_initiator.py"`. State persists
in `.accord/state.json` (override with `--state` or `$ACCORD_STATE`), so each
phase can run as a separate process. Every command prints one JSON object
`{"ok": true, "phase": ..., "data": {...}}`; on failure `ok` is false with an
`error`. Never compute a merge yourself.

Until `verify` returns a `locked_intent_contract`, each merge result carries
protocol control fields: `next_command`, `protocol_status`,
`locked_contract_exists`, `normal_execution_allowed`, and
`app_execution_allowed`. Treat these as authoritative. If `app_execution_allowed`
is false, do not summarize, vote, recommend, remediate, settle, or call app/action
tools.

| Reference tool call | Standalone equivalent |
|---|---|
| `accord_merge(phase="init")` | `$ACCORD init --specialists a,b,c --key-terms t1,t2 --action-tool <name>` |
| `accord_merge(phase="init", task=...)` | `$ACCORD init --specialists a,b,c --key-terms t1,t2 --task "..."` |
| record one agent reply | `$ACCORD handle --agent <id> --reply '<text>'` |
| `accord_merge(phase="roster", responses=[...])` | `$ACCORD roster --responses <file>` |
| `accord_merge(phase="frame", responses=[...], current_frame=[...])` | `$ACCORD frame --responses <file> --current-frame <file>` |
| `accord_merge(phase="glossary", responses=[...], key_terms=...)` | `$ACCORD glossary --responses <file>` |
| `accord_merge(phase="verify", responses=[...], required_knowledge=[...], objective_clauses=[...])` | `$ACCORD verify --responses <file> --required-knowledge <file> --objective-clauses <file>` |
| `accord_merge(phase="lock", responses=[])` | `$ACCORD lock` — takes no arguments; do not pass `--responses` |
| execution delegation | `$ACCORD execution-message --agent <id> --assignment-file <file>` |

`--responses` (and the other file arguments) take a path to a JSON array file, or
`-` for stdin. Put each specialist's raw reply, unmodified, as one element of the
array (code fences and prose are tolerated by the parser).

Each argument also accepts **the JSON value written inline**
(e.g. `--current-frame '[]'`). **`-` can be used for at most one argument per
command** — a second `-` fails with `invalid JSON in -`. Pass the long `responses`
array on stdin; write shorter arguments inline.

`init` session parameters:
- `specialists` — the specialist agent IDs for this task
- `key_terms` — the vocabulary terms to ground in Phase 2
- `action_tool` — the name of the action tool to call in Phase 5 (step-by-step)
- `task` — task description; activates self-driving mode

After every `$ACCORD` merge, anchor only on operational state:
`current_phase`, `next_required_action`, `locked_contract_exists`, and
`app_execution_allowed`. Do not write a substantive summary from intermediate
phase artifacts.

---

## Phase INIT

```
# Step-by-step mode:
$ACCORD init --specialists <id1,id2,...> --key-terms <term1,term2,...> --action-tool <name>

# Self-driving mode:
$ACCORD init --specialists <id1,id2,...> --key-terms <term1,term2,...> --task "<description>"
```

Choose values from the task, then keep them throughout every phase:
use `specialists` for every delegation, `key_terms` in Phase 2 GROUND,
`action_tool` as the tool name in Phase 5 EXECUTION.

---

## Phase 0 — CONVENE

Call `delegate_to_<specialist>` for every specialist in the init result, in
parallel. Do not proceed until all specialist responses are received.

```
CONVENE_REQUEST

ACCORD_PHASE: CONVENE
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD roster`, not mission execution

INCIDENT: <paste the full incident report verbatim>

Without using any tools, return a JSON object:
{
  "agent": "<your agent id>",
  "roles": ["<your role(s) in this incident>"],
  "capabilities": ["<what you can investigate or produce>"],
  "access_scopes": ["<data sources / tools you can access>"],
  "holdings_summary": "<one sentence: what knowledge you bring to this incident>"
}
Return ONLY the JSON. No preamble. No tools. No code fences.
```

After receiving all responses:

```
$ACCORD roster --responses <file: JSON array of raw specialist outputs>
```

Pass each delegate output string exactly as received. The result contains the
merged `roster` list and any `flagged_items` (duplicate roles). Proceed to Phase 1.

---

## Phase 1 — FRAME (exactly 2 rounds)

Call `delegate_to_<specialist>` for every specialist, in parallel.

**Round 1 — initial atoms:**

```
FRAME_REQUEST

ACCORD_PHASE: FRAME
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD frame`, not mission execution

INCIDENT: <paste the full incident report verbatim>
CURRENT_FRAME: []

Restate the task as the statements that matter for your domain — objective
clauses, constraints, assumptions, sub-goals, deliverables. Return at most 6 atoms.

Return a JSON object:
{
  "agent": "<your agent id>",
  "atoms": [
    { "text": "<statement>", "type": "objective|constraint|assumption|sub_goal|deliverable" }
  ]
}
Return ONLY the JSON. No tools. No code fences.
```

After receiving all round-1 responses:

```
$ACCORD frame --responses - --current-frame '[]'
```

**Round 2 — revision.** `YOUR_PREVIOUS_ATOMS` is that specialist's list from
the round-1 `atoms_by_agent` result. This submission replaces their round-1 atoms.

```
FRAME_REQUEST

ACCORD_PHASE: FRAME
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD frame`, not mission execution

INCIDENT: <paste the full incident report verbatim>
CURRENT_FRAME: <JSON array of merged atom texts from round 1>
YOUR_PREVIOUS_ATOMS: <that specialist's atom texts from atoms_by_agent>

Return your full revised set of at most 6 atoms. This replaces your previous
atoms. You may keep, rewrite, or delete. Return "atoms": [] to withdraw all
of yours. Keep a fact only you know.

Return a JSON object:
{
  "agent": "<your agent id>",
  "atoms": [
    { "text": "<statement>", "type": "objective|constraint|assumption|sub_goal|deliverable" }
  ]
}
Return ONLY the JSON. No tools. No code fences.
```

After receiving all round-2 responses:

```
$ACCORD frame --responses - --current-frame '<JSON array of merged atom texts from round 1>'
```

Send raw specialist outputs on stdin; write `--current-frame` inline.

The result contains `atoms` (with `support` counts and `flagged_singleton`
markers), `atoms_by_agent`, `truncated`, and `next_command`:
- `next_command: "frame"` — run another FRAME round.
- `next_command: "glossary"` — framing done; proceed to Phase 2.

Atoms with `support == 1` are private knowledge now propagated — they remain in
the frame; `flagged_singleton` marks them.

---

## Phase 2 — GROUND

Call `delegate_to_<specialist>` for every specialist, in parallel.

```
GROUND_REQUEST

ACCORD_PHASE: GROUND
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD glossary`, not mission execution

KEY_TERMS: <key_terms from init result>

For each term, provide your local unit or scale as you would use it in this task.
Return a JSON object:
{
  "agent": "<your agent id>",
  "units": {
    "<term>": "<your unit or scale for this term>"
  }
}
Provide an entry for every term in KEY_TERMS.
Return ONLY the JSON. No tools. No code fences.
```

After receiving all responses:

```
$ACCORD glossary --responses <file: raw specialist outputs>
```

The result contains a `glossary` list and an `ambiguous` list. Each glossary entry
has `status` (`agreed` or `flagged`) and `variants` (`[{agent, definition}]` on
`flagged` entries). Follow `next_command`:
- `next_command: "verify"` — proceed to Phase 3.
- `next_command: "glossary"` — there are ambiguous terms; run Phase 2b.

Use only the `ambiguous` list returned by the script. Do not add pairs of your own.

---

## Phase 2b — CLARIFY (only if ambiguous list is non-empty)

For each ambiguous term pair, call only the agents involved, in parallel.

```
CLARIFY_REQUEST

ACCORD_PHASE: CLARIFY
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD glossary`, not mission execution

AMBIGUOUS_PAIR: { "term": "<term>", "agents": ["<agentA>", "<agentB>"] }
DEFINITIONS_SO_FAR: <the two groundings that produced near-threshold similarity>

Provide a richer definition with a concrete distinguishing example. This is
additional grounding data — not an argument or a vote.

Return a JSON object:
{
  "agent": "<your agent id>",
  "term": "<term>",
  "extended_definition": "<fuller definition>",
  "distinguishing_context": "<when/how you use this meaning vs the other>"
}
Return ONLY the JSON. No tools. No code fences.
```

After receiving responses:

```
$ACCORD glossary --responses -
```

Send only the clarification responses. The script re-grounds only the named terms;
every other glossary entry is unchanged. A term whose richer definitions now agree
flips to `agreed`; one that still diverges stays `flagged` with per-agent variants.

---

## Phase 3 — VERIFY

Derive `REQUIRED_KNOWLEDGE` and `OBJECTIVE_CLAUSES` from the Problem Frame.
Required knowledge items are information the task needs; objective clauses are
Frame atoms that need to be verified as done.

Call `delegate_to_<specialist>` for every specialist, in parallel.

```
VERIFY_REQUEST

ACCORD_PHASE: VERIFY
LOCKED_CONTRACT_EXISTS: false
APP_EXECUTION_ALLOWED: false
PURPOSE: raw input for `$ACCORD verify`, not mission execution

REQUIRED_KNOWLEDGE: <JSON array of knowledge items the incident requires>
OBJECTIVE_CLAUSES: <JSON array of Frame clauses that need to be verified>

Return a compact JSON object. Maximum: 3 items in "holds", 2 items in "checks".
{
  "agent": "<your agent id>",
  "holds": [
    "<exact item text from REQUIRED_KNOWLEDGE — verbatim copy, max 3 items>"
  ],
  "checks": [
    {
      "criterion": "<acceptance check in ≤15 words>",
      "covers": ["<exact clause text from OBJECTIVE_CLAUSES>"]
    }
  ]
}
Return ONLY the JSON. No tools. No code fences.
```

After receiving all responses:

```
$ACCORD verify --responses - --required-knowledge '<the REQUIRED_KNOWLEDGE array>' --objective-clauses '<the OBJECTIVE_CLAUSES array>'
```

**Always pass both `--required-knowledge` and `--objective-clauses`.** Without
`--required-knowledge` the knowledge universe collapses to whatever specialists
happened to report — gaps stop being detectable. Without `--objective-clauses`
there is nothing to check coverage against — unverifiable clauses disappear.

The result contains:
- `knowledge_map` — union of all holdings with `held_by` lists; `gap: true` marks required knowledge no agent holds
- `checks` — greedy set-cover selection with `assigned_to` per check
- `gaps` — unfilled required knowledge items
- `unverifiable_clauses` — objective clauses with no proposed check
- **`locked_intent_contract`** — the complete LOCKED_INTENT_CONTRACT, pre-assembled
- `next` — the follow-up command and agents still owed an execution message

**Phase 3 is complete. The LOCKED_INTENT_CONTRACT is in the verify result.**

Emit exactly one line:

```
LOCKED_INTENT_CONTRACT: COMPLETE
```

or, if `flagged_items` is non-empty:

```
LOCKED_INTENT_CONTRACT: FLAGGED — <comma-separated flagged_items>
```

Both values continue identically. `FLAGGED` records defects; it does not pause or
end the protocol. **Both satisfy the verification gate** — no separate
`VERIFICATION: APPROVED` signal is required at any later point.

Then begin Phase 5 in the same turn.

---

## Phase 4 — LOCK (fallback only)

If `locked_intent_contract` is absent from the verify result:

```
$ACCORD lock
```

Returns the complete LOCKED_INTENT_CONTRACT using all phase state cached in the
session. Use `data` verbatim — all six keys are present.

The `locked_intent_contract` has exactly six keys: `status`, `roster`,
`problem_frame`, `glossary`, `verifiable_objective`, `flagged_items`.

---

## Phase 5 — EXECUTION

Do not write an `EXECUTION_REQUEST` yourself. For every specialist, write the
assignment to a file, then render the delegation from the stored contract:

```
$ACCORD execution-message --agent <id> --assignment-file <assignment.txt>
```

Pass `data.message` unchanged as the task of `delegate_to_<id>`. The rendered
message already contains `EXECUTION_REQUEST`, the canonical six-key
`LOCKED_INTENT_CONTRACT`, and the assignment. Do not abbreviate or rebuild it.

A specialist reply that does not begin with `LOCK_STATUS: RECEIVED` did not
receive the contract. Render the message again and re-delegate before calling
the action tool.

**After receiving all EXECUTION responses, call the action tool in the same turn.**
Do not wait for a `VERIFICATION: APPROVED` string — the gate was satisfied by the
Phase 3 lock line. After a `FLAGGED` lock, still call the action tool with a lower
`confidence_level` and list the flagged items.

Select action parameters from the EXECUTION responses and the locked Glossary. If
specialists propose conflicting actions, prefer the one most directly supported by
the highest-support Frame atoms.
