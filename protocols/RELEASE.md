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
