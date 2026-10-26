"""`answer` takes plain flags, the ones the task prompt shows: no key naming the task, no JSON. (JSON still works.)
Usage: python3 test_answer_flags.py <skills dir>"""
import os, re, subprocess, sys, tempfile

SK = sys.argv[1]
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(*a, inp=""):
    p = subprocess.run([sys.executable, REC, "--me", "a", *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout


agents = "<participants><agent>a</agent><agent>b</agent></participants>"
params = "<params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps><budget>8</budget></params>"
to_ab = "<addressed_to><agent>a</agent><agent>b</agent></addressed_to>"
LONG = "a concrete reason with enough detail"

out = run("handle", inp=f"<concord-anchor><x_reject>r</x_reject><x_ideal>i</x_ideal><state_id>zz</state_id>{agents}{params}{to_ab}</concord-anchor>")
assert "answer --reject N --ideal N" in out and "<<'EOF'" not in out, "the prompt shows the command, not a JSON template"

out = run("answer")                                         # nothing given: told what to run, task shown again
assert out.startswith("STATUS: ASK") and "--reject N --ideal N" in out and "TASK: anchor" in out, out[:300]
assert run("answer", "--reject", "150", "--ideal", "90").startswith("STATUS: ASK"), "outside the scale is refused"
out = run("answer", "--reject", "10", "--ideal", "90")
assert out.startswith("STATUS: SEND") and "<raw_reject>10.0</raw_reject>" in out, out

out = run("handle", inp=f"<concord-propose-seed><context>c</context><candidate_schema>s</candidate_schema><state_id>zz</state_id>{agents}{params}{to_ab}</concord-propose-seed>")
assert '--candidate "TEXT" --rationale' in out, out[:300]
out = run("answer", "--candidate", "line one\nline two", "--rationale", LONG)
assert out.startswith("STATUS: SEND") and "line one\nline two" in out, out

b_anchor = '<concord-anchor-reply agent="b" state="zz"><raw_reject>0</raw_reject><raw_ideal>100</raw_ideal></concord-anchor-reply>'
b_seed = f'<concord-candidate-reply id="seed-b" agent="b" state="zz"><candidate>theirs</candidate><rationale>{LONG}</rationale></concord-candidate-reply>'
out = run("handle", inp=f"--- from: b\n{b_anchor}\n{b_seed}")
assert '--score seed-a N "reasons"' in out and '--score seed-b N "reasons"' in out, out[-500:]
out = run("answer", "--score", "seed-a", "80", LONG, "--score", "seed-b", "60", LONG)
assert out.startswith("STATUS: SEND") and 'candidate_id="seed-a"' in out and 'candidate_id="seed-b"' in out, out[:300]

print("answer takes plain flags (anchor, seed, scores); nothing given is told what to run; the prompt shows the command: ok")
