#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Bundle library modules into self-contained scripts, like `go generate`.

A skill script must stay one self-contained file, but the code it shares with other scripts (and with any other integration)
lives in a library. This tool copies the modules a script uses into it, between two marker lines, so the script runs with no
install and the library stays the only source.

    python3 tools/bundle.py            write every bundled block (run it after editing a library)
    python3 tools/bundle.py --check    exit 1 if a script differs from its library, change nothing (for CI)
    python3 tools/bundle.py --ref REV  bundle the library as it is at the git revision REV instead of its pin
                                       (`--ref worktree`: the files on disk, for trying a library that is not committed yet)

A library is pinned to a git revision, a commit id or a tag (`ref` in tools/bundle.toml): the scripts carry that version of the
library, not whatever is on disk, so the library can change without the scripts. To adopt a newer library, change the pin and run
`just sync`. The pin must be a commit that is on `main` (or a tag), so that a fresh clone has it.

tools/bundle.toml says which modules go into which script. A script marks where its block goes with two lines:

    # >>> BUNDLED <library>
    # <<< END BUNDLED <library>

A module is bundled as it is, minus its docstring, its imports (hoisted to the top of the block) and `__all__`. So it must:

    - import only the standard library, and the library's own modules as `from <library>.<module> import <names>`
    - keep its top-level names unique across the bundled modules and distinct from the script's own top-level names

