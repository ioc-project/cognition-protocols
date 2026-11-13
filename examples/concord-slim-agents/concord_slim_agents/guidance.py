#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the model reads about CONCORD: the wording of the run and of each task, in one place.

The wording comes from the protocol's skills (concord-receiver and concord-initiator: SKILL.md, references/details.md and the
prompts of the scripts), moved to the current protocol (spec 0.3.0): one init reply carries the anchors, the seed and its own
score; there are no requests, deliveries or acks. Each text says where it comes from. For now it lives in the example; it may
move to the library later.

    RUN_DESCRIPTION       once, in the system prompt of every agent
    render_init_reply     the task owed after the init
    render_score          the task owed once there are proposals of the others to rate
"""
import re

from ioc_concord.protocol import format_number
from ioc_concord.schema import render_schema

# ---------------------------------------------------------------------------------------------------------------------------
# the system prompt: how a run goes (concord-receiver SKILL.md: "How a run goes", 4 steps, and its opening)
# ---------------------------------------------------------------------------------------------------------------------------

RUN_DESCRIPTION = """## How you decide together: CONCORD
You are one of the participants of a group that must choose a single outcome. One participant, the initiator, opens a run, and
everyone answers it through the tools you are given.

Your part: rate options honestly against your own role's criteria, and, when it is your turn, propose a better option. Your
criteria are the ones your role already gives you; CONCORD supplies none. You may also know things the others do not, and they may
know things you do not: what you know and leave out of a rationale or a reasoning is invisible to everyone else, and what another
agent reports from its own evidence is evidence you use in your reasoning.

A run goes like this:
1. First answer. Each participant gives its anchors (how satisfied it would be with the worst and with the best outcome, on its own
   0 to 100 scale: every later rating is read against these two points), proposes the one option that suits its role best, and
   rates it.
2. Score. Each participant rates the proposals of the others, with reasons. If one option reaches the threshold for every
   participant, the run ends there: SUCCESS.
3. Repair. Otherwise the least satisfied participant alone proposes one option and rates it (a new one, or one already in the run
   that it now rates differently because of a fact another agent shared). The others rate it, and the cycle repeats until an
   option clears everyone or the budget is spent (BEST_EFFORT).
4. Result. The run is over once the first participant has reported the outcome and the others have compared it with their own.

