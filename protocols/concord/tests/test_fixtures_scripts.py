#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The cross-implementation fixtures (protocols/concord/spec/fixtures) at the level of a script.

The library is tested against these files as it is imported and as it is bundled (packages/ioc-concord/tests/test_fixtures.py): what a
message means, what is refused, the check. That says nothing about the script: that it reads a message from standard input whatever
the message holds, that it never crashes on one that is wrong, that a wrong message changes nothing and is not answered, and that a
message with an odd character in it comes out as it went in. That is what is tested here, by handing each fixture to `receive`.

The cases run the script in this process, as `__main__` (the real wiring, with no process for each case); the scenarios of
test_scripts.py run it as a process.
"""
import io
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from driver import RECEIVER  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "spec" / "fixtures"
WIRE = FIXTURES / "wire"
REFUSED = json.loads((WIRE / "refused.json").read_text(encoding="utf-8"))
VALID = json.loads((WIRE / "valid.json").read_text(encoding="utf-8"))
TRICKY = sorted(p.name for p in (WIRE / "tricky").glob("*.xml"))


def text(rel):
    return (FIXTURES / rel).read_text(encoding="utf-8")


@pytest.fixture
def invoke(monkeypatch, capsys):
    """Run the receiver script, as `__main__`, in a directory: (exit code, what it printed, what it wrote on the error stream)."""
    def run(cwd, me, command, stdin=""):
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", [str(RECEIVER), "--me", me, command])
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        with pytest.raises(SystemExit) as stop:
            runpy.run_path(str(RECEIVER), run_name="__main__")
        printed = capsys.readouterr()
        return stop.value.code, printed.out, printed.err
    return run


@pytest.fixture(scope="module")
def joined(tmp_path_factory):
    """A directory in which `security` and `cost` have joined the run of the fixtures (the init of `wire/init.xml`)."""
    d = tmp_path_factory.mktemp("joined")
    for me in ("cost", "security"):
        p = subprocess.run([sys.executable, "-I", str(RECEIVER), "--me", me, "receive"], input=text("wire/init.xml"), capture_output=True,
                           text=True, cwd=d)
        assert p.returncode == 0 and p.stdout.startswith("STATUS: ANSWER"), p.stdout + p.stderr
    return d


def copy_of(joined, tmp_path):
    d = tmp_path / "agents"
    shutil.copytree(joined, d)
    return d


def sends(out):
    """The messages a script asked to be sent."""
    return [block for block in out.split("\n--- ") if block.startswith("SEND to:")]


def note_reasons(out):
    return [r for line in out.splitlines() if line.startswith("not used: ") for r in line[len("not used: "):].split("; ")]


# --- a message that breaks a rule of the spec ------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", REFUSED, ids=[c["file"] for c in REFUSED])
def test_an_agent_in_the_run_is_not_moved_by_a_message_that_must_be_refused(invoke, joined, tmp_path, case):
    d = copy_of(joined, tmp_path)
    before = (d / "concord-state-cost.json").read_text()
    code, out, err = invoke(d, "cost", "receive", text(case["file"]))
    assert code == 0 and "Traceback" not in err and not out.startswith("STATUS: ERROR"), out + err
    assert (d / "concord-state-cost.json").read_text() == before            # it is not recorded
    assert not sends(out)                                                   # and nobody is answered


@pytest.mark.parametrize("case", REFUSED, ids=[c["file"] for c in REFUSED])
def test_an_agent_that_is_not_in_a_run_learns_nothing_from_a_message_that_must_be_refused(invoke, tmp_path, case):
    code, out, err = invoke(tmp_path, "cost", "receive", text(case["file"]))
    assert code == 0 and "Traceback" not in err and not out.startswith("STATUS: ERROR"), out + err
    state = tmp_path / "concord-state-cost.json"
    assert not state.exists() or not json.loads(state.read_text())["runs"]  # it joined no run
    for block in sends(out):                                                # the one thing it may say: that an init is invalid
        assert block.split("\n", 1)[1].lstrip().startswith("<concord-error") and 'reason="invalid"' in block


# --- a message that is valid ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", [c for c in VALID if c["kind"] != "init"], ids=[c["file"] for c in VALID if c["kind"] != "init"])
def test_a_valid_message_is_never_taken_for_a_wrong_one(invoke, joined, tmp_path, case):
    d = copy_of(joined, tmp_path)
    code, out, err = invoke(d, "security", "receive", text(case["file"]))
    assert code == 0 and "Traceback" not in err and not out.startswith("STATUS: ERROR"), out + err
    assert not {"invalid", "check", "version"} & set(note_reasons(out))      # it may be ignored for a reason of the state, not of the message


def test_the_init_of_the_fixtures_gives_the_task_the_fixtures_ask_for(invoke, tmp_path):
    code, out, err = invoke(tmp_path, "cost", "receive", text("wire/init.xml"))
    assert code == 0 and out.startswith("STATUS: ANSWER\n--- BRIEF")
    assert "The run was opened by alice; participants: cost, security." in out
    assert "threshold 0.7, repair budget 3, eps 0.1, timeout 60 s, retries 2" in out         # the parameters of the init, as it says them


def test_an_init_that_leaves_parameters_out_is_run_with_the_defaults(invoke, tmp_path):
    (tmp_path / "all").mkdir()
    code, out, err = invoke(tmp_path / "all", "cost", "receive", text("wire/init-defaults.xml"))
    assert code == 0 and "threshold 0.6, repair budget 8, eps 0.05, timeout 300 s, retries 3" in out
    (tmp_path / "one").mkdir()                                              # one parameter left out: only that one is the default
    code, out, err = invoke(tmp_path / "one", "cost", "receive", text("wire/init-with-a-parameter-left-out.xml"))
    assert code == 0 and "threshold 0.7, repair budget 3, eps 0.05, timeout 60 s, retries 2" in out


def test_a_valid_message_of_another_agent_is_read_by_a_member_of_the_run(invoke, joined, tmp_path):
    d = copy_of(joined, tmp_path)
    code, out, err = invoke(d, "security", "receive", text("wire/init-reply-cost.xml"))
    assert code == 0 and "--- READ" in out and 'from="cost"' in out and "cache the answers" in out        # it took it, and shows it


# --- a message with an odd character in it ---------------------------------------------------------------------------------------

def test_a_message_that_hides_an_attribute_in_a_value_is_the_message_it_is(invoke, joined, tmp_path):
    """`from=` or `check=` written inside the quoted value of another attribute must not be read as the real one."""
    for name in ("hidden-check-in-a-single-quoted-value.xml", "hidden-from-in-a-single-quoted-value.xml",
                 "single-quotes-and-a-gt-in-a-value.xml"):
        d = copy_of(joined, tmp_path / name)
        doc = text(f"wire/tricky/{name}")
        code, out, err = invoke(d, "security", "receive", doc)
        assert code == 0 and "Traceback" not in err
        assert not {"invalid", "check", "version", "sender is not in the run"} & set(note_reasons(out)), (name, out)


def test_a_character_reference_in_from_names_the_initiator_as_it_is_written(invoke, tmp_path):
    code, out, err = invoke(tmp_path, "cost", "receive", text("wire/tricky/a-character-reference-in-from.xml"))
    assert code == 0 and out.startswith("STATUS: ANSWER") and "The run was opened by alice;" in out


def test_a_message_with_a_no_break_space_comes_out_as_it_went_in_whatever_the_locale_says(joined, tmp_path):
    """The message is UTF-8 on standard input, and the check is computed on its text: a script that read it as ASCII would drop it."""
    doc = text("wire/tricky/blanks-and-unicode-spaces-in-the-text.xml")
    assert " " in doc
    d = copy_of(joined, tmp_path)
    p = subprocess.run([sys.executable, "-I", str(RECEIVER), "--me", "security", "receive"], input=doc.encode("utf-8"), capture_output=True,
                       cwd=d, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    out = p.stdout.decode("utf-8")
    assert p.returncode == 0 and "--- READ" in out and doc.strip() in out, out + p.stderr.decode()
