//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Where a run's state is kept. The library asks only for `load` and `save`: how and where is the caller's choice (a file, a
//! database, memory). Locking and path policy belong to the caller too: a store makes one `save` all-or-nothing, and nothing more.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::Value;

use crate::errors::{ConcordError, Result};

/// What a state store must offer.
pub trait Store {
    /// The saved state. Fails with `MissingState` if nothing has been saved, and `State` if what is saved cannot be read.
    fn load(&self) -> Result<Value>;

    /// Replace the saved state with `state`.
    fn save(&mut self, state: &Value) -> Result<()>;
}

/// A state kept in one JSON file. A save writes a temporary file next to it and renames it over the old one, so a reader (or a crash)
/// never sees half a state. The rename gives the file a new identity: serialise writers with a lock on a separate file, not on this
/// one.
#[derive(Debug, Clone)]
pub struct FileStore {
    pub path: PathBuf,
}

impl FileStore {
    pub fn new(path: impl AsRef<Path>) -> Self {
        FileStore { path: path.as_ref().to_path_buf() }
    }

    pub fn exists(&self) -> bool {
        self.path.exists()
    }
}

impl Store for FileStore {
    fn load(&self) -> Result<Value> {
        let text = match std::fs::read_to_string(&self.path) {
            Ok(text) => text,
            Err(exc) if exc.kind() == std::io::ErrorKind::NotFound => {
                return Err(ConcordError::missing_state(format!("no state file at {}", self.path.display())));
            }
            Err(exc) => return Err(ConcordError::state(format!("state file {} cannot be read ({exc})", self.path.display()))),
        };
        serde_json::from_str(&text)
            .map_err(|exc| ConcordError::state(format!("state file {} is not valid JSON ({exc})", self.path.display())))
    }

    fn save(&mut self, state: &Value) -> Result<()> {
        let mut text = serde_json::to_string_pretty(state).map_err(|exc| ConcordError::state(format!("the state is not JSON ({exc})")))?;
        text.push('\n');
        let name = self.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        // a name of its own for each write: two stores of one process that write the same file name must not meet in one temporary file
        static WRITES: AtomicU64 = AtomicU64::new(0);
        let tmp = self.path.with_file_name(format!(".{name}.{}.{}.tmp", std::process::id(), WRITES.fetch_add(1, Ordering::Relaxed)));
        let written =
            write_durably(&tmp, text.as_bytes()).and_then(|()| std::fs::rename(&tmp, &self.path)).and_then(|()| sync_dir(&self.path));
        if written.is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
        written.map_err(|exc| ConcordError::state(format!("state file {} cannot be written ({exc})", self.path.display())))
    }
}

/// Write `bytes` to `path` and make them reach the disk before the file is renamed over the old state: without it a crash can leave a file
/// that is empty.
fn write_durably(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = std::fs::File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

/// Make the rename of the file reach the disk too: the directory that holds it is what changed.
fn sync_dir(path: &Path) -> std::io::Result<()> {
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

/// A state kept in memory. A clone shares the state it holds, so a test can give one to an agent and read it, or give it to the
/// agent that replaces it after a restart.
#[derive(Debug, Clone, Default)]
pub struct MemoryStore {
    state: Arc<Mutex<Option<Value>>>,
}

impl MemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// The saved state, if any.
    pub fn get(&self) -> Option<Value> {
        self.guard().clone()
    }

    /// The state behind its lock. A thread that panicked while it held the lock left the state whole (it is replaced in one assignment),
    /// so a poisoned lock is no reason to fail.
    fn guard(&self) -> MutexGuard<'_, Option<Value>> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl Store for MemoryStore {
    fn load(&self) -> Result<Value> {
        self.get().ok_or_else(|| ConcordError::missing_state("nothing has been saved"))
    }

    fn save(&mut self, state: &Value) -> Result<()> {
        *self.guard() = Some(state.clone());
        Ok(())
    }
}
