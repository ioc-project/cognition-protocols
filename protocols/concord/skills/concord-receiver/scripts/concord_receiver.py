#!/usr/bin/env python3
"""CONCORD — receiver-side reply forger.

Each sub-command corresponds to exactly one incoming CONCORD message type and
produces exactly the one valid XML reply for that message type.  Plain text
arguments are accepted; XML escaping is handled by the script.

Any text argument may be passed as @path to read from a file.

Usage:

  Incoming <concord-anchor>        → reply with <concord-anchor-reply>:
    concord_receiver.py forge anchor --raw-reject N --raw-ideal N [--note TEXT]

  Incoming <concord-propose-seed>  → reply with <concord-candidate-reply>:
    concord_receiver.py forge seed --candidate TEXT --rationale TEXT

  Incoming <concord-score>         → reply with <concord-score-reply>:
    concord_receiver.py forge score \\
        --candidate-id ID --raw N --reasoning TEXT \\
        [--candidate-id ID2 --raw N2 --reasoning TEXT2 ...]

  Incoming <concord-repair>        → reply with <concord-candidate-reply>:
    concord_receiver.py forge repair --candidate TEXT --rationale TEXT

  Incoming <concord-commit>        → no reply; exits 0 with no output:
    concord_receiver.py forge commit

Stdlib only; no network, no dependencies.
"""

import argparse
import sys
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(value: str) -> str:
    """Resolve @path references by reading the file."""
    if isinstance(value, str) and value.startswith("@"):
        with open(value[1:], encoding="utf-8") as fh:
            return fh.read()
    return value


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def _emit(root: ET.Element) -> None:
    try:
        ET.indent(root, space="  ")  # Python 3.9+
    except AttributeError:
        pass
    print(ET.tostring(root, encoding="unicode"))


# ---------------------------------------------------------------------------
# One forger per incoming message type
# ---------------------------------------------------------------------------

def forge_anchor(args: argparse.Namespace) -> None:
    """<concord-anchor> → <concord-anchor-reply>"""
    # `not (a > b)` rather than `a <= b`, so a NaN is refused too.
    if not (args.raw_ideal > args.raw_reject):
        sys.exit(
            f"error: raw_ideal ({args.raw_ideal:g}) must be strictly greater than raw_reject "
            f"({args.raw_reject:g}). These are satisfaction ratings of the two outcomes (higher = better "
            "for your role), not measurements of the current state or metrics quoted in the anchors: "
            "re-rate the two outcomes, then run forge again."
        )
    root = ET.Element("concord-anchor-reply")
    _sub(root, "raw_reject", str(args.raw_reject))
    _sub(root, "raw_ideal", str(args.raw_ideal))
    if args.note:
        _sub(root, "note", _expand(args.note))
    _emit(root)


def forge_seed(args: argparse.Namespace) -> None:
    """<concord-propose-seed> → <concord-candidate-reply>"""
    root = ET.Element("concord-candidate-reply")
    _sub(root, "candidate", _expand(args.candidate))
    _sub(root, "rationale", _expand(args.rationale))
    _emit(root)


def forge_score(args: argparse.Namespace) -> None:
    """<concord-score> → <concord-score-reply>"""
    ids = args.candidate_id or []
    raws = args.raw or []
    reasonings = args.reasoning or []

    if not ids:
        sys.exit("error: at least one --candidate-id / --raw / --reasoning triplet is required")
    if not (len(ids) == len(raws) == len(reasonings)):
        sys.exit(
            "error: --candidate-id, --raw, and --reasoning must each appear "
            "the same number of times (one per candidate)"
        )

    root = ET.Element("concord-score-reply")
    for cid, raw, reasoning in zip(ids, raws, reasonings):
        score = ET.SubElement(root, "score", attrib={"candidate_id": cid})
        _sub(score, "raw", str(raw))
        _sub(score, "reasoning", _expand(reasoning))
    _emit(root)


def forge_repair(args: argparse.Namespace) -> None:
    """<concord-repair> → <concord-candidate-reply> (rationale required)"""
    root = ET.Element("concord-candidate-reply")
    _sub(root, "candidate", _expand(args.candidate))
    _sub(root, "rationale", _expand(args.rationale))
    _emit(root)


def forge_commit(_args: argparse.Namespace) -> None:
    """<concord-commit> → no reply; exits 0 silently."""
    pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_FORGERS = {
    "anchor": forge_anchor,
    "seed": forge_seed,
    "score": forge_score,
    "repair": forge_repair,
    "commit": forge_commit,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concord_receiver.py",
        description="CONCORD receiver-side reply forger.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    forge_p = sub.add_parser(
        "forge",
        help="Forge the reply for an incoming CONCORD message.",
    )
    forge_sub = forge_p.add_subparsers(
        dest="msg_type",
        required=True,
        metavar="{anchor,seed,score,repair,commit}",
    )

    # anchor — reply to <concord-anchor>
    ap = forge_sub.add_parser("anchor", help="Reply to <concord-anchor>.")
    ap.add_argument("--raw-reject", type=float, required=True, metavar="N",
                    help="Your raw rating for x_reject (0-100).")
    ap.add_argument("--raw-ideal", type=float, required=True, metavar="N",
                    help="Your raw rating for x_ideal (0-100).")
    ap.add_argument("--note", default="",
                    help="Optional note. It does not bypass the raw_ideal > raw_reject check.")

    # seed — reply to <concord-propose-seed>
    sp = forge_sub.add_parser("seed", help="Reply to <concord-propose-seed>.")
    sp.add_argument("--candidate", required=True,
                    help="Your proposed candidate (schema text or @file).")
    sp.add_argument("--rationale", required=True,
                    help="Supporting evidence and reasoning for your proposal (@file ok).")

    # score — reply to <concord-score>
    scp = forge_sub.add_parser("score", help="Reply to <concord-score>.")
    scp.add_argument("--candidate-id", action="append", metavar="ID",
                     help="Candidate id — repeat once per candidate, in order.")
    scp.add_argument("--raw", type=float, action="append", metavar="N",
                     help="Raw score 0-100 — repeat in the same order.")
    scp.add_argument("--reasoning", action="append", metavar="TEXT",
                     help="Your full reasoning for the score — repeat in the same order (@file ok).")

    # repair — reply to <concord-repair>
    rp = forge_sub.add_parser("repair", help="Reply to <concord-repair>.")
    rp.add_argument("--candidate", required=True,
                    help="Your new candidate (schema text or @file).")
    rp.add_argument("--rationale", required=True,
                    help="What changed and why it stays compatible with other agents' floors.")

    # commit — reply to <concord-commit> (no reply needed)
    forge_sub.add_parser("commit", help="Reply to <concord-commit> — no output.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "forge":
        _FORGERS[args.msg_type](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
