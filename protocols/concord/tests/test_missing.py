"""An agent that stays silent: the orchestrator marks it missing and passes the notice on, and every agent reaches
BEST_EFFORT by itself, with no commit. Usage: python3 test_missing.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
AG = ["a", "b", "c"]
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def sh(*cmd, inp=None):
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, input=inp, env=env, cwd=tmp)
    assert p.returncode == 0, (cmd, p.stdout, p.stderr)
    return p.stdout, p.stderr


def status(out):
    first, _, rest = out.partition("\n")
    assert first.startswith("STATUS: "), out
    return first[8:].strip(), rest.strip()


out, _ = sh(INI, "init", "--quiet", "--agents", "a,b,c", "--schema", "s", "--context", "c", "--x-reject", "r",
            "--x-ideal", "i", "--satisfaction-threshold", "0.7", "-B", "1")
ST = re.search(r"State: (\S+)", out).group(1)
inbox = {a: [] for a in AG}


def deliver(payload):
    parts = re.split(r"^--- to: (.+)$", payload, flags=re.M)
    if len(parts) == 1:
        for a in AG: inbox[a].append(parts[0].strip())
    else:
        for i in range(1, len(parts), 2):
            for a in [x.strip() for x in parts[i].split(",")]: inbox[a].append(parts[i + 1].strip())


def lead(text=""):
    o, e = sh(INI, "--state", ST, "handle", inp=text)
    st, payload = status(o)
    if st == "SEND": deliver(payload)
    return st, payload, e


def answer(me, prompt):
    data = {}
    for task in re.findall(r"^TASK: (.*)$", prompt, re.M):
        kind, _, arg = task.partition(" ")
        if kind == "anchor": data["anchor"] = {"raw_reject": 5, "raw_ideal": 90}
        elif kind == "seed": data["seed"] = {"candidate": f"seed {me}", "rationale": "a stubbed rationale of enough length"}
        elif kind == "score":
            data.setdefault("scores", []).extend({"id": c, "raw": 80 if c.startswith("r") else (85 if c == f"seed-{me}" else 40),
                                                  "reasoning": "a stubbed reason of enough length"} for c in arg.split(","))
        elif kind == "repair":
            data["repair"] = {"candidate": "better", "rationale": "a stubbed rationale of enough length"}
            data.setdefault("scores", []).append({"id": arg, "raw": 88, "reasoning": "a stubbed reason of enough length"})
    return status(sh(REC, "--me", me, "answer", inp=json.dumps(data))[0])[1]


def agents_step(silent=()):
    replies = []
    for me in AG:
        if not inbox[me]:
            continue
        text, inbox[me] = "\n\n".join(inbox[me]), []
        st, payload = status(sh(REC, "--me", me, "handle", inp=text)[0])
        if me in silent:
            continue                                   # c "never replies": it asks, but nothing is sent
        if st == "SEND":
            replies.append(payload)                    # an acknowledgement (relay mode)
            st, payload = status(sh(REC, "--me", me, "due")[0])
        if st == "ASK":
            replies.append(answer(me, payload))
    return replies


lead()
last_phase_scoring = False
for _ in range(30):
    rs = agents_step(silent=("c",) if last_phase_scoring else ())
    st, payload, err = lead("\n\n".join(rs))
    state = json.load(open(os.path.join(tmp, ST)))
    if len(state["pool"]) == 3 and not last_phase_scoring:
        last_phase_scoring = True                      # from now on c stays silent
    if state["outcome"] or (not rs and not any(inbox.values())):
        break
state = json.load(open(os.path.join(tmp, ST)))
# c never rated: the orchestrator marks it missing on every candidate it owes
for _ in range(6):
    owed = [cid for cid in [c["id"] for c in state["pool"]] if cid not in state["raw"].get("c", {})]
    if state["outcome"] or not owed:
        break
    for cid in owed:
        o, e = sh(INI, "--state", ST, "mark-missing", "c", cid)
        st, payload = status(o)
        assert st == "SEND" and "<concord-missing" in payload and "--- to: a, b" in payload, o
        deliver(payload)
    for _ in range(4):                                 # the others run on what they hold, a repair may follow
        rs = agents_step(silent=("c",))
        if not rs: break
        lead("\n\n".join(rs))
    state = json.load(open(os.path.join(tmp, ST)))
assert state["outcome"] == "BEST_EFFORT", state["outcome"]
rep = sh(INI, "--state", ST, "handle", inp="")[0]
assert "outcome BEST_EFFORT" in rep and "Never rated" in rep and "c" in rep, rep
for me in ("a", "b"):
    o = sh(REC, "--me", me, "due")[0]
    assert o.startswith("STATUS: NONE") and "BEST_EFFORT" in o, (me, o)       # reached by their own engines
print("silent agent: mark-missing passed on, agents a and b reach BEST_EFFORT on their own, no commit: ok")
