"""A nudge carries only what the agent has not acknowledged, not the whole run again: a long
message is the one a model is most likely to fail to send. Usage: python3 test_nudge_size.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
tmp = tempfile.mkdtemp()


def run(*a, inp=None):
    p = subprocess.run([sys.executable, INI, *a], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout


out = run("init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i")
state = re.search(r"State: (\S+)", out).group(1)
S = ["--state", state]
run(*S, "handle")


def deliv(agent):
    return json.load(open(f"{tmp}/{state}"))["deliv"].get(agent, 0)


for ag in "ab":
    run(*S, "handle", inp=f'<concord-anchor-reply agent="{ag}" ack="{deliv(ag)}"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
for ag in "ab":
    run(*S, "handle", inp=f'<concord-candidate-reply id="seed-{ag}" agent="{ag}" ack="{deliv(ag)}"><candidate>option {ag}</candidate>'
                          f'<rationale>a rationale of {ag}</rationale></concord-candidate-reply>')
# every agent acknowledges everything it was sent
for ag in "ab":
    run(*S, "handle", inp=f'<concord-ack agent="{ag}" state="{state}" seq="{deliv(ag)}"></concord-ack>')

nudge = run(*S, "handle")
assert nudge.startswith("STATUS: SEND") and "nudge" in nudge.splitlines()[1].lower(), nudge[:200]
assert "<concord-score>" not in nudge and "<concord-repair>" not in nudge, "a nudge never carries a request"
assert nudge.count("<concord-delivery") == 2 and 'full="1"' in nudge, "each agent only gets a delivery to check what is due"
assert "concord-anchor-reply" not in nudge and "concord-candidate-reply" not in nudge, \
    "what the agent already acknowledged must not be sent again:\n" + nudge[:600]

print("a nudge is a bare delivery (no Score or Repair request) and carries only what is unacknowledged: ok")
