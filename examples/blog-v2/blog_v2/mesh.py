#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""A full mesh over direct connections (TCP): every agent has a link to every other, and nothing in between (no SLIM, no hub).

Each agent listens on a port of its own and dials the agents after it in `AGENTS`; the others dial it, so each pair has one link. The
first message on a link is the name of the dialer. `send` writes the text on the links of the agents it names (every link by default), and
the link says who sent it. The framing of a message is in `wire.py`.
"""
import asyncio

from blog_v2.scenario import AGENTS
from blog_v2.transport import SessionClosed
from blog_v2.wire import pump, read_frame, write_frame


class Mesh:
    """One agent's end of the mesh. `ports` maps each agent name to the port it listens on (all on `host`)."""

    def __init__(self, local, ports, host="127.0.0.1"):
        self.local, self.host, self.ports = local, host, dict(ports)
        self.peers = [a for a in AGENTS if a != local]
        self._writers = {}
        self._queue = asyncio.Queue()
        self._server = None
        self._tasks = []

    async def connect(self, timeout=60):
        """Listen, dial the agents after this one, and wait until all the links are up."""
        self._server = await asyncio.start_server(self._accept, self.host, self.ports[self.local])
        await asyncio.sleep(0)
        for name in AGENTS[AGENTS.index(self.local) + 1:]:
            self._tasks.append(asyncio.create_task(self._dial(name, timeout)))
        async with asyncio.timeout(timeout):
            while set(self._writers) != set(self.peers):
                await asyncio.sleep(0.1)

    async def _dial(self, name, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.ports[name])
                break
            except OSError:
                if loop.time() > deadline:
                    raise
                await asyncio.sleep(0.5)
        await write_frame(writer, self.local)
        self._writers[name] = writer
        await self._serve(reader, writer, name)

    async def _accept(self, reader, writer):
        name = await read_frame(reader)
        if name not in self.peers or name in self._writers:     # not a peer of this mesh
            writer.close()
            return
        self._writers[name] = writer
        await self._serve(reader, writer, name)

    async def _serve(self, reader, writer, name):
        await pump(reader, self._queue, name)
        self._writers.pop(name, None)
        writer.close()

    async def send(self, text, to=None):
        """An own message: to the agents in `to`, or to every other agent."""
        for name, writer in list(self._writers.items()):
            if to is not None and name not in to:
                continue
            try:
                await write_frame(writer, text)
            except (ConnectionError, OSError):                   # a peer that has gone
                pass

    async def recv(self, timeout=1.0):
        try:
            name, text = await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if text is None:                                          # a peer has left
            if not self._writers:
                raise SessionClosed()
            return None
        return name, text

    async def close(self):
        for task in self._tasks:
            task.cancel()
        for writer in list(self._writers.values()):
            writer.close()
        if self._server:
            self._server.close()