Only what the script reaches is copied: a function, class or constant that neither the script nor anything it uses refers to
is left out, so a script does not carry library code it never runs. A target with `shake = false` gets every module whole.
Reachability is by name, so a module must not be reached by `globals()` or `getattr` on its own namespace.
"""
import argparse
import ast
import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).resolve().parent / "bundle.toml"
WORKTREE = "worktree"      # as a `ref`: read the library from the files on disk, not from a git revision


class BundleError(Exception):
    pass


class MissingSource(BundleError):
    """A file that is not in the library (on disk, or at its pinned revision)."""


def read_source(lib, rel):
    """The text of `rel`, a path from the repository root, in the revision that `lib` is pinned to (`ref`), or on disk when it is not
    pinned or its ref is `worktree`. Raises BundleError when the revision or the file is not there."""
    ref = lib.get("ref")
    if not ref or ref == WORKTREE:
        try:
            return (ROOT / rel).read_text()
        except FileNotFoundError:
            raise MissingSource(f"{lib['name']}: no such file ({rel})")
    git = ["git", "-C", str(ROOT)]
    if subprocess.run(git + ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True).returncode != 0:
        raise BundleError(f"{lib['name']} is pinned to {ref}, which this clone does not have. Fetch the history and the tags "
                          "(`git fetch --tags --unshallow`), or check `ref` in tools/bundle.toml")
    shown = subprocess.run(git + ["show", f"{ref}:{rel}"], capture_output=True, text=True)
    if shown.returncode != 0:
        raise MissingSource(f"{lib['name']}: no such file ({rel}) at {ref}")
    return shown.stdout


def marker_re(name):
    return re.compile(rf"^# >>> BUNDLED {re.escape(name)}\b.*?^# <<< END BUNDLED {re.escape(name)}[^\n]*\n", re.S | re.M)


def extract_block(text, name):
    """The bundled block of library `name` in `text` (markers included), or None."""
    m = marker_re(name).search(text)
    return m.group(0) if m else None


def _assigned(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]


def top_level_names(tree):
    """Every name a module binds at the top level, imports included."""
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names += _assigned(node)
        elif isinstance(node, ast.Import):
            names += [(a.asname or a.name).split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [a.asname or a.name for a in node.names]
    return names


class Unit:
    """A top-level definition that can be left out: the names it binds and uses, and its lines start..end-1."""

    def __init__(self, names, refs, start, end):
        self.names, self.refs, self.start, self.end = names, refs, start, end


def names_used(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


class Module:
    """One library module, read for bundling."""

    def __init__(self, lib, name):
        self.lib, self.name = lib, name
        rel = (Path(lib["path"]) / "src" / lib["name"] / f"{name}.py").as_posix()
        try:
            text = read_source(lib, rel)
        except MissingSource:
            at = f" at {lib['ref']}" if lib.get("ref") not in (None, WORKTREE) else ""
            raise BundleError(f"{lib['name']}.{name}: no such module ({rel}{at})")
        self.path = ROOT / rel
        self.tree = ast.parse(text, filename=str(self.path))
        self.where = f"{lib['name']}.{name}"
        self.imports, self.deps, self.defined = {}, {}, []   # imports: source -> the names it binds
        self.units, self.always = [], set()                  # always: names used by statements that cannot be left out
        lines = text.split("\n")
        drop = []
        for i, node in enumerate(self.tree.body):
            if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                drop.append(node)                                  # the module docstring
                for n in range(node.lineno - 1):                   # and the licence header above it
                    drop.append(ast.Pass(lineno=n + 1, end_lineno=n + 1))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                drop.append(node)
                self._import(node, text)
            elif isinstance(node, ast.Assign) and _assigned(node) == ["__all__"]:
                drop.append(node)
            else:
                names = top_level_names(ast.Module([node], []))
                self.defined += names
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                    self.units.append(Unit(names, names_used(node), *self._span(node, lines)))
                else:
                    self.always |= names_used(node)
        for node in drop:
            for n in range(node.lineno - 1, node.end_lineno):
                lines[n] = None
        self.lines = lines

    @staticmethod
    def _span(node, lines):
        """The lines of a definition: its decorators and the comment lines right above it, and the blank lines after."""
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
        while start > 0 and lines[start - 1] is not None and lines[start - 1].startswith("#"):
            start -= 1
        end = node.end_lineno
        while end < len(lines) and lines[end] == "":
            end += 1
        return start, end

    def body(self, keep=None):
        """The module's code, without its docstring, imports and `__all__`; with only the units in `keep` if given."""
        lines = list(self.lines)
        for u in self.units:
            if keep is not None and u not in keep:
                for n in range(u.start, u.end):
                    lines[n] = None
        return "\n".join(line for line in lines if line is not None).strip("\n") + "\n"

    def _import(self, node, text):
        lib = self.lib["name"]
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise BundleError(f"{self.where}: relative import (line {node.lineno}): write `from {lib}.<module> import ...`")
            mod = node.module or ""
            if mod == "__future__":
                raise BundleError(f"{self.where}: `from __future__` (line {node.lineno}) cannot be bundled")
            if mod == lib or mod.startswith(lib + "."):
                sub = mod[len(lib) + 1:]
                if not sub or "." in sub:
                    raise BundleError(f"{self.where}: line {node.lineno}: import a module's names as "
                                      f"`from {lib}.<module> import <names>`")
                if any(a.name == "*" for a in node.names):
                    raise BundleError(f"{self.where}: line {node.lineno}: no `import *`")
                self.deps.setdefault(sub, []).extend(a.name for a in node.names)
                return
            roots = [mod.split(".")[0]]
        else:
            roots = [a.name.split(".")[0] for a in node.names]
        for root in roots:
            if root == lib:
                raise BundleError(f"{self.where}: line {node.lineno}: import the library's names as "
                                  f"`from {lib}.<module> import <names>`")
            if root not in sys.stdlib_module_names:
                raise BundleError(f"{self.where}: line {node.lineno}: `{root}` is not in the standard library; a bundled "
                                  "module is stdlib-only")
        self.imports[ast.get_source_segment(text, node)] = top_level_names(ast.Module([node], []))


def resolve(lib, wanted):
    """The modules to bundle, dependencies first. A dependency that is not listed is an error: the config is explicit."""
    mods = {n: Module(lib, n) for n in wanted}
    order, state = [], {}

    def visit(name, chain):
        if state.get(name) == "done":
            return
        if state.get(name) == "busy":
            raise BundleError("import cycle: " + " -> ".join(chain + [name]))
        state[name] = "busy"
        for dep in mods[name].deps:
            if dep not in mods:
                raise BundleError(f"{lib['name']}.{name} imports {lib['name']}.{dep}, which is not in `modules` "
                                  f"for this target: add \"{dep}\"")
            visit(dep, chain + [name])
        state[name] = "done"
        order.append(name)

    for name in wanted:
        visit(name, [])
    for name in order:
        for dep, names in mods[name].deps.items():
            missing = sorted(set(names) - set(mods[dep].defined))
            if missing:
                raise BundleError(f"{lib['name']}.{name} imports {', '.join(missing)} from {lib['name']}.{dep}, "
                                  "which does not define it")
    return [mods[n] for n in order]


