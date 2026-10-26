"""Whatever fails, the output brings the model back: `STATUS: ERROR`, what happened, and a `NEXT:` line saying what to do.
Never a bare usage text or a traceback. Usage: python3 test_errors.py <skills dir>"""
import os, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(script, *a, inp=""):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    return p.returncode, p.stdout


def expect(what, script, args, must):
    code, out = run(script, *args)
    lines = out.splitlines()
    assert code != 0, (what, "should fail", out[:200])
    assert lines and lines[0] == "STATUS: ERROR", (what, out[:300])
    assert any(l.startswith("NEXT: ") for l in lines), (what, "no NEXT line", out[:300])
    assert "Traceback" not in out, (what, out[:300])
    nxt = next(l for l in lines if l.startswith("NEXT: "))
    for k in must:
        assert k in nxt, (what, k, nxt)


expect("initiator: an unknown command", INI, ["--state", "x.json", "certificate"], ["init", "handle"])
expect("initiator: no state", INI, ["--state", "nope.json", "handle"], ["init"])
expect("initiator: init without its arguments", INI, ["init", "--agents", "a,b"], ["init --help"])
open(f"{tmp}/bad.json", "w").write("{not json")
expect("initiator: a damaged state file", INI, ["--state", f"{tmp}/bad.json", "handle"], ["Run the same command once more"])
expect("initiator: agents that are too few", INI, ["init", "--quiet", "--agents", "a", "--schema", "s", "--context", "c",
                                                   "--x-reject", "r", "--x-ideal", "i"], ["init --help"])
expect("receiver: no --me", REC, ["handle"], ["--me"])
expect("receiver: an unknown command", REC, ["--me", "a", "frobnicate"], ["handle", "answer"])

print("every failure prints STATUS: ERROR, what happened and a NEXT line, with a non-zero exit and no traceback: ok")
