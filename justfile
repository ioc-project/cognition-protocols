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

rust := "packages/ioc-concord-rs"
python := "packages/ioc-concord-python"

# Rust, every crate of the workspace: formatting, then clippy for every combination of the features, and the documentation, all without warnings
rust-lint:
    cargo fmt --all --check
    cargo hack clippy --workspace --locked --feature-powerset --all-targets -- -D warnings
    RUSTDOCFLAGS="-D warnings" cargo doc --workspace --locked --no-deps --all-features

# Rust, every crate of the workspace: the tests, for every combination of the features
rust-test:
    cargo hack test --workspace --locked --feature-powerset

# Rust: the workspace builds and passes its tests on the oldest Rust ioc-concord declares (`rust-version` in its Cargo.toml)
rust-msrv:
    #!/usr/bin/env bash
    set -euo pipefail
    msrv=$(sed -n 's/^rust-version = "\(.*\)"/\1/p' {{rust}}/Cargo.toml)
    rustup toolchain install "$msrv" --profile minimal
    cargo "+$msrv" hack test --workspace --locked --feature-powerset

# ioc-concord, Python: build the wheel of this platform into dist; the arguments go to maturin (`--zig --compatibility manylinux_2_28`, ...)
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
    @ls {{python}}/dist/*.whl >/dev/null 2>&1 || { echo "no wheel in {{python}}/dist: run 'just wheel' first"; exit 1; }
    cd {{python}} && uv run --no-project --isolated --with pytest --with dist/*.whl python -I -m pytest -q -p no:cacheprovider tests

# ioc-concord, Python: in a new environment with nothing but the wheel, check that the library and the command work
wheel-smoke:
    cd {{python}} && uv run --no-project --isolated --with dist/*.whl python -I tests/smoke.py

# ioc-concord, Python: the command of the wheel, in a new environment, against the transcripts and tests of the skill scripts (protocols/concord/tests)
test-transcripts:
    uv run --no-project --isolated --with pytest --with {{python}}/dist/*.whl python -I -m pytest protocols/concord/tests -q -p no:cacheprovider

# Test the ioc-concord library: the Rust crate, the Python package built from it, and its command against the transcripts of the scripts
test-concord: rust-test wheel wheel-test wheel-smoke test-transcripts

# Run the ACCORD tests: the initiator script, against the recorded transcripts
test-accord:
    uv run --no-project --with pytest python -m pytest protocols/accord/tests -q -p no:cacheprovider
