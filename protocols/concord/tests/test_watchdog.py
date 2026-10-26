"""The watchdog wakes the orchestrator when nothing happens: it returns the nudge after a stall, escalates to a decision
(STATUS: ASK) after a few useless nudges, and resets when the run moves. Usage: python3 test_watchdog.py <skills dir>"""
import os, re, subprocess, sys, tempfile, time

SK = sys.argv[1]
INI = f"{SK}/skills/concord-initiator/scripts/concord_initiator.py"
tmp = tempfile.mkdtemp()


def sh(*cmd, inp=None):
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, input=inp, cwd=tmp, timeout=60)
    assert p.returncode == 0, (cmd, p.stdout, p.stderr)
    return p.stdout, p.stderr


out, _ = sh(INI, "init", "--quiet", "--no-relay", "--agents", "a,b", "--schema", "s", "--context", "c", "--x-reject", "r",
            "--x-ideal", "i")
ST = re.search(r"State: (\S+)", out).group(1)
sh(INI, "--state", ST, "handle", inp="")                        # the first message goes out, nobody answers
wd = lambda: sh(INI, "--state", ST, "watchdog", "--timeout", "2", "--max-nudges", "2")

t0 = time.time(); o, e = wd(); dt = time.time() - t0
assert o.startswith("STATUS: SEND") and "<concord-anchor>" in o and "nudge 1 of 2" in e, (o, e)   # the request again
assert 1.5 <= dt < 8, dt
o, e = wd()
assert o.startswith("STATUS: SEND") and "nudge 2 of 2" in e, (o, e)
o, e = wd()
assert o.startswith("STATUS: ASK") and "stuck" in o and "mark-missing" in o, o                      # now a decision
print("stall: nudge, nudge, then a decision for the orchestrator: ok")

# progress resets the count
reply = ('<concord-anchor-reply agent="a"><raw_reject>5</raw_reject><raw_ideal>90</raw_ideal></concord-anchor-reply>')
sh(INI, "--state", ST, "handle", inp=reply)
o, e = wd()
assert o.startswith("STATUS: SEND") and "nudge 1 of 2" in e, (o, e)
print("progress resets the nudge count: ok")
