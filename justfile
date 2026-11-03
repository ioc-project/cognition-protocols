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

# Run the CONCORD script tests (pytest is fetched on the fly)
test-concord:
    uv run --no-project --with pytest python -m pytest protocols/concord/tests -q
