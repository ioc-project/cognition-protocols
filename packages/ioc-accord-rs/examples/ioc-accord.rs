//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! `ioc-accord` as a process: the arguments, the environment, the working folder and the standard input of the process go to
//! `ioc_accord::cli::run`, and what it prints comes out with its exit code. Not shipped: the differential test against the script runs it
//! (`cargo run --example ioc-accord --features cli -- init ...`).

use std::io::Write;

use ioc_accord::cli::{run, Context};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let out = run(&args, &mut Context::from_env());
    let _ = std::io::stdout().write_all(out.stdout.as_bytes());
    let _ = std::io::stderr().write_all(out.stderr.as_bytes());
    std::process::exit(out.code);
}
