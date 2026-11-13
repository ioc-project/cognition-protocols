#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Show the CONCORD messages of a run: who sent what, in order, and what each one looks like.

    uv run python show.py runs/<run folder>                 the exchange, one line per message
    uv run python show.py runs/<run folder> --text          ... with the rationale and the reasoning of each message
    uv run python show.py runs/<run folder> --xml           ... with the whole message, as it went over SLIM
    uv run python show.py runs/<run folder> --formats       one example of each kind of message (the format), from this run
    uv run python show.py runs/<run folder> --mermaid       a Mermaid sequence diagram of the exchange
    uv run python show.py runs/<run folder> --verify        check the run against the spec (see verify.py)

The messages are those each agent kept in its state file (`participant-X-state.json`): the text it sent, word for word. Every message
is a broadcast: SLIM delivers it to every other agent of the run. The order and the times come from the events of each agent.
"""
import argparse
import json
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

AGENTS = ("participant-a", "participant-b", "participant-c")
SEND_EVENTS = ("started", "sent")        # an agent's own messages: the init (`started`), then every answer (`sent`)


def load(run_dir):
    """The messages of the run, oldest first: [{"at", "from", "text", "uuid"}]. `at` is None when the events cannot be matched."""
    out = []
    for who in AGENTS:
        try:
            state = json.loads((run_dir / f"{who}-state.json").read_text())
            events = json.loads((run_dir / f"{who}.json").read_text())["events"]
        except (OSError, ValueError, KeyError):
            continue
        run = next(iter(state["runs"].values()), None)
        if run is None:
            continue
        sent = list(run["sent_by_uuid"].items())                       # in the order the agent sent them
        times = [e["at"] for e in events if e["event"] in SEND_EVENTS]
        known = len(times) == len(sent)                                # a resend or an error has no event of its own
        for n, (uid, text) in enumerate(sent):
            out.append({"at": times[n] if known else None, "from": who, "uuid": uid, "text": text, "n": n})
    stamped = [m for m in out if m["at"] is not None]
    t0 = min((m["at"] for m in stamped), default=0.0)
    for m in out:
        m["t"] = (m["at"] - t0) if m["at"] is not None else None
    out.sort(key=lambda m: (m["t"] if m["t"] is not None else 1e9, AGENTS.index(m["from"]), m["n"]))
    return out


def parse(text):
    root = ET.fromstring(text)
    inner = root[0] if root.tag == "concord-resend" and len(root) else root
    return root, inner


def _t(el, path, default=""):
    found = el.find(path)
    return (found.text or "").strip() if found is not None else default


def summary(text):
    """(kind, one line about the message)."""
    try:
        root, el = parse(text)
    except ET.ParseError:
        return "?", "(not readable)"
    kind = root.tag.replace("concord-", "")
    if root.tag == "concord-init":
        agents = ",".join(a.text for a in root.findall("participants/agent"))
        try:
            schema = json.loads(_t(root, "proposal/candidate_schema"))
            fmt = "one of " + " | ".join(schema["enum"]) if "enum" in schema else "open format"
        except (ValueError, KeyError):
            fmt = "?"
        return kind, f"participants {agents}; bar {_t(root, 'params/satisfaction_threshold')}, budget {_t(root, 'params/budget')}, " \
                     f"timeout {_t(root, 'params/timeout')}s; candidates: {fmt}"
    if root.tag in ("concord-init-reply", "concord-repair"):
        p = root.find("proposal")
        anchors = f"anchors {_t(root, 'anchors/raw_reject')}/{_t(root, 'anchors/raw_ideal')}; " if root.tag == "concord-init-reply" else ""
        return kind, f"{anchors}proposes {_t(p, 'candidate')} as {p.get('id')}, scores it {_t(p, 'score/raw')}"
    if root.tag == "concord-score":
        return kind, "rates " + ", ".join(f"{s.get('candidate_id')}={_t(s, 'raw')}" for s in root.findall("score"))
    if root.tag == "concord-result":
        return kind, f"{_t(root, 'outcome')}, winner {_t(root, 'winning_candidate_id')}"
    if root.tag == "concord-resend":
        return kind, f"carries a stored {el.tag.replace('concord-', '')} of {el.get('from')}"
    attrs = " ".join(f"{k}={v}" for k, v in root.attrib.items() if k in ("what", "reason", "to", "agent", "candidate"))
    return kind, attrs


def texts(text):
    """The free text of a message, by label: the rationale and the reasoning of a proposal, the reasoning of each score."""
    root, _ = parse(text)
    out = []
    for p in root.findall("proposal"):
        out += [("rationale", _t(p, "rationale")), ("reasoning", _t(p, "score/reasoning"))]
    for s in root.findall("score"):
        out.append((f"reasoning for {s.get('candidate_id')}", _t(s, "reasoning")))
    if root.tag == "concord-init":
        out += [("context", _t(root, "context")), ("x_reject", _t(root, "anchors/x_reject")), ("x_ideal", _t(root, "anchors/x_ideal"))]
    return [(label, t) for label, t in out if t]


def shorten(text, width=150):
    """The XML with every long text cut, to show the format and not the prose."""
    return re.sub(r">([^<>]{%d,})<" % (width + 1), lambda m: ">" + m.group(1).strip()[:width] + " […]<", text)


def timeline(msgs, show_text, show_xml):
    for m in msgs:
        kind, line = summary(m["text"])
        when = f"{m['t']:6.1f}s" if m["t"] is not None else "     ?"
        print(f"{when}  {m['from']:<14} -> all  {kind:<11} {line}")
        if show_text:
            for label, t in texts(m["text"]):
                print(f"{'':27}{label}:")
                print(textwrap.indent(textwrap.fill(t, 108), " " * 29))
        if show_xml:
            print(textwrap.indent(m["text"].strip(), " " * 8) + "\n")


def formats(msgs):
    seen = set()
    for m in msgs:
        kind, _ = summary(m["text"])
        if kind in seen:
            continue
        seen.add(kind)
        print(f"----- {kind}: sent by {m['from']}, shown as in this run (long texts cut)\n")
        print(shorten(m["text"].strip()) + "\n")


def mermaid(msgs):
    print("sequenceDiagram")
    for who in AGENTS:
        print(f"    participant {who[-1].upper()} as {who}")
    print("    participant S as SLIM group")
    for m in msgs:
        kind, line = summary(m["text"])
        label = f"{kind}: {line}".replace(";", ",")[:90]
        print(f"    {m['from'][-1].upper()}->>S: {label}")
    print("    Note over S: every message is delivered to every other agent")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", help="a run folder, such as runs/manual or runs/batch-<time>/t0.7-r0")
    p.add_argument("--text", action="store_true", help="show the rationale and the reasoning of each message")
    p.add_argument("--xml", action="store_true", help="show each message as it was sent")
    p.add_argument("--formats", action="store_true", help="show one example of each kind of message")
    p.add_argument("--mermaid", action="store_true", help="print a Mermaid sequence diagram")
    p.add_argument("--verify", action="store_true", help="check the run against the spec")
    args = p.parse_args()
    if args.verify:
        import verify
        result = verify.verify(args.run)
        verify.print_result(result, args.run)
        return 1 if result["failed"] else 0
    msgs = load(Path(args.run))
    if not msgs:
        sys.exit(f"no messages found in {args.run}: it needs the participant-X-state.json and participant-X.json files of a run")
    if args.formats:
        formats(msgs)
    elif args.mermaid:
        mermaid(msgs)
    else:
        timeline(msgs, args.text, args.xml)


if __name__ == "__main__":
    sys.exit(main())
