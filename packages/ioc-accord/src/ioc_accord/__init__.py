#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""ioc-accord: the ACCORD protocol as a library. Stdlib only.

The library is the source of truth for the protocol. The skill script (`accord_initiator.py`) carries a generated copy of
the modules it uses (see `tools/bundle.py`), so a skill stays one self-contained file, and any other integration can
import the same code.

Modules:

    errors      AccordError and its subclasses: what the library raises (it never prints or exits)
    similarity  text normalisation and the similarity score the merges are built on (embeddings, or difflib)
    ingest      reading what the specialists replied: raw text or JSON in, plain records out
    engine      the deterministic merges: clustering, the glossary, set cover, the locked contract
    wire        the documents: every request a specialist receives, and the payload envelopes
    state       the shape of a session's state, and where it is kept on disk
    store       loading and saving that state, with an exclusive lock for concurrent replies
    flow        the self-driving loop: which phase a session is in, who still owes a reply, what to send next

A module that is bundled into a script follows the rules in `tools/bundle.py`: stdlib imports at the top, intra-package
imports of the form `from ioc_accord.<module> import <names>`, and top-level names that are unique across modules.
"""
__version__ = "0.1.0"   # keep equal to pyproject.toml (tests/test_package.py checks)
