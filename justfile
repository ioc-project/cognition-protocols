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
