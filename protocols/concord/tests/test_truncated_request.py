"""A model copying a request often drops its last closing tags (after the final prose sentence of the instructions). The
receiver gives them back, so the agent is not sent round in circles; a message cut anywhere else stays unreadable.
Usage: python3 test_truncated_request.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()


def run(script, *a, inp=None, env=None):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60,
                       env={**os.environ, "CONCORD_STATE_DIR": f"{tmp}/state-{env or 'x'}"})
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p


out = run(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i").stdout
state = re.search(r"State: (\S+)", out).group(1)
msg = run(INI, "--state", state, "handle").stdout.split("--- to:", 1)[1].split("\n", 1)[1]
assert msg.rstrip().endswith("</concord-anchor>") and "</instructions>" in msg

cases = {
    "both closing tags dropped": msg.split("</instructions>")[0],
    "only the document end dropped": msg.split("</concord-anchor>")[0],
}
for i, (label, cut) in enumerate(cases.items()):
    p = run(REC, "--me", "a", "handle", inp=cut, env=f"c{i}")
    assert p.stdout.startswith("STATUS: ASK") and "TASK: anchor" in p.stdout, (label, p.stdout[:300])
    assert "closing tags of the request were missing" in p.stderr, label
    print("ok:", label)

# not too lenient: a message cut before its instructions, or in the middle of a reply, is still unreadable
before = msg.split("<instructions>")[0]
p = run(REC, "--me", "a", "handle", inp=before, env="c5")
assert "<concord-error" in p.stdout, p.stdout[:300]
reply = '<concord-anchor-reply agent="b" state="x" ack="1">\n  <raw_reject>5.0</raw_reject>\n  <raw_ideal>9'
p = run(REC, "--me", "a", "handle", inp=reply, env="c6")
assert "<concord-error" in p.stdout or "note: no CONCORD" in p.stdout or "STATUS: NONE" in p.stdout, p.stdout[:300]
print("a request without its instructions, and a cut reply, are not accepted: ok")
