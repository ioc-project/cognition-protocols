"""The initiator accepts replies in any arrival order, so the same state machine serves a lead that polls agents
one by one and a room where agents reply unprompted. Usage: python3 test_transport.py <path to concord_initiator.py>"""
import json, subprocess, sys, tempfile, os

S = sys.argv[1]
AG = ["a", "b", "c"]


def run(st, *a, check=True, inp=""):
    p = subprocess.run([sys.executable, S, "--state", st, *a], capture_output=True, text=True, input=inp)
    if check and p.returncode:
        raise SystemExit(f"FAIL {a}: {p.stdout}{p.stderr}")
    return p


def anchor(): return "<concord-anchor-reply><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>"
def cand(t): return f"<concord-candidate-reply><candidate>{t}</candidate><rationale>r</rationale></concord-candidate-reply>"
def scores(d):
    return "<concord-score-reply>" + "".join(
        f'<score candidate_id="{c}"><raw>{v}</raw><reasoning>because</reasoning></score>' for c, v in d.items()
    ) + "</concord-score-reply>"


def init(extra):
    d = tempfile.mkdtemp()
    p = run(os.path.join(d, "s.json"), "init", "--agents", ",".join(AG), "--schema", "s", "--context", "c",
            "--x-reject", "r", "--x-ideal", "i", "--satisfaction-threshold", "0.7", *extra)
    return [l.split("State: ")[1] for l in p.stdout.splitlines() if l.startswith("State: ")][0]


def state(st): return json.load(open(st))


# 1. room order: scores of seeds arrive before the last seed and before some anchors
st = init([])
assert "<addressed_to>" in run(st, "handle").stdout and "Phase: Anchor" in run(st, "status").stdout
for ag in "ab": run(st, "handle", "--from", ag, inp=anchor())
for ag in "ab": run(st, "handle", "--from", ag, inp=cand(f"seed {ag}"))
run(st, "handle", "--from", "a", inp=scores({"seed-a": 80, "seed-b": 70, "seed-c": 60}))      # seed-c unknown yet: parked
assert state(st)["deferred"], "should be parked"
run(st, "handle", "--from", "c", inp=scores({"seed-a": 80, "seed-b": 70, "seed-c": 90}))     # c has no anchor yet: parked
run(st, "handle", "--from", "c", inp=anchor())
run(st, "handle", "--from", "c", inp=cand("seed c"))                                          # unparks a's and c's
assert not state(st)["deferred"], state(st)["deferred"]
p = run(st, "handle", "--from", "a", inp=scores({"seed-a": 80}))                              # duplicate: harmless
assert p.stdout.startswith("STATUS: NONE") and "already" not in p.stderr, p.stdout
run(st, "handle", "--from", "b", inp=scores({"seed-a": 40, "seed-b": 90, "seed-c": 50}))
s = state(st)
assert all(len(s["raw"][a]) == 3 for a in AG), s["raw"]
print("room order ok, phase after seeds:", run(st, "status").stdout.splitlines()[0])

# 2. one-by-one order (a lead polling agents): same calls, other order, no reply_to element
st = init([])
assert "<addressed_to>" in run(st, "handle").stdout
for ag in AG:
    run(st, "handle", "--from", ag, inp=anchor()); run(st, "handle", "--from", ag, inp=cand(f"seed {ag}"))
for ag in AG: run(st, "handle", "--from", ag, inp=scores({f"seed-{x}": 50 for x in AG}))
assert state(st)["pending"] or state(st)["outcome"]
print("one-by-one order ok:", run(st, "status").stdout.splitlines()[0])

# 3. one reply holding two documents (anchor then seed), as an agent sends when one message carried both
st = init([])
two = anchor() + "\n\n" + cand("seed a")
p = run(st, "handle", "--from", "a", inp=two)
s = state(st)
assert "a" in s["anchors"] and any(c["id"] == "seed-a" for c in s["pool"]), s["pool"]
print("two documents in one reply ok")

# 4. a proposer's repair and its rating of its own candidate in one message
st = init([])
for ag in AG:
    run(st, "handle", "--from", ag, inp=anchor() + "\n\n" + cand(f"seed {ag}"))
for ag in AG: run(st, "handle", "--from", ag, inp=scores({f"seed-{x}": 50 for x in AG}))
s = state(st)
assert s["pending"], "needs a repair round"
b, rnd = s["pending"]["agent"], s["pending"]["round"]
nudge = run(st, "handle").stdout
assert "<concord-repair>" not in nudge and "<concord-score>" not in nudge, "the initiator never asks for a repair or a rating"
assert "<concord-delivery" in nudge, "a nudge only makes the agents check what is due"
run(st, "handle", "--from", b, inp=cand("better") + "\n\n" + scores({f"r{rnd}-{b}": 88}))
s = state(st)
assert s["raw"][b][f"r{rnd}-{b}"] == 88.0 and any(c["id"] == f"r{rnd}-{b}" for c in s["pool"])
owed = run(st, "status").stdout.split("Awaiting replies:")[1]
who = [l[2:].split(" ")[0] for l in owed.splitlines() if l.startswith("* ")]
assert who == [a for a in AG if a != b], who
print("repair + own rating in one message ok; others owed:", [a for a in AG if a != b])
