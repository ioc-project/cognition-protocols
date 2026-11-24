#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Loading and saving a session."""
import json

import pytest

from conftest import store  # noqa: F401


def test_a_session_that_was_never_saved_loads_blank(store, tmp_path):  # noqa: F811
    st = store.load_state(tmp_path / "state.json")
    assert st["roster"] == [] and st["frame_rounds"] == 0


def test_saving_creates_the_directory_and_the_state_survives_the_round_trip(store, tmp_path):  # noqa: F811
    path = tmp_path / ".accord" / "state.json"
    store.save_state(path, {"specialists": ["sre"], "frame_rounds": 2})
    assert store.load_state(path)["specialists"] == ["sre"]


def test_a_field_added_after_a_state_was_written_is_filled_in(store, tmp_path):  # noqa: F811
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"specialists": ["sre"]}))
    st = store.load_state(path)
    assert st["specialists"] == ["sre"] and st["handle_replies"] == {} and st["locked_contract"] is None


def test_locking_a_session_that_does_not_exist_is_an_error(store, tmp_path):  # noqa: F811
    with pytest.raises(Exception) as exc:
        with store.locked_state(tmp_path / "state.json"):
            pass
    assert exc.value.code == "state_error"
    assert "run init first" in str(exc.value)


def test_a_locked_mutation_is_written_back_whole(store, tmp_path):  # noqa: F811
    path = tmp_path / "state.json"
    store.save_state(path, {"specialists": ["sre", "dba", "net"], "handle_replies": {"convene": {"sre": "x" * 500}}})
    with store.locked_state(path) as st:
        st["handle_replies"] = {}
        st["roster"] = [{"identity": "sre"}]
    reloaded = store.load_state(path)
    assert reloaded["roster"] == [{"identity": "sre"}]
    assert reloaded["handle_replies"] == {}          # the file was truncated, not overwritten in place


def test_a_failed_call_records_nothing(store, tmp_path):  # noqa: F811
    path = tmp_path / "state.json"
    store.save_state(path, {"roster": []})
    with pytest.raises(ValueError):
        with store.locked_state(path) as st:
            st["roster"] = [{"identity": "sre"}]
            raise ValueError("something went wrong half way")
    assert store.load_state(path)["roster"] == []


def test_a_refusal_leaves_its_bookkeeping_behind(store, tmp_path):  # noqa: F811
    """The count of unverifiable attempts has to survive the refusal, or the next attempt starts from zero."""
    path = tmp_path / "state.json"
    store.save_state(path, {"check_fail": {}})
    with pytest.raises(Exception) as exc:
        with store.locked_state(path) as st:
            st["check_fail"]["convene:sre"] = 1
            raise store.AccordRefusal("no check line")
    assert exc.value.code == "refused"
    assert store.load_state(path)["check_fail"] == {"convene:sre": 1}
