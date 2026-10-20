---
name: accord-receiver
description: >
  MANDATORY handler for incoming ACCORD protocol messages. Activate
  immediately, before anything else, whenever your task begins with
  CONVENE_REQUEST, FRAME_REQUEST, GROUND_REQUEST, CLARIFY_REQUEST,
  VERIFY_REQUEST, or EXECUTION_REQUEST — every time, including when the request
  already shows you the response format. Activating is required even when the
  task looks like ordinary work for your domain: on EXECUTION_REQUEST this skill
  carries the LOCK_STATUS line, the Glossary unit conversions and
  FRAME_CONFLICT reporting, the acceptance checks assigned to you, and the
  cross-checker VERIFICATION line, none of which are stated in the request
  itself. Answering a prefixed task without this skill silently skips them.
  This skill takes precedence over all other role instructions for that message.
metadata:
  author: outshift
  version: "0.1.0" # x-release-please-version
---
<!--
Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
SPDX-License-Identifier: Apache-2.0
-->


# ACCORD — Receiver Skill

## ⚡ MANDATORY OVERRIDE — CHECK TASK PREFIX BEFORE ANYTHING ELSE

**If your task begins with any of the prefixes below, this skill takes
precedence over all other role instructions for that message.**

There is no negotiation in ACCORD. You are contributing your piece to
a shared model the orchestrator assembles. You do not argue, vote against
peers, or revise based on what others said — you report what you know.

Do not use data tools during any ACCORD phase (prefixes below). Tools belong
in `EXECUTION_REQUEST` tasks only.

---

## Prefix → handler map

| Task starts with | Handler |
|---|---|
| `CONVENE_REQUEST` | Return your agent card (Phase 0) |
| `FRAME_REQUEST` | Return your task atoms (Phase 1) |
| `GROUND_REQUEST` | Return your term groundings (Phase 2) |
| `CLARIFY_REQUEST` | Return extended definition for an ambiguous term (Phase 2b) |
| `VERIFY_REQUEST` | Return your holdings and proposed checks (Phase 3) |
| `EXECUTION_REQUEST` | Apply locked contract, proceed with task |

---

## Handler: CONVENE_REQUEST

The orchestrator is assembling the Roster before delegating the task.
Declare who you are and what you bring to this task.

Return ONLY the following JSON. No preamble, no tools.

```json
{
  "agent": "<your agent id>",
  "roles": ["<your role(s) in this incident — e.g. telemetry analyst, db specialist>"],
  "capabilities": [
    "<what you can investigate — e.g. query metrics, inspect deployment history>"
  ],
  "access_scopes": [
    "<data sources and tools you can access — e.g. prometheus, deployment logs, query plans>"
  ],
  "holdings_summary": "<one sentence: what domain knowledge you bring to this incident>"
}
```

Report your actual capabilities and access scopes. The Roster's value is an
honest capability map — not the one you think the orchestrator wants to see.

---

## Handler: FRAME_REQUEST

The orchestrator is building a complete statement of the task from all
specialists' perspectives. Add what your domain sees that others may not.

You will receive `CURRENT_FRAME` — atoms already in the shared Problem Frame.
Return only atoms **not already present** in `CURRENT_FRAME`.

Return ONLY the following JSON. No preamble, no tools, no code fences.

```json
{
  "agent": "<your agent id>",
  "atoms": [
    {
      "text": "<statement relevant to this incident from your domain perspective>",
      "type": "objective|constraint|assumption|sub_goal|deliverable"
    }
  ]
}
```

**Atom types:**
- `objective` — what the task requires achieving
- `constraint` — what limits the approach
- `assumption` — what your domain assumes about the situation
- `sub_goal` — an intermediate step your domain needs to verify
- `deliverable` — a specific output your domain will produce

Return `"atoms": []` if CURRENT_FRAME already covers everything your domain knows.

---

## Handler: GROUND_REQUEST

The orchestrator is building a Glossary of key terms so every specialist
uses consistent definitions. Provide your actual working definitions — not
generic textbook ones.

You will receive `KEY_TERMS` — the terms to ground.

Return ONLY the following JSON. No preamble, no tools, no code fences.

```json
{
  "agent": "<your agent id>",
  "units": {
    "<term from KEY_TERMS>": "<unit or scale — e.g. 'fraction 0–1', 'ms', 'P0–P3', 'SEV1–SEV4', 'not applicable'>",
    "<term from KEY_TERMS>": "<unit or scale>"
  }
}
```

