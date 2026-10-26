"""The initiator has the commands it needs and no others: init, handle, watchdog, mark-missing, and a read-only status.
Usage: python3 test_commands.py <skills dir>"""
import hashlib, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
tmp = tempfile.mkdtemp()


def run(*a, inp=""):
    return subprocess.run([sys.executable, INI, *a], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60)


out = run("init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i").stdout
state = re.search(r"State: (\S+)", out).group(1)
S = ["--state", state]

for gone in ("next", "record-reply", "record-replies"):
    p = run(*S, gone)
    assert p.returncode != 0 and p.stdout.startswith("STATUS: ERROR") and "handle" in p.stdout, (gone, p.stdout[:200])

digest = lambda: hashlib.sha256(open(f"{tmp}/{state}", "rb").read()).hexdigest()
before = digest()
p = run(*S, "status")
assert p.returncode == 0 and "Phase: Anchor" in p.stdout and "<concord-" not in p.stdout, p.stdout[:300]
assert digest() == before, "status must change nothing"
first = run(*S, "handle").stdout
assert first.startswith("STATUS: SEND") and "<concord-anchor>" in first, "status did not swallow the first message"

help_text = run("--help").stdout
for name in ("init", "handle", "watchdog", "mark-missing", "status"):
    assert name in help_text, name
listed = re.search(r"\{(init[a-z,-]*)\}", help_text).group(1).split(",")
assert sorted(listed) == sorted(["init", "mark-missing", "handle", "watchdog", "status"]), listed

print("the initiator has init, handle, watchdog, mark-missing and a read-only status, and nothing else: ok")
