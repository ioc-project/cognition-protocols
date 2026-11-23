//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The file backend: a state kept in one file, as the text its `Codec` writes.
//!
//! A save writes a temporary file next to it, makes it reach the disk, and renames it over the old one, so a reader (or a crash) never
//! sees half a state. The rename gives the file a new identity: serialise writers with a lock on a separate file (`lock`), not on this
//! one.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use super::{Codec, JsonCodec, Store, StoreError, StoreErrorKind};

/// A state kept in one file, written by the codec `C`.
#[derive(Debug, Clone)]
pub struct FileStore<C = JsonCodec> {
    /// The file.
    pub path: PathBuf,
    codec: C,
}

impl<C: Codec + Default> FileStore<C> {
    /// The store of the file `path`, with the default codec.
    pub fn new(path: impl AsRef<Path>) -> Self {
        FileStore::with_codec(path, C::default())
    }
}

impl<C: Codec> FileStore<C> {
    /// The store of the file `path`, written by `codec`.
    pub fn with_codec(path: impl AsRef<Path>, codec: C) -> Self {
        FileStore { path: path.as_ref().to_path_buf(), codec }
    }

    /// Whether the file is there.
    pub fn exists(&self) -> bool {
        self.path.exists()
    }

    /// The codec.
    pub fn codec(&self) -> &C {
        &self.codec
    }
}

impl<C: Codec> Store for FileStore<C> {
    type State = C::State;

    fn load(&self) -> Result<C::State, StoreError> {
        let path = self.path.display();
        let text = match std::fs::read_to_string(&self.path) {
            Ok(text) => text,
            Err(exc) if exc.kind() == std::io::ErrorKind::NotFound => {
                return Err(StoreError::new(StoreErrorKind::Missing, format!("no state file at {path}")));
            }
            Err(exc) => return Err(StoreError::new(StoreErrorKind::Unreadable, format!("state file {path} cannot be read ({exc})"))),
        };
        self.codec
            .decode(&text)
            .map_err(|exc| StoreError::new(StoreErrorKind::Invalid, format!("state file {path} is not valid {} ({exc})", C::FORMAT)))
    }

    fn save(&mut self, state: &C::State) -> Result<(), StoreError> {
        let text = self
            .codec
            .encode(state)
            .map_err(|exc| StoreError::new(StoreErrorKind::Unencodable, format!("the state is not {} ({exc})", C::FORMAT)))?;
        write_atomic(&self.path, text.as_bytes()).map_err(|exc| {
            StoreError::new(StoreErrorKind::Unwritable, format!("state file {} cannot be written ({exc})", self.path.display()))
        })
    }
}

/// Replace the file `path` with `bytes`, all or nothing: they are written to a temporary file next to it, which reaches the disk and is
/// renamed over `path`, and the rename reaches the disk too. On a failure the temporary file is removed and `path` is as it was.
///
/// # Errors
///
/// The I/O error of the write, the sync or the rename.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    // a name of its own for each write: two stores of one process that write the same file name must not meet in one temporary file
    static WRITES: AtomicU64 = AtomicU64::new(0);
    let tmp = path.with_file_name(format!(".{name}.{}.{}.tmp", std::process::id(), WRITES.fetch_add(1, Ordering::Relaxed)));
    let written = write_durably(&tmp, bytes).and_then(|()| std::fs::rename(&tmp, path)).and_then(|()| sync_dir(path));
    if written.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    written
}

