#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What a session is made of, and where it is kept. The state is a plain dict, so any store can hold it; `blank_state` is
the one place that says what is in it. `resolve_state_path` decides which file a given invocation belongs to."""
import os
import uuid
from pathlib import Path

DEFAULT_STATE = ".accord/state.json"


def resolve_state_path(path: str) -> str:
    """Resolve the effective state path for this invocation.

    `path` is whatever the caller passed (via --state in the skill script), or the $ACCORD_STATE / DEFAULT_STATE
    default -- the same literal value on every command in a session. Unmodified, that default is the same physical file
    for every concurrent or sequential run that uses this skill, so sessions silently overwrite each other.

    When ACCORD_RUN_KEY is set, this returns a deterministic, isolated path instead: a hash of the run key and the
    original filename, placed in ACCORD_STATE_DIR (or next to the original path if that is unset). Every command under
    the same run key maps to the same isolated path, so a caller doing record/replay sets ACCORD_RUN_KEY to something
    stable across both runs (for example a hash of the LLM cache path) to get byte-identical, collision-free state paths
    and real cache hits. When ACCORD_RUN_KEY is unset, `path` is returned unchanged -- today's default behavior,
    preserved for existing callers.
    """
    run_key = os.environ.get("ACCORD_RUN_KEY")
    if not run_key:
        return path
    p = Path(path)
    state_dir = Path(os.environ.get("ACCORD_STATE_DIR") or p.parent)
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_key}:{p.name}").hex[:8]
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / f"{p.stem}-{suffix}{p.suffix}")


def blank_state() -> dict:
    return {
        "specialists": [], "key_terms": [], "action_tool": "", "coverage_warnings": [],
        "roster": [], "obs": [], "frame": [], "glossary": [],
        "owned": {}, "uncovered": [], "checks": [], "defects": [],
        "frame_rounds": 0, "locked_contract": None,
        # Self-driving fields — only populated when init is called with --task.
        # Current phase is inferred from the per-phase trackers below (not stored explicitly).
        "task": "",
        "state_id": "",
        "handle_replies": {},  # {phase_key: {agent: raw_text}}
        "clarify_terms": [],   # populated by ground advance when ambiguous pairs exist
        "issued": {},          # {phase:agent -> True} prevents re-sending the same message
        "required_knowledge": [],
        "objective_clauses": [],
    }


def new_session(specialists: list[str], key_terms: list[str], action_tool: str = "", task: str = "") -> dict:
    """A fresh session. A `task` turns on the self-driving mode and mints the id every message carries."""
    st = blank_state()
    st["specialists"] = list(specialists)
    st["key_terms"] = list(key_terms)
    st["action_tool"] = action_tool or ""
    if task.strip():
        st["task"] = task.strip()
        st["state_id"] = uuid.uuid4().hex[:8]
    return st
