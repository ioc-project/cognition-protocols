"""Relay mode: every delivery is numbered; an agent with nothing to say acknowledges it, an agent that has something to say
sends that directly (and the reply carries the number). Not in --no-relay mode. Usage: python3 test_ack.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"


def setup(relay):
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")

    def run(script, *a, inp=None):
        p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
        assert p.returncode == 0, (a, p.stdout, p.stderr)
        return p.stdout, p.stderr

    out, _ = run(INI, "init", "--quiet", *([] if relay else ["--no-relay"]), "--agents", "a,b", "--schema", "s", "--context", "c",
                 "--x-reject", "r", "--x-ideal", "i")
    st = re.search(r"State: (\S+)", out).group(1)
    return run, st


def agent_anchor(run, me, text):
    out, _ = run(REC, "--me", me, "handle", inp=text)
    assert out.startswith("STATUS: ASK"), out
    out, _ = run(REC, "--me", me, "answer", inp=json.dumps({"anchor": {"raw_reject": 5, "raw_ideal": 90}}))
    return out.split("\n", 1)[1]


def block_for(out, agent):
    """What the runner would hand to `agent`: the block of the `--- to:` list that names it."""
    parts = re.split(r"^--- to: (.+)$", out.split("\n", 1)[1], flags=re.M)
    for i in range(1, len(parts), 2):
        if agent in [x.strip() for x in parts[i].split(",")]:
            return parts[i + 1].strip()
    return None


# ---------------- relay mode
run, ST = setup(relay=True)
out, _ = run(INI, "--state", ST, "handle", inp="")
first_a, first_b = block_for(out, "a"), block_for(out, "b")
assert re.match(r'<concord-delivery seq="1"', first_a) and re.match(r'<concord-delivery seq="1"', first_b), out   # numbered
reply_a = agent_anchor(run, "a", first_a)
assert 'ack="1"' in reply_a, reply_a                             # a reply carries the delivery number it processed
out, err = run(INI, "--state", ST, "handle", inp=reply_a)
delivery_b = block_for(out, "b")
assert re.match(r'<concord-delivery seq="2"', delivery_b) and "<concord-anchor-reply" in delivery_b, out        # b: 2nd delivery
# b gets a's reply and the original message in one go; it owes its anchor, so no ack now: that is a task
o, _ = run(REC, "--me", "b", "handle", inp=first_b + "\n\n" + delivery_b)
assert o.startswith("STATUS: ASK"), o
reply_b = agent_anchor(run, "b", "")
assert 'ack="2"' in reply_b, reply_b                             # ...and its reply is the acknowledgement
out, err = run(INI, "--state", ST, "handle", inp=reply_b)
assert "not yet acknowledged: b" not in out + err, out
# a delivery that needs no answer: b is told something it already knows
info = '<concord-delivery seq="3" state="%s"></concord-delivery>\n\n%s' % (re.search(r'state_id>(\w+)<', first_b).group(1), reply_a)
o, _ = run(REC, "--me", "b", "handle", inp=info)
assert o.startswith("STATUS: SEND") and "<concord-ack" in o and 'seq="3"' in o and 'agent="b"' in o, o
ack = o.split("\n", 1)[1]
# the orchestrator records the ack, does not relay it, and counts nothing as a reply
out, err = run(INI, "--state", ST, "handle", inp=ack)
assert out.startswith("STATUS: NONE") and "recorded 0" in out and "<concord-ack" not in out, out
state = json.load(open(os.path.join(os.path.dirname(ST) or ".", ST))) if os.path.isabs(ST) else None
print("relay mode: numbered deliveries; a reply carries the number it processed; nothing to say -> an ack; the orchestrator "
      "records it and never relays it: ok")

# ---------------- no relay: no numbers, no acknowledgements
run, ST = setup(relay=False)
out, _ = run(INI, "--state", ST, "handle", inp="")
msg = block_for(out, "a")
assert msg and "<concord-delivery" not in msg, out
reply_a = agent_anchor(run, "a", msg)
assert "ack=" not in reply_a, reply_a
out, _ = run(INI, "--state", ST, "handle", inp=reply_a)
assert "<concord-delivery" not in out, out
o, _ = run(REC, "--me", "b", "handle", inp=reply_a)
assert o.startswith("STATUS: NONE") or o.startswith("STATUS: ASK"), o            # no ack: there was no delivery number
print("no-relay mode: no delivery numbers, no acknowledgements: ok")

# ---------------- the acknowledged position is contiguous: a lost delivery is never hidden by a later one
run, ST = setup(relay=True)
out, _ = run(INI, "--state", ST, "handle", inp="")
sid = re.search(r"state_id>(\w+)<", out).group(1)
ctx = block_for(out, "a")
run(REC, "--me", "a", "handle", inp=ctx)                                  # delivery 1 (the anchor): a is asked to anchor
run(REC, "--me", "a", "answer", inp=json.dumps({"anchor": {"raw_reject": 5, "raw_ideal": 90}}))
note = lambda n, full="": '<concord-delivery seq="%d" state="%s"%s></concord-delivery>\n\n' % (n, sid, full) + \
    '<concord-anchor-reply agent="b" state="%s"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>' % sid
o, _ = run(REC, "--me", "a", "handle", inp=note(3))                       # delivery 2 was lost; 3 arrives
assert o.startswith("STATUS: SEND") and 'seq="1"' in o, o                 # it can only vouch for 1: not 3
o, _ = run(REC, "--me", "a", "handle", inp=note(4, ' full="1"'))          # a full delivery closes the gap
assert o.startswith("STATUS: SEND") and 'seq="4"' in o, o
print("acknowledgement is contiguous (a lost delivery is not hidden by a later one), and a full delivery closes the gap: ok")

# ---------------- the nudge goes only to the agents that owe a reply or have not acknowledged
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")
def run3(script, *a, inp=None):
    p = subprocess.run([sys.executable, script, *a], capture_output=True, text=True, input=inp, env=env, cwd=tmp, timeout=60)
    assert p.returncode == 0, (a, p.stdout, p.stderr)
    return p.stdout, p.stderr
out, _ = run3(INI, "init", "--quiet", "--agents", "x,y,z", "--schema", "s", "--context", "c", "--x-reject", "r", "--x-ideal", "i",
              "--satisfaction-threshold", "0.6")
ST3 = os.path.join(tmp, re.search(r"State: (\S+)", out).group(1))
run3(INI, "--state", ST3, "handle", inp="")
sid3 = json.load(open(ST3))["state_id"]
def doc(kind, agent, body): return f'<concord-{kind}-reply agent="{agent}" state="{sid3}">{body}</concord-{kind}-reply>'
anchors = "\n".join(doc("anchor", a, "<raw_reject>5</raw_reject><raw_ideal>90</raw_ideal>") for a in "xyz")
seeds = "\n".join(doc("candidate", a, f"<candidate>c{a}</candidate><rationale>r</rationale>").replace("-reply>", "-reply>", 1)
                  .replace(f'agent="{a}"', f'id="seed-{a}" agent="{a}"', 1) for a in "xyz")
run3(INI, "--state", ST3, "handle", inp=anchors)
run3(INI, "--state", ST3, "handle", inp=seeds)
def ratings(a, v):
    return doc("score", a, "".join(f'<score candidate_id="seed-{c}"><raw>{v}</raw><reasoning>w</reasoning></score>' for c in "xyz"))
run3(INI, "--state", ST3, "handle", inp=ratings("x", 50) + "\n" + ratings("y", 50))        # z owes its ratings
st3 = json.load(open(ST3))
assert st3["raw"]["z"] == {} and st3["raw"]["x"], st3["raw"]
# x acknowledged its latest delivery; y has not; z owes a reply
dv = st3["deliv"]
run3(INI, "--state", ST3, "handle",
     inp=f'<concord-ack agent="x" state="{sid3}" seq="{dv["x"]}"></concord-ack>')
o, e = run3(INI, "--state", ST3, "handle", inp="")                                    # the nudge
targets = re.findall(r"^--- to: (.+)$", o, re.M)
named = sorted({n.strip() for t in targets for n in t.split(",")})
assert named == ["y", "z"], (named, o[:300])                                          # not x: up to date, owes nothing
print("the nudge goes only to the agents that owe a reply or have not acknowledged (here y and z, not x): ok")
