//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The file store: a state is saved all-or-nothing and loads back; a missing one is told from one that cannot be read.
// `FileStore` has no `indent` option (it always writes two spaces), so only the default is tested. A `serde_json::Value` is always
// writable, so no state fails to serialise. A failed rename is tested for real: the target is a directory.

use std::path::{Path, PathBuf};

use ioc_concord::store::{FileStore, MemoryStore, Store};
use ioc_concord::ConcordError;
use serde_json::json;

/// A directory of its own, removed at the end of the test.
struct TempDir(PathBuf);

impl TempDir {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("ioc-concord-store-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        TempDir(dir)
    }

    fn path(&self) -> &Path {
        &self.0
    }

    fn names(&self) -> Vec<String> {
        let mut names: Vec<String> =
            std::fs::read_dir(&self.0).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        names
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn a_saved_state_loads_back() {
    let dir = TempDir::new("loads_back");
    let mut s = FileStore::new(dir.path().join("state.json"));
    s.save(&json!({"a": [1, 2], "b": {"c": "é"}})).unwrap();
    assert!(s.exists());
    assert_eq!(s.load().unwrap(), json!({"a": [1, 2], "b": {"c": "é"}}));
    assert!(std::fs::read_to_string(dir.path().join("state.json")).unwrap().ends_with("}\n"));
}

#[test]
fn the_indent_is_two_spaces() {
    let dir = TempDir::new("indent");
    FileStore::new(dir.path().join("two.json")).save(&json!({"k": 1})).unwrap();
    assert_eq!(std::fs::read_to_string(dir.path().join("two.json")).unwrap(), "{\n  \"k\": 1\n}\n");
}

#[test]
fn loading_a_state_that_is_not_there_says_where() {
    let dir = TempDir::new("missing");
    let s = FileStore::new(dir.path().join("nope.json"));
    assert!(!s.exists());
    let err = s.load().unwrap_err();
    assert!(matches!(err, ConcordError::MissingState(_)));
    assert_eq!(err.code(), "missing_state");
    assert!(err.is_state());
    let text = err.to_string();
    assert!(text.starts_with("no state file at ") && text.ends_with("nope.json"), "{text}");
}

#[test]
fn loading_a_file_that_is_not_json_is_a_state_error_and_not_a_missing_one() {
    let dir = TempDir::new("not_json");
    std::fs::write(dir.path().join("bad.json"), "{oops").unwrap();
    let err = FileStore::new(dir.path().join("bad.json")).load().unwrap_err();
    assert!(matches!(err, ConcordError::State(_)), "{err:?}");
    assert!(err.to_string().contains("bad.json is not valid JSON"), "{err}");
    assert_eq!(err.code(), "state_error");
    assert!(!matches!(err, ConcordError::MissingState(_)));
}

#[test]
fn a_save_replaces_the_state_and_leaves_nothing_behind() {
    let dir = TempDir::new("replaces");
    let mut s = FileStore::new(dir.path().join("state.json"));
    s.save(&json!({"v": 1})).unwrap();
    s.save(&json!({"v": 2})).unwrap();
    assert_eq!(s.load().unwrap(), json!({"v": 2}));
    assert_eq!(dir.names(), ["state.json"]);
}

#[test]
fn a_failed_rename_cleans_up_its_temporary_file() {
    let dir = TempDir::new("failed_rename");
    let target = dir.path().join("state.json");
    std::fs::create_dir(&target).unwrap();
    let err = FileStore::new(&target).save(&json!({"v": 2})).unwrap_err();
    assert!(matches!(err, ConcordError::State(_)), "{err:?}");
    assert!(err.to_string().contains("cannot be written"), "{err}");
    assert_eq!(dir.names(), ["state.json"]);
}

#[cfg(unix)]
#[test]
fn the_file_keeps_the_permissions_a_plain_write_would_give() {
    use std::os::unix::fs::PermissionsExt;
    let dir = TempDir::new("permissions");
    let plain = dir.path().join("plain.json");
    std::fs::write(&plain, "{}").unwrap();
    let mut s = FileStore::new(dir.path().join("state.json"));
    s.save(&json!({})).unwrap();
    let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o7777;
    assert_eq!(mode(&s.path), mode(&plain));
}

#[test]
fn a_file_store_is_a_store() {
    fn is_a_store<S: Store>(_: &S) {}
    let dir = TempDir::new("is_a_store");
    is_a_store(&FileStore::new(dir.path().join("x.json")));
    is_a_store(&MemoryStore::new());
}

#[test]
fn a_memory_store_shares_what_it_holds_with_its_clones() {
    let mut s = MemoryStore::new();
    assert!(matches!(s.load(), Err(ConcordError::MissingState(_))));
    let twin = s.clone();
    s.save(&json!({"v": 1})).unwrap();
    assert_eq!(twin.load().unwrap(), json!({"v": 1}));
    assert_eq!(twin.get(), Some(json!({"v": 1})));
}