What you receive: the messages of the other participants, exactly as they were sent: the init, their first answers, their scores
and their repairs. What you send: the answers the tools build from what you write; you write only the content.
"""

# ---------------------------------------------------------------------------------------------------------------------------
# the budget line and the opening, said at every task (script prompts, _BUDGET and the head of _ask_text)
# ---------------------------------------------------------------------------------------------------------------------------

BUDGET = "Be detailed and informative, but stay within 1000 characters for each part of your answer."

OPENING = ("Decide from YOUR role's criteria and from what you know. Be honest: never inflate to seem agreeable or deflate for "
           "leverage, and never soften your criteria to make something look acceptable. If you cannot be satisfied, say so.")

# ---------------------------------------------------------------------------------------------------------------------------
# init reply: the anchors, the seed and its own score in one answer. It was three tasks (anchor, seed, and the rating of the seed
# in the score round). Anchor and seed: the script's _ask_text; the rating: its score task.
# ---------------------------------------------------------------------------------------------------------------------------

_ANCHORS = """1. ANCHORS
Rate two outcomes on your own raw 0-100 scale, as the satisfaction of your role if each happened (not how true it is today:
answering how true it is now inverts the scale and is refused). x_reject should get a LOW number, x_ideal a HIGH one strictly
above it. These are satisfaction ratings, not metrics: no error rate or latency in these fields. The anchors are rated once and stay
fixed for the whole run. Rate your raw scale, never a normalised 0-1 score.
  x_reject: {x_reject}
  x_ideal:  {x_ideal}"""

_PROPOSAL = """2. YOUR PROPOSAL
Propose the single candidate that best satisfies your own role, in this format. Do not hedge, do not offer alternatives, do not pre-compromise for
other roles: this round represents your viewpoint undiluted. The rationale carries the concrete facts and evidence you hold that
support it, why the other options are less suitable, but only where you have evidence against them (if you have none, say so:
never invent a risk to fill the gap), the trade-offs you considered, and why it will hold up under scrutiny: the others use it to
detect incorrect logic. Use only facts you hold: do not speculate. Your rationale is your only voice here: anything you know and
do not write in it is invisible to every other participant. Everything you write is read by every participant: do not write what
you need to keep private."""

_OWN_SCORE = """3. YOUR OWN SCORE FOR IT
Rate your own proposal on the same raw scale, with the reasons. Do not give it a high number because it is yours. Before the
number, state what your facts imply for this option, in their own words; then check that the number matches that reasoning. Give
concrete reasons: the properties that drove it and the evidence for and against it ("too expensive" is not a reason; "adds about
$400 a month, over the budget" is)."""


def options_lines(schema):
    """How the candidate must look, when the schema enforces more than a description (script: _options_lines)."""
    if schema.get("enum"):
        return (["options (the candidate must be exactly one of these, written as listed; detail about it goes in the rationale, "
                 "and added wording does not make a different option):"] + [f"  - {o}" for o in schema["enum"]])
    if {"pattern", "minLength", "maxLength"} & set(schema):
        return ["(the candidate is checked against the format above and refused if it does not satisfy it)"]
    return []


def render_init_reply(me, task, participants):
    """The text of the init-reply task `task` (see ioc_concord.Agent.due) for the agent `me`; `participants` are the run's."""
    out = [f"You are {me}. The run was opened by {task['initiator']}; participants: {', '.join(participants)}.",
           "CONCORD needs your first answer now. " + OPENING, "",
           f"context: {task['context']}", f"candidate format: {render_schema(task['schema'])}",
           *options_lines(task["schema"]), "",
           "Your answer has three parts, written together.", "",
           _ANCHORS.format(x_reject=task["x_reject"], x_ideal=task["x_ideal"]), "", _PROPOSAL, "", _OWN_SCORE, "", BUDGET]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------------
# score: the others' candidates, rated on one's own scale. The script's _FACTS and its score task; the first sentences of
# FACTS_RULED_OUT are in references/details.md ("What you learn from other agents' replies") and not in the script.
# ---------------------------------------------------------------------------------------------------------------------------

FACTS = ("Facts. What you hold is a fact. What other agents report from their own evidence (what they saw, were given or read) is "
         "evidence you must use: build it into your reasoning, apply it to every candidate it affects, and do not set it aside "
         "only because you cannot check it. Weigh it as evidence, not as certainty: a specific report counts for more than a "
         "vague one, and if a report contradicts your own facts or another report, say so in your reasoning and state which you "
         "rely on and why. An agent's conclusion or expectation is an argument, not a fact: weigh it, and set aside any claim "
         "that comes with no evidence, saying so. Do not speculate, and do not assume a fix, a delay or a way around something "
         "nobody reported. Before you rate a candidate, state what the evidence implies for it, in its own words, then give the "
         "number, and check that it matches that reasoning. Your own facts change only when another agent reports evidence that "
         "contradicts them, never because someone offers another reading of them. When two reports seem to conflict, do not "
         "invent a cause that reconciles them: say that they conflict and rate on what each supports.")

NOT_REASONS = "A candidate's name or wording, and other agents' numbers, are not reasons."

FACTS_RULED_OUT = ("A fact that rules a candidate out keeps its rating low, and a fact that supports it keeps it high, until a "
                   "reported fact says otherwise; a risk or an effect that nobody reported is not a reason.")

_SCORE = """TASK: rate {n} candidate{s} of the others
Rate each candidate listed on your own raw 0-100 scale, the same scale as your anchors (raw_reject={raw_reject}, raw_ideal={raw_ideal}),
for the whole run: the same quality gets the same number later as at anchoring. Place each between your anchors; if one lies beyond
an anchor, use the nearest anchor value and say so in the reasoning. Move a number only for a real change (a specific fact another
agent shared, or a rationale that answers a concern you raised), and be able to say in one sentence what changed your mind;
refusing to budge is inertia, not honesty. Never refuse to score: a candidate you consider terrible gets a low number with honest
reasons. Give the concrete reasons for each number (the properties that drove it, the evidence from your criteria, both for and
against it: "too expensive" is not a reason, "adds about $400 a month, over the budget" is), and say if a candidate rests on a
false claim or a flawed step. Write the facts that matter, even if they seem obvious to you: the repair agent and the others see only your
reasoning, and what you leave out is invisible to the rest of the run."""

_SAME = ("  this is the same candidate as {oid}, which you rated {raw}: keep that number unless a specific fact shared since then "
         "changes your view. If you rate it differently, begin your reasoning with: Same as {oid}, which I rated {raw}. I now rate "
         "it M because <agent> reported ...")


def _same_text(a, b):
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def run_view(run, me):
    """What the score task needs from the state of `run` (see ioc_concord.state): the anchors of `me`, as text, and the
    candidates it has rated or proposed, as [(id, candidate, raw as text)]."""
    st = run["engine"]
    a = st["anchors"].get(me)
    anchors = {"raw_reject": format_number(a["raw_reject"]), "raw_ideal": format_number(a["raw_ideal"])} if a else None
    pool = {c["id"]: c for c in st["pool"]}
    rated = [(cid, pool[cid]["candidate"], format_number(raw)) for cid, raw in st["raw"].get(me, {}).items() if cid in pool]
    return {"anchors": anchors, "rated": rated}


def render_score(me, task, view):
    """The text of the score task `task` for the agent `me`; `view` is run_view(run, me)."""
    n = len(task["candidates"])
    anchors = view["anchors"] or {"raw_reject": "?", "raw_ideal": "?"}
    out = [f"You are {me}. CONCORD needs your ratings now. " + OPENING, "", FACTS, NOT_REASONS, FACTS_RULED_OUT, "",
           _SCORE.format(n=n, s="" if n == 1 else "s", **anchors), BUDGET, ""]
    for c in task["candidates"]:
        out += [f"candidate {c['id']} (proposed by {c['from']}): {c['candidate']}", f"  its rationale: {c['rationale']}",
                f"  its proposer's reasoning for its own score: {c['reasoning']}"]
        for oid, text, raw in view["rated"]:
            if oid != c["id"] and _same_text(text, c["candidate"]):
                out.append(_SAME.format(oid=oid, raw=raw))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------------
# repair: the participant least satisfied with the incumbent proposes one better candidate, and rates it. The script's repair
# task; (3) has a second case from the spec (an agent may re-argue its own earlier proposal), and the header carries what the
# library's task now holds (the bar, the floor, the shortfall, the repairs left) in place of the old one-line reason.
# ---------------------------------------------------------------------------------------------------------------------------

_REPAIR_HEAD = """TASK: repair, round {round}
REPAIR ROUND, YOU ALONE. Every rating of the incumbent {id} is in. Its weakest rating is {floor}, below the bar of {threshold}: you are
the least satisfied participant ({mine}). You have {left} repair{s} left, this one included."""

_REPAIR = """Read the incumbent's ratings and their reasoning, not just the numbers. Propose ONE candidate that:
(1) clears your own bar, something you would rate close to your x_ideal and not marginally above what you just rejected;
(2) does not knowingly push any other agent below the threshold: infer from their reasoning what they are protecting and keep it;
(3) is not yet in the run, or is one already in the run that you now rate differently because of a named fact, or your own earlier
    proposal if you hold it is right and you can argue it better: then say so at the start of your rationale and give the new
    arguments, and start the reasoning of your rating with "Same as <id>, which I rated N. I now rate it M because <agent>
    reported ..."; a reworded copy of a scored candidate is not new;
(4) follows the candidate format;
(5) answers the strongest reasoning against your position, in your rationale: agree with part of it, refute it, or say why it does
    not apply;
(6) states any domain knowledge of your role that has not yet appeared and bears on why this candidate is the right one: it stays
    invisible to everyone if you do not write it.
{budget} Rate your own new candidate, honestly, in the same answer. If you believe none exists, give the candidate you rate
highest, even one already in the run, and state the conflict plainly in the rationale (which agent, over what property). Do not
lower the threshold, do not weaken your criteria, and do not propose something you are not satisfied by. The ratings of the
incumbent below are normalised 0-1 for you to read: never echo one back as your own rating."""


def render_repair(me, task, context, schema):
    """The text of the repair task `task` for the agent `me`; `context` and `schema` are those of the run."""
    inc = task["incumbent"]
    left = task["repairs_left"]
    out = [f"You are {me}. CONCORD needs your repair now. " + OPENING, "", FACTS, NOT_REASONS, FACTS_RULED_OUT, "",
           _REPAIR_HEAD.format(round=task["round"], id=inc["id"], floor=task["floor"], threshold=task["threshold"],
                               mine=task["scores"].get(me, "?"), left=left, s="" if left == 1 else "s"),
           _REPAIR.format(budget=BUDGET), "",
           f"candidate format: {render_schema(schema)}", f"context: {context}", *options_lines(schema), "",
           f"incumbent {inc['id']}: {inc['candidate']}", f"its rationale: {inc['rationale']}",
           f"ratings of the incumbent (the bar is {task['threshold']}):"]
    for agent, score in task["scores"].items():
        short = task["shortfall"].get(agent)
        below = f", below the bar by {short}" if short and float(short) > 0 else ""
        out.append(f"  - {agent}: {score}{below} ({task['reasoning'].get(agent, '')})")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------------
# the initiator: how to fill concord_start. The text of `init --help` ("How to fill the arguments") and details.md ("Starting a
# run"), moved to the tool. The order of the participants is from the spec; timeout, budget and eps are not the agent's to choose.
# ---------------------------------------------------------------------------------------------------------------------------

START_PARAMS = {
    "participants": ("The ids of the agents whose satisfaction counts, exactly as written in the task. The order matters: the "
                     "first one reports the outcome and wins ties, so list yourself first."),
    "satisfaction_threshold": ("The standard the outcome genuinely has to meet, a number above 0 and at most 1, not a level you are "
                               "sure is reachable (0.6 is a starting point). If it sits below what the agents already reach on "
                               "their own proposals, the run ends with no negotiation at all. It never changes during the run."),
    "options": "The options of the task, each copied exactly as given. Never write the list yourself.",
    "x_reject": ("The objective, never the means: the state the context is trying to produce, as a plain before/after. This is "
                 "the worst outcome: never a proposal, and no word about how it is reached. If an anchor could pass as something "
                 "an agent might propose, rewrite it one level up."),
    "x_ideal": ("The objective, never the means: the best outcome, as a plain before/after in the same terms. Never a proposal, and "
                "no word about how it is reached."),
    "context": "The problem to decide, as every participant must understand it.",
}

INITIATOR_GUIDANCE = """## Starting the run: you are the initiator
concord_start opens the run. Fill it like this:
""" + "\n".join(f"- {k}: {v}" for k, v in START_PARAMS.items())


# ---------------------------------------------------------------------------------------------------------------------------
# soft checks before the library's own: what the old receiver script refused and the library does not
# (a reason of at least 20 characters, a 0-1 number as a raw score). A rating beyond an anchor is not refused: the spec clamps it.
# ---------------------------------------------------------------------------------------------------------------------------

MIN_REASON = 20


def _number(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _short(label, text):
    if len(str(text or "").strip()) < MIN_REASON:
        return (f"{label} is too short: give the concrete reasons (the properties that drove it, the evidence from your criteria). "
                '"too expensive" is not a reason.')


def check_answer(tool, args, anchors):
    """The problems of an answer of the model, as a list of sentences (empty when it is fine). `anchors` are the raw anchors of
    the agent, as numbers (None before its init reply): in an init reply they are the ones in `args`."""
    out = []

    def raw_ok(label, value, ideal):
        v = _number(value)
        if v is not None and 0 <= v <= 1 < (ideal or 0):
            out.append(f"{label} is {value}: it looks like a normalised 0-1 score. Use your raw scale, never a normalised one.")

    if tool == "submit_init_reply":
        ideal = _number(args.get("raw_ideal"))
        out += [p for p in (_short("the rationale", args.get("rationale")), _short("the reasoning", args.get("reasoning"))) if p]
        raw_ok("your score", args.get("own_score"), ideal)
    elif tool == "submit_score":
        ideal = (anchors or {}).get("raw_ideal")
        for s in args.get("scores") or []:
            if isinstance(s, dict):
                out += [p for p in (_short(f"the reasoning for {s.get('id')}", s.get("reasoning")),) if p]
                raw_ok(f"your rating of {s.get('id')}", s.get("score"), ideal)
    elif tool == "submit_repair":
        ideal = (anchors or {}).get("raw_ideal")
        out += [p for p in (_short("the rationale", args.get("rationale")), _short("the reasoning", args.get("reasoning"))) if p]
        raw_ok("your score", args.get("own_score"), ideal)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# errors: what the model reads when a call is refused. Every refusal says what went wrong and what to do, as the old scripts did
# (`STATUS: ERROR`, the fault, `NEXT:`): never the complaint alone. The hints are those of the receiver script's _hint_for, with
# the commands of the script replaced by the tool.
# ---------------------------------------------------------------------------------------------------------------------------

# the fault is that the call is already answered or not the agent's to answer: asking again does nothing
_ALREADY = ("already sent", "already rated", "owes no score", "no repair is owed", "is not a participant", "knows no run",
            "has ended", "has aborted", "in a run already")

HINT_ALREADY = ("That is already answered, or not yours to answer: nothing is lost. Do not call the tool again for it; wait for "
                "your next task, and if none comes, send nothing.")
HINT_MISSING = "Call the tool again with every required argument."
HINT_ANCHORS = ("Rate the two outcomes again, as satisfaction of your role if each happened (reject LOW, ideal HIGH), and call the "
                "tool again.")
HINT_IDS = "Rate exactly the candidates the task lists, with their ids exactly as printed, and call the tool again."
HINT_FORMAT = ("The candidate must be exactly as the format says: put any extra detail in the rationale, and call the tool "
               "again.")
HINT_NUMBER = "Give every score as a number from 0 to 100 on your raw scale, and call the tool again."
HINT_DEFAULT = ("Fix what the message says and call the tool again. Never work around it: the tool is the only way to answer, "
                "and it writes the message itself.")
HINT_SHAPE = ("Send it in the shape the tool takes: a list is a list of separate entries, not one piece of text, and an entry is an "
              "object with the fields the tool takes. Then call the tool again.")
HINT_UNEXPECTED = ("Call the tool again once, with the same values. If it fails again, stop: do not try to work around it.")

UNEXPECTED = "the tool failed unexpectedly: {kind}: {detail}"
UNKNOWN_TOOL = "{name} is not a tool you have for this task: use {tools}."
NO_TOOL = "Answer with the tool: it is the only way to answer, and nothing was sent."
REPEATED = ("You have been refused the same way {n} times in a row: re-read the task and the tool's required fields, and change "
            "the call, not only its wording.")


def lacks(parts, sent=(), takes=()):
    """The sentence for the arguments a call lacks; `parts` are `name` (what it is) texts. With what the call sent and what the tool
    takes, the model sees the difference: one that copies its own earlier call otherwise repeats the slip."""
    out = "your call lacks " + "; ".join(parts) + "."
    if sent and takes:
        out += f" You sent: {', '.join(map(str, sent))}. The tool takes: {', '.join(takes)}."
    return out


def _kind(value):
    """What a value is, in words the model reads."""
    if isinstance(value, str):
        return "text"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    return "a list" if isinstance(value, list) else "an object" if isinstance(value, dict) else type(value).__name__


def wrong_shape(field, expected, got):
    """The sentence for an argument that has the wrong shape, such as a list sent as one piece of text."""
    return f"`{field}` must be {expected}, not {_kind(got)}."


def settled(text):
    """True when the refusal says the call is already answered or not the agent's: the turn is over, not to be retried."""
    return any(k in text.lower() for k in _ALREADY)


def hint_for(text):
    """What to do about the refusal `text`: always a way back onto the path, never just the complaint."""
    m = text.lower()
    if settled(m):
        return HINT_ALREADY
    if "your call lacks" in m:
        return HINT_MISSING
    if "raw_ideal" in m and "raw_reject" in m:
        return HINT_ANCHORS
    if "covers exactly the candidates" in m or "is not a candidate id" in m or "rated twice" in m or "which it proposed" in m:
        return HINT_IDS
    if m.startswith("the candidate") or "the candidate must" in m:
        return HINT_FORMAT
    if "from 0 to 100" in m or "must be a number" in m:
        return HINT_NUMBER
    if "must be a list" in m or "must be an object" in m:
        return HINT_SHAPE
    if "failed unexpectedly" in m:
        return HINT_UNEXPECTED
    return HINT_DEFAULT


def refusal(text, repeats=1):
    """What the model reads for a refused call: the fault, that nothing was sent, and what to do. After the same refusal twice in
    a row it is also told to change the call."""
    out = f"refused: {text.strip().rstrip('.')}. Nothing was sent.\nNEXT: {hint_for(text)}"
    if repeats >= 2 and not settled(text):
        out += " " + REPEATED.format(n=repeats)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# what the agent receives: the messages of the others, as they arrived. In the skills the agent passed each delivery (the replies of
# the other agents, as XML) to its script, so they stayed in its context; here the node reads them, and the model gets the same text
# in front of its next task. A resend is shown as the message it carries.
# ---------------------------------------------------------------------------------------------------------------------------

DELIVERY_HEADER = "CONCORD messages you received since your last answer, exactly as they arrived (every message goes to all the participants):"


def unwrap(text):
    """The message a `concord-resend` carries (the resend itself says nothing new), or `text` if it is not one."""
    if not re.search(r"<concord-resend\b", text):
        return text
    start = re.search(r"<concord-(?!resend\b)[a-z-]+\b", text)
    end = list(re.finditer(r"</concord-(?!resend\b)[a-z-]+\s*>", text))
    return text[start.start():end[-1].end()] if start and end else text


def render_delivery(texts):
    """The messages received since the last answer, as the agent reads them ('' when there are none)."""
    return DELIVERY_HEADER + "\n\n" + "\n\n".join(unwrap(t).strip() for t in texts) if texts else ""


# ---------------------------------------------------------------------------------------------------------------------------
# the end: in the spec the reporter (the first participant) announces the outcome to all the others in a `concord-result`, and
# each participant compares it with its own. The old receiver skill ended with: "Nothing is due from you then: do not re-open the
# decision, do not re-propose, and do not send an acknowledgement. If SUCCESS, proceed with the winning candidate. If BEST_EFFORT,
# say so when you report, naming the agents below their floor."
# ---------------------------------------------------------------------------------------------------------------------------

_END_RULES = ("Nothing is due from you: do not re-open the decision, do not re-propose, and do not send an acknowledgement. If SUCCESS, "
              "proceed with the winning candidate. If BEST_EFFORT, say so when you report, naming the agents below their floor.")


def render_end(me, report):
    """What the agent knows when its run is over, from its report (see ioc_concord.Agent.report)."""
    if report["outcome"] == "ABORTED":
        why = (report.get("reason") or {})
        return ("The run was aborted" + (f" ({why.get('reason')}" + (f", agent {why['agent']}" if why.get("agent") else "") + ")"
                                         if why.get("reason") else "") + ". There is no outcome to act on: nothing is certified.")
    cmp_ = report["results_compared"]
    reporter = cmp_["reporter"]
    who = "You reported" if reporter == me else f"{reporter} reported"
    win = report["winner"]
    lines = [f"The run is over. {who} the result to all the participants: {report['outcome']}, winning candidate {win['id']} "
             f"({win['candidate']}).",
             "Final scores of the winning candidate, normalized 0-1: "
             + ", ".join(f"{a} {format_number(s)}" for a, s in report["scores"].items()) + f". Repair rounds used: {report['rounds']}."]
    if report["shortfall"]:
        lines.append("Below the bar: " + ", ".join(f"{a} by {format_number(s)}" for a, s in report["shortfall"].items()) + ".")
    if reporter != me and cmp_["matches"] is not None:
        lines.append("Your own outcome " + ("matches" if cmp_["matches"] else "DIFFERS from") + " the reporter's result.")
    lines.append(f"Status of the run for you: {report['status']}.")
    return "\n".join(lines) + "\n" + _END_RULES
