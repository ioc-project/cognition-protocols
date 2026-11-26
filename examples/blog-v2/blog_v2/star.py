#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A star over direct connections (TCP), the other transport of the example: no SLIM.

The centre (`Hub`, the facilitator) listens; each spoke (`Spoke`, a participant) connects to it and to nothing else.
A spoke sends every message to the centre only. The centre sends its own messages to every spoke, and what it passes on of the others
is up to its agent (`send_to`): here a model decides.

The framing of a message is in `wire.py`; the first message of a spoke is its name.
"""
import asyncio

from blog_v2.transport import SessionClosed
from blog_v2.wire import next_item, pump, read_frame, write_frame


class Hub:
    """The centre of the star. `spokes` are the names it waits for."""

    def __init__(self, local, listen, spokes):
        self.local = local
        self.host, _, port = listen.rpartition(":")
        self.port = int(port)
        self.expected = set(spokes)
        self._writers = {}
        self._queue = asyncio.Queue()
        self._server = None

    async def connect(self):
        self._server = await asyncio.start_server(self._accept, self.host, self.port)

    async def _accept(self, reader, writer):
        name = await read_frame(reader)
        if name not in self.expected or name in self._writers:     # not a spoke of this star
            writer.close()
            return
        self._writers[name] = writer
        await pump(reader, self._queue, name)
        self._writers.pop(name, None)
        writer.close()

    async def create(self, timeout=30):
        """Wait until every spoke is connected."""
        async with asyncio.timeout(timeout):
            while set(self._writers) != self.expected:
                await asyncio.sleep(0.1)

    async def _to(self, spokes, text):
        for name in spokes:
            try:
                await write_frame(self._writers[name], text)
            except (KeyError, ConnectionError, OSError):          # a spoke that has gone
                pass

    async def send(self, text):
        """An own message of the agent: to every spoke."""
        await self._to(list(self._writers), text)

    async def send_to(self, names, text):
        """A message to the spokes `names` and to no one else: what the centre does when it decides the recipients itself."""
        await self._to([n for n in names if n in self._writers], text)

    async def recv(self, timeout=1.0):
        got = await next_item(self._queue, timeout)
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
        await write_frame(self._writer, self.local)
        self._reader_task = asyncio.create_task(pump(reader, self._queue, "hub"))

    async def send(self, text):
        try:
            await write_frame(self._writer, text)
        except (ConnectionError, OSError):
            raise SessionClosed()

    async def recv(self, timeout=1.0):
        got = await next_item(self._queue, timeout)
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
