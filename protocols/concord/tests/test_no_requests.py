"""In relay mode the initiator never asks for a rating or a repair: each agent works out from the relay what it owes.
It only relays replies and acknowledges; a nudge (the watchdog) is a bare delivery that makes each agent check what is due.
Usage: python3 test_no_requests.py <skills dir>"""
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
first = run(*S, "handle")
assert '<concord-delivery seq="1"' in first and "full=" not in first.split("<concord-anchor>")[0], "the first delivery is no nudge:\n" + first[:300]
outs = [first]
st = lambda: json.load(open(f"{tmp}/{state}"))
dv = lambda ag: st()["deliv"].get(ag, 0)
for ag in "ab":
    outs.append(run(*S, "handle", inp=f'<concord-anchor-reply agent="{ag}" ack="{dv(ag)}"><raw_reject>0</raw_reject><raw_ideal>100</raw_ideal></concord-anchor-reply>'))
for ag in "ab":
    outs.append(run(*S, "handle", inp=f'<concord-candidate-reply id="seed-{ag}" agent="{ag}" ack="{dv(ag)}"><candidate>option {ag}</candidate>'
                                      f'<rationale>a rationale of {ag}</rationale></concord-candidate-reply>'))
# each agent likes its own seed and rejects the other's: no seed clears everyone, so a repair starts
for ag in "ab":
    o = "b" if ag == "a" else "a"
    outs.append(run(*S, "handle", inp=f'<concord-score-reply agent="{ag}" ack="{dv(ag)}">'
                                      f'<score candidate_id="seed-{ag}"><raw>95</raw><reasoning>it is what I would build</reasoning></score>'
                                      f'<score candidate_id="seed-{o}"><raw>5</raw><reasoning>it breaks what I protect</reasoning></score>'
                                      f'</concord-score-reply>'))
assert st()["pending"], "the run must be in a repair round"
for ag in "ab":                                             # acknowledgements alone: nothing to send
    o = run(*S, "handle", inp=f'<concord-ack agent="{ag}" state="{st()["state_id"]}" seq="{dv(ag)}"></concord-ack>')
    outs.append(o)
    assert o.startswith("STATUS: NONE"), o[:200]
for o in outs[3:]:
    assert "<concord-score>" not in o and "<concord-repair>" not in o, "no request for a rating or a repair:\n" + o[:400]
nudge = run(*S, "handle")
assert nudge.startswith("STATUS: SEND") and "<concord-delivery" in nudge, nudge[:300]
assert "<concord-score>" not in nudge and "<concord-repair>" not in nudge
print("no rating or repair request is ever sent; a nudge only makes the agents check what is due: ok")
