//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The protocol constants: the version, the forms of ids and numbers, the defaults.
// Python's `is_agent_id`, `is_uuid` and `parse_number` are also tried with None, 5 and 0.5, and `to_millionths` with True and None: a
// Rust function that takes a `&str` or a `Num` cannot be given those, so those cases are left out.

use ioc_concord::engine::Params;
use ioc_concord::protocol::*;

#[test]
fn the_wire_version_and_defaults_are_the_ones_of_the_spec() {
    assert_eq!(PROTOCOL_VERSION, "0.3.0");
    assert_eq!([Outcome::Success, Outcome::BestEffort, Outcome::Aborted].map(|o| o.as_str()), ["SUCCESS", "BEST_EFFORT", "ABORTED"]);
    assert_eq!(RESULT_OUTCOMES, [Outcome::Success, Outcome::BestEffort]);
    for o in [Outcome::Success, Outcome::BestEffort, Outcome::Aborted] {
        assert_eq!(Outcome::parse(o.as_str()), Some(o));
        assert_eq!(o.to_string(), o.as_str());
    }
    assert_eq!(Outcome::parse("success"), None);
}

#[test]
fn an_agent_id_is_one_to_64_ascii_letters_digits_underscores_or_hyphens() {
    for ok in ["a", "cost", "Agent_1", "a-b", &"x".repeat(64), "0"] {
        assert!(is_agent_id(ok), "{ok}");
    }
    for bad in ["", &"x".repeat(65), "a b", "a.b", "é", "a\n", "a/b", "<a>"] {
        assert!(!is_agent_id(bad), "{bad:?}");
    }
}

#[test]
fn a_uuid_is_lowercase_hexadecimal_with_hyphens() {
    assert!(is_uuid("6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51"));
    for bad in [
        "6C0F9D1E-8a52-4b73-a1d4-0e9f2c3b4a51",
        "6c0f9d1e8a524b73a1d40e9f2c3b4a51",
        "{6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51}",
        "urn:uuid:6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51",
        "6c0f9d1e-8a52-4b73-a1d4-0e9f2c3b4a51\n",
        "RUNID",
        "",
    ] {
        assert!(!is_uuid(bad), "{bad:?}");
    }
}

#[test]
fn a_number_is_read_exactly_in_millionths() {
    for (text, value) in [
        ("82", 82_000_000),
        ("82.0", 82_000_000),
        ("0.5", 500_000),
        ("007", 7_000_000),
        ("0", 0),
        ("100", 100_000_000),
        ("0.000001", 1),
        ("999.999999", 999_999_999),
        ("0.60", 600_000),
    ] {
        assert_eq!(parse_number(text), Some(value), "{text}");
    }
}

#[test]
fn a_number_in_another_form_is_not_a_number() {
    for bad in [
        ".5",
        "82.",
        "82.1234567",
        "+5",
        "-0",
        "-1",
        "1e2",
        "NaN",
        "inf",
        "infinity",
        "1_0",
        "8 2",
        " 82",
        "82 ",
        "82\n",
        "1000",
        "٣٢",
        "８２",
        "",
        "1,5",
        "0x10",
    ] {
        assert_eq!(parse_number(bad), None, "{bad:?}");
    }
}

#[test]
fn a_number_given_as_text_int_or_float_is_read_in_millionths() {
    let cases: Vec<(Num, Option<i64>)> = vec![
        ("82.5".into(), Some(82_500_000)),
        (82.into(), Some(82_000_000)),
        (82.5.into(), Some(82_500_000)),
        (0.1.into(), Some(100_000)),
        (0.05.into(), Some(50_000)),
        (0.into(), Some(0)),
        (0.0.into(), Some(0)),
        (1e-07.into(), None),
        ((1.0 / 3.0).into(), None),
        ((-1).into(), None),
        ((-0.5).into(), None),
        (f64::NAN.into(), None),
        (f64::INFINITY.into(), None),
        ("1e2".into(), None),
        (1000.into(), None),
    ];
    for (value, millionths) in cases {
        assert_eq!(value.to_millionths(), millionths, "{value:?}");
    }
}

// Python writes 1e-06 as "1e-06" (an exponent: not a number); Rust writes it as "0.000001" and reads 1. Blame: `Num::to_millionths`
// (`Num::Float(f) => parse_number(&f.to_string())`); fix: refuse a float whose shortest repr in Python would use an exponent (below 1e-4).
#[test]
fn a_float_whose_repr_has_an_exponent_is_not_a_number() {
    assert_eq!(Num::from(1e-06).to_millionths(), None);
}

#[test]
fn a_number_is_written_the_way_the_spec_writes_it() {
    for (millionths, text) in [
        (82_000_000, "82.0"),
        (500_000, "0.5"),
        (1, "0.000001"),
        (0, "0.0"),
        (600_000, "0.6"),
        (999_999_999, "999.999999"),
        (50_000, "0.05"),
        (100_000_000, "100.0"),
    ] {
        assert_eq!(format_number(millionths), text);
        assert_eq!(parse_number(text), Some(millionths));
    }
}

#[test]
fn the_defaults_are_the_ones_of_the_spec_in_millionths() {
    assert_eq!(Params::default(), Params { satisfaction_threshold: 600_000, eps: 50_000, budget: 8, timeout: 300, retries: 3 });
    assert_eq!((DEFAULT_THRESHOLD, DEFAULT_EPS, DEFAULT_BUDGET, DEFAULT_TIMEOUT, DEFAULT_RETRIES), (600_000, 50_000, 8, 300, 3));
}
