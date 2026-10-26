"""A wrong answer command brings the agent back with the flags the task shows, never with a way the script no longer takes.
Usage: python3 test_hints.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = {**os.environ, "CONCORD_STATE_DIR": f"{tmp}/receiver-state"}


def run(script, *a, inp=None, ok=True):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, cwd=tmp, env=env, timeout=60)
    if ok:
        assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p


out = run(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i").stdout
state = re.search(r"State: (\S+)", out).group(1)
msg = run(INI, "--state", state, "handle").stdout.split("--- to:", 1)[1].split("\n", 1)[1]
run(REC, "--me", "a", "handle", inp=msg)
bad = run(REC, "--me", "a", "answer", "--seed-a", "90", "--option-b", "70", ok=False)
text = bad.stdout + bad.stderr
assert "STATUS: ERROR" in text and "JSON" not in text, text[:600]
assert "the flags the task shows" in text, text[:600]
print("a wrong answer command is sent back to the flags the task shows, not to JSON: ok")
