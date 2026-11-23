//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The state of a session: one JSON object, the same file the script keeps, key for key and byte for byte.
//!
//! The state is a `serde_json::Value` and not a typed struct because the script keeps in it what the specialists wrote (a proposed
//! check is stored as the agent sent it, a defect can be a string or an object), and a state saved by either implementation has to load
//! in the other unchanged.

use ioc_protocol::store::Codec;
use serde_json::Value;
use uuid::Uuid;

use crate::errors::{AccordError, Result};
use crate::py;
use crate::pyjson::{dumps_pretty, loads};

/// The state of a new session: every key, in the order of the script.
///
/// The self-driving keys (`task` to `objective_clauses`) are only filled when `init` is given a task. The current phase is not stored:
/// it is worked out from the merged data (`Session::phase_key`).
pub fn blank_state() -> Value {
    serde_json::json!({
        "specialists": [], "key_terms": [], "action_tool": "", "coverage_warnings": [],
        "roster": [], "obs": [], "frame": [], "glossary": [],
        "owned": {}, "uncovered": [], "checks": [], "defects": [],
        "frame_rounds": 0, "locked_contract": null,
        "task": "",
        "state_id": "",
        "handle_replies": {},
        "clarify_terms": [],
        "issued": {},
        "issued_checks": {},
        "check_fail": {},
        "required_knowledge": [],
        "objective_clauses": [],
    })
}

/// A saved state over the blank one: `{**blank_state(), **saved}`, so the keys a state saved by an older version lacks get their
/// default, the blank keys keep their order and keys the blank state lacks follow, in their order.
pub fn with_defaults(saved: Value) -> Result<Value> {
    let Value::Object(saved) = saved else {
        return Err(AccordError::type_error(format!("'{}' object is not a mapping", py::type_name(&saved))));
    };
    let Value::Object(mut st) = blank_state() else { unreachable!("the blank state is an object") };
    for (k, v) in saved {
        st.insert(k, v);
    }
    Ok(Value::Object(st))
}

/// A state as it was saved: its text read as JSON, over the blank state.
pub fn load_state(text: &str) -> Result<Value> {
    with_defaults(loads(text)?)
}

/// The text of a state file: `json.dumps(st, indent=2)`, without a final line break.
pub fn dump_state(st: &Value) -> String {
    dumps_pretty(st)
}

/// The id of a new session: the first 8 hex digits of a random UUID.
pub fn new_state_id() -> String {
    Uuid::new_v4().simple().to_string()[..8].to_string()
}

/// The state file of this invocation.
///
/// `path` is the `--state` of the command (or `$ACCORD_STATE`, or `DEFAULT_STATE`), the same on every command of a session. Without a
/// run key (`$ACCORD_RUN_KEY`, None or empty) it is the state file, unchanged. With one, the state file is isolated by the key: it is
/// named `<stem>-<8 hex digits><suffix>` after the original file name, the digits those of `uuid5(NAMESPACE_URL, "<run_key>:<name>")`,
/// and placed in `state_dir` (`$ACCORD_STATE_DIR`) or else next to `path`. The same key gives the same path on every command, so a
/// record/replay pair that keeps its key gets the same state paths, and unrelated runs do not share a file.
///
/// This only computes the path, the way Python's `pathlib` writes it (`a//b/./c` is `a/b/c`); creating the folder is the caller's.
pub fn resolve_state_path(path: &str, run_key: Option<&str>, state_dir: Option<&str>) -> String {
    ioc_protocol::path::keyed_path(path, run_key, state_dir)
}

/// The state file as a `Codec` of `ioc_protocol::store`: the text `dump_state` writes, read back by `load_state` (over the blank state).
/// A `FileStore<StateCodec>` keeps a state as the script does.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct StateCodec;

impl Codec for StateCodec {
    type State = Value;
    const FORMAT: &'static str = "JSON";

    fn encode(&self, st: &Value) -> std::result::Result<String, String> {
        Ok(dump_state(st))
    }

    fn decode(&self, text: &str) -> std::result::Result<Value, String> {
        load_state(text).map_err(|e| e.message().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ioc_protocol::path::suffix_and_stem;
    use ioc_protocol::store::{conformance, FileStore, MemoryStore, Store};
    use serde_json::{json, Map};

    fn keys(v: &Value) -> Vec<String> {
        v.as_object().map(Map::keys).into_iter().flatten().cloned().collect()
    }

    #[test]
    fn a_saved_state_is_merged_over_the_blank_one() {
        let st = load_state("{\"extra\": 1, \"task\": \"t\"}").unwrap();
        let k = keys(&st);
        assert_eq!(k.first().map(String::as_str), Some("specialists"));
        assert_eq!(k.last().map(String::as_str), Some("extra"));
        assert_eq!(st["task"], "t");
        assert_eq!(load_state("[1]").unwrap_err().message(), "TypeError: 'list' object is not a mapping");
    }

    #[test]
    fn the_blank_state_prints_like_python() {
        let text = dump_state(&blank_state());
        assert!(text.starts_with("{\n  \"specialists\": [],\n  \"key_terms\": [],\n  \"action_tool\": \"\","));
        assert!(text.ends_with("\"objective_clauses\": []\n}"));
        assert_eq!(new_state_id().len(), 8);
    }

    #[test]
    fn without_a_run_key_the_path_is_unchanged() {
        assert_eq!(resolve_state_path("a//b.json", None, Some("x")), "a//b.json");
        assert_eq!(resolve_state_path("a//b.json", Some(""), None), "a//b.json");
    }

    #[test]
    fn names_split_like_pathlib() {
        assert_eq!(suffix_and_stem("x.json"), (".json", "x"));
        assert_eq!(suffix_and_stem(".hidden"), ("", ".hidden"));
        assert_eq!(suffix_and_stem("x."), (".", "x"));
        assert_eq!(suffix_and_stem("a.b."), (".", "a.b"));
        assert_eq!(suffix_and_stem(".."), ("", ".."));
        assert_eq!(suffix_and_stem("...a.b"), (".b", "...a"));
        assert_eq!(suffix_and_stem(""), ("", ""));
    }

    #[test]
    fn a_state_is_kept_by_either_backend_through_the_store_trait() {
        let dir = std::env::temp_dir().join(format!("ioc-accord-state-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        // the file backend writes the script's text and reads a saved state over the blank one
        let first = with_defaults(json!({"task": "t"})).unwrap();
        let second = with_defaults(json!({"task": "é", "frame_rounds": 2})).unwrap();
        let mut file = FileStore::with_codec(&path, StateCodec);
        assert!(file.load().unwrap_err().is_missing());
        file.save(&first).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), dump_state(&first));
        std::fs::write(&path, "{\"extra\": 1}").unwrap();
        let loaded = file.load().unwrap();
        assert_eq!(keys(&loaded).first().map(String::as_str), Some("specialists"));
        assert_eq!(loaded["extra"], 1);
        std::fs::write(&path, "[1]").unwrap();
        assert_eq!(
            file.load().unwrap_err().message(),
            format!("state file {} is not valid JSON (TypeError: 'list' object is not a mapping)", path.display())
        );
        let _ = std::fs::remove_dir_all(&dir);

        let memory = MemoryStore::<Value>::refusing(|st| st["task"] == "refused");
        conformance::run(|| memory.clone(), first, second, with_defaults(json!({"task": "refused"})).unwrap());
    }
}
