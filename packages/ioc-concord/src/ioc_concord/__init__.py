#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""ioc-concord: the CONCORD protocol as a library. Stdlib only.

The library is the source of truth for the protocol. The skill scripts (`concord_initiator.py`, `concord_receiver.py`)
carry a generated copy of the modules they use (see `tools/bundle.py`), so a skill stays one self-contained file, and any
other integration can import the same code.

Modules:

    errors    ConcordError and its subclasses: what the library raises (it never prints or exits)
    protocol  the protocol version, the default parameters and the outcomes: one place to change them
    xmlutil   escaping, and the integrity check of a document (computed on its text, never on a parsed copy)
    schema    the candidate schema: parse, validate, check a proposal, describe it
    engine    the decision engine: record the messages, evaluate (SELECT / ACCEPT / bottleneck / outcome) from the seeds
    wire      the messages: build each one (the author's checks), and read each one in the layers of the Order of checks
    store     the Store protocol, and FileStore (atomic writes) as one implementation
    state     the state of an agent: its runs as plain dicts, and the keys of the items a run is made of
    liveness  the messages an agent is owed and the timers that wait for them, as functions of the state of a run
    tasks     what the agent's model must write now, derived from the state
    report    where a run stands, and the report of the run
    agent     the node: receive messages in the order of checks, ask again, give up, build what it sends

A module that is bundled into a script follows the rules in `tools/bundle.py`: stdlib imports at the top, intra-package
imports of the form `from ioc_concord.<module> import <names>`, and top-level names that are unique across modules.
"""
__version__ = "0.1.0"   # keep equal to pyproject.toml (tests/test_package.py checks)
