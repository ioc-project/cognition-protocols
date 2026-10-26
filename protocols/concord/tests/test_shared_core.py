"""The decision engine has ONE source, shared/concord_core.py, and tools/sync_core.py copies it into the initiator and
receiver scripts (a skill must not import a sibling file). Checks: the copies match the source, the source works on
its own, and no script loads a sibling module. Usage: python3 test_shared_core.py <protocols/concord directory>"""
import importlib.util, subprocess, sys

sk = sys.argv[1]
r = subprocess.run([sys.executable, f"{sk}/tools/sync_core.py", "--check"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr

spec = importlib.util.spec_from_file_location("concord_core_src", f"{sk}/shared/concord_core.py")
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
st = core.new_engine_state(["a", "b"], {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 3})
st["anchors"] = {"a": {"raw_reject": 0, "raw_ideal": 100}, "b": {"raw_reject": 0, "raw_ideal": 100}}
assert core.add_candidate(st, "b", "x", cid="seed-b") == "seed-b"        # out of order is fine: ids name themselves
assert core.add_candidate(st, "a", "y", cid="seed-a") == "seed-a"
assert core.add_candidate(st, "a", "y", cid="seed-a") is None            # a repeat is ignored
core.add_ratings(st, "a", [("seed-a", 80, "w"), ("seed-b", 80, "w")])
core.add_ratings(st, "b", [("seed-a", 80, "w"), ("seed-b", 80, "w")])
core.advance(st)
assert st["outcome"] == "SUCCESS" and st["x_star"] == "seed-a", st      # a tie: canonical order picks seed-a, always

for p in (f"{sk}/skills/concord-initiator/scripts/concord_initiator.py", f"{sk}/skills/concord-receiver/scripts/concord_receiver.py"):
    t = open(p).read()
    assert "sys.path.insert" not in t and "import concord_core" not in t, f"{p} loads a sibling module"
print("single source for the engine: copies match, source works on its own, no sibling imports: ok")
