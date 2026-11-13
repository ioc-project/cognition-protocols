#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A star over direct connections (TCP), the other transport of the example: no SLIM.

The centre (`Hub`, participant-a) listens; each spoke (`Spoke`, participant-b and participant-c) connects to it and to nothing else.
A spoke sends every message to the centre only. The centre sends its own messages to every spoke and passes the messages of the spokes
on to the others: which ones, and to whom, is decided by the library's `Relay`, which the hub owns and gives to its agent.

On the wire a message is its text, UTF-8, behind four bytes with its length. The first message of a spoke is its name.
"""
import asyncio
import struct

from ioc_concord.relay import Relay

from concord_slim_agents.transport import SessionClosed


async def _write(writer, text):
    data = text.encode("utf-8")
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


async def _read(reader):
    size, = struct.unpack(">I", await reader.readexactly(4))
    return (await reader.readexactly(size)).decode("utf-8")


async def _pump(reader, queue, name):
    """Put what comes in on `queue` as (name, text), and (name, None) when the link is gone. A reader of its own, so that waiting for
    a message with a timeout never leaves half of one read."""
    try:
        while True:
            await queue.put((name, await _read(reader)))
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        await queue.put((name, None))


async def _next(queue, timeout):
    try:
        return await asyncio.wait_for(queue.get(), timeout)
    except asyncio.TimeoutError:
        return None


class Hub:
    """The centre of the star. `spokes` are the names it waits for; `relay` decides what it passes on."""

    def __init__(self, local, listen, spokes, relay=None):
        self.local = local
        self.host, _, port = listen.rpartition(":")
        self.port = int(port)
        self.expected = set(spokes)
        self.relay = relay or Relay()
        self._writers = {}
        self._queue = asyncio.Queue()
        self._server = None

    @property
    def me(self):
        return self.local

    async def connect(self):
        self._server = await asyncio.start_server(self._accept, self.host, self.port)

    async def _accept(self, reader, writer):
        name = await _read(reader)
        if name not in self.expected or name in self._writers:     # not a spoke of this star
            writer.close()
            return
        self._writers[name] = writer
        self.relay.add_spoke(name)
        await _pump(reader, self._queue, name)
        self._writers.pop(name, None)
        self.relay.remove_spoke(name)
        writer.close()

    async def create(self, timeout=30):
        """Wait until every spoke is connected."""
        async with asyncio.timeout(timeout):
            while set(self._writers) != self.expected:
                await asyncio.sleep(0.1)

    async def _to(self, spokes, text):
        for name in spokes:
            try:
                await _write(self._writers[name], text)
            except (KeyError, ConnectionError, OSError):          # a spoke that has gone
                pass

    async def send(self, text):
        """An own message of the agent: to every spoke."""
        await self._to(self.relay.targets(), text)

    async def forward(self, text, origin):
        """A message to pass on, as it came: to every spoke but the one it came from. Returns who got it."""
        targets = self.relay.targets(origin)
        await self._to(targets, text)
        return targets

    async def recv(self, timeout=1.0):
        got = await _next(self._queue, timeout)
        if got is None:
            return None
        name, text = got
        if text is None:                                          # a spoke has left
            if not self._writers:
                raise SessionClosed()
            return None
        return name, text

    async def close(self):
        for writer in list(self._writers.values()):
            writer.close()
        if self._server:
            self._server.close()


class Spoke:
    """A spoke: one link, to the centre."""

    def __init__(self, local, hub):
        self.local = local
        self.host, _, port = hub.rpartition(":")
        self.port = int(port)
        self._queue = asyncio.Queue()
        self._writer = None
        self._reader_task = None

    @property
    def me(self):
        return self.local

    async def connect(self, tries=30):
        """Connect to the centre, which may not be up yet, and say who this is."""
        for n in range(tries):
            try:
                reader, self._writer = await asyncio.open_connection(self.host, self.port)
                break
            except OSError:
                if n == tries - 1:
                    raise
                await asyncio.sleep(1)
        await _write(self._writer, self.local)
        self._reader_task = asyncio.create_task(_pump(reader, self._queue, "hub"))

    async def send(self, text):
        try:
            await _write(self._writer, text)
        except (ConnectionError, OSError):
            raise SessionClosed()

    async def recv(self, timeout=1.0):
        got = await _next(self._queue, timeout)
        if got is None:
            return None
        if got[1] is None:
            raise SessionClosed()
        return got

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self._writer:
            self._writer.close()
