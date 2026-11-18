#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Command line: `python -m concord_comparison server` runs the SLIM node, `... agent NAME` runs one participant, over SLIM (the default)
or, with `--transport star`, over direct connections to the facilitator, the centre of a star (no SLIM node is needed)."""
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

from concord_comparison.agent import ConcordAgent
from concord_comparison.agent import client_from_env
from concord_comparison.free import FreeAgent, FreeFacilitator
from concord_comparison.facilitator import ConcordFacilitator, DelegatingFacilitator, DelegatingFreeFacilitator
from concord_comparison.fake_llm import FakeLLM
from concord_comparison.prompts import SESSION_NOTE, system_prompt
from concord_comparison.scenario import AGENTS, FACILITATOR, FACT_ORDER_SEED, Scenario

from concord_comparison.star import Hub, Spoke
from concord_comparison.transport import Group, serve

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
            "threshold": args.threshold, "fresh": args.fresh, "session": session, "timeout": args.timeout,
            "scenario": scenario.id, "scenario_file": args.scenario, "fact_order_seed": FACT_ORDER_SEED,
            "endpoint_host": urlparse(os.environ.get("LLM_BASE_URL", "")).netloc or None,
            "versions": {"python": sys.version.split()[0], "ioc_concord": _version("ioc-concord"), "anthropic": _version("anthropic"),
                         "slim_bindings": _version("slim-bindings")},
            "code": _git(HERE), "system_prompt": system}


def transport_of(args):
    """How this agent talks to the others: a SLIM group session, or a star whose centre is the facilitator."""
    me = args.name
    if args.transport == "slim":
        return Group(f"{NAMESPACE}/{me}", url=f"http://{args.slim}")
    if me != FACILITATOR:
        return Spoke(me, args.hub)
    return Hub(me, args.hub, list(AGENTS))


async def run_agent(args):
    # run.sh stops the others when one crashes, and batch.py stops a run that takes too long: both with SIGTERM, which must still
    # write the record of the agent (the `finally` below), and not end it without a trace
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    scenario = Scenario(args.scenario)
    me = args.name
    out_dir = Path(args.out)
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s {me}] {msg}", flush=True)

    group = transport_of(args)
    needs_llm = not (me == FACILITATOR and args.mode == "concord" and args.transport == "slim")      # that facilitator has no model
    llm = (FakeLLM() if args.fake_llm else client_from_env(args.fresh)) if needs_llm else None
    if me == FACILITATOR and args.mode == "free" and args.transport == "star":     # the hub routes with its model: delegate_to
        agent = DelegatingFreeFacilitator(me, group, scenario, AGENTS, out_dir, llm, args.model,
                                          temperature=args.temperature, idle=args.idle, max_time=args.max_time, max_posts=args.max_posts,
                                          config=run_config(args, scenario, "", None), log=log)
    elif me == FACILITATOR and args.mode == "free":
        agent = FreeFacilitator(me, group, scenario, out_dir, llm, args.model,
                                temperature=args.temperature, idle=args.idle, max_time=args.max_time, max_posts=args.max_posts,
                                config=run_config(args, scenario, "", None), log=log)
    elif me == FACILITATOR and args.transport == "star":     # the hub routes with its model: delegate_to
        agent = DelegatingFacilitator(me, group, scenario, AGENTS, out_dir, llm,
                                      args.model, temperature=args.temperature, threshold=args.threshold, timeout=args.timeout,
                                      config=run_config(args, scenario, "", None), log=log)
    elif me == FACILITATOR:
        agent = ConcordFacilitator(me, group, scenario, AGENTS, out_dir, threshold=args.threshold, timeout=args.timeout,
                                   config=run_config(args, scenario, "", None), log=log)
    else:
        system = system_prompt(scenario, me, args.mode)
        run_nonce = None
        if args.fresh:
            # a request that is new every run: an exact-match cache on the way cannot answer it from an earlier run
            run_nonce = uuid.uuid4().hex[:8]
            system += SESSION_NOTE.format(nonce=run_nonce)
            log(f"fresh: session {run_nonce}")
        make = FreeAgent if args.mode == "free" else ConcordAgent
        agent = make(me, group, system, scenario.options, args.model, out_dir, temperature=args.temperature, fresh=args.fresh,
                             config=run_config(args, scenario, system, run_nonce), log=log,
                             llm=llm)
    await group.connect()
    log("connected")
    if args.transport == "star":
        if me == FACILITATOR:
            await group.create()
            log("the centre of the star: every spoke is connected")
    elif me == FACILITATOR:
        await group.create(f"{NAMESPACE}/group", [f"{NAMESPACE}/{a}" for a in AGENTS])
        log("session created, everybody invited")
    else:
        await group.join()
        log("joined the session")
    try:
        await agent.run()
    finally:
        report = agent.finish()
        await group.close()
    if args.mode == "free":
        chosen = (report or {}).get("option")
        log(f"{report}, correct answer {scenario.correct!r}, tokens {agent.usage}")
        return exit_status(me, chosen == scenario.correct)
    winner = (report or {}).get("winner")
    log(f"outcome {report and report['outcome']}, status {report and report['status']}, winner {winner and winner['candidate']!r}, "
        f"correct answer {scenario.correct!r}, tokens {agent.usage}")
    return exit_status(me, bool(winner) and winner["candidate"] == scenario.correct)


def exit_status(me, correct):
    """The facilitator's status says if the outcome is the correct answer. A participant exits 0 whatever it chose: for it, a non-zero
    status means that it crashed, which run.sh watches for."""
    return (0 if correct else 1) if me == FACILITATOR else 0


def _temperature(text):
    return None if text == "default" else float(text)


def main():
    p = argparse.ArgumentParser(prog="concord_comparison")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("server", help="run a SLIM node")
    s.add_argument("--slim", default="127.0.0.1:12345")
    a = sub.add_parser("agent", help="run one participant")
    a.add_argument("name", choices=AGENTS + (FACILITATOR,))
    a.add_argument("--slim", default="127.0.0.1:12345")
    a.add_argument("--mode", choices=("concord", "free"), default="concord",
                   help="concord: the CONCORD protocol (the facilitator starts the run); free: free speech on the channel (over a star, the facilitator's model routes every post)")
    a.add_argument("--idle", type=float, default=60, help="free: the facilitator closes after this many seconds without a post")
    a.add_argument("--max-time", type=float, default=600, help="free: ... or after this many seconds in all")
    a.add_argument("--max-posts", type=int, default=100, help="free: ... or after this many posts")
    a.add_argument("--transport", choices=("slim", "star"), default="slim",
                   help="slim: a SLIM group session; star: direct connections, the facilitator is the centre (default slim)")
    a.add_argument("--hub", default="127.0.0.1:12346", help="star: where the centre (the facilitator) listens")
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
    except (KeyboardInterrupt, asyncio.CancelledError):
        sys.exit(130)


if __name__ == "__main__":
    main()
