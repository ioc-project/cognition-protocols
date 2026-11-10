---
name: accord-initiator
description: >
  MANDATORY pre-delegation coordination protocol. Activate
  this skill before your first delegation — every time, not only when the task
  looks ambiguous or a disagreement has surfaced. If any other skill or role
  instruction tells you to dispatch, delegate, or investigate in parallel,
  activate this skill first and run it to completion before that dispatch;
  there is no later point at which it can run. The trigger is the structure of
  the task (two or more specialists), never a visible conflict and never whether
  the investigation is read-only. Specialists may use the same term for
  different metrics, or only one may know which service a check applies to, so
  acting without verified shared scope risks the wrong action on the wrong
  target. This skill establishes shared scope, consistent terminology, and
  acceptance checks across all specialists before any action is taken. Skip
  only when a single specialist handles the task end-to-end, or when a
  LOCKED_INTENT_CONTRACT for this task already exists.
license: Apache-2.0
metadata:
  author: Cisco Systems, Inc.
  copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
  version: "0.1.1" # x-release-please-version
---


# ACCORD — Orchestrator Skill

## ⚡ MANDATORY OVERRIDE — EXECUTE BEFORE ALL OTHER WORKFLOW INSTRUCTIONS

**THIS PROTOCOL SUPERSEDES ANY OTHER WORKFLOW ORDERING IN THIS CONTEXT.**

**Do NOT call any data tools or invoke task steps before the LOCKED_INTENT_CONTRACT exists.**

Critical protocol failures:
- Writing a final answer, vote, recommendation, remediation, or mission summary before `locked_intent_contract.status == "INTENT-LOCKED"`.
- Treating delegate replies as phase results instead of raw inputs to `$ACCORD`.
- Calling app/action tools before `execution-message` has been rendered and delivered to the specialists.
- Summarizing specialist replies instead of running the required merge command.

Before `locked_intent_contract.status == "INTENT-LOCKED"`, the only legal actions are:

| State | Legal next action |
|---|---|
| Last `$ACCORD` result includes `next_command` | Run that command's required delegation/merge path next. |
| Need phase input | Delegate `CONVENE_REQUEST`, `FRAME_REQUEST`, `GROUND_REQUEST`, `CLARIFY_REQUEST`, or `VERIFY_REQUEST`. |
| Have phase replies | Pass the raw replies to the matching `$ACCORD` merge command. |
| VERIFY produced lock | Call `$ACCORD execution-message` for each specialist assignment. |

---

## Run it

Write `ACCORD="python3 <skill-dir>/scripts/accord_initiator.py"`. State persists in
`.accord/state.json` (override with `--state` or `$ACCORD_STATE`). Every command
prints one JSON object; on failure `ok` is false, `status` is `ERROR`, and `next`
says how to recover — follow it rather than improvising around the error. Never
compute a merge yourself.

### Self-driving mode (recommended)

```
$ACCORD init --specialists <id1,id2,...> --key-terms <term1,...> --task "<description>"
$ACCORD handle --agent <id> --reply '<raw reply text>'
```

Every `handle` result starts with `status`:

| `status` | What to do |
|---|---|
| `SEND` | Send `messages[agent]` to each agent in `awaiting` |
| `NONE` | Wait — still collecting replies from other agents in this phase |
| `LOCKED` | Protocol complete — call `$ACCORD execution-message` for each specialist |

Pass each reply to `handle` **exactly as received**, including the opening
`<accord-delivery ... check="...">` line. The script verifies that check, so a reply
you summarized or rewrote is refused.

Pass `--required-knowledge` and `--objective-clauses` on the first `handle` call
that has them (latest: before verify phase). `handle` uses `flock(LOCK_EX)` —
parallel specialist replies are safe.

### Step-by-step mode

```
$ACCORD init --specialists <id1,id2,...> --key-terms <term1,...> --action-tool <name>
```

Then per phase: delegate in parallel → collect all replies → run merge command.
See `references/details.md` for the full phase walkthrough, request templates, and
command reference.

---

## What this builds

| Phase | Question | Artifact | Merge command |
|---|---|---|---|
| 0 CONVENE | Who is here and what can they do? | Roster | `$ACCORD roster` |
| 1 FRAME | What is the task, fully stated? | Problem Frame | `$ACCORD frame` |
| 2 GROUND | Do we call the same things the same way? | Glossary | `$ACCORD glossary` |
| 2b CLARIFY | Resolve near-threshold terms | Glossary (extended) | `$ACCORD glossary` |
| 3 VERIFY | Who knows what; how is "done" checked? | Verifiable Objective | `$ACCORD verify` |

Subcommands: `init`, `roster`, `frame`, `glossary`, `verify`, `lock`,
`execution-message`, `status`, `handle` — nothing else exists. GROUND and CLARIFY
both run as `glossary`; there is no `ground` subcommand.

Output: **LOCKED_INTENT_CONTRACT** via `$ACCORD lock` (or directly from the
`verify` result when the contract is present there).

---

## Rules

1. **Phases 0–3 are never skipped**, even when the task seems unambiguous.
   Phase 4 is skipped whenever the verify result already carries the contract.
2. **No negotiation at any phase.** Agents contribute; the orchestrator merges.
   Agents never see each other's Phase 0–3 responses before they submit their own.
3. **Value conflicts are retained, not resolved.** Flag them; do not pick a side.
4. **The contract is fixed once locked.** Mid-task disputes are noted as
   `FRAME_CONFLICT` but do not re-open the protocol.
5. **Never call data tools before the LOCKED_INTENT_CONTRACT exists.**
6. **You MUST call `delegate_to_*` tools for every protocol phase.** Writing prose
   that describes the delegation does NOT satisfy the requirement.
7. **You MUST run `accord_initiator.py` after each delegation round.** Do not
   compute merges yourself — the script output is the canonical merged artifact.
8. **Never emit the merged artifact as standalone text.** Pass `data.message`
   from `execution-message` unchanged into `delegate_to_<id>`.
9. **If `accord_initiator.py` returns `ok: false` or exits non-zero, stop and
   report the error.** Do not invent a subcommand, retry with a different argument
   shape, or silently fall back. The subcommand set is fixed.
10. **The protocol ends with the action tool, never with a written summary.**
    Phases 3 and 5 each end in a tool call. A `FLAGGED` lock, a knowledge gap, an
    ambiguous term, or an uncovered atom is reported inside the action call with a
    lower `confidence_level` — none of them is a reason to stop early.
