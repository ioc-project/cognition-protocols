"""MkDocs hooks — run before the build."""
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import json
from pathlib import Path

GITHUB_API = "https://api.github.com"
REPO = "ioc-project/cognition-protocols"
SPEC_TAG_PREFIX = "concord-spec-v"
SPEC_ASSET_NAME = "CONCORD-SPEC.html"


def _fetch_latest_spec_html():
    """Return CONCORD-SPEC.html bytes from the latest concord-spec release, or None."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{GITHUB_API}/repos/{REPO}/releases"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read())
    except urllib.error.URLError as exc:
        print(f"hooks.py: GitHub API unavailable ({exc}), falling back to local generation")
        return None

    release = next(
        (r for r in releases if r.get("tag_name", "").startswith(SPEC_TAG_PREFIX) and not r.get("draft") and not r.get("prerelease")),
        None,
    )
    if release is None:
        print("hooks.py: no concord-spec release found, falling back to local generation")
        return None

    asset = next((a for a in release.get("assets", []) if a["name"] == SPEC_ASSET_NAME), None)
    if asset is None:
        print(f"hooks.py: {SPEC_ASSET_NAME} not found in release {release['tag_name']}, falling back to local generation")
        return None

    asset_url = asset["browser_download_url"]
    asset_req = urllib.request.Request(asset_url, headers=headers)
    try:
        with urllib.request.urlopen(asset_req, timeout=30) as resp:
            content = resp.read()
        print(f"hooks.py: fetched {SPEC_ASSET_NAME} from release {release['tag_name']}")
        return content
    except urllib.error.URLError as exc:
        print(f"hooks.py: failed to download asset ({exc}), falling back to local generation")
        return None


def _generate_spec_html_locally(output_path):
    """Generate CONCORD-SPEC.html from the spec source in protocols/concord/spec/."""
    script = Path("protocols/concord/spec/generate_respec.py")
    source = Path("protocols/concord/spec/CONCORD-SPEC.md")
    if not script.exists() or not source.exists():
        raise FileNotFoundError("protocols/concord/spec/ source files not found")
    with tempfile.TemporaryDirectory() as tmp:
        generated = Path(tmp) / output_path.name
        subprocess.run(
            [sys.executable, str(script), str(source), "-o", str(generated)],
            check=True,
        )
        return generated.read_bytes()


def on_pre_build(config, **kwargs):
    output = Path("docs/protocols/concord/spec/CONCORD-SPEC.html")
    output.parent.mkdir(parents=True, exist_ok=True)

    content = _fetch_latest_spec_html()
    if content is None:
        content = _generate_spec_html_locally(output)

    # `mkdocs serve` watches docs/: avoid rewrite if unchanged (prevents rebuild loop).
    if not output.exists() or output.read_bytes() != content:
        output.write_bytes(content)
