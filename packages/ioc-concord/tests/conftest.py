#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Each library module is tested twice over: as imported, and as bundled into every script that carries it."""
import importlib.util
import sys
import tomllib
import types
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _bundle_tool():
    spec = importlib.util.spec_from_file_location("bundle_tool", ROOT / "tools" / "bundle.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bundle_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


bundle_tool = _bundle_tool()
CONFIG = tomllib.loads((ROOT / "tools" / "bundle.toml").read_text())


# The modules of the library that a script can bundle, in dependency order. They are those of the library as it is on disk: not the
# list of tools/bundle.toml, which says what the scripts carry, at the revision they are pinned to.
MODULES = ["errors", "protocol", "xmlutil", "schema", "engine", "wire", "store", "state", "liveness", "tasks", "report", "relay", "agent"]


@lru_cache(maxsize=None)
def _bundles():
    """{script path: the library as it exists inside that script}: the bundled block of every script, run on its own.

    The library is the one on disk, with every module of it (MODULES), not the revision the scripts are pinned to: these tests are
    about the library as it is being written, and `just check-sync` is about the scripts. The block is rendered whole (no tree
    shaking), so every name the tests call is there. Scripts that would carry the same block are tested once (under the name of the
    first): running the same code twice would only slow the tests down."""
    out, seen = {}, set()
    for lib in CONFIG["library"]:
        for target in lib["target"]:
            block = bundle_tool.render({**lib, "ref": bundle_tool.WORKTREE}, {**target, "modules": MODULES},
                                       (ROOT / target["file"]).read_text(), shake=False)
            if block in seen:
                continue
            seen.add(block)
            ns = types.ModuleType(f"bundled_{Path(target['file']).stem}")
            exec(compile(block, target["file"], "exec"), ns.__dict__)
            out[target["file"]] = ns
    return out


def bundled(module):
    """{script path: the module as it exists inside that script}, for a module of the library that a script can bundle."""
    return _bundles() if module in MODULES else {}


@lru_cache(maxsize=None)
def implementations(module):
    """{name: the module}: the library, and each distinct bundled block. Built once per module for the whole run."""
    import importlib
    impls = {"library": importlib.import_module(f"ioc_concord.{module}")}
    impls.update({Path(f).name: ns for f, ns in bundled(module).items()})
    return impls


def _fixture(module):
    @pytest.fixture(params=list(implementations(module)), name=module)
    def fixture(request):
        return implementations(module)[request.param]
    return fixture


engine, xmlutil, schema, errors, protocol, wire, store, state, relay, agent = (_fixture(m) for m in (
    "engine", "xmlutil", "schema", "errors", "protocol", "wire", "store", "state", "relay", "agent"))
