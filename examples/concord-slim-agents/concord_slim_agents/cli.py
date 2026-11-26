#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Command line: `python -m concord_slim_agents server` runs the SLIM node, `... agent NAME` runs one participant, over SLIM (the default)
or, with `--transport star`, over direct connections to participant-a, the centre of a star (no SLIM node is needed)."""
import argparse
import asyncio
import importlib.metadata
import os
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse
from pathlib import Path

from concord_slim_agents.agent import ConcordAgent
from concord_slim_agents.fake_llm import FakeLLM
from concord_slim_agents.fake_skill_llm import FakeSkillLLM
from concord_slim_agents.scenario import AGENTS, FACT_ORDER_SEED, INITIATOR, Scenario, skills_system_prompt, system_prompt
from concord_slim_agents.skill_agent import SkillAgent
from ioc_concord.relay import Relay
from ioc_concord.store import FileStore

from concord_slim_agents.star import Hub, Spoke
from concord_slim_agents.transport import Group, serve

HERE = Path(__file__).resolve().parent.parent
SKILLS = HERE.parent.parent / "protocols" / "concord" / "skills"
NAMESPACE = "concord/evac"


def _git(root):
    """The commit of the code that ran, and whether it had changes that are not committed (None when it is not a git checkout)."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=root, capture_output=True, text=True).stdout.strip())
        return {"commit": sha, "uncommitted_changes": dirty}
    except (OSError, subprocess.CalledProcessError):
        return None


def _version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_config(args, scenario, system, session):
    """What this agent ran with, for the record of the run (never the key: only the host of the endpoint)."""
    return {"agent": args.name, "mode": args.mode, "skills": args.skills if args.mode == "skills" else None, "transport": args.transport, "model": args.model, "fake_llm": args.fake_llm, "temperature": args.temperature,
            "threshold": args.threshold, "fresh": args.fresh, "session": session, "timeout": args.timeout,
            "scenario": scenario.id, "scenario_file": args.scenario, "fact_order_seed": FACT_ORDER_SEED,
            "endpoint_host": urlparse(os.environ.get("LLM_BASE_URL", "")).netloc or None,
            "versions": {"python": sys.version.split()[0], "ioc_concord": _version("ioc-concord"), "anthropic": _version("anthropic"),
                         "slim_bindings": _version("slim-bindings")},
            "code": _git(HERE.parent.parent), "system_prompt": system}


def transport_of(args, out_dir):
    """How this agent talks to the others: a SLIM group session, or a star whose centre is the initiator (which then also relays)."""
    me = args.name
    if args.transport == "slim":
        return Group(f"{NAMESPACE}/{me}", url=f"http://{args.slim}")
    if me != INITIATOR:
        return Spoke(me, args.hub)
    store = FileStore(out_dir / "relay.json")
    out_dir.mkdir(parents=True, exist_ok=True)
    store.path.unlink(missing_ok=True)
    return Hub(me, args.hub, [a for a in AGENTS if a != me], relay=Relay(store=store))


