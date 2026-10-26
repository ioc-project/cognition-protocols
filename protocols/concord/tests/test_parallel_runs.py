"""Two runs share a state folder (parallel runs, the same agent id in both). The command a task shows names its run, so
`answer` never takes the other run's state. Usage: python3 test_parallel_runs.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = {**os.environ, "CONCORD_STATE_DIR": f"{tmp}/receiver-state"}


def run(script, *a, inp=None):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, cwd=tmp, env=env, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout


def first_message():
    out = run(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i")
    state = re.search(r"State: (\S+)", out).group(1)
    msg = run(INI, "--state", state, "handle")
    return msg.split("--- to:", 1)[1].split("\n", 1)[1]


m1, m2 = first_message(), first_message()
sid1 = re.search(r'state="(\w+)"', m1).group(1)
sid2 = re.search(r'state="(\w+)"', m2).group(1)
assert sid1 != sid2
ask1 = run(REC, "--me", "a", "handle", inp=m1)
ask2 = run(REC, "--me", "a", "handle", inp=m2)               # run 2 is now the newest state of agent a
cmd1 = re.search(r"Answer by running: python3 <this script> (--me a --state-id \w+) answer", ask1)
assert cmd1 and sid1 in cmd1.group(1), ask1[-600:]
assert sid2 in re.search(r"Answer by running: python3 <this script> (.*?) answer", ask2).group(1)
reply = run(REC, *cmd1.group(1).split(), "answer", "--reject", "5", "--ideal", "90")
assert f'state="{sid1}"' in reply and sid2 not in reply, reply
print("the answer command names its run: an answer for run 1 stays in run 1 while run 2 is newer: ok")
