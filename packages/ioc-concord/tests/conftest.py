#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Each library module is tested twice over: as imported, and as bundled into every script that carries it."""
import importlib.util
import sys
import tomllib
import types
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


def bundled(module):
    """{script path: the module as it exists inside that script}: the bundled block, run on its own."""
    out = {}
    for lib in CONFIG["library"]:
        for target in lib["target"]:
            if module not in target["modules"]:
                continue
            block = bundle_tool.extract_block((ROOT / target["file"]).read_text(), lib["name"])
            ns = types.ModuleType(f"bundled_{Path(target['file']).stem}")
            exec(compile(block, target["file"], "exec"), ns.__dict__)
            out[target["file"]] = ns
    return out


def implementations(module):
    import importlib
    impls = {"library": importlib.import_module(f"ioc_concord.{module}")}
    impls.update({Path(f).name: ns for f, ns in bundled(module).items()})
    return impls


def _fixture(module):
    @pytest.fixture(params=list(implementations(module)), name=module)
    def fixture(request):
        return implementations(module)[request.param]
    return fixture


engine, xmlutil, schema, errors, wire, store, state = (_fixture(m) for m in (
    "engine", "xmlutil", "schema", "errors", "wire", "store", "state"))
