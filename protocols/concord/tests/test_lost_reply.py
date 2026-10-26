"""A reply that never reached the initiator (lost, or refused) is sent again when the initiator nudges: the nudge names the
agents still awaited, and an agent that has nothing due sends its last reply again. Usage: python3 test_lost_reply.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = {**os.environ, "CONCORD_STATE_DIR": f"{tmp}/receiver-state"}


def run(script, *a, inp=None):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, cwd=tmp, env=env, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout


out = run(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i")
state = re.search(r"State: (\S+)", out).group(1)
S = ["--state", state]
first = run(INI, *S, "handle")
assert "awaiting=" not in first, "the first message is no nudge"
msg = first.split("--- to:", 1)[1].split("\n", 1)[1]
replies = {}
for ag in "ab":
    assert "STATUS: ASK" in run(REC, "--me", ag, "handle", inp=msg)
    replies[ag] = run(REC, "--me", ag, "answer", "--reject", "5", "--ideal", "90").split("\n", 1)[1].strip()
run(INI, *S, "handle", inp=replies["a"])                      # b's reply is lost on the way
st = lambda: json.load(open(f"{tmp}/{state}"))
assert "b" not in st()["anchors"] and "a" in st()["anchors"]
nudge = run(INI, *S, "handle")
assert nudge.startswith("STATUS: SEND") and 'awaiting="b"' in nudge, nudge[:500]
blocks = {b.split("\n", 1)[0].strip(): b.split("\n", 1)[1] for b in nudge.split("--- to:")[1:]}
block_b = next(v for k, v in blocks.items() if "b" in [x.strip() for x in k.split(",")])
again = run(REC, "--me", "b", "handle", inp=block_b)
assert again.startswith("STATUS: SEND") and '<concord-anchor-reply agent="b"' in again, again[:500]
assert "<concord-ack" in again, "the delivery that asked is acknowledged too"
run(INI, *S, "handle", inp=again.split("\n", 1)[1])
assert "b" in st()["anchors"], "the reply sent again is recorded"
print("a lost reply is sent again on the nudge, with the acknowledgement, and recorded: ok")
