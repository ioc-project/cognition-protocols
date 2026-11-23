#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The bundler's rules, on a small library built in a temp directory.

    uv run --no-project --with pytest python -m pytest tools
"""
import subprocess
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

    def run(*modules, check=False, shake=False, script=None, ref=None, args=()):
        if script:
            (tmp_path / "script.py").write_text(SCRIPT.replace("return 1", script))
        path, lib = config(*modules)
        pin = f"ref = {ref!r}\n" if ref else ""
        path.write_text(f"[[library]]\nname = 'lib'\npath = 'pkg'\n{pin}[[library.target]]\nfile = 'script.py'\n"
                        f"modules = {list(modules)!r}\nshake = {str(shake).lower()}\n")
        return bundle.main(["--config", str(path)] + (["--check"] if check else []) + list(args))

    def commit():
        """Commit the library as it is, in a git repository made here; the id of the commit."""
        git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        if not (tmp_path / ".git").exists():
            subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "pkg"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "lib"], check=True)
        return subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    write.run, write.root, write.commit = run, tmp_path, commit
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


# --- a library pinned to a git revision ---------------------------------------------------------------------

def test_a_pinned_library_is_read_from_its_revision_and_not_from_disk(repo, capsys):
    repo("one", "X = 1\n")
    pin = repo.commit()
    assert repo.run("one", ref=pin) == 0                                  # the script now carries the pinned version
    repo("one", "X = 2\n")                                                 # the library moves on, uncommitted
    assert "X = 1" in (repo.root / "script.py").read_text()
    assert repo.run("one", ref=pin, check=True) == 0                      # still in sync with the pin
    assert repo.run("one", check=True) == 1                               # but not with the files on disk
    assert repo.run("one", ref=pin) == 0 and "X = 1" in (repo.root / "script.py").read_text()


def test_ref_on_the_command_line_overrides_the_pin(repo):
    repo("one", "X = 1\n")
    pin = repo.commit()
    assert repo.run("one", ref=pin) == 0
    repo("one", "X = 2\n")
    assert repo.run("one", ref=pin, check=True, args=["--ref", "worktree"]) == 1      # the files on disk differ
    assert repo.run("one", ref=pin, args=["--ref", "worktree"]) == 0
    assert "X = 2" in (repo.root / "script.py").read_text()
    assert repo.run("one", ref=pin, check=True, args=["--ref", "worktree"]) == 0
    assert repo.run("one", ref="nonsense", check=True, args=["--ref", "HEAD"]) == 1   # HEAD is the commit, not the disk


def test_the_version_in_the_block_comes_from_the_pinned_revision(repo):
    repo("one", "X = 1\n")
    pin = repo.commit()
    (repo.root / "pkg/pyproject.toml").write_text('[project]\nname = "lib"\nversion = "9.9.9"\n')
    assert repo.run("one", ref=pin) == 0
    assert "BUNDLED lib 1.2.3 " in (repo.root / "script.py").read_text()
    assert repo.run("one", args=["--ref", "worktree"]) == 0
    assert "BUNDLED lib 9.9.9 " in (repo.root / "script.py").read_text()


def test_a_revision_the_clone_does_not_have_is_explained(repo, capsys):
    repo("one", "X = 1\n")
    repo.commit()
    assert repo.run("one", ref="0" * 40, check=True) == 2
    err = capsys.readouterr().err
    assert "pinned to " + "0" * 40 in err and "git fetch --tags" in err


def test_a_module_that_was_not_in_the_pinned_revision_is_named_with_the_revision(repo, capsys):
    repo("one", "X = 1\n")
    pin = repo.commit()
    repo("two", "Y = 1\n")                                                 # added after the pin
    assert repo.run("one", "two", ref=pin) == 2
    assert f"lib.two: no such module (pkg/src/lib/two.py at {pin})" in capsys.readouterr().err


def test_a_tag_can_be_the_pin(repo):
    repo("one", "X = 1\n")
    repo.commit()
    subprocess.run(["git", "-C", str(repo.root), "tag", "lib-v1"], check=True)
    repo("one", "X = 2\n")
    assert repo.run("one", ref="lib-v1") == 0 and "X = 1" in (repo.root / "script.py").read_text()
    assert repo.run("one", ref="lib-v1", check=True) == 0


# --- a library that builds on another one in the same script (`uses`) -----------------------------------------------------------

TWO = "import os\n\n# >>> BUNDLED lib\n# <<< END BUNDLED lib\n\n# >>> BUNDLED cli\n# <<< END BUNDLED cli\n\nprint(run())\n"


@pytest.fixture
def two(tmp_path, monkeypatch):
    """A library `lib` (with a version) and a source-only library `cli` that uses it, bundled into one script."""
    monkeypatch.setattr(bundle, "ROOT", tmp_path)
    (tmp_path / "pkg/src/lib").mkdir(parents=True)
    (tmp_path / "cli/src/cli").mkdir(parents=True)
    (tmp_path / "pkg/pyproject.toml").write_text('[project]\nname = "lib"\nversion = "1.2.3"\n')
    (tmp_path / "script.py").write_text(TWO)
    (tmp_path / "pkg/src/lib/a.py").write_text("def f():\n    return 'f'\n\ndef unused():\n    return 'u'\n")
    (tmp_path / "cli/src/cli/c.py").write_text("from lib.a import f\n\ndef run():\n    return f() + '!'\n")

    def sync(uses='uses = ["lib"]', check=False, cli_modules=("c",), lib_uses="", lib_file="script.py", cli_source_only="source_only = true",
             lib_modules=("a",)):
        (tmp_path / "bundle.toml").write_text(
            f"[[library]]\nname = 'lib'\npath = 'pkg'\n{lib_uses}\n[[library.target]]\nfile = '{lib_file}'\nmodules = {list(lib_modules)!r}\n\n"
            f"[[library]]\nname = 'cli'\npath = 'cli'\n{cli_source_only}\n{uses}\n[[library.target]]\nfile = 'script.py'\nmodules = {list(cli_modules)!r}\n")
        return bundle.main(["--config", str(tmp_path / "bundle.toml")] + (["--check"] if check else []))

    sync.root = tmp_path
    return sync


def test_a_library_can_import_the_names_of_the_one_it_uses(two, capsys):
    assert two() == 0
    out = (two.root / "script.py").read_text()
    ns = {}
    exec(compile(out.replace("print(run())", ""), "script.py", "exec"), ns)
    assert ns["run"]() == "f!"
    assert "from lib.a import" not in out                       # the import is not copied: the names are in the other block
    assert "# >>> BUNDLED lib 1.2.3 [a]" in out and "# >>> BUNDLED cli [c] sha256:" in out     # a source-only library has no version


def test_what_the_user_library_reaches_keeps_the_names_of_the_other_one_in_a_single_run(two):
    two()
    out = (two.root / "script.py").read_text()
    assert "def f(" in out and "def unused(" not in out         # f is kept only because the cli block uses it
    before = out
    assert two() == 0 and (two.root / "script.py").read_text() == before     # and a second run changes nothing
    assert two(check=True) == 0


def test_a_check_fails_when_a_block_of_the_other_library_is_stale(two, capsys):
    two()
    (two.root / "cli/src/cli/c.py").write_text("from lib.a import f, unused\n\ndef run():\n    return f() + unused()\n")
    assert two(check=True) == 1
    assert "script.py" in capsys.readouterr().err
    assert two() == 0 and "def unused(" in (two.root / "script.py").read_text()


def test_without_uses_an_import_of_the_other_library_is_refused(two, capsys):
    assert two(uses="") == 2
    assert "not in the standard library" in capsys.readouterr().err


def test_a_user_library_must_import_names_of_a_module(two, capsys):
    (two.root / "cli/src/cli/c.py").write_text("from lib import f\n\ndef run():\n    return f()\n")
    assert two() == 2
    assert "from lib.<module> import <names>" in capsys.readouterr().err


def test_a_name_in_both_libraries_is_refused(two, capsys):
    # `unused` is left out of the block of lib (nothing reaches it), and it still counts: a name is unique across what each library defines
    (two.root / "cli/src/cli/c.py").write_text("from lib.a import f\n\ndef f2():\n    return 1\n\ndef unused():\n    return 2\n\n"
                                              "def run():\n    return f() + unused()\n")
    assert two() == 2
    assert "unused" in capsys.readouterr().err


# --- what is checked before anything is written -----------------------------------------------------------------------------------

def test_a_script_without_the_block_of_the_user_library_is_told_what_to_add(two, capsys):
    (two.root / "script.py").write_text(TWO.replace("# >>> BUNDLED cli\n# <<< END BUNDLED cli\n", ""))
    assert two() == 2
    err = capsys.readouterr().err
    assert "no markers" in err and "# >>> BUNDLED cli" in err


@pytest.mark.parametrize("uses, why", [
    ('uses = "lib"', "`uses` must be a list of library names"),
    ('uses = ["lib", 3]', "`uses` must be a list of library names"),
    ('uses = ["nope"]', "cli uses nope, which is not a library of this config"),
    ('uses = ["cli"]', "cli lists itself in `uses`"),
])
def test_a_uses_that_is_not_a_list_of_other_libraries_is_refused_and_nothing_is_written(two, capsys, uses, why):
    assert two(uses=uses) == 2
    assert why in capsys.readouterr().err
    assert (two.root / "script.py").read_text() == TWO


def test_libraries_that_use_each_other_are_refused(two, capsys):
    assert two(lib_uses='uses = ["cli"]') == 2
    assert "libraries that use each other: lib -> cli -> lib" in capsys.readouterr().err


def test_a_script_that_bundles_a_library_but_not_the_one_it_uses_is_refused_before_any_script_is_written(two, capsys):
    (two.root / "other.py").write_text("# >>> BUNDLED lib\n# <<< END BUNDLED lib\n")       # lib goes into other.py, cli into script.py
    assert two(lib_file="other.py") == 2
    assert "script.py: cli is bundled here and uses lib, which is not: add a target for lib in this file" in capsys.readouterr().err
    assert (two.root / "script.py").read_text() == TWO
    assert (two.root / "other.py").read_text() == "# >>> BUNDLED lib\n# <<< END BUNDLED lib\n"


def test_the_block_of_the_used_library_must_come_first_in_the_script(two, capsys):
    swapped = "# >>> BUNDLED cli\n# <<< END BUNDLED cli\n\n# >>> BUNDLED lib\n# <<< END BUNDLED lib\n\nprint(run())\n"
    (two.root / "script.py").write_text(swapped)
    assert two() == 2
    assert "the block of lib must come before the block of cli, which uses it" in capsys.readouterr().err
    assert (two.root / "script.py").read_text() == swapped
    (two.root / "script.py").write_text(TWO)                                  # in order, it is bundled
    assert two() == 0


# --- what a library that builds on another takes from it ---------------------------------------------------------------------------

def test_a_module_the_used_library_does_not_carry_in_the_script_is_refused(two, capsys):
    (two.root / "pkg/src/lib/b.py").write_text("def g():\n    return 'g'\n")
    (two.root / "cli/src/cli/c.py").write_text("from lib.b import g\n\ndef run():\n    return g()\n")
    assert two() == 2                                                        # lib is bundled with `a` only
    assert 'cli.c imports lib.b, which is not in `modules` of lib for this script: add "b"' in capsys.readouterr().err
    assert (two.root / "script.py").read_text() == TWO
    assert two(lib_modules=("a", "b")) == 0                                  # carried, it is bundled, and the script runs
    ns = {}
    exec(compile((two.root / "script.py").read_text().replace("print(run())", ""), "script.py", "exec"), ns)
    assert ns["run"]() == "g"


def test_a_name_the_used_library_does_not_define_is_refused(two, capsys):
    (two.root / "cli/src/cli/c.py").write_text("from lib.a import nothere\n\ndef run():\n    return nothere()\n")
    assert two() == 2
    assert "cli.c imports nothere from lib.a, which does not define it" in capsys.readouterr().err
    assert (two.root / "script.py").read_text() == TWO


# --- the version -----------------------------------------------------------------------------------------------------------------

def test_a_library_without_a_pyproject_must_say_that_it_is_only_source(two, capsys):
    assert two(cli_source_only="") == 2
    err = capsys.readouterr().err
    assert "cli: no pyproject.toml (cli/pyproject.toml) to take a version from" in err and "source_only = true" in err


def test_a_pyproject_without_a_version_or_that_is_not_toml_is_an_error_and_not_a_traceback(two, capsys):
    (two.root / "pkg/pyproject.toml").write_text('[project]\nname = "lib"\n')
    assert two() == 2
    assert "lib: pkg/pyproject.toml has no [project].version" in capsys.readouterr().err
    (two.root / "pkg/pyproject.toml").write_text("not = [toml")
    assert two() == 2
    assert "pkg/pyproject.toml is not valid TOML" in capsys.readouterr().err


# --- the markers -----------------------------------------------------------------------------------------------------------------

def test_the_markers_of_a_library_are_not_those_of_one_whose_name_starts_the_same(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "ROOT", tmp_path)
    for rel, text in {"p1/pyproject.toml": '[project]\nname = "a"\nversion = "1.0.0"\n', "p1/src/a/x.py": "def fx():\n    return 1\n",
                      "p2/pyproject.toml": '[project]\nname = "ab"\nversion = "2.0.0"\n', "p2/src/a-b/y.py": "def fy():\n    return 2\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (tmp_path / "script.py").write_text("# >>> BUNDLED a-b\n# <<< END BUNDLED a-b\n\n# >>> BUNDLED a\n# <<< END BUNDLED a\n\nresult = (fx(), fy())\n")
    (tmp_path / "bundle.toml").write_text(
        "[[library]]\nname = 'a'\npath = 'p1'\n[[library.target]]\nfile = 'script.py'\nmodules = ['x']\n"
        "[[library]]\nname = 'a-b'\npath = 'p2'\n[[library.target]]\nfile = 'script.py'\nmodules = ['y']\n")
    assert bundle.main(["--config", str(tmp_path / "bundle.toml")]) == 0         # the block of `a` is not taken for the one of `a-b`
    text = (tmp_path / "script.py").read_text()
    assert text.count("def fx") == 1 and text.count("def fy") == 1
    assert text.index("# >>> BUNDLED a-b 2.0.0") < text.index("# >>> BUNDLED a 1.0.0")
    ns = {}
    exec(compile(text, "script.py", "exec"), ns)
    assert ns["result"] == (1, 2)


# --- a config that is wrong, and a run -----------------------------------------------------------------------------------------------

LIB_OK = "[[library]]\nname = 'lib'\npath = 'pkg'\n[[library.target]]\nfile = 'script.py'\nmodules = ['a']\n"


@pytest.mark.parametrize("toml, why", [
    ("", "the config has no [[library]]"),
    ("[[library]]\npath = 'pkg'\n", "a [[library]] has no `name`"),
    ("[[library]]\nname = 'lib'\n", "lib: no `path`"),
    ("[[library]]\nname = 'lib'\npath = 'pkg'\n", "lib: no [[library.target]]"),
    ("[[library]]\nname = 'lib'\npath = 'pkg'\n[[library.target]]\nmodules = ['a']\n", "lib: a [[library.target]] has no `file`"),
    ("[[library]]\nname = 'lib'\npath = 'pkg'\n[[library.target]]\nfile = 'script.py'\n", "the target script.py has no `modules`"),
    (LIB_OK + LIB_OK, "two [[library]] are called lib"),
    (LIB_OK.replace("script.py", "nope.py"), "nope.py: no such script (lib is to be bundled into it)"),
])
def test_a_config_that_is_wrong_is_an_error_that_says_what_is_missing(two, capsys, toml, why):
    path = two.root / "wrong.toml"
    path.write_text(toml)
    assert bundle.main(["--config", str(path)]) == 2                         # an error, never a traceback
    assert why in capsys.readouterr().err


def test_a_run_reads_each_file_of_a_library_once(two, monkeypatch):
    reads, real = [], bundle._read_source
    monkeypatch.setattr(bundle, "_read_source", lambda lib, rel: (reads.append(rel), real(lib, rel))[1])
    assert two() == 0
    assert reads and len(reads) == len(set(reads))                           # though every script is rendered more than once
    assert bundle._READ is None                                              # and nothing is kept after the run

