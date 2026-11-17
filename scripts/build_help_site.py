"""Build the manual as a static site and a PDF (ADR 0083 step 5).

    pixi run -e dev python scripts/build_help_site.py OUT [--pdf FILE] [--no-site]

OUT gets index.html, a page per help page, style.css and search.js; --pdf writes the
whole manual as one PDF, its links and bookmarks jumping to pages and headings. Links
that resolve nowhere are listed and fail the build.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="the site's folder")
    parser.add_argument("--pdf", type=Path, help="also write the manual as this PDF")
    parser.add_argument("--no-site", action="store_true", help="only the PDF")
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from puzzle.help import site

    status = 0
    if not args.no_site:
        done = site.build_site(args.out)
        print(f"{done['pages']} pages in {args.out}")
        for link in done["missing"]:
            print(f"  no page for {link}")
        status = 1 if done["missing"] else 0
    if args.pdf:
        done = site.build_pdf(args.pdf)
        print(f"{done['pages']} pages in {args.pdf}")
        for link in done["missing"]:
            print(f"  no page for {link}")
        status = 1 if done["missing"] else status
    return status


if __name__ == "__main__":
    sys.exit(main())
