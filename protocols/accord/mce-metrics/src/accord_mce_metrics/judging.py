"""Judge prompts, redaction and evidence rules, copied unchanged from the standalone eval.

Source: ``accord-benefit/accord_benefit_eval.py``. Kept here: the handling
vocabulary, the blinding redactions, transcript rendering, the glossary-uptake
judge prompt and JSON parsing. Left behind: the HTTP judge client (MCE's
``call_llm`` replaces it, so judge cost is tracked by MCE), goal success (not an
ACCORD metric), the paired statistics and the CLI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_TRANSCRIPT_CHARS = 60_000
MAX_MESSAGE_CHARS = 1_500
# How an agent handled a term that is known to be defined differently across the
# roster. Everything above "unqualified" counts as uptake: the agent showed it
# knew the term was contested. Divergence itself is not a defect, so there is no
# category for it here.
HANDLING_LEVELS = (
    "explicit_definition",
    "sense_qualified",
    "cross_agent_translation",
    "ambiguity_acknowledged",
    "unqualified",
    "absent",
)
UPTAKE_LEVELS = frozenset(HANDLING_LEVELS[:4])

# Anything that identifies the protocol arm to the judge.
REDACTIONS = (
    (re.compile(r"\bL9[\s\-_]*ACCORD\b", re.I), "[PROTOCOL]"),
    (re.compile(r"\bACCORD\b"), "[PROTOCOL]"),
    (re.compile(r"\bCONCORD\b", re.I), "[PROTOCOL]"),
    (re.compile(r"\bINTENT[_\s]CONTRACT\b", re.I), "[ARTIFACT]"),
    (re.compile(r"\bLOCKED[_\s]SEMANTIC[_\s]FRAME\b", re.I), "[ARTIFACT]"),
    (re.compile(r"\bLOCKED[_\s]INTENT[_\s]CONTRACT\b", re.I), "[ARTIFACT]"),
    (re.compile(r"\bALIGNMENT_(?:REQUEST|REVIEW)\b", re.I), "[MESSAGE]"),
    (re.compile(r"\bEXECUTION_REQUEST\b", re.I), "[MESSAGE]"),
    (re.compile(r"\bINTENT-LOCKED\b", re.I), "[STATUS]"),
    (re.compile(r"\bcanonical_definitions\b", re.I), "shared_definitions"),
    (re.compile(r"\bsemantic (?:frame|contract|alignment)\b", re.I), "shared understanding"),
)


# --------------------------------------------------------------------------
# run records
# --------------------------------------------------------------------------


@dataclass
class Message:
    speaker: str
    recipient: str
    text: str

    def render(self) -> str:
        head = self.speaker + (f" -> {self.recipient}" if self.recipient else "")
        return f"[{head}] {self.text}"


@dataclass
class RunRecord:
    """One execution of one mission, stripped of domain specifics."""

    run_id: str
    scenario_id: str
    arm: str
    mission: str
    transcript: list[Message] = field(default_factory=list)
    terms_at_issue: list[str] = field(default_factory=list)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def render_transcript(run: RunRecord, budget: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Render the transcript blind, trimming the middle if it is over budget."""
    lines = [redact(m.render()[:MAX_MESSAGE_CHARS]) for m in run.transcript]
    if not lines:
        return "(no inter-agent messages recorded)"
    body = "\n".join(lines)
    if len(body) <= budget:
        return body
    keep = budget // 2
    head, tail = body[:keep], body[-keep:]
    dropped = len(body) - 2 * keep
    return f"{head}\n\n... [{dropped} characters elided from the middle] ...\n\n{tail}"


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are a careful evaluator of multi-agent systems. You grade only what the \
evidence supports.

Rules that override any instinct to be generous:
- Judge substance, never style. Length, formatting, confidence and fluency are \
not evidence of quality. A short correct answer beats a long wrong one.
- Never reward an agent for restating the task or for describing its own process.
- If the material does not let you decide, say so using the provided escape \
value. Guessing is worse than abstaining.
- Quote the specific text you relied on. A claim you cannot quote is one you \
should not make.
- Return only the JSON object requested, with no surrounding prose or code fences."""


GLOSSARY_UPTAKE_PROMPT = """\
Below is a transcript of messages exchanged between agents collaborating on one \
mission, and a list of terms that different agents in this system are known to \
define differently.

For each listed term, determine how the agents handled it when they used it.

Read this carefully, because it is easy to grade the wrong thing. You are not \
assessing whether the agents agreed on what the terms mean, and you are not \
looking for mistakes. Agents holding different definitions is expected here and \
is not a fault. The only thing you are detecting is whether an agent showed, in \
its own words, that it knew the term needed pinning down: did it say which sense \
it meant, or appeal to a common definition, or restate another agent's usage, or \
note the ambiguity before relying on the term?

Classify each term with exactly one of:
- explicit_definition: an agent states what the term means before relying on it.
- sense_qualified: an agent marks the sense in use - the unit, scale, scope, \
timeframe or audience - rather than using the bare word.
- cross_agent_translation: an agent converts or restates another agent's usage \
into the sense it is using, or vice versa.
- ambiguity_acknowledged: an agent notes the term is ambiguous or contested and \
proceeds deliberately, for instance by giving both readings or narrowing the claim.
- unqualified: the term is used plainly, with nothing indicating awareness that \
it carries more than one reading.
- absent: the term does not appear in the transcript.

Pick the strongest category the evidence supports, and quote the words that \
justify it. If you cannot quote them, the category is "unqualified".

Then, separately, say whether that handling visibly affected the reasoning - \
did an agent reach, qualify or change a conclusion because of how the term was \
pinned down? Answer true only if you can quote the effect. Careful wording that \
changed nothing is still careful wording; record it as not influential rather \
than inflating it.

## MISSION
{mission}

## TERMS DEFINED DIFFERENTLY ACROSS THIS ROSTER
{terms}

## TRANSCRIPT
{transcript}

Return JSON:
{{
  "terms": [
    {{
      "term": "<the term from the list>",
      "handling": "<one of the six categories above>",
      "evidence": "<the agent's exact words, or an empty string if unqualified or absent>",
      "by_agent": "<which agent, or an empty string>",
      "influenced_reasoning": true | false,
      "influence_evidence": "<quote showing the effect, or an empty string>"
    }}
  ],
  "notes": "<anything that limited your assessment, or an empty string>"
}}

Return one entry for every listed term, including the ones you mark absent. Do \
not add terms that were not listed."""


# --------------------------------------------------------------------------
# judge client
# --------------------------------------------------------------------------

def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"judge did not return JSON: {content[:200]}")
        parsed = json.loads(text[start : end + 1])
    if isinstance(parsed, list):
        # Some models answer the per-term question with the bare list instead of
        # the requested wrapper. That is a formatting slip, not a different
        # answer, so accept it rather than discarding a whole verdict.
        return {"terms": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("judge returned JSON that is not an object")
    return parsed
