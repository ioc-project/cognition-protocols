#!/usr/bin/env python3
"""Generate a standalone ReSpec HTML document from a markdown spec file.

Embeds the markdown in a <script type="text/markdown"> element so the
browser's HTML parser never sees XML tags inside it as DOM elements.
A small inline script converts the content to a Blob URL that ReSpec's
data-include fetches — no web server required.
"""

import argparse
import sys
from pathlib import Path

RESPEC_URL = "https://www.w3.org/Tools/respec/respec-w3c"

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <script src="{respec_url}" class="remove"></script>
  <script class="remove">
    var respecConfig = {{
      specStatus:  "unofficial",
      shortName:   "concord",
      title:       "{title}",
      subtitle:    "Version {version}",
      github:      "https://github.com/cisco-eti/ioc-core-l9-skills",
      editors: [
        {{ name: "Outshift" }}
      ],
      xref: false,
      lint: {{ "no-unused-dfns": false }},
    }};
  </script>
</head>
<body>
<!--
  The markdown is stored in a <script type="text/markdown"> element.
  The browser does not HTML-parse its content, so XML tags inside code
  blocks and inline code are never turned into DOM elements.
  The inline script below runs synchronously during parsing (before
  DOMContentLoaded) and wires a Blob URL to a data-include section,
  which ReSpec fetches and converts to HTML.
-->
<script type="text/markdown" id="spec-source">
{markdown}
</script>
<section id="spec-body"></section>
<script class="remove">
  (function () {{
    var md  = document.getElementById("spec-source").textContent;
    var url = URL.createObjectURL(new Blob([md], {{type: "text/markdown"}}));
    var sec = document.getElementById("spec-body");
    sec.setAttribute("data-include", url);
    sec.setAttribute("data-include-format", "markdown");
    document.getElementById("spec-source").remove();
  }})();
</script>
</body>
</html>
"""


def parse_meta(text):
    """Return (title, version) from the first H1 and Version line."""
    import re
    title, version = "Specification", "unknown"
    for line in text.splitlines():
        if line.startswith("# ") and title == "Specification":
            title = line[2:].strip()
        m = re.match(r"^Version\s+(\S+)\s*$", line)
        if m:
            version = m.group(1)
            break
    return title, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        default="CONCORD-SPEC.md",
        help="Input markdown file (default: CONCORD-SPEC.md)",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output HTML file (default: <input stem>.html)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"error: {input_path} not found")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".html")

    text = input_path.read_text(encoding="utf-8")
    title, version = parse_meta(text)

    # Markdown must not contain </script> — safe for spec content.
    if "</script>" in text.lower():
        sys.exit("error: markdown contains </script>, which would break the embedding")

    output_path.write_text(
        HTML_TEMPLATE.format(
            title=title,
            version=version,
            respec_url=RESPEC_URL,
            markdown=text,
        ),
        encoding="utf-8",
    )
    print(f"Written: {output_path}")


if __name__ == "__main__":
    main()
