"""The receiver reminds the model at each step: the task prompt carries the guidance, a NONE says what to do, a SEND
carries its reminder on the error output so the message itself stays exactly as it is.
Usage: python3 test_receiver_next.py <skills dir>"""
import json, os, subprocess, sys, tempfile

SK = sys.argv[1]
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(*a, inp=None):
    p = subprocess.run([sys.executable, REC, "--me", "a", *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout, p.stderr


ANCHOR = ('<concord-anchor><x_reject>r</x_reject><x_ideal>i</x_ideal><state_id>zz</state_id><participants><agent>a</agent>'
          '<agent>b</agent></participants><params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps>'
          '<budget>8</budget></params><addressed_to><agent>a</agent><agent>b</agent></addressed_to></concord-anchor>')

out, err = run("handle", inp=ANCHOR)
assert out.startswith("STATUS: ASK"), out[:200]
for k in ("inverts the scale", "never a normalised", "Be honest", "never write the reply yourself"):
    assert k in out, ("anchor prompt lacks guidance", k)

out, err = run("answer", inp=json.dumps({"anchor": {"raw_reject": 5, "raw_ideal": 90}}))
assert out.startswith("STATUS: SEND") and "<concord-anchor-reply" in out
assert "NEXT:" not in out, "the reminder must not be part of the message"
assert "NEXT: Send the text printed under STATUS: SEND exactly" in err, err

out, err = run("handle", inp='--- from: b\n<concord-anchor-reply agent="b" state="zz"><raw_reject>1</raw_reject>'
                             '<raw_ideal>99</raw_ideal></concord-anchor-reply>')    # another agent's reply: nothing is due
assert out.startswith("STATUS: NONE") and "NEXT: Send nothing" in out, out[:300]
assert "acknowledgement" in out

print("receiver: the task prompt carries the guidance, NONE says what to do, SEND keeps its reminder off the message: ok")
