"""Malformed input never crashes a script and never costs the valid messages that arrive with it. Usage:
python3 test_malformed.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(script, *args, inp=None):
    p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    return p.returncode, p.stdout, p.stderr


rc, out, _ = run(INI, "init", "--quiet", "--no-relay", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r",
                 "--x-ideal", "i")
ST = re.search(r"State: (\S+)", out).group(1)
rc, anchor, _ = run(INI, "--state", ST, "handle", inp="")
anchor_msg = anchor.split("\n", 1)[1]
good = '<concord-anchor-reply agent="b" state="%s"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>' % \
    re.search(r"<state_id>(\w+)</state_id>", anchor_msg).group(1)

# ---- receiver
for label, text in [("prose", "hello there"), ("empty", ""), ("only a separator", "--- from: x\n"),
                    ("binary-ish", "\x00\x01\x02 </concord-anchor>")]:
    rc, out, err = run(REC, "--me", "a", "handle", inp=text)
    assert rc == 0 and out.startswith("STATUS: NONE"), (label, rc, out, err)
rc, out, err = run(REC, "--me", "a", "handle", inp="<concord-anchor><x_reject>oops</concord-anchor>")
assert rc == 0 and out.startswith("STATUS: SEND") and "<concord-error" in out, (rc, out, err)   # no crash: the sender is told
rc, out, err = run(REC, "--me", "a", "handle", inp="<concord-anchor><x_reject>oops</concord-anchor>\n\n" + anchor_msg)
assert rc == 0 and out.startswith("STATUS: ASK") and "TASK: anchor" in out, out        # the valid message still counts
assert "not well-formed" in out, out
print("receiver: no crash on prose, empty, binary or broken XML; a valid message next to a broken one still counts: ok")

# ---- answer
for label, text in [("not json", "nope"), ("wrong type", '{"anchor": {"raw_reject": "x"}}'),
                    ("inverted", '{"anchor": {"raw_reject": 90, "raw_ideal": 5}}'), ("empty object", "{}")]:
    rc, out, err = run(REC, "--me", "a", "answer", inp=text)
    assert rc == 0 and out.startswith("STATUS: ASK"), (label, rc, out, err)           # asked again, with what was wrong
print("answer: not JSON, wrong type, inverted scale, empty: asked again each time, never a crash: ok")

# ---- initiator
bad_missing = '<concord-anchor-reply agent="a"><raw_reject>5</raw_reject></concord-anchor-reply>'
bad_xml = '<concord-anchor-reply agent="a"><raw_reject>5</concord-anchor-reply>'
for label, text in [("missing field", bad_missing), ("broken XML", bad_xml), ("prose", "I think 80")]:
    rc, out, err = run(INI, "--state", ST, "handle", inp=text)
    if label == "prose":                                  # said so, and the agents owing a reply are sent the request again
        assert rc == 0 and out.startswith("STATUS: SEND") and "holds no CONCORD reply" in err, (label, rc, out, err)
        continue
    assert rc == 0 and out.startswith("STATUS: NONE") and "recorded 0" in out, (label, rc, out, err)
    assert "NOT RECORDED" in err, (label, err)                                          # said so, and carried on
rc, out, err = run(INI, "--state", ST, "handle", inp=bad_xml + "\n\n" + good)
assert rc == 0 and "recorded 1" in out and "NOT RECORDED" in err, (rc, out, err)        # the valid reply next to it counts
print("initiator: a reply that is incomplete, broken XML or prose is reported and skipped; the valid one next to it counts: ok")
