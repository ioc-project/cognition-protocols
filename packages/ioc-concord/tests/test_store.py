#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The file store, as imported and as bundled into each script (see conftest.py)."""
import json
import os
import stat

import pytest


def test_a_saved_state_loads_back(store, tmp_path):
    s = store.FileStore(tmp_path / "state.json")
    s.save({"a": [1, 2], "b": {"c": "é"}})
    assert s.exists() and s.load() == {"a": [1, 2], "b": {"c": "é"}}
    assert (tmp_path / "state.json").read_text().endswith("}\n")


def test_the_indent_is_the_callers_choice(store, tmp_path):
    store.FileStore(tmp_path / "two.json").save({"k": 1})
    store.FileStore(tmp_path / "one.json", indent=1).save({"k": 1})
    assert (tmp_path / "two.json").read_text() == '{\n  "k": 1\n}\n'
    assert (tmp_path / "one.json").read_text() == '{\n "k": 1\n}\n'


def test_loading_a_state_that_is_not_there_says_where(store, tmp_path):
    s = store.FileStore(tmp_path / "nope.json")
    assert not s.exists()
    with pytest.raises(store.StateError, match="no state file at .*nope.json") as e:
        s.load()
    assert e.value.code == "state_error"


def test_a_save_replaces_the_state_and_leaves_nothing_behind(store, tmp_path):
    s = store.FileStore(tmp_path / "state.json")
    s.save({"v": 1})
    s.save({"v": 2})
    assert s.load() == {"v": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_state_that_cannot_be_saved_leaves_the_old_one_untouched(store, tmp_path):
    s = store.FileStore(tmp_path / "state.json")
    s.save({"v": 1})
    with pytest.raises(TypeError):
        s.save({"v": object()})
    assert s.load() == {"v": 1} and [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_failed_rename_cleans_up_its_temporary_file(store, tmp_path, monkeypatch):
    s = store.FileStore(tmp_path / "state.json")
    s.save({"v": 1})
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        s.save({"v": 2})
    assert s.load() == {"v": 1} and [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_the_file_keeps_the_permissions_a_plain_write_would_give(store, tmp_path):
    plain = tmp_path / "plain.json"
    plain.write_text("{}")
    s = store.FileStore(tmp_path / "state.json")
    s.save({})
    assert stat.S_IMODE(os.stat(s.path).st_mode) == stat.S_IMODE(os.stat(plain).st_mode)


def test_a_file_store_is_a_store(store, tmp_path):
    assert isinstance(json.loads("{}"), dict)
    for method in ("load", "save"):
        assert callable(getattr(store.FileStore(tmp_path / "x.json"), method))
        assert method in vars(store.Store)
