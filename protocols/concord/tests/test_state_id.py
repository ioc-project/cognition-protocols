"""Each run has a random state id; every message carries it, every reply echoes it, and each agent keeps one state file
per run: runs never mix, and a reply of another run is skipped. Usage: python3 test_state_id.py <skills dir>"""
import json, os, re, subprocess, sys, tempfile, glob

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
REC = f"{SK}/skills/concord-receiver/scripts/concord_receiver.py"
tmp = tempfile.mkdtemp()
env = dict(os.environ, CONCORD_STATE_DIR=f"{tmp}/ledgers")


def sh(*cmd, inp=None):
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, input=inp, env=env, cwd=tmp)
    assert p.returncode == 0, (cmd, p.stdout, p.stderr)
    return p.stdout, p.stderr


def new_run():
    out, _ = sh(INI, "init", "--quiet", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r",
                "--x-ideal", "i", "--no-relay")
    st = re.search(r"State: (\S+)", out).group(1)
    msg, _ = sh(INI, "--state", st, "handle")
    sid = re.search(r"<state_id>(\w+)</state_id>", msg).group(1)
    assert st.endswith(f"-{sid}.json"), (st, sid)           # the id is the random suffix of the state file
    return st, sid, msg.split("\n", 1)[1]


st1, id1, m1 = new_run()
st2, id2, m2 = new_run()
assert id1 != id2
a1, _ = sh(REC, "--me", "a", "handle", inp=m1)
a2, _ = sh(REC, "--me", "a", "handle", inp=m2)
assert "STATUS: ASK" in a1 and "STATUS: ASK" in a2
files = sorted(os.path.basename(f) for f in glob.glob(f"{tmp}/ledgers/state-*.a.json"))
assert files == sorted([f"state-{id1}.a.json", f"state-{id2}.a.json"]), files        # one file per run
ans = json.dumps({"anchor": {"raw_reject": 5, "raw_ideal": 90}})
out2, _ = sh(REC, "--me", "a", "answer", inp=ans)                  # newest run: run 2
assert f'state="{id2}"' in out2
out1, _ = sh(REC, "--me", "a", "--state-id", id1, "answer", inp=ans)   # explicitly run 1
assert f'state="{id1}"' in out1
# the orchestrator of run 1 records its own run's reply and skips run 2's
rec1, err1 = sh(INI, "--state", st1, "handle", inp=out2.split("\n", 1)[1])
assert "another run" in err1 and "recorded 0" in rec1, (rec1, err1)
rec1b, err1b = sh(INI, "--state", st1, "handle", inp=out1.split("\n", 1)[1])
assert "recorded 1" in rec1b, rec1b
print("state id: one state file per run and agent, replies echo it, other runs' replies are skipped: ok")

# a commit ends the run for an agent, even one whose own picture is incomplete
commit = ("<concord-commit><outcome>SUCCESS</outcome><winning_candidate_id>seed-b</winning_candidate_id>"
          f"<state_id>{id1}</state_id><participants><agent>a</agent><agent>b</agent></participants>"
          "<addressed_to></addressed_to></concord-commit>")
out, _ = sh(REC, "--me", "a", "handle", inp=commit)
assert out.startswith("STATUS: NONE") and "SUCCESS" in out and "seed-b" in out, out
print("a commit ends the run for an agent with an incomplete picture: ok")

# a Score request is self-sufficient: an agent that missed every proposal can still be asked to rate them
anc = ("<concord-anchor><x_reject>r</x_reject><x_ideal>i</x_ideal><state_id>zz11</state_id>"
       "<participants><agent>a</agent><agent>b</agent></participants>"
       "<params><satisfaction_threshold>0.6</satisfaction_threshold><eps>0.05</eps><budget>8</budget></params>"
       "<addressed_to><agent>a</agent><agent>b</agent></addressed_to></concord-anchor>")
sh(REC, "--me", "a", "handle", inp=anc)
sh(REC, "--me", "a", "answer", inp=json.dumps({"anchor": {"raw_reject": 5, "raw_ideal": 90}}))
score_req = ("<concord-score><context>c</context><candidates>"
             "<candidate id=\"seed-a\"><text>text a</text><rationale>ra</rationale></candidate>"
             "<candidate id=\"seed-b\"><text>text b</text><rationale>rb</rationale></candidate></candidates>"
             "<state_id>zz11</state_id><participants><agent>a</agent><agent>b</agent></participants>"
             "<addressed_to><agent>a</agent><agent>b</agent></addressed_to></concord-score>")
out, _ = sh(REC, "--me", "a", "handle", inp=score_req)
assert out.startswith("STATUS: ASK") and "TASK: score seed-a,seed-b" in out and "text b" in out, out
print("a Score request alone is enough to be asked to rate: ok")

# being asked again wins over "the run is over": the orchestrator may not have heard from an agent that thinks it is done
sh(REC, "--me", "a", "handle", inp=anc)                                  # (anchor already answered above for run zz11)
commit2 = ("<concord-commit><outcome>SUCCESS</outcome><winning_candidate_id>seed-a</winning_candidate_id>"
           "<state_id>zz11</state_id><participants><agent>a</agent><agent>b</agent></participants>"
           "<addressed_to></addressed_to></concord-commit>")
out, _ = sh(REC, "--me", "a", "handle", inp=commit2)
assert out.startswith("STATUS: NONE") and "run is over" in out, out
out, _ = sh(REC, "--me", "a", "handle", inp=anc)                          # the orchestrator asks for the anchor again
assert out.startswith("STATUS: SEND") and "<concord-anchor-reply" in out, out
print("an agent that thinks the run is over still resends what the orchestrator asks for again: ok")
