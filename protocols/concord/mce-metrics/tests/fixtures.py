# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""A small synthetic CONCORD session, as the KG stores it: tool spans carrying the initiator's calls."""

import json

from concord_mce_metrics import protocolcost, session

STATE = "abc123"
TEXTS = ("restart api", "enable debug logging")


def anchor_reply(agent, reject=0, ideal=100):
    return f'<concord-anchor-reply agent="{agent}" state="{STATE}" ack="1"><raw_reject>{reject}</raw_reject><raw_ideal>{ideal}</raw_ideal></concord-anchor-reply>'


def candidate_reply(agent, cid, text):
    return f'<concord-candidate-reply id="{cid}" agent="{agent}" state="{STATE}" ack="2"><candidate>{text}</candidate><rationale>fits {agent}</rationale></concord-candidate-reply>'


def score_reply(agent, **by_candidate):
    """score_reply("a", **{"seed-a": (80, "good fit")}) -> a score reply."""
    inner = "".join(f'<score candidate_id="{cid}"><raw>{raw}</raw><reasoning>{why}</reasoning></score>' for cid, (raw, why) in by_candidate.items())
    return f'<concord-score-reply agent="{agent}" state="{STATE}" ack="3">{inner}</concord-score-reply>'


def scores(**by_candidate):
    """scores(**{"seed-a": (80, "good fit")}) -> what an agent rates; the fixture wraps it in a reply."""
    return by_candidate


def call(t, args, stdout="STATUS: SEND", exit_code=0, stdin="", stderr=""):
    return {
        "id": f"t{t}",
        "startTime": t,
        "endTime": t + 0.5,
        "toolName": "run_skill_script",
        "toolArguments": json.dumps({"script": "concord_initiator.py", "args": args, "stdin": stdin}),
        "toolOutput": json.dumps({"exit_code": exit_code, "stdout": stdout, "stderr": stderr}),
    }


def handle(t, *replies, **kw):
    return call(t, ["--state", f"concord-state-{STATE}.json", "handle"], stdin="\n\n".join(replies), **kw)


COMPLETE = "RUN COMPLETE: outcome {outcome}.\nWinning candidate {winner} (proposed by a): x\nFinal scores: a 0.90, b 0.85 (threshold 0.7)"


def tool_spans(finish="SUCCESS", repairs=0, score_a=None, score_b=None, texts=TEXTS):
    """init, anchors, seeds, scoring, `repairs` repair rounds, then the end.

    finish: SUCCESS or BEST_EFFORT (the engine decides, from the scores: BEST_EFFORT needs a budget of 2 spent), or None
    to leave the last agent unscored. With `repairs` the first scores are below tau (0.7), so the engine asks for a
    repair; the last round's scores clear tau for SUCCESS and stay below it for BEST_EFFORT.
    """
    clears = finish != "BEST_EFFORT"
    repairs = 2 if finish == "BEST_EFFORT" else repairs
    low = repairs > 0
    a_seed = score_a or scores(**{"seed-a": (90, "I favour this"), "seed-b": (20, "this is wrong")})
    b_seed = score_b or scores(**{"seed-a": (40 if low else 85, "works for me"), "seed-b": (90, "my own")})
    spans = [
        call(1, ["init", "--quiet", "--agents", "a,b", "--satisfaction-threshold", "0.7", "-B", "2", "--schema", '{"type": "string", "description": "an action"}'],
             stdout=f"State: concord-state-{STATE}.json\nNEXT: run handle"),
        call(2, ["--state", f"concord-state-{STATE}.json", "handle"]),
        handle(3, anchor_reply("a"), anchor_reply("b")),
        handle(4, candidate_reply("a", "seed-a", texts[0]), candidate_reply("b", "seed-b", texts[1])),
        handle(5, score_reply("a", **a_seed), score_reply("b", **b_seed)),
    ]
    t = 6
    for k in range(1, repairs + 1):
        # the least-satisfied agent (b) answers the repair and rates its own candidate, then a rates it
        last = k == repairs
        a_raw = 90 if (last and clears) else 30
        spans.append(handle(t, candidate_reply("b", f"r{k}-b", f"new plan {k}"), score_reply("b", **{f"r{k}-b": (95, "my own")})))
        spans.append(handle(t + 1, score_reply("a", **{f"r{k}-b": (a_raw, "a's view")})))
        t += 2
    if finish is None:
        spans.pop()  # the last reply never came
    else:
        winner = f"r{repairs}-b" if repairs and clears else "seed-a"
        spans[-1]["toolOutput"] = json.dumps({"exit_code": 0, "stdout": "STATUS: NONE", "stderr": COMPLETE.format(outcome=finish, winner=winner)})
    return spans


def llm(span_id, start, end, prompt, cache=0, completion=10):
    return {
        "id": span_id,
        "startTime": start,
        "endTime": end,
        "promptTokenCount": prompt,
        "cacheReadTokenCount": cache,
        "completionTokenCount": completion,
    }


def context(spans=None, llms=None, **kw):
    session._CACHE.clear()
    protocolcost._CACHE.clear()
    return {"session_id": "s", "tool_spans": spans if spans is not None else tool_spans(**kw), "llm_spans": llms or []}
