#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What an agent's model reads about the protocol: the wording of the run and of each task, in one place.

The node (agent.py) decides, and its model only writes content. This module is the text that tells the model what is asked of it, as
Appendix A of the specification describes: the run, the first answer, a score, a repair, how to weigh evidence, what the others
wrote, how the run ended and what to do about a refused answer. It is the reference wording: an agent may use it as it is, or write its
own with the same points. The wording comes from the receiver and initiator skills, moved to the current protocol.

    RUN_DESCRIPTION, INITIATOR_GUIDANCE   once, with the standing context of the model
    Agent.instructions(task)              the text of a task of `due()`
    render_init_reply, render_score, render_repair, render_task      the same, from the parts
    render_delivery(texts)                the messages of the others, as they arrived, in front of the next task
    render_end(me, report)                how the run ended
    check_answer, refusal                 what to say when an answer is refused

It holds only text and the functions that fill it in: no I/O, no model, no clock.
"""
import re

from ioc_concord.protocol import format_number
from ioc_concord.schema import render_schema

# ---------------------------------------------------------------------------------------------------------------------------
# the points on evidence (Appendix A.6). FACTS and NOT_REASONS are the receiver script's `_FACTS`; FACTS_RULED_OUT is in the long
# form of the receiver skill and not in its script. They are part of the standing context and are repeated in the tasks that weigh
# what the others wrote.
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

# ---------------------------------------------------------------------------------------------------------------------------
# the standing context: how a run goes (concord-receiver SKILL.md: "How a run goes", and its opening)
# ---------------------------------------------------------------------------------------------------------------------------

RUN_DESCRIPTION = """## How you decide together: CONCORD
You are one of the participants of a group that must choose a single outcome. One participant, the initiator, opens a run, and
everyone answers it.

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
and their repairs. What you send: the answers that are built and sent from what you write; you write only the content.

How to weigh evidence:
""" + FACTS + "\n" + NOT_REASONS + "\n" + FACTS_RULED_OUT + "\n"

# the budget line and the opening, said at every task
BUDGET = "Be detailed and informative, but stay within 1000 characters for each part of your answer."

OPENING = ("Decide from YOUR role's criteria and from what you know. Be honest: never inflate to seem agreeable or deflate for "
           "leverage, and never soften your criteria to make something look acceptable. If you cannot be satisfied, say so.")

# ---------------------------------------------------------------------------------------------------------------------------
# the first answer: the anchors, the proposal and its own score in one answer (spec: <concord-init-reply>)
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
    """How the candidate must look, when the schema enforces more than a description."""
    if schema.get("enum"):
        return (["options (the candidate must be exactly one of these, written as listed; detail about it goes in the rationale, "
                 "and added wording does not make a different option):"] + [f"  - {o}" for o in schema["enum"]])
    if {"pattern", "minLength", "maxLength"} & set(schema):
        return ["(the candidate is checked against the format above and refused if it does not satisfy it)"]
    return []


def _parameters(params):
    """The parameters of the run as one line, from `run["params"]` (the threshold and eps in millionths); nothing when they are unknown."""
    if not params:
        return []
    return [f"parameters: threshold {format_number(params['satisfaction_threshold'])}, repair budget {params['B']}, "
            f"eps {format_number(params['eps'])}, timeout {params['timeout']} s, retries {params['retries']}"]


def render_init_reply(me, task, participants, params=None):
    """The text of the init-reply task `task` (see Agent.due) for the agent `me`; `participants` and `params` are the run's."""
    out = [f"You are {me}. The run was opened by {task['initiator']}; participants: {', '.join(participants)}.",
           "CONCORD needs your first answer now. " + OPENING, "",
           f"context: {task['context']}", f"candidate format: {render_schema(task['schema'])}",
           *options_lines(task["schema"]), *_parameters(params), "",
           "Your answer has three parts, written together.", "",
           _ANCHORS.format(x_reject=task["x_reject"], x_ideal=task["x_ideal"]), "", _PROPOSAL, "", _OWN_SCORE, "", BUDGET]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------------
