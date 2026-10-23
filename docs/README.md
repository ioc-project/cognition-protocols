# Documentation

Sources of the [documentation site](https://ioc-project.github.io/cognition-protocols/),
built with MkDocs (configuration in [`mkdocs.yml`](../mkdocs.yml) at the repository root).

## Building locally

The toolchain is pinned in [`mkdocs/pyproject.toml`](mkdocs/pyproject.toml) and
[`mkdocs/uv.lock`](mkdocs/uv.lock). The only tool you need to install is
[`uv`](https://docs.astral.sh/uv/); it creates the environment on first use.

With [`just`](https://just.systems):

```bash
just build   # strict build into .build/site
just run     # live-reload server
```

With `uv` alone, from the repository root:

```bash
uv run --project docs/mkdocs mkdocs build --strict --site-dir .build/site
uv run --project docs/mkdocs mkdocs serve
```

## Changing a dependency

Edit `mkdocs/pyproject.toml`, run `uv lock --project docs/mkdocs`, and commit both files.
