#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""What the direct-connection transports (`mesh.py`, `star.py`) share: a message on a TCP link is its text, UTF-8, behind four bytes with its
length."""
import asyncio
import struct


async def write_frame(writer, text):
    data = text.encode("utf-8")
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


async def read_frame(reader):
    size, = struct.unpack(">I", await reader.readexactly(4))
    return (await reader.readexactly(size)).decode("utf-8")


async def pump(reader, queue, name):
    """Put what comes in on `queue` as (name, text), and (name, None) when the link is gone. A reader of its own, so that waiting for
    a message with a timeout never leaves half of one read."""
    try:
        while True:
            await queue.put((name, await read_frame(reader)))
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        await queue.put((name, None))


async def next_item(queue, timeout):
    """The next item of `queue`, or None after `timeout` seconds."""
    try:
        return await asyncio.wait_for(queue.get(), timeout)
    except asyncio.TimeoutError:
        return None
