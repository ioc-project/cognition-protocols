#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""SLIM: an in-process server, and a group session that carries the CONCORD messages as text.

Every CONCORD message is a broadcast, which is what a SLIM group session is: the moderator creates the session on a topic and
invites the others, who wait for it. The CONCORD id of an agent is the last part of its SLIM name (`org/ns/participant-a`).
"""
import asyncio
import datetime

import slim_bindings

SECRET = "concord-slim-agents-dev-secret-0123456789"   # development only: at least 32 characters


class SessionClosed(Exception):
    pass


def _service(level="warn"):
    tracing, runtime, service = slim_bindings.new_tracing_config(), slim_bindings.new_runtime_config(), slim_bindings.new_service_config()
    tracing.log_level = level
    slim_bindings.initialize_with_configs(tracing_config=tracing, runtime_config=runtime, service_config=[service])
    return slim_bindings.get_global_service()


async def serve(address):
    """Run a SLIM node that listens on `address` (host:port, no TLS) until the task is cancelled."""
    service = _service()
    await service.run_server_async(slim_bindings.new_insecure_server_config(address))
    try:
        await asyncio.Event().wait()
    finally:
        await service.shutdown_async()


class Group:
    """One agent's end of the group session."""

    def __init__(self, local, url="http://127.0.0.1:12345", secret=SECRET):
        self.local, self.url, self.secret = local, url, secret
        self.app = self.conn = self.session = None

    @property
    def me(self):
        return self.local.split("/")[-1]

    async def connect(self, tries=30):
        service = _service()
        name = slim_bindings.Name.from_string(self.local)
        for i in range(tries):
            try:
                self.conn = await service.connect_async(slim_bindings.new_insecure_client_config(self.url))
                break
            except Exception:
                if i == tries - 1:
                    raise
                await asyncio.sleep(1)
        self.app = service.create_app_with_secret(name, self.secret)
        await self.app.subscribe_async(name, self.conn)

    async def create(self, topic, invites, tries=30):
        """The moderator: create the session on `topic` and invite each of `invites`, waiting for it to be up."""
        config = slim_bindings.SessionConfig(session_type=slim_bindings.SessionType.GROUP, max_retries=5,
                                             interval=datetime.timedelta(seconds=5), metadata={}, mls_settings=None)
        created = self.app.create_session(config, slim_bindings.Name.from_string(topic))
        await created.completion.wait_async()
        self.session = created.session
        for who in invites:
            name = slim_bindings.Name.from_string(who)
            await self.app.set_route_async(name, self.conn)
            for i in range(tries):
                try:
                    await (await self.session.invite_async(name)).wait_async()
                    break
                except Exception:
                    if i == tries - 1:
                        raise
                    await asyncio.sleep(1)

    async def join(self):
        """A participant: wait for the moderator's invitation."""
        self.session = await self.app.listen_for_session_async(None)

    async def send(self, text):
        await self.session.publish_async(text.encode(), None, None)

    async def recv(self, timeout=1.0):
        """The next message as (sender, text), or None when nothing came within `timeout` seconds."""
        try:
            got = await self.session.get_message_async(timeout=datetime.timedelta(seconds=timeout))
        except Exception as e:
            if "closed" in str(e).lower():
                raise SessionClosed(str(e)) from e
            return None
        sender = getattr(got.context, "source_name", None)
        return (str(sender) if sender is not None else None), got.payload.decode("utf-8", "replace")

    async def close(self):
        try:
            handle = await asyncio.wait_for(self.app.delete_session_async(self.session), 5)
            await asyncio.wait_for(handle.wait_async(), 5)      # the moderator would wait a minute for agents that have left
        except Exception:
            pass
