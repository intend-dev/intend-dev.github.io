# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdf>=5"]
# ///
"""Check the committed CV PDF: page count, the absolute /#work link, no local URLs.

Reads the PDF with pypdf instead of a hand-written parser, so an object-stream PDF is
read normally rather than tripping a bespoke decoder.
"""

import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path.cwd()
target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "public" / "Francisco_Mateus_CV.pdf"

config = (ROOT / "astro.config.mjs").read_text(encoding="utf-8")
site = re.search(r"site:\s*'([^']+)'", config)
base = re.search(r"base:\s*'([^']*)'", config)
if not site:
    print("PDF check failed: astro.config.mjs has no site.")
    sys.exit(1)

origin = site.group(1).rstrip("/") + (base.group(1).rstrip("/") if base else "")
problems: list[str] = []

if not target.is_file():
    print(f"PDF is missing: {target}. Run npm run cv.")
    sys.exit(1)

try:
    reader = PdfReader(str(target))
except Exception as error:  # noqa: BLE001 - reported as a failure
    print(f"PDF check failed: {error}")
    sys.exit(1)

if len(reader.pages) != 2:
    problems.append(f"expected 2 pages, found {len(reader.pages)}")

uris = [annotation.get("/A", {}).get("/URI", "") for page in reader.pages for annotation in page.get("/Annots", [])]
uris = [uri for uri in uris if uri]

work_links = [uri for uri in uris if "#work" in uri]
if len(work_links) != 1:
    problems.append(f"expected one /#work link annotation, found {len(work_links)}")
elif work_links[0] != f"{origin}/#work":
    problems.append(f"the /#work annotation is {work_links[0]}, not {origin}/#work")

local = [uri for uri in uris if re.search(r"localhost|127\.0\.0\.1", uri)]
if local:
    problems.append(f"annotations point at the print server: {', '.join(local)}")

if problems:
    print(f"PDF check failed with {len(problems)} problem(s):")
    for problem in problems:
        print(f"- {problem}")
    sys.exit(1)

print(f"PDF check OK: 2 pages, {len(uris)} links, {origin}/#work is absolute.")
