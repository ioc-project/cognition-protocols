# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

"""A small synthetic CONCORD session, as the KG stores it: tool spans carrying the initiator's calls."""

import json

from concord_mce_metrics import protocolcost, session


def reply(agent, body, before=None):
    return ["record-reply", agent, body]


def anchor(reject=0, ideal=100):
    return f"<raw_reject>{reject}</raw_reject><raw_ideal>{ideal}</raw_ideal>"


def scores(**by_candidate):
    """scores(c1=(80, 'good fit')) -> a score reply."""
    return "".join(
        f'<score candidate_id="{cid}"><raw>{raw}</raw><reasoning>{why}</reasoning></score>' for cid, (raw, why) in by_candidate.items()
    )


CANDIDATES = (
    '<candidate id="c1-a"><text>restart api | RATIONALE fast</text></candidate>'
    '<candidate id="c2-b"><text>enable debug logging | RATIONALE safe</text></candidate>'
)


def call(t, args, stdout, exit_code=0, stdin=""):
    return {
        "id": f"t{t}",
        "startTime": t,
        "endTime": t + 0.5,
        "toolName": "run_skill_script",
        "toolArguments": json.dumps({"script": "concordv2.py", "args": args, "stdin": stdin}),
        "toolOutput": json.dumps({"exit_code": exit_code, "stdout": stdout}),
    }


def tool_spans(finish="SUCCESS", repairs=0, score_a=None, score_b=None):
    """init, anchors, seeds, scoring, `repairs` repair rounds, then Complete (or stop at Score when finish is None)."""
    score_a = score_a or scores(**{"c1-a": (90, "I favour this"), "c2-b": (20, "this is wrong")})
    score_b = score_b or scores(**{"c1-a": (85, "works for me"), "c2-b": (90, "my own")})
    def after_scoring(left):
        return "Phase: Repair" if left else "Phase: Score"

    spans = [
        call(1, ["init", "--agents", "a,b", "--satisfaction-threshold", "0.7", "-B", "2", "--schema", "pick an action"], "Phase: Anchor"),
        call(2, ["record-reply", "a", anchor()], "Phase: Anchor"),
        call(3, ["record-reply", "b", anchor()], "Phase: Seed"),
        call(4, ["record-reply", "a", "restart api"], "Phase: Seed"),
        call(5, ["record-reply", "b", "enable debug logging"], "Phase: Score\n" + CANDIDATES),
        call(6, ["record-reply", "a", score_a], "Phase: Score"),
        call(7, ["record-reply", "b", score_b], after_scoring(repairs)),
    ]
    t = 8
    for left in range(repairs - 1, -1, -1):
        # the least-satisfied agent answers the repair, then everyone scores the new candidate; the last scoring decides
        spans.append(call(t, ["record-reply", "a", "new candidate"], "Phase: Score\n" + CANDIDATES))
        spans.append(call(t + 1, ["record-reply", "b", score_b], after_scoring(left)))
        t += 2
    if finish:
        spans.append(call(t, ["status"], f"Phase: Complete - {finish}\n<winning_candidate_id>c1-a</winning_candidate_id>"))
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
