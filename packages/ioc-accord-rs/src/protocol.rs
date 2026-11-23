//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The parameters of a session: one place to change them. The values are the script's.

/// The state file of a session when neither `--state` nor `$ACCORD_STATE` names one.
pub const DEFAULT_STATE: &str = ".accord/state.json";
/// The agent that checks and never owns knowledge (`algorithms::assign_holdings`).
pub const DEFAULT_VERIFIER_ID: &str = "verifier";
/// Two atoms of the frame whose similarity reaches this are one atom.
pub const GROUP_THRESHOLD: f64 = 0.72;
/// Two definitions of a term whose similarity reaches this agree.
pub const SIM_MERGE: f64 = 0.72;
/// Lower bound of the near-match band `[SIM_CLARIFY, SIM_MERGE)`: definitions of a term from different clusters that score in it are
/// sent to CLARIFY. With `SIM_CLARIFY = SIM_MERGE` the band is empty and CLARIFY is skipped: the definitions either agree or stay flagged.
pub const SIM_CLARIFY: f64 = 0.55;
/// The knowledge items that may stay without an owner; the others go to the first agent that can own knowledge.
pub const MAX_UNCOVERED_HOLDINGS: usize = 1;
/// The atoms an agent may put in the frame; the ones after are dropped.
pub const MAX_FRAME_ATOMS: usize = 6;
/// The rounds of FRAME.
pub const MAX_FRAME_ROUNDS: i64 = 2;
