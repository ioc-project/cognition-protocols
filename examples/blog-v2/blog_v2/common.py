#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the agents of the example share: the model call, the conversation, the record of an agent. (What a model reads is in prompts.py.)"""
import json
import time

MAX_TOOL_ROUNDS = 4       # model calls a turn may take to get one accepted tool call (three times as many for a role with data tools)
SLOW_CALL = 30        # seconds: a model call that takes longer is logged, and the longest one is kept in the record

NO_DECISION = "no decision"
ORCH = "orchestrator"                      # the node of the coordinator, in the scenarios that have one


def sender_of(raw):
    """The agent name inside the name of a sender (a SLIM name, or the name a mesh link gives)."""
    from blog_v2.scenario import AGENTS
    return next((a for a in AGENTS + (ORCH,) if a in str(raw)), str(raw))


def data_tools_for(scenario, me):
    """The data tools of a role in a scenario of the mas-lab library (the harness runs them; the model reads the result): {name: definition}."""
    if scenario.kind != "mas":
        return {}
    from blog_v2 import tools_mas
    return tools_mas.tool_defs(scenario, me)


def run_data_tool(scenario, me, block, events, log):
    """One call of a data tool: the result goes back to the model as the result of its call, and the turn goes on."""
    from blog_v2 import tools_mas
    note(events, log, "tool_call", tool=block.name, args=block.input)
    return {"type": "tool_result", "tool_use_id": block.id, "content": tools_mas.run_tool(scenario, me, block.name, block.input),
            "is_error": False}


def wrong_choice(scenario):
    """What a model reads when its choice is not one of the options (or not in the open format of the task)."""
    options = scenario.options
    if options.open:
        return f"The option must be: {options.description} (or {NO_DECISION!r})."
    return f"The option must be one of: {', '.join(list(options) + [NO_DECISION])}."


def pick(scenario, option):
    """The canonical form of a choice (`no decision` is one), or None when it is not a choice."""
    return option if option == NO_DECISION else scenario.normalize(option)


async def wait_opening(group, timeout=120):
    """What a leader does first when a coordinator opens the session: wait for its opening (the first message of the coordinator that is not
    a CONCORD message) and return the text."""
    from blog_v2.prompts import read_hub_post
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = await group.recv(timeout=1.0)
        if not got:
            continue
        who, text = sender_of(got[0]), got[1]
        if who == "hub":                              # a star: the centre says who wrote it, in front of the text, or it is its own
            who, text = read_hub_post(text) or (ORCH, text)
        if who == ORCH and not text.lstrip().startswith("<"):
            return text
    raise TimeoutError("the coordinator did not open the session")


def tool_def(name, description, properties, required):
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": properties, "required": required}}


def say(messages, blocks):
    """Add to what the model reads. Two messages in a row of the same role are one."""
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": "user", "content": list(blocks)})


def new_usage():
    """The use of the model by one agent. `input` is what the endpoint counts as new input; what it read from a cache (`cache_read`) or
    wrote to one (`cache_write`) is counted apart, so the cost of a run is the sum of the three."""
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}


async def chat(llm, model, system, tools, messages, usage, temperature=None, max_tokens=2048, log=None):
    """One call of the model, which must answer with a tool: the answer is added to `messages` and the use to `usage`."""
    # this SDK has no `temperature` argument: it goes in the body of the request
    extra = {} if temperature is None else {"extra_body": {"temperature": temperature}}
    started = time.monotonic()
    resp = await llm.messages.create(model=model, max_tokens=max_tokens, system=system, tools=tools, tool_choice={"type": "any"},
                                     messages=messages, **extra)
    took = time.monotonic() - started
    usage["max_seconds"] = round(max(usage.get("max_seconds", 0), took), 1)
    if log and took > SLOW_CALL:
        log(f"slow model call: {took:.0f}s")
    usage["calls"] += 1
    if "first_call_raw" not in usage:                                  # which fields the endpoint returns, to read a record by
        usage["first_call_raw"] = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else vars(resp.usage)
    usage["input"] += resp.usage.input_tokens
    usage["output"] += resp.usage.output_tokens
    usage["cache_read"] += getattr(resp.usage, "cache_read_input_tokens", None) or 0       # absent when the endpoint does not cache
    usage["cache_write"] += getattr(resp.usage, "cache_creation_input_tokens", None) or 0
    messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
    return resp


def note(events, log, name, **detail):
    """Record an event of an agent, and log it."""
    events.append({"event": name, "at": time.time(), **detail})
    log(f"event {name} {json.dumps(detail, ensure_ascii=False, default=str)[:300]}")


def write_record(out_dir, me, **fields):
    """The record of an agent: <out_dir>/<me>.json."""
    record = {"agent": me, "usage": new_usage(), "events": [], "transcript": [], "status": None, **fields}
    (out_dir / f"{me}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


async def flush(step, group, events, log):
    """Do what the node says: log its events and send its messages."""
    for event in step.events:
        events.append(event)
        log(f"event {event['event']} {json.dumps({k: v for k, v in event.items() if k not in ('event', 'at')}, default=str)[:300]}")
    for text in step.send:
        await group.send(text)


def max_rounds(data_tools):
    return MAX_TOOL_ROUNDS * 3 if data_tools else MAX_TOOL_ROUNDS


async def tool_turn(agent, tools, handle, no_tool, data=True, settled=None):
    """The model calls of one turn of `agent`: it answers with tools until one is accepted (True), or the rounds are over (False).

    `handle(name, args)` runs a tool of the turn and returns (what the model reads, is_error); the tool is accepted when it is not an error.
    A tool of the role (`agent.data_tools`) is run by the harness when `data` is set, and the turn goes on. `no_tool` is what the model
    reads when it called nothing. `settled()`, when it is true after a refusal, ends the turn: asking again changes nothing."""
    for _ in range(agent.max_rounds):
        resp = await chat(agent.llm, agent.model, agent.system, tools, agent.messages, agent.usage, agent.temperature, log=agent.log)
        results, accepted = [], False
        for block in resp.content:
            if block.type != "tool_use":
                continue
            agent.log(f"tool {block.name} {json.dumps(block.input, ensure_ascii=False)[:600]}")
            if data and block.name in agent.data_tools:
                results.append(run_data_tool(agent.scenario, agent.me, block, agent.events, agent.log))
                continue
            content, is_error = await handle(block.name, block.input)
            if is_error:
                agent.log(f"tool {block.name} {content}")
            accepted = accepted or not is_error
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
        if not results:
            results.append({"type": "text", "text": no_tool})
        say(agent.messages, results)
        if accepted:
            return True
        if settled and settled():
            return False
    return False
