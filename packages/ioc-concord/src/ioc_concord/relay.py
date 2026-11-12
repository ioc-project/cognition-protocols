#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The centre of a star: which messages it passes on to the spokes (see Transport in the spec).

On a broadcast channel and on a full mesh nothing relays. In a star every agent, a spoke, has a link to one central agent, the
centre. A spoke sends each message to the centre only; the centre sends the messages of the runs it knows on to every spoke except
the one a message came from, and its own messages go to every spoke. Passing on is mechanical: the centre never changes a message,
and it does not depend on what the centre does with it as an agent. It passes a message on even when it drops it as a duplicate,
ignores it, or has ended or aborted the run, since the spokes behind it still need it.

A `Relay` makes that decision, and keeps what it needs for it: the ids of the runs it knows, and the spokes. A centre that does not take
part in any run is just a `Relay`; one that does gives it to its `Agent` (`Agent(me, relay=relay)`), which then says in `step.forward` what
to pass on. The relay never sends anything: the caller does, to the spokes that `targets` names, which are every spoke except the one a
message came from, and every spoke for an own message of the agent.

The spokes are the links of the centre, which only the transport knows (the init lists the agents that negotiate, not the links, and a
spoke need not be listed in it), so the caller registers them: `add_spoke` when one connects, `remove_spoke` when it goes. A spoke is any
hashable handle that the caller can send through: a connection, a socket id, a name. They are not saved, since they are live links that
connect again after a restart, unlike the run ids.

The centre knows a run once it has passed on that run's `<concord-init>`, bare or inside a `<concord-resend>`, since the spokes learn
the run only from that message. It passes on a message of any other run only if it knows it. To decide it reads only the name and the
`run_id` of the outermost element and, for a `<concord-resend>`, the name of the element inside: not the version, the check, the sender
or what the message says. So it also passes on a message of a version it does not speak, and a name it has never heard of. It keeps
the run ids for as long as it keeps the state of the run, ended runs included, so that errors and re-sends after the end still reach
the spokes: `forget` is how the caller says a run's state is dropped.
"""
from collections import namedtuple

from ioc_concord.errors import InvalidMessage, MissingState, StateError
from ioc_concord.protocol import is_uuid
from ioc_concord.wire import INIT, RESEND, locate, peek


# What the relay reads of a message: the name of the outermost element, the name of the element inside a resend (or None), the run id.
_Read = namedtuple("_Read", "tag inner run_id")


class Relay:
    """The relay of a centre. `store` keeps the run ids between calls (None keeps them in memory only). The state is a plain dict,
    `relay.state`, {"runs": {run id: True}}, saved when a run is learned or forgotten: the ids are a set, kept as a dict so that the
    state is JSON."""

    def __init__(self, store=None, spokes=()):
        self.store = store
        self.state = self._load()
        self._spokes = dict.fromkeys(spokes)               # in the order they were added; not part of the state

    def _load(self):
        if self.store is None:
            return {"runs": {}}
        try:
            state = self.store.load()
        except MissingState:                    # nothing saved yet; a state that cannot be read is an error: the runs would be forgotten
            return {"runs": {}}
        if not isinstance(state, dict) or not isinstance(state.get("runs"), dict):
            raise StateError("the saved relay state holds no runs")
        return state

    def _save(self):
        if self.store is not None:
            self.store.save(self.state)

    def _read(self, text):
        """What the relay reads of `text` (a `_Read`), or None when it is not a CONCORD message with a run id of the right form."""
        try:
            info = peek(locate(text))
        except InvalidMessage:
            return None
        if info is None or not is_uuid(info["attrs"].get("run_id")):
            return None
        return _Read(info["tag"], (info["inner"] or {}).get("tag"), info["attrs"]["run_id"])

    def _learn(self, read):
        """Remember the run of a message that has been read, if it is that run's init. Returns whether it is."""
        opens = read is not None and (read.tag == INIT or (read.tag == RESEND and read.inner == INIT))
        if opens and read.run_id not in self.state["runs"]:
            self.state["runs"][read.run_id] = True
            self._save()
        return opens

    def learn(self, text):
        """Remember the run of `text` if it is that run's init, bare or inside a resend. Returns whether `text` is such an init: the
        centre passes on, or sends, the message that makes it know the run."""
        return self._learn(self._read(text))

    def should_pass_on(self, text):
        """Whether the centre passes `text` on to the spokes: it is the init of a run, which it then knows, or a message of a run it
        knows. Whatever the centre does with the message as an agent changes nothing."""
        read = self._read(text)
        return self._learn(read) or (read is not None and read.run_id in self.state["runs"])

    def receive(self, text):
        """What to pass on for `text`, as a list: the text itself, unchanged, or nothing."""
        return [text] if self.should_pass_on(text) else []

    def forget(self, run_id):
        """Drop a run: the centre no longer passes on its messages. Call it when the state of the run is dropped, not when it ends."""
        if self.state["runs"].pop(run_id, None) is not None:
            self._save()

    @property
    def spokes(self):
        """The spokes, in the order they were added."""
        return tuple(self._spokes)

    def add_spoke(self, spoke):
        """Register a spoke: a link of the centre, as a hashable handle that the caller can send through. Adding one twice changes nothing."""
        self._spokes.setdefault(spoke)

    def remove_spoke(self, spoke):
        """Drop a spoke that is gone. A spoke that was not registered changes nothing."""
        self._spokes.pop(spoke, None)

    def targets(self, origin=None):
        """The spokes to send a message to: every spoke except `origin`, the one the message came from. With no origin, which is the case
        for an own message of the agent, every spoke. An origin that is not registered leaves them all."""
        return [spoke for spoke in self._spokes if spoke != origin]
