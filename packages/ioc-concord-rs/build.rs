//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Keep the shared library small: drop its debug information and its local symbols. The global symbols stay, and with them the metadata
//! that `uniffi-bindgen` reads in the library to generate the bindings (a full strip would leave it nothing to read).

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let args: &[&str] = match std::env::var("CARGO_CFG_TARGET_OS").as_deref() {
        Ok("macos") => &["-Wl,-x"],
        Ok("linux") => &["-Wl,--discard-all"],
        _ => &[],
    };
    for arg in args {
        println!("cargo:rustc-cdylib-link-arg={arg}");
    }
}
