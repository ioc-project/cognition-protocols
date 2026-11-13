#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What a session holds, and which file it belongs to."""
from conftest import state  # noqa: F401


def test_a_blank_session_starts_every_phase_empty(state):  # noqa: F811
    st = state.blank_state()
    assert st["roster"] == [] and st["frame"] == [] and st["glossary"] == []
    assert st["frame_rounds"] == 0 and st["locked_contract"] is None
    assert st["task"] == "" and st["state_id"] == ""
    assert st["issued"] == {} and st["issued_checks"] == {} and st["check_fail"] == {}


def test_a_session_without_a_task_is_not_self_driving(state):  # noqa: F811
    st = state.new_session(["sre", "dba"], ["severity"], "deploy")
    assert st["specialists"] == ["sre", "dba"] and st["key_terms"] == ["severity"]
    assert st["action_tool"] == "deploy"
    assert st["task"] == "" and st["state_id"] == ""


def test_a_task_mints_the_id_every_message_carries(state):  # noqa: F811
    st = state.new_session(["sre", "dba"], [], task="  why is checkout slow  ")
    assert st["task"] == "why is checkout slow"
    assert len(st["state_id"]) == 8


def test_without_a_run_key_the_path_is_the_one_the_caller_gave(state, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ACCORD_RUN_KEY", raising=False)
    assert state.resolve_state_path(".accord/state.json") == ".accord/state.json"


def test_a_run_key_maps_every_command_of_a_session_to_one_isolated_file(state, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("ACCORD_RUN_KEY", "replay-7")
    monkeypatch.setenv("ACCORD_STATE_DIR", str(tmp_path / "states"))
    first = state.resolve_state_path(".accord/state.json")
    assert first == state.resolve_state_path(".accord/state.json")
    assert first != ".accord/state.json"
    assert (tmp_path / "states").is_dir()

    monkeypatch.setenv("ACCORD_RUN_KEY", "replay-8")
    assert state.resolve_state_path(".accord/state.json") != first