/// Write `bytes` to `path` and make them reach the disk before the file is renamed over the old state: without it a crash can leave a file
/// that is empty.
fn write_durably(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = std::fs::File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

/// Make the rename of the file `path` reach the disk too: the directory that holds it is what changed. Nothing on a platform where a
/// directory cannot be opened to be synced.
///
/// # Errors
///
/// The I/O error of opening or syncing the directory.
pub fn sync_dir(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let dir = path.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
        std::fs::File::open(dir)?.sync_all()
    }
    #[cfg(not(unix))]
    {
        let _ = path; // a directory cannot be opened to be synced on every platform
        Ok(())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::store::conformance;
    use serde_json::{json, Value};

    /// A directory of its own, removed at the end of the test.
    pub(crate) struct TempDir(PathBuf);

    impl TempDir {
        pub(crate) fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("ioc-protocol-{}-{name}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).unwrap();
            TempDir(dir)
        }

        pub(crate) fn path(&self) -> &Path {
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

    /// JSON that refuses to write a state holding `"poison"`: a save that fails, for the conformance suite.
    #[derive(Debug, Clone, Default)]
    struct Picky;

    impl Codec for Picky {
        type State = Value;
        const FORMAT: &'static str = "picky JSON";

        fn encode(&self, state: &Value) -> Result<String, String> {
            if state.get("poison").is_some() {
                return Err("poison".into());
            }
            JsonCodec.encode(state)
        }

        fn decode(&self, text: &str) -> Result<Value, String> {
            JsonCodec.decode(text)
        }
    }

    #[test]
    fn the_file_backend_conforms() {
        let dir = TempDir::new("conforms");
        let path = dir.path().join("state.json");
        conformance::run(|| FileStore::with_codec(&path, Picky), json!({"v": 1}), json!({"v": [2, "é"]}), json!({"poison": 1}));
        assert_eq!(dir.names(), ["state.json"]);
    }

    #[test]
    fn a_saved_state_is_the_text_of_the_codec() {
        let dir = TempDir::new("text");
        let mut s: FileStore = FileStore::new(dir.path().join("two.json"));
        s.save(&json!({"k": 1})).unwrap();
        assert!(s.exists());
        assert_eq!(std::fs::read_to_string(dir.path().join("two.json")).unwrap(), "{\n  \"k\": 1\n}\n");
    }

    #[test]
    fn a_missing_file_is_told_from_one_that_is_not_a_state() {
        let dir = TempDir::new("missing");
        let s: FileStore = FileStore::new(dir.path().join("nope.json"));
        assert!(!s.exists());
        let err = s.load().unwrap_err();
        assert_eq!(err.kind(), StoreErrorKind::Missing);
        assert!(err.message().starts_with("no state file at ") && err.message().ends_with("nope.json"), "{err}");
        std::fs::write(dir.path().join("bad.json"), "{oops").unwrap();
        let err = FileStore::<JsonCodec>::new(dir.path().join("bad.json")).load().unwrap_err();
        assert_eq!(err.kind(), StoreErrorKind::Invalid);
        assert!(err.message().contains("bad.json is not valid JSON ("), "{err}");
        let err = FileStore::with_codec(dir.path().join("x.json"), Picky).save(&json!({"poison": 0})).unwrap_err();
        assert_eq!((err.kind(), err.message()), (StoreErrorKind::Unencodable, "the state is not picky JSON (poison)"));
    }

    #[test]
    fn a_failed_rename_cleans_up_its_temporary_file() {
        let dir = TempDir::new("failed_rename");
        let target = dir.path().join("state.json");
        std::fs::create_dir(&target).unwrap();
        let err = FileStore::<JsonCodec>::new(&target).save(&json!({"v": 2})).unwrap_err();
        assert_eq!(err.kind(), StoreErrorKind::Unwritable);
        assert!(err.message().contains("cannot be written"), "{err}");
        assert_eq!(dir.names(), ["state.json"]);
        let err = FileStore::<JsonCodec>::new(&target).load().unwrap_err();
        assert_eq!(err.kind(), StoreErrorKind::Unreadable);
    }

    #[cfg(unix)]
    #[test]
    fn the_file_keeps_the_permissions_a_plain_write_would_give() {
        use std::os::unix::fs::PermissionsExt;
        let dir = TempDir::new("permissions");
        let plain = dir.path().join("plain.json");
        std::fs::write(&plain, "{}").unwrap();
        let mut s: FileStore = FileStore::new(dir.path().join("state.json"));
        s.save(&json!({})).unwrap();
        let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o7777;
        assert_eq!(mode(&s.path), mode(&plain));
    }

    #[test]
    fn write_atomic_replaces_the_file_in_a_relative_folder_too() {
        let dir = TempDir::new("atomic");
        let path = dir.path().join("f");
        write_atomic(&path, b"one").unwrap();
        write_atomic(&path, b"two").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"two");
        assert_eq!(dir.names(), ["f"]);
        sync_dir(Path::new("relative-name")).unwrap();
    }
}
