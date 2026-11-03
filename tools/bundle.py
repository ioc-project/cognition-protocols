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

tools/bundle.toml says which modules go into which script. A script marks where its block goes with two lines:

    # >>> BUNDLED <library>
    # <<< END BUNDLED <library>

A module is bundled as it is, minus its docstring, its imports (hoisted to the top of the block) and `__all__`. So it must:

    - import only the standard library, and the library's own modules as `from <library>.<module> import <names>`
    - keep its top-level names unique across the bundled modules and distinct from the script's own top-level names
"""
import argparse
import ast
import hashlib
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).resolve().parent / "bundle.toml"


class BundleError(Exception):
    pass


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


class Module:
    """One library module, read for bundling."""

    def __init__(self, lib, name):
        self.lib, self.name = lib, name
        self.path = ROOT / lib["path"] / "src" / lib["name"] / f"{name}.py"
        if not self.path.is_file():
            raise BundleError(f"{lib['name']}.{name}: no such module ({self.path.relative_to(ROOT)})")
        text = self.path.read_text()
        self.tree = ast.parse(text, filename=str(self.path))
        self.where = f"{lib['name']}.{name}"
        self.imports, self.deps, self.defined = [], {}, []
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
                self.defined += top_level_names(ast.Module([node], []))
        lines = text.split("\n")
        for node in drop:
            for n in range(node.lineno - 1, node.end_lineno):
                lines[n] = None
        self.body = "\n".join(line for line in lines if line is not None).strip("\n") + "\n"

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
        self.imports.append(ast.get_source_segment(text, node))


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


def version_of(lib):
    return tomllib.loads((ROOT / lib["path"] / "pyproject.toml").read_text())["project"]["version"]


def render(lib, target, script_text):
    """The bundled block for `target`, markers included."""
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
    own = set(top_level_names(ast.parse(script_text[:block.start()] + script_text[block.end():])))
    clash = sorted(own & set(seen))
    if clash:
        raise BundleError(f"{target['file']} defines {', '.join(clash)} outside the bundled block, where "
                          f"{lib['name']} defines it too")
    imports = sorted({i for m in mods for i in m.imports}, key=lambda i: (i.startswith("from "), i))
    parts = ["\n".join(imports) + "\n"] if imports else []
    parts += [f"# --- {m.where} ---\n\n{m.body}" for m in mods]
    content = "\n".join(parts)
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    src = (Path(lib["path"]) / "src" / name).as_posix()
    head = (f"# >>> BUNDLED {name} {version_of(lib)} [{', '.join(m.name for m in mods)}] sha256:{digest}\n"
            f"# GENERATED by tools/bundle.py from {src}. Do not edit it here: edit the library and run `just sync`.\n")
    return head + content + f"\n# <<< END BUNDLED {name}\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a script differs from its library; change nothing")
    ap.add_argument("--config", type=Path, default=CONFIG)
    args = ap.parse_args(argv)
    config = tomllib.loads(args.config.read_text())
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