# a score: the candidates of the others, rated on one's own scale (spec: <concord-score>). It repeats the points on evidence, as the
# receiver script did at each score.
# ---------------------------------------------------------------------------------------------------------------------------

_SCORE = """TASK: rate {n} candidate{s} of the others
Rate each candidate listed on your own raw 0-100 scale, the same scale as your anchors (raw_reject={raw_reject}, raw_ideal={raw_ideal}),
for the whole run: the same quality gets the same number later as at anchoring. Place each between your anchors; if one lies beyond
an anchor, use the nearest anchor value and say so in the reasoning. Move a number only for a real change (a specific fact another
agent shared, or a rationale that answers a concern you raised), and say in one sentence what changed your mind and who shared it;
refusing to budge is inertia, not honesty. Never refuse to score: a candidate you consider terrible gets a low number with honest
reasons. Give the concrete reasons for each number (the properties that drove it, the evidence from your criteria, both for and
against it: "too expensive" is not a reason, "adds about $400 a month, over the budget" is), and say if a candidate rests on a
false claim or a flawed step. Write the facts that matter, even if they seem obvious to you: the repair agent and the others see only your
reasoning, and what you leave out is invisible to the rest of the run."""

_SAME = ("  this is the same candidate as {oid}, which you rated {raw}: keep that number unless a specific fact shared since then "
         "changes your view. If you rate it differently, begin your reasoning with: Same as {oid}, which I rated {raw}. I now rate "
         "it M because <agent> reported ...")


def _same_candidate(a, b):
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def run_view(run, me):
    """What the score and repair tasks need from the state of `run` (see state.py): the anchors of `me`, as text, the candidates it has
    rated or proposed, as [(id, candidate, raw as text)], and every candidate of the run, as [(id, proposer, candidate)]."""
    st = run["engine"]
    a = st["anchors"].get(me)
    anchors = {"raw_reject": format_number(a["raw_reject"]), "raw_ideal": format_number(a["raw_ideal"])} if a else None
    pool = {c["id"]: c for c in st["pool"]}
    rated = [(cid, pool[cid]["candidate"], format_number(raw)) for cid, raw in st["raw"].get(me, {}).items() if cid in pool]
    return {"anchors": anchors, "rated": rated, "pool": [(c["id"], c["proposer"], c["candidate"]) for c in st["pool"]]}


def _weighing(me, what):
    """The opening of a task that weighs what the others wrote (a score, a repair): who, what is needed now, and the points on evidence."""
    return [f"You are {me}. CONCORD needs your {what} now. " + OPENING, "", FACTS, NOT_REASONS, FACTS_RULED_OUT, ""]


def render_score(me, task, view):
    """The text of the score task `task` for the agent `me`; `view` is run_view(run, me)."""
    n = len(task["candidates"])
    anchors = view["anchors"] or {"raw_reject": "?", "raw_ideal": "?"}
    out = [*_weighing(me, "ratings"), _SCORE.format(n=n, s="" if n == 1 else "s", **anchors), BUDGET, ""]
    for c in task["candidates"]:
        out += [f"candidate {c['id']} (proposed by {c['from']}): {c['candidate']}", f"  its rationale: {c['rationale']}",
                f"  its proposer's reasoning for its own score: {c['reasoning']}"]
        for oid, text, raw in view["rated"]:
            if oid != c["id"] and _same_candidate(text, c["candidate"]):
                out.append(_SAME.format(oid=oid, raw=raw))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------------
# a repair: the participant least satisfied with the incumbent proposes one better candidate, and rates it (spec: <concord-repair>).
# (3) has the case of the spec that an agent may re-argue its own earlier proposal.
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


