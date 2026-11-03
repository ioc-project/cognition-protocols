#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Copy the decision engine from protocols/concord/shared/concord_core.py into the initiator and receiver scripts.

    python3 protocols/concord/tools/sync_core.py            write the block into both scripts
    python3 protocols/concord/tools/sync_core.py --check    exit 1 if a script differs from the source (no change made)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "shared" / "concord_core.py"
TARGETS = [ROOT / "skills" / "concord-initiator" / "scripts" / "concord_initiator.py",
           ROOT / "skills" / "concord-receiver" / "scripts" / "concord_receiver.py"]
BLOCK = re.compile(r"# >>> SHARED CORE.*?# <<< END SHARED CORE\n", re.S)


def block_of(text, where):
    m = BLOCK.search(text)
    if not m:
        sys.exit(f"error: no SHARED CORE block in {where}")
    return m


def main():
    check = "--check" in sys.argv[1:]
    want = block_of(SOURCE.read_text(), SOURCE).group(0)
    drifted = []
    for target in TARGETS:
        text = target.read_text()
        m = block_of(text, target)
        if m.group(0) == want:
            continue
        drifted.append(target)
        if not check:
            target.write_text(text[:m.start()] + want + text[m.end():])
    if check:
        if drifted:
            print("The decision engine in these files differs from protocols/concord/shared/concord_core.py:")
            for t in drifted:
                print("  " + str(t.relative_to(ROOT)))
            print("Run: python3 protocols/concord/tools/sync_core.py")
            sys.exit(1)
        print("ok: both scripts carry exactly the engine in protocols/concord/shared/concord_core.py")
    else:
        print("updated: " + ", ".join(str(t.relative_to(ROOT)) for t in drifted) if drifted else "already in sync")


main()
