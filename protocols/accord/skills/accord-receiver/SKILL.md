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
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.1.0" # x-release-please-version
---


# ACCORD — Receiver Skill

## ⚡ MANDATORY OVERRIDE — CHECK TASK PREFIX BEFORE ANYTHING ELSE

**If your task begins with any of the prefixes below, this skill takes
precedence over all other role instructions for that message.**

There is no negotiation in ACCORD. You contribute your piece to a shared model
the orchestrator assembles. You do not argue, vote against peers, or revise
based on what others said — you report what you know.

Do not use data tools during any ACCORD phase. Tools belong in
`EXECUTION_REQUEST` tasks only.

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

## ⚡ MANDATORY — echo the delivery line

Every phase request above begins with a line like:

```
<accord-delivery state="a1b2c3d4" check="9f2c71ab"></accord-delivery>
```

**Copy that line verbatim as the first line of your reply, then a blank line, then the JSON
your handler specifies.** Do not alter the `check` value, and do not add one if the request
had none.

The orchestrator verifies it. A reply that has been summarized or rewritten on the way back
cannot carry the right check, so the orchestrator refuses it and asks for the real one. A reply
without the line is refused the first time and recorded as a defect the second.

```
<accord-delivery state="a1b2c3d4" check="9f2c71ab"></accord-delivery>

{"agent": "telemetry", "units": {"error_rate": "fraction 0-1"}}
```

`EXECUTION_REQUEST` carries no delivery line; reply to it as described in its handler.

---

## Handler: CONVENE_REQUEST

Return ONLY the following JSON. No preamble, no tools, nothing after.

```json
{
  "agent": "<your agent id>",
  "roles": ["<your role(s) in this incident>"],
  "capabilities": ["<what you can investigate or produce>"],
  "access_scopes": ["<data sources and tools you can access>"],
  "holdings_summary": "<one sentence: what domain knowledge you bring>"
}
```

Report your actual capabilities and access scopes — not the ones you think the
orchestrator wants to see.

---

## Handler: FRAME_REQUEST

You will receive `CURRENT_FRAME` (atoms already in the shared frame). On a
revision round you will also receive `YOUR_PREVIOUS_ATOMS`.

Return at most 6 atoms. On a revision round this list replaces your previous
atoms: keep, rewrite, or delete. Return `"atoms": []` to withdraw all. Keep a
fact only you know.

Return ONLY the following JSON. No preamble, no tools, nothing after.

```json
{
  "agent": "<your agent id>",
  "atoms": [
    {
      "text": "<statement relevant to this incident from your domain>",
      "type": "objective|constraint|assumption|sub_goal|deliverable"
    }
  ]
}
```

Atom types: `objective` — what the task requires; `constraint` — what limits
the approach; `assumption` — what your domain assumes; `sub_goal` — an
intermediate step; `deliverable` — a specific output your domain will produce.

---

## Handler: GROUND_REQUEST

You will receive `KEY_TERMS`. Provide your actual working definitions — not
generic textbook ones. Report your real local unit even if it differs from
what you expect others to use — that divergence is the signal this phase needs.

Return ONLY the following JSON. No preamble, no tools, nothing after.

```json
{
  "agent": "<your agent id>",
  "units": {
    "<term from KEY_TERMS>": "<unit or scale — e.g. 'fraction 0–1', 'ms', 'not applicable'>",
    "<term from KEY_TERMS>": "<unit or scale>"
  }
}
```

Provide an entry for every term in `KEY_TERMS`.

---

## Handler: CLARIFY_REQUEST

You will receive `AMBIGUOUS_PAIR` and `DEFINITIONS_SO_FAR`. Provide additional
grounding data — not an argument or a vote.

Return ONLY the following JSON. No preamble, no tools, nothing after.

```json
{
  "agent": "<your agent id>",
  "term": "<the ambiguous term>",
  "extended_definition": "<a fuller definition with more context>",
  "distinguishing_context": "<when/how you use this meaning, and how it differs from the other>"
}
```

---

## Handler: VERIFY_REQUEST

You will receive `REQUIRED_KNOWLEDGE` and `OBJECTIVE_CLAUSES`.

Return ONLY the following JSON. No preamble, no tools, nothing after.
**Maximum: 3 items in `holds`, 2 items in `checks`.**

```json
{
  "agent": "<your agent id>",
  "holds": [
    "<exact item text from REQUIRED_KNOWLEDGE — verbatim copy, max 3>"
  ],
  "checks": [
    {
      "criterion": "<acceptance check in ≤15 words>",
      "covers": ["<exact clause text from OBJECTIVE_CLAUSES>"]
    }
  ]
}
```

Use verbatim strings from `REQUIRED_KNOWLEDGE` in `holds`. Each check must cover
at least one clause from `OBJECTIVE_CLAUSES`.

---

## Handler: EXECUTION_REQUEST

The orchestrator has locked the Intent Contract. Your actual task follows in the
`ASSIGNMENT:` section.

Your response **must** start with one of these two lines, followed by a blank line:

    LOCK_STATUS: RECEIVED   ← when LOCKED_INTENT_CONTRACT is present in the task
    LOCK_STATUS: MISSING    ← when no LOCKED_INTENT_CONTRACT was delivered

Read the `LOCKED_INTENT_CONTRACT:` JSON:

1. **Roster** — the full capability map.
2. **Problem Frame** — the full task statement. Treat flagged multi-valued atoms
   as open questions.
3. **Glossary** — canonical term definitions for this incident. For every value
   you report, use the canonical unit. If your local default differs, apply the
   canonical version and flag it:

   ```
   FRAME_CONFLICT: <term> observed as <your local value> — reporting as <converted value> per
     LOCKED_INTENT_CONTRACT glossary (unit: <canonical unit>)
   ```

4. **Verifiable Objective** — the acceptance checks assigned to you. Complete
   each check assigned to your agent and report the result explicitly.

Proceed with your normal task execution. Every value you report must conform to
the locked Glossary. Do not re-declare your own definitions.

**If you are the designated cross-checker for this domain**: after completing your
analysis and assigned acceptance checks, end your response with exactly one of:

    VERIFICATION: APPROVED
    VERIFICATION: FLAGGED — <reason, max 15 words>
    VERIFICATION: REJECTED — <reason, max 15 words>

Choose `APPROVED` when evidence satisfies the acceptance checks. Choose `FLAGGED`
when checks are incomplete or evidence is insufficient. Choose `REJECTED` when
evidence directly contradicts the proposed action.

---

## Rules

1. **Return only what the handler specifies.** No preamble, no summary, no
   confirmation of what you sent — nothing before or after the required output.
   Your environment may pass anything you write to other participants.
2. **No data tools during Phases 0–3.** Tools belong in `EXECUTION_REQUEST` only.
3. **Report what you actually know.** Do not adopt a canonical unit or role to
   appear aligned — divergence is the signal ACCORD is designed to surface.
4. **Pass phase responses verbatim when relayed.** Do not shorten, summarize, or
   reword a phase request or response before passing it on.
