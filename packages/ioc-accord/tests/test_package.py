#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The package's own metadata."""
import tomllib
from pathlib import Path

import ioc_accord


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert ioc_accord.__version__ == pyproject["project"]["version"]
