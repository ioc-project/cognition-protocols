"""The simple rules an answer must meet are checked by the receiver, and a broken answer is sent back with the reason (and
nothing is recorded), so the model corrects itself. Usage: python3 test_answer_rules.py <skills dir>"""
import json, os, subprocess, sys, tempfile

SK = sys.argv[1]
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def run(*a, inp=None):
    p = subprocess.run([sys.executable, REC, "--me", "a", *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout


def doc(tag, body, **attrs):
    return f'<{tag}{"".join(f" {k}={chr(34)}{v}{chr(34)}" for k, v in attrs.items())}>{body}</{tag}>'


def send(inp):
    return run("handle", inp=inp)


def answer(data):
    return run("answer", inp=json.dumps(data))


def refused(out, *words):
    assert out.startswith("STATUS: ASK") and "not usable" in out and "Nothing was recorded" in out, out[:300]
    assert "TASK:" in out, "the task is shown again, so the model sees what is due"
    for w in words:
        assert w in out, (w, out[:400])


LONG = "a concrete reason with enough detail"
agents = "<participants><agent>a</agent><agent>b</agent></participants>"
params = "<params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps><budget>8</budget></params>"
to_ab = "<addressed_to><agent>a</agent><agent>b</agent></addressed_to>"

# an answer that is not what the task asks (a JSON keyed after the message) is refused with the exact command
out = send(f"<concord-anchor><x_reject>r</x_reject><x_ideal>i</x_ideal><state_id>zz</state_id>{agents}{params}{to_ab}</concord-anchor>")
refused(answer({"propose": {"raw_reject": 10, "raw_ideal": 90}}), "missing what this task needs (anchor)", "--reject N --ideal N")

# anchor: outside the 0-100 scale, then fine
out = send(f"<concord-anchor><x_reject>r</x_reject><x_ideal>i</x_ideal><state_id>zz</state_id>{agents}{params}{to_ab}</concord-anchor>")
assert out.startswith("STATUS: ASK")
refused(answer({"anchor": {"raw_reject": -5, "raw_ideal": 120}}), "outside the 0-100 scale")
assert answer({"anchor": {"raw_reject": 10, "raw_ideal": 90}}).startswith("STATUS: SEND")

# seed: a rationale that is no reason
send(f"<concord-propose-seed><context>c</context><candidate_schema>s</candidate_schema><state_id>zz</state_id>{agents}{params}{to_ab}</concord-propose-seed>")
refused(answer({"seed": {"candidate": "mine", "rationale": "ok"}}), "seed rationale")
refused(answer({"seed": {"candidate": "  ", "rationale": LONG}}), "seed candidate")
assert answer({"seed": {"candidate": "mine", "rationale": LONG}}).startswith("STATUS: SEND")

# scores: every candidate listed must be rated between the anchors, with a real reason
b_anchor = doc("concord-anchor-reply", "<raw_reject>0</raw_reject><raw_ideal>100</raw_ideal>", agent="b", state="zz")
b_seed = doc("concord-candidate-reply", f"<candidate>theirs</candidate><rationale>{LONG}</rationale>", id="seed-b", agent="b", state="zz")
out = send(f"--- from: b\n{b_anchor}\n{b_seed}")
assert out.startswith("STATUS: ASK") and "TASK: score" in out, out[:300]
ids = ["seed-a", "seed-b"]


def scores(**over):
    rows = {c: {"id": c, "raw": 60, "reasoning": LONG} for c in ids}
    for c, kv in over.items():
        rows[c.replace("_", "-")].update(kv)
    return {"scores": list(rows.values())}


refused(answer(scores(seed_b={"raw": 97})), "outside your anchors", "raw_ideal=90")
refused(answer(scores(seed_b={"raw": 3})), "outside your anchors", "raw_reject=10")
refused(answer(scores(seed_b={"raw": 0.8})), "0-1 score")
refused(answer(scores(seed_b={"reasoning": "too costly"})), "the reasoning for seed-b")
refused(answer(scores(seed_b={"raw": "high"})), "must be a number")
assert answer(scores(seed_b={"raw": 90})).startswith("STATUS: SEND"), "the edge of the range is allowed"

print("an answer outside the rules is refused with the reason and nothing recorded; a correct one goes through: ok")
