#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The scripts must carry exactly the library they bundle (the same check CI runs as `just check-sync`)."""
from conftest import bundle_tool


def test_scripts_are_in_sync_with_the_library(capsys):
    assert bundle_tool.main(["--check"]) == 0, capsys.readouterr().err
