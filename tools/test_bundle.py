#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The bundler's rules, on a small library built in a temp directory.

    uv run --no-project --with pytest python -m pytest tools
"""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle  # noqa: E402

SCRIPT = "# top\nimport os\n\n# >>> BUNDLED lib\n# <<< END BUNDLED lib\n\ndef own():\n    return 1\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "ROOT", tmp_path)
    (tmp_path / "pkg/src/lib").mkdir(parents=True)
    (tmp_path / "pkg/pyproject.toml").write_text('[project]\nname = "lib"\nversion = "1.2.3"\n')
    (tmp_path / "script.py").write_text(SCRIPT)

    def write(name, src):
        (tmp_path / "pkg/src/lib" / f"{name}.py").write_text(textwrap.dedent(src))

    def config(*modules):
        return tmp_path / "bundle.toml", {"name": "lib", "path": "pkg", "target": [{"file": "script.py", "modules": list(modules)}]}

    def run(*modules, check=False, shake=False, script=None):
        if script:
            (tmp_path / "script.py").write_text(SCRIPT.replace("return 1", script))
        path, lib = config(*modules)
        path.write_text("[[library]]\nname = 'lib'\npath = 'pkg'\n[[library.target]]\nfile = 'script.py'\n"
                        f"modules = {list(modules)!r}\nshake = {str(shake).lower()}\n")
        return bundle.main(["--config", str(path)] + (["--check"] if check else []))

    write.run, write.root = run, tmp_path
    return write


def test_bundles_a_module_without_its_docstring_imports_and_all(repo, capsys):
    repo("a", '''
        """Docstring."""
        import json
        __all__ = ["f"]

        K = 1

        def f():
            return json.dumps(K)
        ''')
    assert repo.run("a") == 0
    out = (repo.root / "script.py").read_text()
    assert "# >>> BUNDLED lib 1.2.3 [a] sha256:" in out and "# <<< END BUNDLED lib\n" in out
    assert "import json\n" in out and "def f():" in out
    assert "Docstring" not in out and "__all__" not in out
    assert out.startswith("# top\nimport os\n") and out.endswith("def own():\n    return 1\n")
    ns = {}
    exec(bundle.extract_block(out, "lib"), ns)
    assert ns["f"]() == "1"


def test_check_passes_when_in_sync_and_fails_after_the_library_changes(repo, capsys):
    repo("a", "def f():\n    return 1\n")
    assert repo.run("a") == 0 and repo.run("a", check=True) == 0
    repo("a", "def f():\n    return 2\n")
    assert repo.run("a", check=True) == 1
    assert "just sync" in capsys.readouterr().err
    assert repo.run("a") == 0 and repo.run("a", check=True) == 0


def test_running_twice_changes_nothing(repo):
    repo("a", "def f():\n    return 1\n")
    repo.run("a")
    first = (repo.root / "script.py").read_text()
    repo.run("a")
    assert (repo.root / "script.py").read_text() == first


def test_dependencies_come_first_and_must_be_listed(repo, capsys):
    repo("a", "def base():\n    return 1\n")
    repo("b", "from lib.a import base\n\ndef top():\n    return base() + 1\n")
    assert repo.run("b", "a") == 0
    out = (repo.root / "script.py").read_text()
    assert out.index("def base") < out.index("def top")
    assert repo.run("b") == 2 and 'add "a"' in capsys.readouterr().err


def test_an_import_of_a_name_the_module_lacks_is_refused(repo, capsys):
    repo("a", "def base():\n    return 1\n")
    repo("b", "from lib.a import nope\n")
    assert repo.run("a", "b") == 2 and "nope" in capsys.readouterr().err


def test_import_cycles_are_refused(repo, capsys):
    repo("a", "from lib.b import g\n\ndef f():\n    return g()\n")
    repo("b", "from lib.a import f\n\ndef g():\n    return f()\n")
    assert repo.run("a", "b") == 2 and "cycle" in capsys.readouterr().err


@pytest.mark.parametrize("src, why", [
    ("import requests\n", "not in the standard library"),
    ("from . import a\n", "relative import"),
    ("import lib\n", "from lib.<module> import"),
    ("from lib import a\n", "from lib.<module> import"),
    ("from lib.a import *\n", "import *"),
    ("from __future__ import annotations\n", "__future__"),
])
def test_unsupported_imports_are_refused(repo, capsys, src, why):
    repo("a", "def f():\n    return 1\n")
    repo("b", src)
    assert repo.run("a", "b") == 2 and why in capsys.readouterr().err


def test_a_name_defined_twice_across_modules_is_refused(repo, capsys):
    repo("a", "def f():\n    return 1\n")
    repo("b", "def f():\n    return 2\n")
    assert repo.run("a", "b") == 2 and "`f` is defined in both" in capsys.readouterr().err


def test_a_name_the_script_defines_itself_is_refused(repo, capsys):
    repo("a", "def own():\n    return 2\n")
    assert repo.run("a") == 2 and "defines own outside the bundled block" in capsys.readouterr().err


def test_a_script_without_markers_is_told_what_to_add(repo, capsys):
    (repo.root / "script.py").write_text("print('x')\n")
    repo("a", "def f():\n    return 1\n")
    assert repo.run("a") == 2
    assert "# >>> BUNDLED lib" in capsys.readouterr().err


SHAKEN = """
    import json
    import re

    A = 1
    B = [A]
    UNUSED = 2

    # a helper
    @staticmethod
    def helper():
        return json.dumps(B)

    def dead():
        return re.compile("x")

    class Unused:
        pass

    def entry():
        return helper()
    """


def test_a_script_gets_only_what_it_reaches(repo):
    repo("a", SHAKEN)
    assert repo.run("a", shake=True, script="entry()") == 0
    out = (repo.root / "script.py").read_text()
    for kept in ("import json", "A = 1", "B = [A]", "# a helper", "def helper", "def entry"):
        assert kept in out
    for left_out in ("import re", "UNUSED", "def dead", "Unused"):
        assert left_out not in out
    assert "\n\n\n\n" not in out
    assert repo.run("a", shake=True, check=True) == 0


def test_shake_false_bundles_everything(repo):
    repo("a", SHAKEN)
    assert repo.run("a", shake=False, script="entry()") == 0
    out = (repo.root / "script.py").read_text()
    assert "def dead" in out and "import re" in out


def test_reachability_follows_modules_and_statements_that_stay(repo):
    repo("a", "def f():\n    return 1\n\ndef g():\n    return 2\n")
    repo("b", "from lib.a import f\nimport json\nREG = []\nREG.append(json)\n\ndef h():\n    return f()\n")
    assert repo.run("a", "b", shake=True, script="h()") == 0
    out = (repo.root / "script.py").read_text()
    assert "def f" in out and "def h" in out and "def g" not in out
    assert "import json" in out and "REG.append(json)" in out     # an expression statement is never left out
    ns = {}
    exec(bundle.extract_block(out, "lib"), ns)
    assert ns["h"]() == 1


def test_a_module_nothing_reaches_is_left_out_whole(repo):
    repo("a", "def f():\n    return 1\n")
    repo("b", "def g():\n    return 2\n")
    assert repo.run("a", "b", shake=True, script="f()") == 0
    out = (repo.root / "script.py").read_text()
    assert "[a]" in out and "lib.b" not in out
