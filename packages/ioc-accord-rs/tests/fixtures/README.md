# Golden cases

`golden.json` is a list of cases `{"fn", "input", "expected"}`: a function of
`protocols/accord/skills/accord-initiator/scripts/accord_initiator.py` (or of Python's `difflib`, `json` and `round` that the script
relies on), its input, and its output as the script prints it, `json.dumps({"ok": ...} or {"error": ..., "refusal": ...}, indent=2)`.
A `session` case is a list of commands run in order, with each result, the state after it, and the final state file.

They were produced with a throwaway Python harness (not kept in the repository) that imports the script with `ACCORD_EMBEDDING=off`,
runs hand-written inputs and randomized ones (seed 7) through the same functions, and keeps every hand-written case, two of each
randomized function, and four sessions. `tests/differential.rs` checks that the library prints the same text, byte for byte; the same
test runs a larger file of the same shape when `ACCORD_DIFF_CASES` names one.
