"""A message an agent cannot read is not acknowledged: the agent tells the initiator, which sends it again to that agent
only. (Before, the unreadable part was dropped, the delivery was acknowledged, and the agent never took part.)
Usage: python3 test_unreadable.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(script, *a, inp=""):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout, p.stderr


out, _ = run(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i")
state = re.search(r"State: (\S+)", out).group(1)
S = ["--state", state]
out, _ = run(INI, *S, "handle")
message = out.split("--- to: a, b\n", 1)[1]
assert "<concord-anchor>" in message and 'seq="1"' in message


def receive(text):
    return run(REC, "--me", "a", "handle", inp=text)


# 1. a mismatched closing tag (what a model retyping the message did)
broken = message.replace("</addressed_to>", "</participants>", 1)
assert broken != message
out, err = receive(broken)
assert out.startswith("STATUS: SEND") and "<concord-error" in out and 'seq="1"' in out, out[:300]
assert "<concord-ack" not in out, "an unreadable delivery must not be acknowledged"
error_doc = out.split("\n", 1)[1].strip()

# 2. the initiator hears it and sends the message again to that agent only
out, err = run(INI, *S, "handle", inp=error_doc)
assert out.startswith("STATUS: SEND"), out[:300]
assert "--- to: a\n" in out and "--- to: b" not in out, out[:300]
assert "<concord-anchor>" in out and 'full="1"' in out, out[:400]
assert "could not read" in err, err

# 3. the message cut short, with no closing tag at all, is reported too
out, err = receive(message[: message.index("<participants>")])
assert "<concord-error" in out and "<concord-ack" not in out, out[:300]

# 4. the good message goes through, and the agent takes its place in the run
again = out2 = None
out, _ = run(INI, *S, "handle", inp=error_doc)
again = out.split("--- to: a\n", 1)[1]
out, _ = receive(again)
assert out.startswith("STATUS: ASK") and "TASK: anchor" in out, out[:300]

print("an unreadable message is reported, not acknowledged, and sent again to that agent only: ok")
