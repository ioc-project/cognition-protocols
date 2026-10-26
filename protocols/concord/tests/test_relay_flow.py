"""No group communication at all: the orchestrator and three agents exchange only point-to-point messages. The
orchestrator relays every reply it records to the other agents, so each agent builds the same picture and acts alone
(rates, repairs). A stub plays the LLM. Usage: python3 test_relay_flow.py <skills dir>"""
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
    return p.stdout


def status(out):
    first, _, rest = out.partition("\n")
    assert first.startswith("STATUS: "), out
    return first[8:].strip(), rest.strip()


out = sh(INI, "init", "--quiet", "--agents", "a,b,c", "--schema", "s", "--context", "c", "--x-reject", "r",
         "--x-ideal", "i", "--satisfaction-threshold", "0.7")
ST = re.search(r"State: (\S+)", out).group(1)
inbox = {a: [] for a in AG}
LOSS = int(os.environ.get("LOSS_ROUNDS", "0"))          # lead outputs for which everything sent to agent c is lost
lead_calls = [0]
sent = {"lead": 0, "agents": 0}


def lead(text=""):
    st, payload = status(sh(INI, "--state", ST, "handle", inp=text))
    lead_calls[0] += 1
    if st != "SEND":
        return st
    lost = lead_calls[0] <= LOSS
    parts = re.split(r"^--- to: (.+)$", payload, flags=re.M)
    if len(parts) == 1:                                  # one message for everyone awaited
        for a in AG:
            if not (lost and a == "c"):
                inbox[a].append(parts[0].strip())
            sent["lead"] += 1
    else:
        for i in range(1, len(parts), 2):
            for a in [x.strip() for x in parts[i].split(",")]:
                if not (lost and a == "c"):
                    inbox[a].append(parts[i + 1].strip())
                sent["lead"] += 1
    return st


def stub_answer(me, prompt):
    data = {}
    for task in re.findall(r"^TASK: (.*)$", prompt, re.M):
        kind, _, arg = task.partition(" ")
        if kind == "anchor": data["anchor"] = {"raw_reject": 5, "raw_ideal": 90}
        elif kind == "seed": data["seed"] = {"candidate": f"seed {me}", "rationale": "a stubbed rationale of enough length"}
        elif kind == "score":
            data.setdefault("scores", []).extend({"id": c, "raw": 80 if c.startswith("r") else (85 if c == f"seed-{me}" else 40),
                                                  "reasoning": "a stubbed reason of enough length"} for c in arg.split(","))
        elif kind == "repair":
            data["repair"] = {"candidate": f"better {arg}", "rationale": "a stubbed rationale of enough length"}
            data.setdefault("scores", []).append({"id": arg, "raw": 88, "reasoning": "a stubbed reason of enough length"})
    flags = []                                            # the answer is given with plain flags, as the task prompt shows
    if "anchor" in data: flags += ["--reject", str(data["anchor"]["raw_reject"]), "--ideal", str(data["anchor"]["raw_ideal"])]
    for key in ("seed", "repair"):
        if key in data: flags += ["--candidate", data[key]["candidate"], "--rationale", data[key]["rationale"]]
    for sc in data.get("scores", []): flags += ["--score", sc["id"], str(sc["raw"]), sc["reasoning"]]
    st, reply = status(sh(REC, "--me", me, "answer", *flags))
    assert st == "SEND", reply
    return reply


repairs = set()
final = {}
lead()                                                    # the first message
for step in range(40):
    replies = []
    for me in AG:
        if not inbox[me]:
            continue
        text, inbox[me] = "\n\n".join(inbox[me]), []
        st, payload = status(sh(REC, "--me", me, "handle", inp=text))
        if st == "SEND":
            replies.append(payload)                           # an acknowledgement (relay mode): it goes to the orchestrator
            st, payload = status(sh(REC, "--me", me, "due"))   # and the agent's own status is what `due` says
        final[me] = (st, payload)
        if st == "ASK":
            if "REPAIR ROUND, YOU ALONE" in payload:
                repairs.add(me)
            replies.append(stub_answer(me, payload)); sent["agents"] += 1
    if all(final.get(me, ("", ""))[0] == "NONE" and "SUCCESS" in final.get(me, ("", ""))[1] for me in AG):
        break
    if not replies and not any(inbox.values()):
        nudges = globals().get("nudges", 0) + 1
        globals()["nudges"] = nudges
        if nudges > 4:
            break
        lead("")                                          # stuck: the nudge
        continue
    lead("\n\n".join(replies))
done = {me: final[me] for me in AG}
# Every agent that received its messages reaches the outcome by itself. An agent whose messages were lost may never
# learn that the run is over (there is no commit): it stays idle, which does no harm, so it is not required to.
reliable = [me for me in AG if not (LOSS and me == "c")]
assert all(done[me][0] == "NONE" and "SUCCESS" in done[me][1] for me in reliable), done
assert len(repairs) == 1, repairs                          # only one agent ever repairs
state = json.load(open(os.path.join(tmp, ST)))
assert state["outcome"] == "SUCCESS", state["outcome"]
print(f"relay flow with point-to-point messages only (lost rounds: {LOSS}, nudges: {globals().get('nudges', 0)}): ok (repair by {sorted(repairs)}, orchestrator sent {sent['lead']} messages, agents {sent['agents']} replies)")

