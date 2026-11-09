#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The protocol constants, as imported and as bundled into each script (see conftest.py)."""


def test_the_wire_version_and_defaults_are_the_ones_of_the_spec(protocol):
    assert protocol.PROTOCOL_VERSION == "0.3.0"
    assert protocol.DEFAULT_PARAMS == {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 8, "timeout": 300, "retries": 3}
    assert protocol.OUTCOMES == ("SUCCESS", "BEST_EFFORT", "ABORTED")
