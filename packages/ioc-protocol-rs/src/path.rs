//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The path of a state file. Two rules, each a protocol's, both the same mechanics: a tag inserted between the stem and the suffix of
//! the file name.
//!
//! * `unique_path`: the path given when it is free, else a new one with a random tag (CONCORD's initiator: a new run never shares a
//!   file with another). File names are split as `std::path::Path` splits them.
//! * `keyed_path`: under a run key, a path of its own for each key, the same on every call (ACCORD's `$ACCORD_RUN_KEY`). Paths are
//!   POSIX paths written as Python's `pathlib` writes them, file names split as `pathlib` splits them since Python 3.14.
//!
//! These only compute a path: creating the folder is the caller's.

use std::path::Path;

use uuid::Uuid;

/// `path` when nothing is there, else `path` with `-<tag>` inserted before its extension (`state.json` → `state-1a2b3c4d.json`), the tag
/// being what `tag` gives.
pub fn unique_path(path: &str, tag: impl FnOnce() -> String) -> String {
    if !Path::new(path).exists() {
        return path.to_string();
    }
    let tag = tag();
    let path = Path::new(path);
    let stem = path.file_stem().map_or_else(String::new, |s| s.to_string_lossy().into_owned());
    let name = match path.extension() {
        Some(ext) => format!("{stem}-{tag}.{}", ext.to_string_lossy()),
        None => format!("{stem}-{tag}"),
    };
    path.with_file_name(name).to_string_lossy().into_owned()
}

/// The path of a state file isolated under a run key.
///
/// Without a key (None or empty) it is `path`, unchanged. With one, it is named `<stem>-<8 hex digits><suffix>` after the file name of
/// `path`, the digits those of `uuid5(NAMESPACE_URL, "<run_key>:<name>")`, and placed in `dir` (when given and not empty) or else next to
/// `path`. The same key gives the same path on every call, and unrelated runs do not share a file. The path is written as `pathlib`
/// writes it (`a//b/./c` is `a/b/c`).
pub fn keyed_path(path: &str, run_key: Option<&str>, dir: Option<&str>) -> String {
    let Some(run_key) = run_key.filter(|k| !k.is_empty()) else {
        return path.to_string();
    };
    let (root, parts) = split_posix(path);
    let name = parts.last().copied().unwrap_or("");
    let (dir_root, dir_parts) = match dir.filter(|d| !d.is_empty()) {
        Some(dir) => split_posix(dir),
        None => (root, parts[..parts.len().saturating_sub(1)].to_vec()),
    };
    let digest = Uuid::new_v5(&Uuid::NAMESPACE_URL, format!("{run_key}:{name}").as_bytes()).simple().to_string();
    let (suffix, stem) = suffix_and_stem(name);
    let file = format!("{stem}-{}{suffix}", &digest[..8]);
    let mut all = dir_parts;
    all.push(&file);
    join_posix(dir_root, &all)
}

/// A POSIX path as `pathlib.PurePosixPath` reads it: its root (`""`, `"/"`, or `"//"` for exactly two leading slashes) and its parts,
/// without empty ones and `.`.
pub fn split_posix(path: &str) -> (&str, Vec<&str>) {
    let root = if path.starts_with("//") && !path.starts_with("///") {
        "//"
    } else if path.starts_with('/') {
        "/"
    } else {
        ""
    };
    (root, path.split('/').filter(|p| !p.is_empty() && *p != ".").collect())
}

/// The path of a root and parts, as `pathlib` writes it: `.` when there is nothing.
pub fn join_posix(root: &str, parts: &[&str]) -> String {
    let joined = format!("{root}{}", parts.join("/"));
    if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

/// The suffix and the stem of a file name, as `pathlib` has them since Python 3.14: `"x.json"` is `(".json", "x")`, `".hidden"` is
/// `("", ".hidden")`, `"x."` is `(".", "x")`.
pub fn suffix_and_stem(name: &str) -> (&str, &str) {
    let trimmed = name.trim_start_matches('.');
    let suffix = match trimmed.rfind('.') {
        Some(i) => &trimmed[i..],
        None => "",
    };
    let stem = match name.rfind('.') {
        Some(i) if !name[..i].trim_start_matches('.').is_empty() => &name[..i],
        _ => name,
    };
    (suffix, stem)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::file::tests::TempDir;

    #[test]
    fn a_free_path_is_kept_and_a_taken_one_gets_a_tag() {
        let dir = TempDir::new("unique");
        let base = dir.path().join("run.json");
        let base = base.to_str().unwrap();
        assert_eq!(unique_path(base, || unreachable!("no tag for a free path")), base);
        std::fs::write(base, "{}").unwrap();
        assert_eq!(unique_path(base, || "abcd1234".into()), dir.path().join("run-abcd1234.json").to_str().unwrap());
        let bare = dir.path().join("run");
        std::fs::write(&bare, "").unwrap();
        assert_eq!(unique_path(bare.to_str().unwrap(), || "t".into()), dir.path().join("run-t").to_str().unwrap());
        let hidden = dir.path().join(".state");
        std::fs::write(&hidden, "").unwrap();
        assert_eq!(unique_path(hidden.to_str().unwrap(), || "t".into()), dir.path().join(".state-t").to_str().unwrap());
    }

    #[test]
    fn a_run_key_gives_a_path_of_its_own() {
        assert_eq!(keyed_path("a//b.json", None, Some("x")), "a//b.json");
        assert_eq!(keyed_path("a//b.json", Some(""), None), "a//b.json");
        let one = keyed_path("a//./b.json", Some("k"), None);
        assert!(one.starts_with("a/b-") && one.ends_with(".json") && one.len() == "a/b-12345678.json".len(), "{one}");
        assert_eq!(keyed_path("b.json", Some("k"), Some("//d/")), format!("//d/{}", &one[2..]));
        assert_ne!(keyed_path("b.json", Some("other"), None), &one[2..]);
        assert_eq!(keyed_path("", Some("k"), None).len(), "-12345678".len());
    }

    #[test]
    fn posix_paths_are_read_and_written_like_pathlib() {
        assert_eq!(split_posix("///a/./b//"), ("/", vec!["a", "b"]));
        assert_eq!(split_posix("//a"), ("//", vec!["a"]));
        assert_eq!(join_posix("", &[]), ".");
        assert_eq!(join_posix("/", &["a", "b"]), "/a/b");
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
}
