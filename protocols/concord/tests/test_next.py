"""Every output of the initiator tells the model what to do next: a `NEXT:` line under the status, an explicit
`--- to:` line on every message, and, when the run is over, the instruction to report the result as printed.
Usage: python3 test_next.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
tmp = tempfile.mkdtemp()


def run(*a, inp=None):
    p = subprocess.run([sys.executable, INI, *a], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout, p.stderr


def check_status(out, what):
    lines = out.splitlines()
    assert lines[0].startswith("STATUS: "), (what, out[:200])
    assert lines[1].startswith("NEXT: "), (what, out[:300])
    if lines[0] == "STATUS: SEND":
        first_doc = next(i for i, l in enumerate(lines) if l.startswith("<"))
        assert any(l.startswith("--- to: ") for l in lines[:first_doc]), (what, "a message with no `--- to:` line", out[:300])
    return lines[1]


out, _ = run("init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i",
             "--satisfaction-threshold", "0.1")
state = re.search(r"State: (\S+)", out).group(1)
assert out.splitlines()[1].startswith("NEXT: ") and state in out and "handle" in out
S = ["--state", state]

out, _ = run(*S, "handle")
nx = check_status(out, "first handle")
assert "nudge" not in nx.lower(), "the first call is not a nudge"
assert "--- to: a, b" in out
assert "never write the reply yourself.</instructions>" in out, \
    "the instructions must end on the old closing sentence: a model retyping the message stops at a sentence that sounds final"

out, _ = run(*S, "handle", inp='<concord-anchor-reply agent="a"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
check_status(out, "one anchor in")
out, _ = run(*S, "handle", inp='<concord-anchor-reply agent="b"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
check_status(out, "both anchors in")

out, _ = run(*S, "handle")                                       # no input: a nudge, and it says so
assert "nudge" in check_status(out, "nudge").lower()

for ag in "ab":
    out, _ = run(*S, "handle", inp=f'<concord-candidate-reply id="seed-{ag}" agent="{ag}"><candidate>opt {ag}</candidate>'
                                  f'<rationale>r</rationale></concord-candidate-reply>')
check_status(out, "seeds in")

scores = "".join(f'<concord-score-reply agent="{ag}"><score candidate_id="seed-a"><raw>80</raw><reasoning>x</reasoning></score>'
                 f'<score candidate_id="seed-b"><raw>85</raw><reasoning>x</reasoning></score></concord-score-reply>' for ag in "ab")
out, err = run(*S, "handle", inp=scores)
text = out + err
assert "RUN COMPLETE" in text, text[:400]
nx = out.splitlines()[1] if out.startswith("STATUS: NONE") else None
if nx:                                                            # nothing left to relay: the report is on stdout
    assert nx.startswith("NEXT: ") and "Report exactly" in nx and "nothing" in nx, nx
else:                                                             # last replies to relay: report on the error output
    nx = check_status(out, "last relay")
    assert "report the RUN COMPLETE result" in nx, nx

out, _ = run(*S, "handle")                                       # a finished run only reports, and says so
assert out.startswith("STATUS: NONE") and "Report exactly" in out.splitlines()[1], out[:300]

out, _ = run("init", "--help")
assert "known-proposals" not in out and "never the means" in out

print("every output carries a NEXT line, every message a `--- to:` line, the first call is no nudge, the end says to report as printed: ok")
