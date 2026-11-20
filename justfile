set windows-shell := ["bash", "-cu"]

mkdocs := "uv run --project docs/mkdocs mkdocs"

# List available recipes
default:
    @just --list

# Build the documentation site into .build/site
build:
    {{mkdocs}} build --strict --site-dir .build/site

# Serve the documentation site with live reload
run:
    {{mkdocs}} serve

# `just sync --ref worktree` bundles the files on disk instead of the pinned revision (it does not apply to ioc_concord: its source is no longer on main)
# Copy the library, at the revision it is pinned to in tools/bundle.toml, into the scripts that bundle it
sync *args:
    python3 tools/bundle.py {{args}}

# Fail if a script differs from the library revision it is pinned to
check-sync *args:
    python3 tools/bundle.py --check {{args}}

# Test the bundler itself
test-bundler:
    uv run --no-project --with pytest python -m pytest tools -q -p no:cacheprovider

rust := "packages/ioc-concord-rs"
python := "packages/ioc-concord-python"

# ioc-concord, Rust: formatting, then clippy for every combination of the features, and the documentation, all without warnings
rust-lint:
    cd {{rust}} && cargo fmt --check
    cd {{rust}} && cargo hack clippy --locked --feature-powerset --all-targets -- -D warnings
    cd {{rust}} && RUSTDOCFLAGS="-D warnings" cargo doc --locked --no-deps --all-features

# ioc-concord, Rust: the tests, for every combination of the features
rust-test:
    cd {{rust}} && cargo hack test --locked --feature-powerset

# ioc-concord, Rust: the crate builds and passes its tests on the oldest Rust it declares (`rust-version` in its Cargo.toml)
rust-msrv:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{rust}}
    msrv=$(sed -n 's/^rust-version = "\(.*\)"/\1/p' Cargo.toml)
    rustup toolchain install "$msrv" --profile minimal
    cargo "+$msrv" hack test --locked --feature-powerset

# ioc-concord, Python: build the wheel of this platform into dist (arguments go to maturin: `--zig --compatibility manylinux_2_28`, ...)
wheel *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{python}}
    rm -rf dist
    zig=false; case " {{args}} " in *" --zig "*) zig=true ;; esac
    # zig links the library without its symbol table, and uniffi-bindgen reads the metadata of the library from that table: keep it (the
    # other linkers keep it already). The other builds strip the library themselves.
    if $zig; then export CARGO_PROFILE_RELEASE_STRIP=none; else export CARGO_PROFILE_RELEASE_STRIP=symbols; fi
    uv build --wheel --out-dir dist -C build-args="--locked {{args}}"
    # the symbol table kept for the bindings is more than half of the library, and of no use once they are generated
    if $zig; then
        uvx --from wheel wheel unpack --dest dist/unpacked dist/*.whl
        find dist/unpacked -name 'libioc_concord.so' -exec strip --strip-unneeded {} +
        rm dist/*.whl
        uvx --from wheel wheel pack --dest-dir dist dist/unpacked/*
        rm -r dist/unpacked
    fi
    ls -l dist

# ioc-concord, Python: install the wheel in a new environment and run the tests of the package
wheel-test:
    cd {{python}} && uv run --no-project --isolated --with pytest --with dist/*.whl python -I -m pytest -q -p no:cacheprovider tests

# Test the skill scripts as they are checked in
test-scripts:
    uv run --no-project --with pytest python -m pytest protocols/concord/tests -q -p no:cacheprovider

# Run all the CONCORD tests (not the sync check: that is `just check-sync`)
test-concord: test-bundler rust-test wheel wheel-test test-scripts
