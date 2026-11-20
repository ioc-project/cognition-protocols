#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The candidate schema: the JSON Schema subset that says what one option may look like, checked on every proposal.

Only a small subset is supported, and anything else is refused, so a rule is never silently left unenforced. An open
format is `{"type": "string", "description": "..."}`; a closed list is `{"enum": ["A", "B"]}`.

A `pattern` is limited to the subset the spec lists, so that every agent reads it the same way: `compile_pattern` reads it
character by character, refuses what is outside the subset and compiles it to a small program of its own. It does not use
Python's `re`, which backtracks: a pattern like `^(a+)+$` would take exponential time on a text a peer chose.
"""
import json
import re
import string

from ioc_concord.errors import SchemaError
from ioc_concord.protocol import WS

SCHEMA_KEYS = ("type", "enum", "pattern", "minLength", "maxLength", "description", "title")

# What `\d`, `\w`, `\s` and `.` stand for in the spec, as ranges of code points, so that Unicode-aware classes never apply.
_CLASSES = {"d": [("0", "9")], "w": [("A", "Z"), ("a", "z"), ("0", "9"), ("_", "_")],
            "s": [(" ", " "), ("\t", "\t"), ("\r", "\r"), ("\n", "\n")]}
_DOT = (True, [("\r", "\r"), ("\n", "\n")])
_QUANT_RE = re.compile(r"\{([0-9]+)(?:(,)([0-9]*))?\}")

# Limits of this library, not of the spec, that keep the cost of a pattern bounded: a count of a quantifier, how deep groups
# nest, and how many instructions the pattern makes.
MAX_COUNT, MAX_DEPTH, MAX_PROGRAM = 1000, 100, 2000


def _escaped(pattern, i, in_class):
    """(the character after the backslash at `pattern[i]`, or None for a class like `\\d`; the ranges it stands for; the next
    index). Raises ValueError."""
    nxt = pattern[i + 1] if i + 1 < len(pattern) else ""
    if nxt and nxt in string.punctuation:
        return nxt, [(nxt, nxt)], i + 2
    if nxt in _CLASSES and not in_class:
        return None, _CLASSES[nxt], i + 2
    raise ValueError(f"\\{nxt} is outside the subset" if nxt else "a backslash at the end")


def _class(pattern, i):
    """((negated, ranges) of the class that starts at `pattern[i]` == "[", the next index). Raises ValueError."""
    i += 1
    negate = pattern.startswith("^", i)
    i += negate
    ranges = []

    def atom(j):
        if j >= len(pattern):
            raise ValueError("a class is not closed")
        if pattern[j] == "\\":
            ch, _, j = _escaped(pattern, j, True)
            if ch is None:
                raise ValueError("\\d, \\w and \\s are outside the subset inside a class")
            return ch, j
        return pattern[j], j + 1

    while i < len(pattern) and pattern[i] != "]":
        lo, i = atom(i)
        if pattern.startswith("-", i) and i + 1 < len(pattern) and pattern[i + 1] != "]":
            hi, i = atom(i + 1)
            if ord(lo) > ord(hi):
                raise ValueError("a range of a class runs backwards")
            ranges.append((lo, hi))
        else:
            ranges.append((lo, lo))
    if i >= len(pattern):
        raise ValueError("a class is not closed")
    if not ranges:
        raise ValueError("a class is empty")
    return (negate, ranges), i + 1


def _count(text):
    if len(text) > 4 or int(text) > MAX_COUNT:
        raise ValueError(f"a count is above {MAX_COUNT}")
    return int(text)


def _parse(pattern):
    """The syntax tree of `pattern`: ("set", negated, ranges), ("cat", [..]), ("alt", [..]), ("rep", node, low, high or None),
    ("bol",) or ("eol",). Raises ValueError when the pattern is outside the subset."""
    frames = [[[], []]]               # for each open group: its alternatives so far and the sequence being read
    prev = None                       # what came before, "atom", "anchor", "quantifier" or None
    i = 0

    def close(frame):
        alts = frame[0] + [("cat", frame[1])]
        return alts[0] if len(alts) == 1 else ("alt", alts)

    while i < len(pattern):
        c = pattern[i]
        seq = frames[-1][1]
        if c == "\\":
            _, ranges, i = _escaped(pattern, i, False)
            seq.append(("set", False, ranges))
            prev = "atom"
        elif c == "[":
            (negate, ranges), i = _class(pattern, i)
            seq.append(("set", negate, ranges))
            prev = "atom"
        elif c == "(":
            if pattern.startswith("(?", i):
                if not pattern.startswith("(?:", i):
                    raise ValueError("(? is outside the subset except (?:")
                i += 3
            else:
                i += 1
            if len(frames) > MAX_DEPTH:
                raise ValueError(f"groups nest deeper than {MAX_DEPTH}")
            frames.append([[], []])
            prev = None
        elif c == ")":
            if len(frames) == 1:
                raise ValueError("a ) has no (")
            frames[-2][1].append(close(frames.pop()))
            i += 1
            prev = "atom"
        elif c == "|":
            frames[-1][0].append(("cat", seq))
            frames[-1][1] = []
            i += 1
            prev = None
        elif c in "^$":
            seq.append(("bol",) if c == "^" else ("eol",))
            i += 1
            prev = "anchor"
        elif c == ".":
            seq.append(("set",) + _DOT)
            i += 1
            prev = "atom"
        elif c in "*+?{":
            if prev != "atom":
                raise ValueError(f"a {c} has nothing to repeat" if prev != "quantifier" else f"a {c} after a quantifier")
            if c == "{":
                m = _QUANT_RE.match(pattern, i)
                if m is None:
                    raise ValueError("a { is not a quantifier")
                low = _count(m.group(1))
                high = (_count(m.group(3)) if m.group(3) else None) if m.group(2) else low
                if high is not None and low > high:
                    raise ValueError("a quantifier runs backwards")
                i = m.end()
            else:
                low, high = {"*": (0, None), "+": (1, None), "?": (0, 1)}[c]
                i += 1
            seq.append(("rep", seq.pop(), low, high))
            prev = "quantifier"
        elif c in "]}":
            raise ValueError(f"a {c} stands alone")
        else:
            seq.append(("set", False, [(c, c)]))
            i += 1
            prev = "atom"
    if len(frames) > 1:
        raise ValueError("a ( is not closed")
    return close(frames[0])


def _emit(node, prog):
    """Appends the instructions of `node` to `prog`: ("set", negated, ranges), ("split", first, second), ("bol",), ("eol",).
    Control passes to the next instruction; a jump is a split to one place."""
    if len(prog) > MAX_PROGRAM:
        raise ValueError(f"the pattern needs more than {MAX_PROGRAM} steps")
    kind = node[0]
    if kind in ("set", "bol", "eol"):
        prog.append(node)
    elif kind == "cat":
        for sub in node[1]:
            _emit(sub, prog)
    elif kind == "alt":
        jumps = []
        for sub in node[1][:-1]:
            split = len(prog)
            prog.append(None)
            _emit(sub, prog)
            jumps.append(len(prog))
            prog.append(None)
            prog[split] = ("split", split + 1, len(prog))
        _emit(node[1][-1], prog)
        for j in jumps:
            prog[j] = ("split", len(prog), len(prog))
    else:
        _, sub, low, high = node
        for _ in range(low):
            _emit(sub, prog)
        if high is None:                                     # sub*: split, sub, back to the split
            split = len(prog)
            prog.append(None)
            _emit(sub, prog)
            prog.append(("split", split, split))
            prog[split] = ("split", split + 1, len(prog))
        else:                                                # (sub(sub(...)?)?)? : each optional copy skips to the end
            splits = []
            for _ in range(high - low):
                splits.append(len(prog))
                prog.append(None)
                _emit(sub, prog)
            for s_ in splits:
                prog[s_] = ("split", s_ + 1, len(prog))


class Pattern:
    """A pattern of the subset, compiled to a program that is run on all the positions of the text at once, so that matching costs
    at most the length of the text times the size of the program, however the pattern is written."""

    def __init__(self, source, prog):
        self.source, self._prog = source, prog

    def _add(self, pcs, pc, pos, end, seen):
        """Adds to `pcs` the instructions that can be reached from `pc` at `pos` without reading a character."""
        todo = [pc]
        while todo:
            pc = todo.pop()
            if pc in seen:
                continue
            seen.add(pc)
            ins = self._prog[pc] if pc < len(self._prog) else None
            if ins is None:
                pcs.append(pc)                                # the end of the program: a match
            elif ins[0] == "split":
                todo += [ins[2], ins[1]]
            elif ins[0] == "bol":
                if pos == 0:
                    todo.append(pc + 1)
            elif ins[0] == "eol":
                if pos == end:
                    todo.append(pc + 1)
            else:
                pcs.append(pc)

    def search(self, text):
        """True if the pattern is found anywhere in `text`."""
        n, size = len(text), len(self._prog)
        current = []
        for pos in range(n + 1):
            self._add(current, 0, pos, n, set(c for c in current))   # a match may start here
            if size in current:
                return True
            if pos == n:
                return False
            ch, nxt, seen = text[pos], [], set()
            for pc in current:
                _, negate, ranges = self._prog[pc]
                if any(lo <= ch <= hi for lo, hi in ranges) != negate:
                    self._add(nxt, pc + 1, pos + 1, n, seen)
            current = nxt
        return False


def compile_pattern(pattern):
    """The compiled pattern for the `pattern` of a schema, with a `search(text)` that says whether it is found. Raises SchemaError,
    with the reason, when the pattern is outside the subset of the spec or too large. It matches if found anywhere in the text,
    unless anchored."""
    try:
        prog = []
        _emit(_parse(pattern), prog)
        return Pattern(pattern, prog)
    except ValueError as exc:
        raise SchemaError(f"is not in the supported subset ({exc})")


def load_json(text):
    """The value of the JSON `text`. Raises ValueError, also for a text nested so deep that the parser gives up: the JSON of a schema
    comes from a peer."""
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("it is nested too deep")


def parse_schema(text, label="schema"):
    """The schema in the JSON `text`, checked. `label` is what the caller calls it in its error messages (a CLI says
    `--schema`). Raises SchemaError."""
    try:
        spec = load_json((text or "").strip(WS))
    except ValueError as exc:
        raise SchemaError(f"{label} must be a JSON Schema object, but it is not valid JSON ({exc}). For an open format use "
                          '\'{"type": "string", "description": "what one option is, in plain words"}\'.')
    return validate_schema(spec, label)


def validate_schema(spec, label="schema"):
    """`spec` itself, if it is a supported schema. Raises SchemaError."""
    if not isinstance(spec, dict):
        raise SchemaError(f"{label} must be a JSON object")
    unknown = sorted(set(spec) - set(SCHEMA_KEYS))
    if unknown:
        raise SchemaError(f"{label} uses keywords that are not enforced here: {', '.join(unknown)}. Supported: "
                          f"{', '.join(SCHEMA_KEYS)}.")
    if "type" in spec and spec["type"] != "string":
        raise SchemaError(f'{label} "type" can only be "string": a candidate is text')
    if not (spec.get("enum") or str(spec.get("description") or "").strip(WS)):
        raise SchemaError(f'{label} needs a "description" (what one option is, in plain words) or an "enum"')
    if "enum" in spec:
        e = spec["enum"]
        if not (isinstance(e, list) and len(e) >= 2 and all(isinstance(x, str) and x.strip(WS) for x in e)):
            raise SchemaError(f'{label} "enum" must be a list of at least 2 non-empty strings')
        if any(x != x.strip(WS) for x in e):
            raise SchemaError(f'{label} "enum" has an option with whitespace at either end: a candidate is trimmed before it is compared, '
                              "so the option could never match")
        if len(set(e)) != len(e):
            raise SchemaError(f'{label} "enum" holds the same option twice')
    if "pattern" in spec:
        if not isinstance(spec["pattern"], str):
            raise SchemaError(f'{label} "pattern" must be a string')
        try:
            compile_pattern(spec["pattern"])
        except SchemaError as exc:
            raise SchemaError(f'{label} "pattern" {exc}')
    for k in ("minLength", "maxLength"):
        if k in spec and not (isinstance(spec[k], int) and not isinstance(spec[k], bool) and spec[k] >= 0):
            raise SchemaError(f'{label} "{k}" must be a non-negative integer')
    if "minLength" in spec and "maxLength" in spec and spec["minLength"] > spec["maxLength"]:
        raise SchemaError(f'{label} "minLength" is greater than "maxLength"')
    return spec


def check_candidate(spec, text):
    """Why `text` does not satisfy the schema `spec` (None when it does). The candidate is the text without the whitespace
    around it; an option of a closed list must match exactly, and lengths count Unicode code points. Raises SchemaError if
    `spec` carries a pattern outside the supported subset: a schema that cannot be enforced accepts nothing."""
    t = str(text).strip(WS)
    if "enum" in spec and t not in spec["enum"]:
        return "must be exactly one of the listed options (" + "; ".join(spec["enum"]) + ")"
    if "pattern" in spec:
        try:
            found = compile_pattern(spec["pattern"]).search(t)
        except SchemaError as exc:
            raise SchemaError(f'the schema "pattern" {exc}')
        if not found:
            return f"must match the regular expression {spec['pattern']}"
    if "minLength" in spec and len(t) < spec["minLength"]:
        return f"must be at least {spec['minLength']} characters"
    if "maxLength" in spec and len(t) > spec["maxLength"]:
        return f"must be at most {spec['maxLength']} characters"
    return None


def render_schema(spec):
    """The candidate format as the agents read it."""
    parts = []
    if spec.get("description"):
        parts.append(str(spec["description"]))
    if "enum" in spec:
        parts.append("exactly one of: " + " | ".join(spec["enum"]))
    if "pattern" in spec:
        parts.append(f"text matching the regular expression {spec['pattern']}")
    if "minLength" in spec or "maxLength" in spec:
        lo, hi = spec.get("minLength"), spec.get("maxLength")
        parts.append("between %s and %s characters" % (lo if lo is not None else 0, hi if hi is not None else "any number of")
                     if lo is not None and hi is not None else
                     (f"at least {lo} characters" if lo is not None else f"at most {hi} characters"))
    return "; ".join(parts) or spec.get("title") or "text"
