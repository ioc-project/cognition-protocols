//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! ioc-accord: the ACCORD protocol as a library.
//!
//! The initiator of ACCORD collects the specialists' replies (raw model text or JSON) and merges them deterministically, phase by
//! phase: CONVENE (the roster), FRAME (the problem frame, in rounds), GROUND and CLARIFY (the glossary of the key terms), VERIFY (who
//! owns which knowledge, and the acceptance checks), then the locked intent contract and the execution messages. This crate is the logic
//! of `protocols/accord/skills/accord-initiator/scripts/accord_initiator.py`, ported faithfully: the same results, the same messages, and
//! a state that is the same JSON, byte for byte.
//!
//! The library does no I/O: it reads no file and no environment, it never prints or exits, and it takes no lock. The caller loads the
//! state, runs a command on a `session::Session`, and saves the state when the command succeeded. The one exception is the module `cli`
//! (feature `cli`), which is such a caller: the script's command line, with its state file and its lock.
//!
//! Similarity is the script's string similarity only (`difflib.SequenceMatcher` on normalized text, as with `ACCORD_EMBEDDING=off`): the
//! sentence-transformers embeddings the script can use instead are not ported.
//!
//! Modules:
//!
//! * `errors`: `AccordError` (with its `refusal` flag) and `hint_for`
//! * `protocol`: the thresholds and limits of a session
//! * `py`: the Python semantics the merges rely on, over JSON values
//! * `pyjson`: JSON read and written as Python's `json` does
//! * `similarity`: `normalize_text`, a port of `difflib.SequenceMatcher`, and the similarity of two texts
//! * `ingest`: reading the replies of each phase
//! * `algorithms`: the merges: roster, frame clusters and coverage, glossary, set covers, the contract
//! * `state`: the state of a session, and the path of its file under a run key
//! * `messages`: the request of each phase, the delivery check, the execution request
//! * `session`: the commands, self-driving (`init` + `handle`) or phase by phase
//! * `cli`: the `ioc-accord` command as a function (feature `cli`): the script's command line, its output and its state file
//!
//! # Quick start
//!
//! ```
//! use ioc_accord::messages::find_check;
//! use ioc_accord::session::{InitArgs, Session};
//!
//! # fn main() -> ioc_accord::Result<()> {
//! let mut session = Session::new();
//! let init = session.init(&InitArgs {
//!     specialists: "ops,dev".into(),
//!     key_terms: "latency".into(),
//!     task: "cut the p95 latency".into(),
//!     state_id: Some("abcd1234".into()),
//!     ..InitArgs::default()
//! })?;
//! assert_eq!(init["phase"], "convene");
//!
//! // each specialist echoes the delivery line of its message, then its answer
//! let message = init["messages"]["ops"].as_str().unwrap();
//! let line = message.lines().next().unwrap();
//! assert!(find_check(line).is_some());
//! let reply = format!("{line}\n{{\"agent\": \"ops\", \"roles\": [\"sre\"]}}");
//! let step = session.handle("ops", &reply, None, None)?;
//! assert_eq!(step["still_waiting_this_phase"][0], "dev");
//!
//! // the state file, as the script writes it
//! let text = session.dump();
//! assert_eq!(Session::load(&text)?, session);
//! # Ok(())
//! # }
//! ```

pub mod algorithms;
#[cfg(feature = "cli")]
pub mod cli;
pub mod errors;
pub mod ingest;
pub mod messages;
pub mod protocol;
pub mod py;
pub mod pyjson;
pub mod session;
pub mod similarity;
pub mod state;

pub use errors::{hint_for, AccordError, Result};
pub use session::Session;

/// The version of the library, as `Cargo.toml` has it.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
