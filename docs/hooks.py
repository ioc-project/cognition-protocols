"""MkDocs hooks — run before the build."""
import subprocess
import sys
import tempfile
from pathlib import Path


def on_pre_build(config, **kwargs):
    spec_dir = Path("docs/protocols/concord/spec")
    script = spec_dir / "generate_respec.py"
    source = spec_dir / "CONCORD-SPEC.md"
    output = spec_dir / "CONCORD-SPEC.html"
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
