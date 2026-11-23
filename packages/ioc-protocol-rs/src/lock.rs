//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! An exclusive lock on a file of its own, to serialise the processes that load, change and save one state.
//!
//! The lock is on a separate file (`lock_path`: the state file's name with `.lock`), never on the state file: a `FileStore` replaces the
//! state file on every save, so a lock on it would be on a file that is no longer the state. The lock is advisory (`flock` on Unix,
//! `LockFileEx` on Windows, through `std::fs::File::lock`): it holds against the processes that take it, and is released when the
//! `FileLock` is dropped, or when the process ends. The lock file is left in place: removing it would let two processes lock two
//! different files of the same name.

use std::fs::{File, OpenOptions, TryLockError};
use std::path::{Path, PathBuf};

/// The lock file of the state file `state`: `<name>.lock` next to it.
pub fn lock_path(state: &Path) -> PathBuf {
    let mut name = state.file_name().map(|n| n.to_os_string()).unwrap_or_default();
    name.push(".lock");
    state.with_file_name(name)
}

/// An exclusive lock, held until it is dropped.
#[derive(Debug)]
pub struct FileLock {
    file: File,
    path: PathBuf,
}

fn open(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)
}

impl FileLock {
    /// The lock of the file `path` (made when it is not there), waiting for another holder to release it.
    ///
    /// # Errors
    ///
    /// The I/O error of opening or locking the file.
    pub fn exclusive(path: impl AsRef<Path>) -> std::io::Result<FileLock> {
        let path = path.as_ref();
        let file = open(path)?;
        file.lock()?;
        Ok(FileLock { file, path: path.to_path_buf() })
    }

    /// The lock of the file `path` (made when it is not there) if no one holds it, else None, without waiting.
    ///
    /// # Errors
    ///
    /// The I/O error of opening or locking the file.
    pub fn try_exclusive(path: impl AsRef<Path>) -> std::io::Result<Option<FileLock>> {
        let path = path.as_ref();
        let file = open(path)?;
        match file.try_lock() {
            Ok(()) => Ok(Some(FileLock { file, path: path.to_path_buf() })),
            Err(TryLockError::WouldBlock) => Ok(None),
            Err(TryLockError::Error(exc)) => Err(exc),
        }
    }

    /// The lock file.
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        // closing the file releases the lock too; unlocking first releases it even while another handle on the file is open
        let _ = self.file.unlock();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::file::tests::TempDir;

    #[test]
    fn the_lock_file_is_next_to_the_state() {
        assert_eq!(lock_path(Path::new("a/state.json")), Path::new("a/state.json.lock"));
        assert_eq!(lock_path(Path::new("s")), Path::new("s.lock"));
    }

    #[test]
    fn one_holder_at_a_time() {
        let dir = TempDir::new("lock");
        let path = lock_path(&dir.path().join("state.json"));
        let held = FileLock::exclusive(&path).unwrap();
        assert_eq!(held.path(), path);
        assert!(FileLock::try_exclusive(&path).unwrap().is_none());
        drop(held);
        let again = FileLock::try_exclusive(&path).unwrap().expect("free once dropped");
        assert!(path.exists());
        drop(again);
    }

    #[test]
    fn a_waiting_holder_gets_the_lock_once_it_is_released() {
        let dir = TempDir::new("lock_wait");
        let path = dir.path().join("x.lock");
        let held = FileLock::exclusive(&path).unwrap();
        let (tx, rx) = std::sync::mpsc::channel();
        let waiter = {
            let path = path.clone();
            std::thread::spawn(move || {
                let lock = FileLock::exclusive(&path).unwrap();
                tx.send(()).unwrap();
                drop(lock);
            })
        };
        assert!(rx.recv_timeout(std::time::Duration::from_millis(100)).is_err(), "the lock is held");
        drop(held);
        rx.recv_timeout(std::time::Duration::from_secs(10)).expect("the waiter gets the lock");
        waiter.join().unwrap();
    }

    #[test]
    fn a_lock_in_a_missing_folder_is_an_error() {
        let dir = TempDir::new("lock_missing");
        assert!(FileLock::exclusive(dir.path().join("no/such/x.lock")).is_err());
        assert!(FileLock::try_exclusive(dir.path().join("no/such/x.lock")).is_err());
    }
}
