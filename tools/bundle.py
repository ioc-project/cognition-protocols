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

A library can build on another one that goes into the same script (`uses = ["ioc_concord"]` in its `[[library]]`): its modules may then
import that library's names as `from ioc_concord.<module> import <names>`. Those imports are not copied: the names come from the other
library's block in the script. A script marks a block for each library, and the block of the library that is used comes first in the
script (the order that matters is the one in the script, not in the config). The blocks are written until they stop changing, since
what one library's block keeps depends on what the other's code uses. All of this is checked before anything is written: `uses` is a list
of other libraries of the config with no cycle, a script that bundles a library bundles the ones it uses, and their blocks are in order.
A library takes the version of its block from its pyproject.toml; one that is only source says `source_only = true` in its `[[library]]`,
and its block carries no version.

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


_READ = None       # while main() runs: what read_source has read, so that a file is read once however many times a script is rendered


def read_source(lib, rel):
    """The text of `rel`, a path from the repository root, in the revision that `lib` is pinned to (`ref`), or on disk when it is not
    pinned or its ref is `worktree`. Raises BundleError when the revision or the file is not there. Within one run of `main` a file is
    read once (a revision is a `git show` each time otherwise)."""
    if _READ is None:
        return _read_source(lib, rel)
    key = (str(ROOT), lib.get("ref") or WORKTREE, rel)
    if key not in _READ:
        try:
            _READ[key] = _read_source(lib, rel)
        except MissingSource as exc:
            _READ[key] = exc
    if isinstance(_READ[key], Exception):
        raise _READ[key]
    return _READ[key]


def _read_source(lib, rel):
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
    """The block of library `name`, markers included. The name is followed by a blank or the end of the line, so that the markers of `a`
    are not those of `a-b`."""
    n = re.escape(name)
    return re.compile(rf"^# >>> BUNDLED {n}(?=[ \t]|$).*?^# <<< END BUNDLED {n}(?=[ \t]|$)[^\n]*\n", re.S | re.M)


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
        self.external = {}                                   # what it takes from the libraries it uses: {library: {module: [names]}}
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

    def _names_of(self, node, name):
        """The module and the names of `from <name>.<module> import <names>`, `name` being a library: any other way to import from a
        library is refused."""
        sub = (node.module or "")[len(name) + 1:]
        if not sub or "." in sub:
            raise BundleError(f"{self.where}: line {node.lineno}: import a module's names as `from {name}.<module> import <names>`")
        if any(a.name == "*" for a in node.names):
            raise BundleError(f"{self.where}: line {node.lineno}: no `import *`")
        return sub, [a.name for a in node.names]

    def _import(self, node, text):
        lib = self.lib["name"]
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise BundleError(f"{self.where}: relative import (line {node.lineno}): write `from {lib}.<module> import ...`")
            mod = node.module or ""
            if mod == "__future__":
                raise BundleError(f"{self.where}: `from __future__` (line {node.lineno}) cannot be bundled")
            if mod == lib or mod.startswith(lib + "."):
                sub, names = self._names_of(node, lib)
                self.deps.setdefault(sub, []).extend(names)
                return
            used = next((u for u in self.lib.get("uses", []) if mod == u or mod.startswith(u + ".")), None)
            if used:
                sub, names = self._names_of(node, used)
                self.external.setdefault(used, {}).setdefault(sub, []).extend(names)
                return                                         # the names are in the block of `used`, in the same script (see check_used_imports)
            roots = [mod.split(".")[0]]
        else:
            roots = [a.name.split(".")[0] for a in node.names]
        for root in roots:
            if root in self.lib.get("uses", []):
                raise BundleError(f"{self.where}: line {node.lineno}: import the names of {root} as "
                                  f"`from {root}.<module> import <names>`")
            if root == lib:
                raise BundleError(f"{self.where}: line {node.lineno}: import the library's names as "
                                  f"`from {lib}.<module> import <names>`")
            if root not in sys.stdlib_module_names:
                raise BundleError(f"{self.where}: line {node.lineno}: `{root}` is not in the standard library; a bundled "
                                  "module is stdlib-only (a library it builds on is named in `uses`)")
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
    """The version in the library's pyproject.toml (at its revision). A library that is only source, and says so with `source_only = true`
    in its `[[library]]`, has none: any other library without a version is an error, so that a version cannot be lost unnoticed."""
    if lib.get("source_only"):
        return None
    rel = f"{lib['path']}/pyproject.toml"
    try:
        data = tomllib.loads(read_source(lib, rel))
    except MissingSource:
        raise BundleError(f"{lib['name']}: no pyproject.toml ({rel}) to take a version from: add one, or write `source_only = true` for "
                          "a library that is only source")
    except tomllib.TOMLDecodeError as exc:
        raise BundleError(f"{lib['name']}: {rel} is not valid TOML ({exc})")
    try:
        return data["project"]["version"]
    except KeyError:
        raise BundleError(f"{lib['name']}: {rel} has no [project].version")


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
    version = version_of(lib)
    head = (f"# >>> BUNDLED {name}{' ' + version if version else ''} [{', '.join(m.name for m in mods)}] sha256:{digest}\n"
            f"# GENERATED by tools/bundle.py from {src}. Do not edit it here: edit the library and run `just sync`.\n")
    return head + content + f"\n# <<< END BUNDLED {name}\n"


