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

# Copy the library modules into the scripts that bundle them (run after editing a library)
sync:
    python3 tools/bundle.py

# Fail if a script differs from the library it bundles
check-sync:
    python3 tools/bundle.py --check

# Run the CONCORD tests: the bundler, the ioc-concord library (also as bundled in each script) and the scripts
test-concord:
    uv run --no-project --with pytest python -m pytest tools protocols/concord/tests -q -p no:cacheprovider
    uv run --directory packages/ioc-concord pytest -q -p no:cacheprovider

# Run only the ioc-concord library tests (as imported and as bundled), skipping the check that the scripts are in sync
test-lib:
    uv run --directory packages/ioc-concord pytest -q -p no:cacheprovider --deselect tests/test_bundled_scripts.py
