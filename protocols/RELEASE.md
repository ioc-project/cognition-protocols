# Release process

How the protocol skills under `protocols/` are versioned, released and validated.
Back to the [main README](../README.md).

## Releases

Releases are managed with [release-please](https://github.com/googleapis/release-please-action/),
which versions each skill independently based on
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) affecting
its folder.

Two files matter:

- [release-config.json](../.github/release-config.json) — lists the packages (skills)
  release-please tracks
- [release-manifest.json](../.github/release-manifest.json) — the current released
  version of each package

release-please opens a PR per skill with the version bump and changelog; merging
it tags and publishes a GitHub release for that skill. A second job then zips the
released skill folder(s) and uploads the zip as an asset on the corresponding
GitHub release.

### Adding a new skill

1. Add the folder under `protocols/<protocol>/skills/` with its `SKILL.md`. In its frontmatter, include a `metadata`
   block with the skill's version, marked with the `x-release-please-version`
   annotation so release-please can bump it in place:

   ```yaml
   ---
   name: my-skill
   description: ...
   metadata:
     author: Cisco Systems, Inc.
     copyright: "Copyright 2026 Cisco Systems, Inc. and its affiliates"
     version: "0.1.0" # x-release-please-version
   ---
   ```

2. Add an entry to `packages` in `.github/release-config.json`, with an
   `extra-files` entry pointing at `SKILL.md` so release-please updates that
   `version` field alongside the changelog and tag:

   ```json
   "protocols/<protocol>/skills/my-skill": {
     "package-name": "my-skill",
     "release-type": "simple",
     "extra-files": [{ "type": "generic", "path": "SKILL.md" }]
   }
   ```

3. Add an entry (initial version, e.g. `"0.1.0"`) to `.github/release-manifest.json`,
   keyed by the same path.
4. Use Conventional Commits for changes under that folder (e.g. `feat(my-skill): ...`).

No workflow changes needed for either release or CI validation — `release.yaml`
reads `release-manifest.json` at release time (by skill path) to find each package's tag, and
`validate-skills.yaml` discovers skill folders by finding every `SKILL.md` at
`protocols/*/skills/*`. Both are the same for any number of skills.

## The Rust crates and the Python package

The Rust crates under `packages/*-rs` are released with [release-plz](https://release-plz.dev), not release-please
([release-plz.toml](../release-plz.toml), [release.yaml](../.github/workflows/release.yaml)). On every push to `main` it opens or
updates a release PR for each crate that changed: the new version (from the Conventional Commits that touched the crate's folder), its
changelog and `Cargo.lock`. Merging that PR publishes the crate to [crates.io](https://crates.io), tags `<crate>-v<version>` and creates the
GitHub release. The publication uses crates.io [trusted publishing](https://crates.io/docs/trusted-publishing): the workflow gets a token
from GitHub's OIDC, so there is no secret to keep. release-plz does that itself, with `id-token: write` on its job.

The Python package `ioc-concord` has the version of the crate `ioc-concord`, and the tag `ioc-concord-v<version>` is what
[publish-packages.yaml](../.github/workflows/publish-packages.yaml) publishes it on. So the Python package is released when the crate is, and
not otherwise: a change that touches only `packages/ioc-concord-python` opens no release PR, and ships with the next release of the crate.
A change of the root `Cargo.lock` does count as a change of the crate (the lock file is part of the package of a crate that has a binary).

### First release of a crate

A trusted publisher can only be set up for a crate that exists on crates.io, so the first release of a crate is made by hand, once, by an
owner, **before** its release PR is merged (release-plz opens a release PR for the first version too, and its release fails until the
trusted publisher is there):

1. `cargo publish -p ioc-concord` from the commit that sets the version (0.3.0 for `ioc-concord`: 0.1.0 and 0.2.0 are releases of the pure-Python library on PyPI), with a crates.io token.
2. Tag that commit `ioc-concord-v0.3.0` (this publishes the Python package too), and `gh release create ioc-concord-v0.3.0`: a tag made by
   hand has no GitHub release.
3. On crates.io, add the repository, the workflow `release.yaml` and no environment as a trusted publisher of the crate.

Then close the release PR of that version that release-plz opened, or let it refresh. From then on the release PRs do it all. A new crate
is released the same way.

## CI validation

[validate-skills.yaml](../.github/workflows/validate-skills.yaml) validates each
skill's `SKILL.md` against the [Agent Skills specification](https://agentskills.io/specification#validation)
using the [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref)
reference library (PyPI package `skills-ref`, CLI command `agentskills validate`).

It discovers skills by finding every `SKILL.md` at `protocols/*/skills/*`, then only validates
the ones that actually changed: the internal
[detect-changes](../.github/actions/detect-changes/action.yaml) composite action
diffs the PR (or push) against its base and outputs which of those directories
changed, and a matrix job runs `agentskills validate` on each of those only.
