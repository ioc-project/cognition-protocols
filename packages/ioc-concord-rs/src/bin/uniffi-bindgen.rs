//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Generates the bindings of the library, with the version of UniFFI it is built with:
//! `cargo run --bin uniffi-bindgen -- generate --library <cdylib> --language <language> --out-dir <dir>`.

fn main() {
    uniffi::uniffi_bindgen_main()
}
