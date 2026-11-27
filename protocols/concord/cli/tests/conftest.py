#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The CLI library as it is on disk, on top of the installed pure-Python library `ioc-concord` 0.2.0 (it is no longer on main: the
scripts bundle it from its pinned revision). (The scripts that carry both are tested with the scripts.)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "protocols/concord/cli/src")]
