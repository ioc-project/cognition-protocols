"""MkDocs hooks — run before the build."""
import subprocess
import sys
from pathlib import Path


def on_pre_build(config, **kwargs):
    spec_dir = Path("docs/cognition-protocols/concord/spec")
    script = spec_dir / "generate_respec.py"
    source = spec_dir / "CONCORD-SPEC.md"
    output = spec_dir / "CONCORD-SPEC.html"
    subprocess.run(
        [sys.executable, str(script), str(source), "-o", str(output)],
        check=True,
    )