async def run_agent(args):
    scenario = Scenario(args.scenario)
    me = args.name
    out_dir = Path(args.out)
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s {me}] {msg}", flush=True)

    kickoff = None
    if me == INITIATOR and args.mode == "skills":
        kickoff = ("Begin: agree with the other leaders where to evacuate."
                   + (f" Use a satisfaction threshold of {args.threshold}." if args.threshold is not None else "")
                   + (f" Use a timeout of {args.timeout} seconds." if args.timeout is not None else ""))
    elif me == INITIATOR:
        kickoff = ("Begin: agree with the other leaders where to evacuate. Start the CONCORD run now with concord_start: "
                   f"participants {', '.join(AGENTS)}, and the options as the only candidates."
                   + (f" Use satisfaction_threshold {args.threshold}." if args.threshold is not None else ""))
    group = transport_of(args, out_dir)
    system = (skills_system_prompt(scenario, me, args.skills_dir, out_dir / me, preload=args.skills == "preload") if args.mode == "skills"
              else system_prompt(scenario, me))
    run_nonce = None
    if args.fresh:
        # a request that is new every run: an exact-match cache on the way cannot answer it from an earlier run
        run_nonce = uuid.uuid4().hex[:8]
        system += f"\n(session {run_nonce})\n"
        log(f"fresh: session {run_nonce}")
    config = run_config(args, scenario, system, run_nonce)
    if args.mode == "skills":
        agent = SkillAgent(me, group, system, args.model, out_dir, kickoff=kickoff, temperature=args.temperature, fresh=args.fresh,
                           config=config, log=log, llm=FakeSkillLLM(star=args.transport == "star") if args.fake_llm else None, deadline=args.deadline)
    else:
        agent = ConcordAgent(me, group, system, scenario.options, args.model, out_dir,
                             kickoff=kickoff, timeout=args.timeout, threshold=args.threshold, temperature=args.temperature, fresh=args.fresh,
                             config=config, log=log, llm=FakeLLM() if args.fake_llm else None)
    await group.connect()
    log("connected")
    if args.transport == "star":
        if me == INITIATOR:
            await group.create()
            log("the centre of the star: every spoke is connected")
    elif me == INITIATOR:
        await group.create(f"{NAMESPACE}/group", [f"{NAMESPACE}/{a}" for a in AGENTS if a != me])
        log("session created, everybody invited")
    else:
        await group.join()
        log("joined the session")
    try:
        await agent.run()
    finally:
        report = agent.finish()
        await group.close()
    winner = (report or {}).get("winner")
    log(f"outcome {report and report['outcome']}, status {report and report['status']}, winner {winner and winner['candidate']!r}, "
        f"correct answer {scenario.correct!r}, tokens {agent.usage}")
    return 0 if winner and winner["candidate"] == scenario.correct else 1


def _temperature(text):
    return None if text == "default" else float(text)


def main():
    p = argparse.ArgumentParser(prog="concord_slim_agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("server", help="run a SLIM node")
    s.add_argument("--slim", default="127.0.0.1:12345")
    a = sub.add_parser("agent", help="run one participant")
    a.add_argument("name", choices=AGENTS)
    a.add_argument("--slim", default="127.0.0.1:12345")
    a.add_argument("--transport", choices=("slim", "star"), default="slim",
                   help="slim: a SLIM group session; star: direct connections, participant-a is the centre and relays (default slim)")
    a.add_argument("--hub", default="127.0.0.1:12346", help="star: where the centre (participant-a) listens")
    a.add_argument("--scenario", default=str(HERE / "scenarios" / "evacuation-west-city.yaml"))
    a.add_argument("--model", default=os.environ.get("LLM_MODEL"), help="default: $LLM_MODEL, passed to the endpoint as it is")
    a.add_argument("--temperature", type=_temperature, default=0.7,
                   help="sampling temperature of the model, 0 to 1 (default 0.7); 'default' leaves the model's own")
    a.add_argument("--fresh", action=argparse.BooleanOptionalAction, default=True,
                   help="make every request new (a session id in the system prompt, and a no-cache header), so that a cache between "
                        "us and the model cannot replay an earlier run (default on; --no-fresh to allow it)")
    a.add_argument("--threshold", type=float, default=None,
                   help="satisfaction threshold of the run, fixed by the harness (the initiator would choose it otherwise)")
    a.add_argument("--timeout", type=int, default=None, help="CONCORD timeout in seconds (the initiator's choice for the run)")
    a.add_argument("--mode", choices=("lib", "skills"), default="lib",
                   help="lib: the model calls the library's node through tools; skills: the model has a shell and the CONCORD skills, "
                        "and runs their scripts (default lib)")
    a.add_argument("--skills", choices=("lazy", "preload"), default="lazy",
                   help="skills mode: lazy, the model is told where the skills are and reads SKILL.md itself; preload, their SKILL.md "
                        "is in its system prompt (default lazy)")
    a.add_argument("--skills-dir", default=str(SKILLS), help="skills mode: the folder that holds concord-initiator and concord-receiver")
    a.add_argument("--deadline", type=int, default=900, help="skills mode: seconds before an agent gives up on a run that is not over")
    a.add_argument("--fake-llm", action="store_true", help="answer by keywords instead of calling the model (no API key)")
    a.add_argument("--out", default=str(HERE / "runs" / time.strftime("%Y%m%d-%H%M%S")))
    args = p.parse_args()
    if args.cmd == "agent" and not args.model and not args.fake_llm:
        p.error("no model: set LLM_MODEL or pass --model")
    try:
        if args.cmd == "server":
            asyncio.run(serve(args.slim))
        else:
            sys.exit(asyncio.run(run_agent(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
