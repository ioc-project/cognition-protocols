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
    xmlutil   escaping, element building, repairing copied text, the patterns that find a document in free text
    schema    the candidate schema: parse, validate, check a proposal, describe it
    engine    the decision engine (SELECT / ACCEPT / REPAIR / COMMIT) over the ratings the agents give
    wire      the documents: build and read every request and reply, the notices, and the tag vocabulary

Planned: store (persistence). A module that is bundled into a script follows
the rules in `tools/bundle.py`: stdlib imports at the top, intra-package imports of the form
`from ioc_concord.<module> import <names>`, and top-level names that are unique across modules.
"""
__version__ = "0.1.0"