def check_config(config):
    """The shape of the config, before anything is read or written: every library has a name (each its own), a path and at least one
    target with a file and a list of modules. A fault is a BundleError, never a traceback."""
    libs = config.get("library")
    if not isinstance(libs, list) or not libs:
        raise BundleError("the config has no [[library]]")
    seen = set()
    for lib in libs:
        name = lib.get("name")
        if not isinstance(name, str) or not name:
            raise BundleError("a [[library]] has no `name`")
        if name in seen:
            raise BundleError(f"two [[library]] are called {name}")
        seen.add(name)
        if not isinstance(lib.get("path"), str):
            raise BundleError(f"{name}: no `path` (the directory that holds src/{name}/)")
        targets = lib.get("target")
        if not isinstance(targets, list) or not targets:
            raise BundleError(f"{name}: no [[library.target]]: say which script gets which modules")
        for target in targets:
            if not isinstance(target.get("file"), str):
                raise BundleError(f"{name}: a [[library.target]] has no `file`")
            modules = target.get("modules")
            if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
                raise BundleError(f"{name}: the target {target['file']} has no `modules`, a list of module names")


def check_uses(config):
    """The `uses` of every library, before anything is read or written: a list of the names of other libraries of the config, and no
    library that uses itself or, through others, uses a library that uses it."""
    names = [lib["name"] for lib in config["library"]]
    uses = {}
    for lib in config["library"]:
        used = lib.get("uses", [])
        if not isinstance(used, list) or not all(isinstance(u, str) for u in used):
            raise BundleError(f"{lib['name']}: `uses` must be a list of library names, like [\"ioc_concord\"]")
        for u in used:
            if u == lib["name"]:
                raise BundleError(f"{lib['name']} lists itself in `uses`")
            if u not in names:
                raise BundleError(f"{lib['name']} uses {u}, which is not a library of this config: add it, or take it out of `uses`")
        uses[lib["name"]] = used

    def visit(name, chain):
        if name in chain:
            raise BundleError("libraries that use each other: " + " -> ".join(chain[chain.index(name):] + [name]))
        for u in uses[name]:
            visit(u, chain + [name])

    for name in uses:
        visit(name, [])


def check_blocks(file, text, targets):
    """What a script needs for a library that builds on another: the library it uses is bundled into the same script, and its block
    comes first (the names of the other are in it). A script that lacks a block is told so by `render`, which needs it."""
    here = {lib["name"] for lib, _ in targets}
    for lib, _ in targets:
        for u in lib.get("uses", []):
            if u not in here:
                raise BundleError(f"{file}: {lib['name']} is bundled here and uses {u}, which is not: add a target for {u} in this file")
            mine, theirs = marker_re(lib["name"]).search(text), marker_re(u).search(text)
            if mine and theirs and theirs.start() > mine.start():
                raise BundleError(f"{file}: the block of {u} must come before the block of {lib['name']}, which uses it: swap them")


def check_used_imports(file, targets):
    """What a library that builds on another takes from it must be in the script: the module is among the `modules` of the used library
    for this script, and defines the name. (`resolve` does the same for the modules of a library's own.) `check_blocks` has seen to it
    that the used library is bundled into the script."""
    here = {lib["name"]: (lib, target) for lib, target in targets}
    for lib, target in targets:
        for module in target["modules"]:
            for used, wanted in Module(lib, module).external.items():
                used_lib, used_target = here[used]
                for sub, names in wanted.items():
                    if sub not in used_target["modules"]:
                        raise BundleError(f"{file}: {lib['name']}.{module} imports {used}.{sub}, which is not in `modules` of {used} for "
                                          f"this script: add \"{sub}\"")
                    missing = sorted(set(names) - set(Module(used_lib, sub).defined))
                    if missing:
                        raise BundleError(f"{file}: {lib['name']}.{module} imports {', '.join(missing)} from {used}.{sub}, which does "
                                          "not define it")


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
    global _READ
    _READ = {}
    try:
        check_config(config)
        check_uses(config)
        files = {}                                             # file -> [(lib, target)] in the order of the config
        for lib in config["library"]:
            for target in lib["target"]:
                files.setdefault(target["file"], []).append((lib, target))
        for file, targets in files.items():                    # all of it is checked before the first script is written
            try:
                text = (ROOT / file).read_text()
            except FileNotFoundError:
                raise BundleError(f"{file}: no such script ({targets[0][0]['name']} is to be bundled into it)")
            check_blocks(file, text, targets)
            check_used_imports(file, targets)
        for file, targets in files.items():
            path = ROOT / file
            original = text = path.read_text()
            # A block depends on what the other blocks of the script use. The library that is used never refers to the one that uses it
            # (see check_uses), so the second round can change the block of the first at most, and the third finds nothing to do: five
            # rounds are a guard against a library that does not settle, and not a number to reach.
            for _ in range(5):
                new = text
                for lib, target in targets:
                    block = render(lib, target, new)           # raises when the script has no markers for this library
                    m = marker_re(lib["name"]).search(new)
                    new = new[:m.start()] + block + new[m.end():]
                if new == text:
                    break
                text = new
            else:
                raise BundleError(f"{file}: the blocks do not settle after 5 rounds (usually a library whose code refers to names of the block "
                                  "of another one)")
            if text != original:
                drifted.append(file)
                if not args.check:
                    path.write_text(text)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        _READ = None
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
