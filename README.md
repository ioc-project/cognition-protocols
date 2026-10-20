# ioc-cognition-protocols

An umbrella repo for all IoC cognition protocols.

## AI Catalog

This repo publishes an [`ai-catalog.json`](https://ioc-project.github.io/ioc-cognition-protocols/ai-catalog.json) at the docs site root (`application/ai-catalog+json`). It lists released skills so they are discoverable via the [`ai-catalog` CLI](https://github.com/agntcy/ai-catalog).

### Register the catalog

```bash
ai-catalog catalog add ioc-protocols https://ioc-project.github.io/ioc-cognition-protocols/ai-catalog.json
```

### Search for skills

```bash
ai-catalog search concord
ai-catalog search accord
```

### Install a skill

Skills are distributed as zip archives containing a `SKILL.md` and supporting scripts. Pull and unzip into a temporary directory, then move the skill folder into `~/.claude/skills/`:

```bash
ai-catalog pull <identifier> -o /tmp/skill-download/
unzip /tmp/skill-download/<skill-name>.zip -d /tmp/skill-download/
mv /tmp/skill-download/<skill-name> ~/.claude/skills/
```

### Adding a new release as a catalog entry

When a skill is released (via release-please), add an entry to `docs/ai-catalog.json` under `entries`. Each entry references the zip asset uploaded to the GitHub release:

```json
{
  "identifier": "urn:ioc-project:<skill-name>",
  "displayName": "<Human-readable name>",
  "description": "<One-line description>",
  "mediaType": "application/agent-skills+md",
  "url": "https://github.com/ioc-project/ioc-cognition-protocols/releases/download/<skill-name>-v<version>/<skill-name>.zip",
  "tags": ["multi-agent", "protocol"]
}
```

Replace `<skill-name>` with the package name from `.github/release-config.json` (e.g. `concord-initiator`) and `<version>` with the released version (e.g. `0.1.0`). The tag format matches what the `publish-release-assets` job in `.github/workflows/release.yaml` creates: `<name>-v<version>`.

The updated catalog is published automatically when the docs workflow runs on the next push to `main`.
