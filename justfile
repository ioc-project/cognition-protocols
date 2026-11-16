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

# `just sync --ref worktree` bundles the files on disk instead of the pinned revision, to try a library that is not committed yet
# Copy the library, at the revision it is pinned to in tools/bundle.toml, into the scripts that bundle it
sync *args:
    python3 tools/bundle.py {{args}}

# Fail if a script differs from the library revision it is pinned to
check-sync *args:
    python3 tools/bundle.py --check {{args}}

# Test the bundler itself
test-bundler:
    uv run --no-project --with pytest python -m pytest tools -q -p no:cacheprovider

# Test the ioc-concord library as it is on disk, as imported and as bundled
test-lib:
    uv run --directory packages/ioc-concord pytest -q -p no:cacheprovider

# Test the skill scripts as they are checked in
test-scripts:
    uv run --no-project --with pytest python -m pytest protocols/concord/tests -q -p no:cacheprovider

# Run all the CONCORD tests (not the sync check: that is `just check-sync`)
test-concord: test-bundler test-lib test-scripts
