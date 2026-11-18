#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the agents of the example share: the model call, the conversation, the record of an agent. (What a model reads is in prompts.py.)"""
import json
import time

SLOW_CALL = 30        # seconds: a model call that takes longer is logged, and the longest one is kept in the record

NO_DECISION = "no decision"


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
