"""MkDocs hooks — run before the build."""
import subprocess
import sys
import tempfile
from pathlib import Path


def on_pre_build(config, **kwargs):
    script = Path("protocols/concord/spec/generate_respec.py")
    source = Path("protocols/concord/spec/CONCORD-SPEC.md")
    output = Path("docs/protocols/concord/spec/CONCORD-SPEC.html")

    # Skip generation if the committed HTML is already up to date with the source.
    # The release workflow commits the rendered HTML to this path on each spec release;
    # local dev and PR builds generate it here from source as a fallback.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        generated = Path(tmp) / output.name
        subprocess.run(
            [sys.executable, str(script), str(source), "-o", str(generated)],
            check=True,
        )
        # `mkdocs serve` watches docs/: rewriting an unchanged file would
        # trigger a rebuild, which regenerates it, and so on forever.
        if not output.exists() or output.read_bytes() != generated.read_bytes():
            output.write_bytes(generated.read_bytes())
