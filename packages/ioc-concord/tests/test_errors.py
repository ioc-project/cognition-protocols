#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The errors, as imported and as bundled into each script (see conftest.py)."""


def test_every_error_is_a_concord_error_with_a_code(errors):
    err = errors.SchemaError("bad schema")
    assert isinstance(err, errors.ConcordError) and str(err) == "bad schema" and err.code == "invalid_schema"
    assert errors.ConcordError("x").code == "concord_error"
    assert errors.ConcordError("x", code="custom").code == "custom"