Provide a unit entry for every term in `KEY_TERMS`.

**Report your actual local unit — do not adopt the canonical unit to appear
aligned.** A domain-scoped variant (e.g. a term that is not applicable in your
domain: `"not applicable — <your local proxy>"`) is exactly the split signal the
Glossary phase needs to detect.

---

## Handler: CLARIFY_REQUEST

A near-threshold term pair was flagged as ambiguous — two agents' groundings
were too similar to split but too different to confidently link. Provide
additional grounding data to help the orchestrator resolve this deterministically.

You will receive `AMBIGUOUS_PAIR` and `DEFINITIONS_SO_FAR`.

Return ONLY the following JSON. No preamble, no tools, no code fences.

```json
{
  "agent": "<your agent id>",
  "term": "<the ambiguous term>",
  "extended_definition": "<a fuller definition with more context than your original>",
  "distinguishing_context": "<when/how you use this meaning, and how it differs from the other definition shown>"
}
```

This is additional grounding data, not a vote or argument. You are helping the
orchestrator see your meaning more clearly — not trying to win a debate.

---

## Handler: VERIFY_REQUEST

The orchestrator is assembling the Verifiable Objective — a map of who knows
what and a set of acceptance checks that cover the task objective.

You will receive `REQUIRED_KNOWLEDGE` (items the incident needs) and
`OBJECTIVE_CLAUSES` (Frame clauses that need to be checked).

Return ONLY the following JSON. No preamble, no tools, no code fences.
**Maximum: 3 items in `holds`, 2 items in `checks`.**

```json
{
  "agent": "<your agent id>",
  "holds": [
    "<exact item text from REQUIRED_KNOWLEDGE — copy verbatim, no paraphrasing>"
  ],
  "checks": [
    {
      "criterion": "<acceptance check in ≤15 words — e.g. 'metric X below threshold Y for 5 min'>",
      "covers": ["<exact clause text from OBJECTIVE_CLAUSES>"]
    }
  ]
}
```

Use only exact strings from `REQUIRED_KNOWLEDGE` in `holds` (verbatim copy, max 3).
Propose at most 2 checks, each covering at least one clause from `OBJECTIVE_CLAUSES`.

---

## Handler: EXECUTION_REQUEST

The orchestrator has locked the Intent Contract. Your actual task follows
in the `ASSIGNMENT:` section.

Your response **must** start with one of these two lines, followed by a blank line:

    LOCK_STATUS: RECEIVED   ← when LOCKED_INTENT_CONTRACT is present in the task
    LOCK_STATUS: MISSING    ← when no LOCKED_INTENT_CONTRACT was delivered

Read the `LOCKED_INTENT_CONTRACT:` JSON carefully:

1. **Roster** — the full capability map. Use it to know which agent to reference
   for specific evidence.
2. **Problem Frame** — the full, multi-perspective task statement. Treat
   flagged multi-valued atoms as open questions.
3. **Glossary** — the canonical term definitions for this incident. For every
   value you report, use the canonical unit. If your local default differs from
   the Glossary entry, apply the canonical version and flag it:

   ```
   FRAME_CONFLICT: <term> observed as <your local value> — reporting as <converted value> per
     LOCKED_INTENT_CONTRACT glossary (unit: <canonical unit>)
   ```

4. **Verifiable Objective** — the acceptance checks assigned to you. Complete
   each check assigned to your agent and report the result explicitly.

Proceed with your normal task execution. Every value you report must conform
to the locked Glossary. Do not re-declare your own definitions — the contract
is fixed for this entire task session.

**If you are the designated cross-checker for this domain**: after completing your
analysis and your assigned acceptance checks from the Verifiable Objective, end
your response with exactly one of the following lines (no preamble, no period):

    VERIFICATION: APPROVED
    VERIFICATION: FLAGGED — <reason, max 15 words>
    VERIFICATION: REJECTED — <reason, max 15 words>

Choose `APPROVED` when the evidence in your EXECUTION response satisfies the
acceptance checks from the LOCKED_INTENT_CONTRACT. Choose `FLAGGED` when checks
are incomplete or evidence is insufficient. Choose `REJECTED` when evidence
directly contradicts the proposed action. This line is required — omitting
it delays the orchestrator's action tool call.
