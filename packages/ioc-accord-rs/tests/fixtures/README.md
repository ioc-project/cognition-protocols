# Golden cases

`golden.json` is a list of cases `{"fn", "input", "expected"}`: a function of
`protocols/accord/skills/accord-initiator/scripts/accord_initiator.py` (or of Python's `difflib`, `json` and `round` that the script
relies on), its input, and its output as the script prints it, `json.dumps({"ok": ...} or {"error": ..., "refusal": ...}, indent=2)`.
A `session` case is a list of commands run in order, with each result, the state after it, and the final state file.

They were produced with a throwaway Python harness (not kept in the repository) that imports the script with `ACCORD_EMBEDDING=off`,
runs hand-written inputs and randomized ones (seed 7) through the same functions, and keeps every hand-written case, two of each
randomized function, and four sessions. `tests/differential.rs` checks that the library prints the same text, byte for byte; the same
test runs a larger file of the same shape when `ACCORD_DIFF_CASES` names one.

# CLI transcripts

`cli.json` is a list of command sequences run through `accord_initiator.py` (with `ACCORD_EMBEDDING=off`), each `{"name", "env",
"state", "steps"}`: the environment (`ACCORD_STATE`, `ACCORD_RUN_KEY`, `ACCORD_STATE_DIR`), the state file the sequence keeps, and for
each step its arguments, its standard input, the files written before it (text, `{"hex": ...}` for bytes that are not UTF-8, or
`{"dir": true}`), its exit code, its standard output and the state file after it. The random id of a session (`init --task`) is written
`{SID}`; `atoms_by_agent` of `frame` is in name order (the script's is Python's hash order); a usage error keeps only its exit code,
since its text is `argparse`'s; the output of `--help` is not kept.

They were recorded by a throwaway harness (not kept in the repository) that runs the same sequences through the script and through the
command (`cargo run --example ioc-accord --features cli`), compares the standard output, the exit code and every file each leaves, byte
for byte, over hand-written sequences (every error path), randomized self-driving and step-by-step sessions, run keys, and a fuzz of the
JSON reader, and keeps a modest subset. `tests/cli.rs` replays them.
