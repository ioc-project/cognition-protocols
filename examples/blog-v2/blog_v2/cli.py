#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Command line: `python -m blog_v2 server` runs the SLIM node, `... agent NAME` runs one of the four agents. The transport is a SLIM group
session (the default) or, with `--transport mesh`, direct connections between every pair of agents (no SLIM node is needed)."""
import argparse
import asyncio
import importlib.metadata
import os
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse
from pathlib import Path

from blog_v2.concord_agent import ConcordAgent
from blog_v2.baseline_agent import DEBOUNCE, BaselineAgent
from blog_v2.fake_llm import FakeLLM
from blog_v2.prompts import SESSION_NOTE, orchestrator_prompt, system_prompt
from blog_v2.common import ORCH, client_from_env
from blog_v2.mesh import Mesh
from blog_v2.orchestrator import ConcordOrchestrator, FreeOrchestrator, StarFreeOrchestrator
from blog_v2.scenario import AGENTS, DEFAULT_SCENARIO, FACT_ORDER_SEED, Scenario, set_roster
from blog_v2.star import Hub, Spoke
from blog_v2.transport import Group, serve

HERE = Path(__file__).resolve().parent.parent
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
    return {"agent": args.name, "mode": args.mode, "transport": args.transport, "model": args.model, "fake_llm": args.fake_llm, "temperature": args.temperature,
            "initiator": args.initiator, "orchestrated": args.orchestrated, "idle": args.idle, "debounce": args.debounce, "max_time": args.max_time, "max_posts": args.max_posts,
            "threshold": args.threshold, "fresh": args.fresh, "session": session, "timeout": args.timeout,
            "scenario": scenario.id, "scenario_file": args.scenario, "agents": list(scenario.agents), "fact_order_seed": FACT_ORDER_SEED,
            "endpoint_host": urlparse(os.environ.get("LLM_BASE_URL", "")).netloc or None,
            "versions": {"python": sys.version.split()[0], "ioc_concord": _version("ioc-concord"), "anthropic": _version("anthropic"),
                         "slim_bindings": _version("slim-bindings")},
            "code": _git(HERE), "system_prompt": system}


def variant_of(args):
    """With a coordinator, which scenario this is: free_star (O1), free_slim (O2), concord (O3); None without one."""
    if not args.orchestrated:
        return None
    return "concord" if args.mode == "concord" else f"free_{args.transport}"


def transport_of(args):
    """How this agent talks to the others: a SLIM group session, a star (a coordinator at the centre) or a full mesh."""
    me = args.name
    if args.transport == "slim":
        return Group(f"{NAMESPACE}/{me}", url=f"http://{args.slim}")
    if args.transport == "star":                    # the coordinator is the centre; the leaders are the spokes
        return Hub(me, args.hub, list(AGENTS)) if me == ORCH else Spoke(me, args.hub)
    ports = [int(p) for p in args.mesh_ports.split(",")]
    return Mesh(me, dict(zip(AGENTS, ports)))


def build_agent(args, scenario, group, llm, log):
    """The agent this process runs: a participant (free speech, or CONCORD) or the coordinator, with the prompt it is given and its record."""
    me, out_dir, variant = args.name, Path(args.out), variant_of(args)
    if me == ORCH:
        system = orchestrator_prompt(scenario, variant)
    else:
        system = system_prompt(scenario, me, args.mode, initiator=args.mode == "concord" and me == args.initiator and not args.orchestrated,
                               variant=variant, directed=args.transport == "mesh")
    run_nonce = None
    if args.fresh:
        # a request that is new every run: an exact-match cache on the way cannot answer it from an earlier run
        run_nonce = uuid.uuid4().hex[:8]
        system += SESSION_NOTE.format(nonce=run_nonce)
        log(f"fresh: session {run_nonce}")
    common = dict(temperature=args.temperature, config=run_config(args, scenario, system, run_nonce), log=log)
    free = dict(idle=args.idle, max_time=args.max_time, max_posts=args.max_posts)
    if me == ORCH:
        if variant == "free_star":
            return StarFreeOrchestrator(group, scenario, out_dir, llm, args.model, **free, **common)
        if variant == "free_slim":
            return FreeOrchestrator(group, scenario, out_dir, llm, args.model, variant="free_slim", **free, **common)
        return ConcordOrchestrator(group, scenario, out_dir, llm, args.model, threshold=args.threshold, timeout=args.timeout, **common)
    if args.mode == "free":
        return BaselineAgent(me, group, system, scenario, args.model, out_dir, fresh=args.fresh, **free, debounce=args.debounce, llm=llm,
                         coordinated=args.orchestrated, directed=args.transport == "mesh", **common)
    return ConcordAgent(me, group, system, scenario, args.model, out_dir, initiator=me == args.initiator and not args.orchestrated,
                        threshold=args.threshold, timeout=args.timeout, fresh=args.fresh, llm=llm, coordinated=args.orchestrated, **common)


async def connect(args, group, log):
    """Connect to the others. Over SLIM the moderator (a transport role, whatever the protocol does) creates the session and invites them."""
    me = args.name
    await group.connect()
    log("connected")
    if args.transport == "star":
        if me == ORCH:
            await group.create()
            log("the centre of the star: every leader is connected")
    elif args.transport == "slim":
        moderator = ORCH if args.orchestrated else AGENTS[0]
        everybody = AGENTS + (ORCH,) if args.orchestrated else AGENTS
        if me == moderator:
            await group.create(f"{NAMESPACE}/group", [f"{NAMESPACE}/{a}" for a in everybody if a != me])
            log("session created, everybody invited")
        else:
            await group.join()
            log("joined the session")
        await group.ready(everybody)
        log("everybody is in the session")


def outcome_line(args, agent, scenario, report):
    """What the agent says at the end: the outcome of the run, the right answer and the tokens used."""
    if args.name == ORCH:
        return (f"outcome {report and {k: v for k, v in report.items() if k in ('outcome', 'option', 'status', 'winner')}}, "
                f"correct answer {scenario.correct!r}, tokens {agent.usage}")
    if args.mode == "concord":
        winner = (report or {}).get("winner")
        return (f"outcome {report and report['outcome']}, status {report and report['status']}, winner {winner and winner['candidate']!r}, "
                f"correct answer {scenario.correct!r}, tokens {agent.usage}")
    return f"{report}, correct answer {scenario.correct!r}, tokens {agent.usage}"


async def run_agent(args):
    # run.sh stops the others when one crashes, and batch.py stops a run that takes too long: both with SIGTERM, which must still
    # write the record of the agent (the `finally` below), and not end it without a trace
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    scenario = Scenario(args.scenario)
    set_roster(scenario.agents)                    # the agents of this scenario: the leaders, or the roles of a mas-lab app
    me = args.name
    if me not in scenario.agents + (ORCH,):
        sys.exit(f"{me} is not an agent of {scenario.id}: {', '.join(scenario.agents + (ORCH,))}")
    if scenario.kind == "mas" and not args.orchestrated:
        sys.exit("the scenarios of the mas-lab library run with a coordinator (--orchestrated) for now")
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s {me}] {msg}", flush=True)

    group = transport_of(args)
    agent = build_agent(args, scenario, group, FakeLLM() if args.fake_llm else client_from_env(args.fresh), log)
    await connect(args, group, log)
    try:
        await agent.run()
    finally:
        report = agent.finish()
        await group.close()
    log(outcome_line(args, agent, scenario, report))
    return 0              # a non-zero status means that the agent crashed, which run.sh watches for


def _temperature(text):
    return None if text == "default" else float(text)


def main():
    p = argparse.ArgumentParser(prog="blog_v2")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roster", help="print the names of the agents of a scenario (run.sh starts one process for each)")
    r.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    r.add_argument("--orchestrated", action="store_true")
    s = sub.add_parser("server", help="run a SLIM node")
    s.add_argument("--slim", default="127.0.0.1:12345")
    a = sub.add_parser("agent", help="run one participant")
    a.add_argument("name", help="an agent of the scenario, or `orchestrator` (with --orchestrated)")
    a.add_argument("--slim", default="127.0.0.1:12345")
    a.add_argument("--orchestrated", action="store_true",
                   help="a coordinator (agent `orchestrator`, a fifth model with no facts) opens the session and runs it")
    a.add_argument("--mode", choices=("concord", "free"), default="concord",
                   help="concord: the CONCORD protocol (the initiator starts the run); free: free speech, every post goes to all the others")
    a.add_argument("--idle", type=float, default=60, help="free: an agent stops after this many seconds without a post (its own included)")
    a.add_argument("--debounce", type=float, default=DEBOUNCE,
                   help="free: seconds an agent waits after a message for the ones that come with it, before it reads (0: at once; what arrives "
                        "while the model works is read together afterwards)")
    a.add_argument("--max-time", type=float, default=600, help="free: ... or after this many seconds in all")
    a.add_argument("--max-posts", type=int, default=100, help="free: ... or after this many posts")
    a.add_argument("--transport", choices=("slim", "mesh", "star"), default="slim",
                   help="slim: a SLIM group session; mesh: direct connections between every pair of agents; star (with --orchestrated): the coordinator is the centre (default slim)")
    a.add_argument("--hub", default="127.0.0.1:12346", help="star: where the centre (the coordinator) listens")
    a.add_argument("--mesh-ports", default="12350,12351,12352,12353", help="mesh: the port each agent listens on, in the order agent-a..agent-d")
    a.add_argument("--initiator", default=AGENTS[0], choices=AGENTS, help="concord: the agent that starts the run (it is a participant too)")
    a.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    a.add_argument("--model", default=os.environ.get("LLM_MODEL"), help="default: $LLM_MODEL, passed to the endpoint as it is")
    a.add_argument("--temperature", type=_temperature, default=0.7,
                   help="sampling temperature of the model, 0 to 1 (default 0.7); 'default' leaves the model's own")
    a.add_argument("--fresh", action=argparse.BooleanOptionalAction, default=True,
                   help="make every request new (a session id in the system prompt, and a no-cache header), so that a cache between "
                        "us and the model cannot replay an earlier run (default on; --no-fresh to allow it)")
    a.add_argument("--threshold", type=float, default=None,
                   help="satisfaction threshold of the run, fixed by the harness (default 0.6)")
    a.add_argument("--timeout", type=int, default=None, help="CONCORD timeout in seconds (the initiator's choice for the run)")
    a.add_argument("--fake-llm", action="store_true", help="answer by keywords instead of calling the model (no API key)")
    a.add_argument("--out", default=str(HERE / "runs" / time.strftime("%Y%m%d-%H%M%S")))
    args = p.parse_args()
    if args.cmd == "agent" and not args.model and not args.fake_llm:
        p.error("no model: set LLM_MODEL or pass --model")
    try:
        if args.cmd == "roster":
            print(" ".join(Scenario(args.scenario).agents + ((ORCH,) if args.orchestrated else ())))
        elif args.cmd == "server":
            asyncio.run(serve(args.slim))
        else:
            sys.exit(asyncio.run(run_agent(args)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        sys.exit(130)


if __name__ == "__main__":
    main()
