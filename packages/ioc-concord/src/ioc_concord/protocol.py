#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The constants of the protocol: the one place that names its version, its defaults and its outcomes."""

# The version every message carries on its root element. An agent processes only messages of exactly this version.
PROTOCOL_VERSION = "0.3.0"

# The parameters of a run and their defaults (see Configuration Parameters in the spec). `B` is the `budget` element.
# A `timeout` of 0 switches the timers off: nobody asks again for a message and nobody is marked missing on a clock.
DEFAULT_PARAMS = {"satisfaction_threshold": 0.6, "eps": 0.05, "B": 8, "timeout": 300, "retries": 3}

SUCCESS, BEST_EFFORT, ABORTED = "SUCCESS", "BEST_EFFORT", "ABORTED"
OUTCOMES = (SUCCESS, BEST_EFFORT, ABORTED)
