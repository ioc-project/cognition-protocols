#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Each library module is tested twice over: as imported, and as bundled into every script that carries it."""
import importlib.util
import os
import sys
import tomllib
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LIBRARY = "ioc_accord"

# The merges cluster text by similarity. Embeddings would make the expected values depend on a model
# that may or may not be on this machine, so the tests run on the difflib comparison.
os.environ["ACCORD_EMBEDDING"] = "off"


def _bundle_tool():
    spec = importlib.util.spec_from_file_location("bundle_tool", ROOT / "tools" / "bundle.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bundle_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


bundle_tool = _bundle_tool()
CONFIG = tomllib.loads((ROOT / "tools" / "bundle.toml").read_text())
LIBS = [lib for lib in CONFIG["library"] if lib["name"] == LIBRARY]


def bundled(module):
    """{script path: the module as it exists inside that script}: the bundled block, run on its own.

    The block is rendered whole (no tree shaking), so every name the tests call is there. The script carries the same code
    minus what it never reaches, and test_bundled_scripts checks that it is exactly that."""
    out = {}
    for lib in LIBS:
        for target in lib["target"]:
            if module not in target["modules"]:
                continue
            block = bundle_tool.render(lib, target, (ROOT / target["file"]).read_text(), shake=False)
            ns = types.ModuleType(f"bundled_{Path(target['file']).stem}")
            exec(compile(block, target["file"], "exec"), ns.__dict__)
            out[target["file"]] = ns
    return out


def implementations(module):
    import importlib
    impls = {"library": importlib.import_module(f"{LIBRARY}.{module}")}
    impls.update({Path(f).name: ns for f, ns in bundled(module).items()})
    return impls


def _fixture(module):
    @pytest.fixture(params=list(implementations(module)), name=module)
    def fixture(request):
        return implementations(module)[request.param]
    return fixture


errors, similarity, ingest, engine, wire, state, store, flow = (_fixture(m) for m in (
    "errors", "similarity", "ingest", "engine", "wire", "state", "store", "flow"))


@pytest.fixture
def session():
    """A session mid-run: three specialists, a roster already merged, nothing else."""
    from ioc_accord.state import new_session
    st = new_session(["sre", "dba", "net"], ["severity", "error_rate"], "deploy", "why is checkout slow")
    return st