def reachable(mods, script_names):
    """(the units to keep, every name they and the script use): what the script reaches, by name."""
    unit_of = {n: u for m in mods for u in m.units for n in u.names}
    needed = set(script_names).union(*(m.always for m in mods))
    keep, todo = set(), [n for n in needed if n in unit_of]
    while todo:
        unit = unit_of[todo.pop()]
        if unit in keep:
            continue
        keep.add(unit)
        needed |= unit.refs
        todo += [n for n in unit.refs if n in unit_of]
    return keep, needed


def version_of(lib):
    return tomllib.loads(read_source(lib, f"{lib['path']}/pyproject.toml"))["project"]["version"]


def render(lib, target, script_text, shake=None):
    """The bundled block for `target`, markers included. `shake` overrides the target's `shake` setting."""
    name = lib["name"]
    block = marker_re(name).search(script_text)
    if not block:
        raise BundleError(f"{target['file']}: no markers. Add these two lines where the block goes:\n"
                          f"# >>> BUNDLED {name}\n# <<< END BUNDLED {name}")
    mods = resolve(lib, target["modules"])
    seen = {}
    for m in mods:
        for n in m.defined:
            if n in seen:
                raise BundleError(f"`{n}` is defined in both {seen[n]} and {m.where}: top-level names must be unique "
                                  "across the bundled modules")
            seen[n] = m.where
    script = ast.parse(script_text[:block.start()] + script_text[block.end():])
    own = set(top_level_names(script))
    clash = sorted(own & set(seen))
    if clash:
        raise BundleError(f"{target['file']} defines {', '.join(clash)} outside the bundled block, where "
                          f"{lib['name']} defines it too")
    if shake is None:
        shake = target.get("shake", True)
    keep, needed = reachable(mods, names_used(script)) if shake else (None, None)
    if shake:
        mods = [m for m in mods if m.always or any(u in keep for u in m.units)]
    imports = sorted({i for m in mods for i, bound in m.imports.items() if not shake or needed.intersection(bound)},
                     key=lambda i: (i.startswith("from "), i))
    parts = ["\n".join(imports) + "\n"] if imports else []
    parts += [f"# --- {m.where} ---\n\n{m.body(keep)}" for m in mods]
    content = "\n".join(parts)
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    src = (Path(lib["path"]) / "src" / name).as_posix()
    head = (f"# >>> BUNDLED {name} {version_of(lib)} [{', '.join(m.name for m in mods)}] sha256:{digest}\n"
            f"# GENERATED by tools/bundle.py from {src}. Do not edit it here: edit the library and run `just sync`.\n")
    return head + content + f"\n# <<< END BUNDLED {name}\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a script differs from its library; change nothing")
    ap.add_argument("--ref", help="bundle the library at this git revision instead of its pin (`worktree`: the files on disk)")
    ap.add_argument("--config", type=Path, default=CONFIG)
    args = ap.parse_args(argv)
    config = tomllib.loads(args.config.read_text())
    if args.ref:
        for lib in config["library"]:
            lib["ref"] = args.ref
    drifted = []
    try:
        for lib in config["library"]:
            for target in lib["target"]:
                path = ROOT / target["file"]
                text = path.read_text()
                new = render(lib, target, text)
                m = marker_re(lib["name"]).search(text)
                if m.group(0) == new:
                    continue
                drifted.append(target["file"])
                if not args.check:
                    path.write_text(text[:m.start()] + new + text[m.end():])
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        if drifted:
            print("These scripts differ from the library they bundle:", file=sys.stderr)
            for f in drifted:
                print("  " + f, file=sys.stderr)
            print("Run: just sync", file=sys.stderr)
            return 1
        print("ok: every script carries exactly the library modules it bundles")
    else:
        print("updated: " + ", ".join(drifted) if drifted else "already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