def render_repair(me, task, context, schema, view=None):
    """The text of the repair task `task` for the agent `me`; `context` and `schema` are those of the run, and `view` is run_view(run, me):
    with it the task lists the candidates already in the run and says on what scale to rate the new one."""
    inc = task["incumbent"]
    left = task["repairs_left"]
    out = [*_weighing(me, "repair"),
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
    if view:
        out += ["", "candidates in the run:"] + [f"  - {cid} (proposed by {who}): {text}" for cid, who, text in view["pool"]]
        if view["anchors"]:
            out.append(f"your own scale, for rating your new candidate: raw_reject={view['anchors']['raw_reject']}, "
                       f"raw_ideal={view['anchors']['raw_ideal']}")
    return "\n".join(out)


def render_task(run, me, task):
    """The text of any task of `Agent.due()` for the agent `me`, from the state of `run`. Raises ValueError for another kind."""
    if task["kind"] == "init-reply":
        return render_init_reply(me, task, run["participants"], run["params"])
    if task["kind"] == "score":
        return render_score(me, task, run_view(run, me))
    if task["kind"] == "repair":
        return render_repair(me, task, run["context"], run["schema"], run_view(run, me))
    raise ValueError(f"no instructions for a task of kind {task['kind']!r}")


# ---------------------------------------------------------------------------------------------------------------------------
# the initiator: how to fill the init (initiator skill: `init --help`, "How to fill the arguments", and its long form, "Starting a
# run"). The order of the participants is from the spec. The timeout, the budget and eps are not for the model to choose.
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
To open the run you give:
""" + "\n".join(f"- {k}: {v}" for k, v in START_PARAMS.items())


# ---------------------------------------------------------------------------------------------------------------------------
# checks that go beyond the specification: a reasoning too short to be one, a number of 0-1 sent as a raw score (the receiver script
# refused both; the library does not). A rating beyond an anchor is not refused: the spec clamps it.
# ---------------------------------------------------------------------------------------------------------------------------

MIN_REASON = 20


def _as_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _short(label, text):
    """The problem of a reasoning or a rationale too short to be one, as a list (empty when it is fine)."""
    if len(str(text or "").strip()) >= MIN_REASON:
        return []
    return [f"{label} is too short: give the concrete reasons (the properties that drove it, the evidence from your criteria). "
            '"too expensive" is not a reason.']


def check_answer(kind, answer, anchors=None):
    """The problems of an answer of the model to a task of `kind`, as a list of sentences (empty when it is fine). `answer` holds what
    the matching `submit_*` takes, by name: for an init-reply `raw_reject`, `raw_ideal`, `candidate`, `rationale`, `raw`, `reasoning`;
    for a score `scores`, a list of {"id", "raw", "reasoning"}; for a repair `candidate`, `rationale`, `raw`, `reasoning`. `anchors` are
    the raw anchors of the agent as numbers, {"raw_reject", "raw_ideal"}, or None before its init reply."""
    out = []

    def raw_ok(label, value, ideal):
        v = _as_float(value)
        if v is not None and 0 < v < 1 < (ideal or 0):
            out.append(f"{label} is {value}: it looks like a normalised 0-1 score. Use your raw scale, never a normalised one.")

    ideal = (anchors or {}).get("raw_ideal")
    if kind in ("init-reply", "repair"):
        out += _short("the rationale", answer.get("rationale")) + _short("the reasoning", answer.get("reasoning"))
        raw_ideal = _as_float(answer.get("raw_ideal"))
        if kind == "init-reply" and raw_ideal is not None and raw_ideal <= 1:
            out.append(f"your anchors are {answer.get('raw_reject')} and {answer.get('raw_ideal')}: they look like normalised 0-1 scores. "
                       "Use your raw 0-100 scale, never a normalised one.")
        raw_ok("your score", answer.get("raw"), _as_float(answer.get("raw_ideal")) if kind == "init-reply" else ideal)
    elif kind == "score":
        for s in answer.get("scores") or []:
            if isinstance(s, dict):
                out += _short(f"the reasoning for {s.get('id')}", s.get("reasoning"))
                raw_ok(f"your rating of {s.get('id')}", s.get("raw"), ideal)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# a refused answer: what the model reads. Every refusal says what went wrong and what to do about it, as the receiver script did
# (`STATUS: ERROR`, the fault, `NEXT:`): never the complaint alone (Appendix A.7). The hints are those of its `_hint_for`.
# ---------------------------------------------------------------------------------------------------------------------------

# the code of the errors that say the answer is already given or not the agent's to give (StateError and MissingState): asking again does
# nothing. It is the code and not the class, so that it holds for the copy of the library inside a script as well.
_STATE_CODES = ("state_error", "missing_state")

HINT_ALREADY = ("That is already answered, or not yours to answer: nothing is lost. Do not answer it again; wait for your next "
                "task, and if none comes, send nothing.")
HINT_MISSING = "Answer again with every required field."
HINT_ANCHORS = "Rate the two outcomes again, as satisfaction of your role if each happened (reject LOW, ideal HIGH), and answer again."
HINT_IDS = "Rate exactly the candidates the task lists, with their ids exactly as printed, and answer again."
HINT_FORMAT = "The candidate must be exactly as the format says: put any extra detail in the rationale, and answer again."
HINT_NUMBER = "Give every score as a number from 0 to 100 on your raw scale, and answer again."
HINT_DEFAULT = ("Fix what the message says and answer again. Never work around it: you write only the content, and the message "
                "itself is written for you.")
HINT_SHAPE = ("Send it in the shape asked for: a list is a list of separate entries, not one piece of text, and an entry is an "
              "object with the fields asked for. Then answer again.")
HINT_UNEXPECTED = "Answer once more, with the same values. If it fails again, stop: do not try to work around it."

UNEXPECTED = "processing the answer failed unexpectedly: {kind}: {detail}"
REPEATED = ("You have been refused the same way {n} times in a row: re-read the task and the fields asked for, and change the "
            "answer, not only its wording.")


def lacks(parts, sent=(), takes=()):
    """The sentence for the fields an answer lacks; `parts` are `name` (what it is) texts. With what the answer sent and what is
    taken, the model sees the difference: one that copies its own earlier answer otherwise repeats the slip."""
    out = "your answer lacks " + "; ".join(parts) + "."
    if sent and takes:
        out += f" You sent: {', '.join(map(str, sent))}. Expected: {', '.join(takes)}."
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
    """The sentence for a field that has the wrong shape, such as a list sent as one piece of text."""
    return f"`{field}` must be {expected}, not {_kind(got)}."


def settled(error):
    """True when `error`, an error of the library, says the answer is already given or not the agent's: the turn is over, it is not to be
    retried. The text of a refusal on its own (a string) is never settled: only the type of an error says so."""
    return getattr(error, "code", None) in _STATE_CODES


# a refusal and what to do about it: a phrase of the refusal (the texts of the library's own errors, and of `lacks` and `wrong_shape`),
# and the hint. The first row that matches is used.
_HINTS = (
    (("your answer lacks",), HINT_MISSING),
    (("strictly greater than raw_reject", "must exceed raw_reject"), HINT_ANCHORS),
    (("covers exactly the candidates", "is not a candidate id", "rated twice", "which it proposed"), HINT_IDS),
    (("the candidate must", "the candidate is required"), HINT_FORMAT),
    (("from 0 to 100", "must be a number"), HINT_NUMBER),
    (("must be a list", "must be an object"), HINT_SHAPE),
    (("failed unexpectedly",), HINT_UNEXPECTED),
)


def hint_for(error):
    """What to do about a refusal, an error of the library or the text of one: always a way back onto the path, never just the complaint."""
    if settled(error):
        return HINT_ALREADY
    m = str(error).lower()
    return next((hint for phrases, hint in _HINTS if any(p in m for p in phrases)), HINT_DEFAULT)


def refusal(error, repeats=1):
    """What the model reads for a refused answer: the fault, that nothing was sent, and what to do. `error` is the error of the library or
    the text of a refusal. After the same refusal twice in a row the model is also told to change the answer."""
    out = f"refused: {str(error).strip().rstrip('.')}. Nothing was sent.\nNEXT: {hint_for(error)}"
    if repeats >= 2 and not settled(error):
        out += " " + REPEATED.format(n=repeats)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# what the agent receives: the messages of the others, as they arrived (Appendix A.4). In the skills the agent passed each delivery (the
# replies of the other agents, as XML) to its script, so they stayed in its context; here the node takes them (`Step.accepted`) and the
# model reads the same text in front of its next task. A resend is shown as the message it carries.
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
# the end: in the spec the reporter (the first participant) announces the outcome to all the others in a `concord-result`, and each
# participant compares it with its own. The receiver skill ended with: "Nothing is due from you then: do not re-open the decision,
# do not re-propose, and do not send an acknowledgement. If SUCCESS, proceed with the winning candidate. If BEST_EFFORT, say so
# when you report, naming the agents below their floor."
# ---------------------------------------------------------------------------------------------------------------------------

_END_DISPUTED = ("Nothing is due from you: do not re-open the decision and do not re-propose. But no result of a disputed run is to be relied "
                 "on: do not act on this outcome, and say so when you report.")

_END_RULES = ("Nothing is due from you: do not re-open the decision, do not re-propose, and do not send an acknowledgement. If SUCCESS, "
              "proceed with the winning candidate. If BEST_EFFORT, say so when you report, naming the agents below their floor.")


def render_end(me, report):
    """What the agent knows when its run is over, from its report (see Agent.report). An initiator that is not a participant has no
    outcome of its own: it learns it from the result of the reporter."""
    cmp_ = report["results_compared"]
    outcome = report["outcome"] or (cmp_.get("result") or {}).get("outcome")
    if outcome == "ABORTED":
        why = report.get("reason") or {}
        return ("The run was aborted" + (f" ({why.get('reason')}" + (f", agent {why['agent']}" if why.get("agent") else "") + ")"
                                         if why.get("reason") else "") + ". There is no outcome to act on: nothing is certified.")
    win = report["winner"]
    if outcome is None or win is None:
        return "The run is not over: the result of the reporter has not arrived."
    reporter = cmp_["reporter"]
    who = "You reported" if reporter == me else f"{reporter} reported"
    lines = [f"The run is over. {who} the result to all the participants: {outcome}, winning candidate {win['id']} "
             f"({win['candidate']})."]
    if report["scores"]:
        lines.append("Final scores of the winning candidate, normalized 0-1: "
                     + ", ".join(f"{a} {format_number(s)}" for a, s in report["scores"].items())
                     + (f". Repair rounds used: {report['rounds']}." if report["rounds"] is not None else "."))
    if report["shortfall"]:
        lines.append("Below the bar: " + ", ".join(f"{a} by {format_number(s)}" for a, s in report["shortfall"].items()) + ".")
    if reporter != me and cmp_["matches"] is not None:
        lines.append("Your own outcome " + ("matches" if cmp_["matches"] else "DIFFERS from") + " the reporter's result.")
    lines.append(f"Status of the run for you: {report['status']}.")
    if report["status"] == "disputed":
        found = report.get("mismatches") or {}
        lines.append(f"This run is DISPUTED (mismatch errors sent: {found.get('sent', 0)}, received: {len(found.get('received', []))}): "
                     "the participants did not all reach the same outcome, or a cancel reached an agent whose run had ended.")
    elif report["status"] == "unverified":
        lines.append("You have no outcome of your own to compare it with: this is the result of the reporter.")
    return "\n".join(lines) + "\n" + (_END_DISPUTED if report["status"] == "disputed" else _END_RULES)
