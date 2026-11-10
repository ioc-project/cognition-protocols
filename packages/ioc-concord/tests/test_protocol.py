#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The protocol constants, as imported and as bundled into each script (see conftest.py)."""


def test_the_wire_version_and_defaults_are_the_ones_of_the_spec(protocol):
    assert protocol.PROTOCOL_VERSION == "0.3.0"
    assert protocol.OUTCOMES == ("SUCCESS", "BEST_EFFORT", "ABORTED")


def test_an_agent_id_is_one_to_64_ascii_letters_digits_underscores_or_hyphens(protocol):
    for ok in ("a", "cost", "Agent_1", "a-b", "x" * 64, "0"):
        assert protocol.is_agent_id(ok), ok
    for bad in ("", "x" * 65, "a b", "a.b", "é", "a\n", "a/b", "<a>", None, 5):
        assert not protocol.is_agent_id(bad), bad


def test_a_uuid_is_lowercase_hexadecimal_with_hyphens(protocol):
    assert protocol.is_uuid("6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51")
    for bad in ("6C0F9D1E-8a52-4b73-a1d4-0e9f2c3b4a51", "6c0f9d1e8a524b73a1d40e9f2c3b4a51", "{6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51}",
                "urn:uuid:6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51", "6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51\n", "RUNID", "", None):
        assert not protocol.is_uuid(bad), bad


def test_a_number_is_read_exactly_in_millionths(protocol):
    for text, value in (("82", 82_000_000), ("82.0", 82_000_000), ("0.5", 500_000), ("007", 7_000_000), ("0", 0),
                        ("100", 100_000_000), ("0.000001", 1), ("999.999999", 999_999_999), ("0.60", 600_000)):
        assert protocol.parse_number(text) == value, text


def test_a_number_in_another_form_is_not_a_number(protocol):
    for bad in (".5", "82.", "82.1234567", "+5", "-0", "-1", "1e2", "NaN", "inf", "infinity", "1_0", "8 2", " 82", "82 ", "82\n",
                "1000", "٣٢", "８２", "", "1,5", "0x10", None, 5, 0.5):
        assert protocol.parse_number(bad) is None, bad


def test_a_number_given_as_text_int_or_float_is_read_in_millionths(protocol):
    for value, millionths in (("82.5", 82_500_000), (82, 82_000_000), (82.5, 82_500_000), (0.1, 100_000), (0.05, 50_000),
                              (0, 0), (0.0, 0), (1e-06, None), (1e-07, None), (1 / 3, None), (-1, None), (-0.5, None),
                              (float("nan"), None), (float("inf"), None), (True, None), (None, None), ("1e2", None), (1000, None)):
        assert protocol.to_millionths(value) == millionths, value


def test_a_number_is_written_the_way_the_spec_writes_it(protocol):
    for millionths, text in ((82_000_000, "82.0"), (500_000, "0.5"), (1, "0.000001"), (0, "0.0"), (600_000, "0.6"),
                             (999_999_999, "999.999999"), (50_000, "0.05"), (100_000_000, "100.0")):
        assert protocol.format_number(millionths) == text
        assert protocol.parse_number(text) == millionths


def test_the_defaults_are_the_ones_of_the_spec_in_millionths(protocol):
    assert protocol.DEFAULT_PARAMS == {"satisfaction_threshold": 600_000, "eps": 50_000, "B": 8, "timeout": 300, "retries": 3}
