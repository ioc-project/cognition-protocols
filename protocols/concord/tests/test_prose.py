"""An agent that answers in words instead of sending the reply its script printed: the initiator says so, records nothing, and
sends the request again; a waiting initiator is told not to end its turn. Usage: python3 test_prose.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
tmp = tempfile.mkdtemp()


def run(*a, inp=""):
    p = subprocess.run([sys.executable, INI, *a], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout, p.stderr


out, _ = run("init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i")
state = re.search(r"State: (\S+)", out).group(1)
S = ["--state", state]
first, _ = run(*S, "handle")
assert "Your answer to this message is only the reply the script tells you to send" in first, "the message tells the agent what its answer is"

out, err = run(*S, "handle", inp="**a: Ratings submitted.** raw_reject = 15, raw_ideal = 88. All done.")
assert "holds no CONCORD reply" in err and "Nothing was recorded" in err, err
assert out.startswith("STATUS: SEND") and "<concord-anchor>" in out, out[:300]     # the request goes again
assert "nudge" in out.splitlines()[1].lower(), out[:300]

run(*S, "handle", inp='<concord-anchor-reply agent="a"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
out, _ = run(*S, "handle", inp='<concord-anchor-reply agent="a"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
if out.startswith("STATUS: NONE"):
    assert "do not end your turn" in out.splitlines()[1], out[:400]

print("prose is reported and the request is sent again; a waiting initiator is told not to end its turn: ok")
