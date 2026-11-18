"""Pictures in the manual (ADR 0083 decision 2, amended 2026-11-16): screenshots of the
real GUI, drawn by `scripts/build_help_images.py` into `help/images/`.

- A page names a picture as Markdown, `![What it shows](image:screens/advisor.png)`.
- A menu location a page names in words ("Model → Studies → Load combinations…") gets
  its picture without being asked: `with_pictures` puts it after the paragraph (or list,
  or table) that first names it on that page. Which locations have pictures is
  `images/index.json`, written by the same script from the menu bar itself, so only
  real menu paths match, and a test fails when a page names one that isn't there.

Nothing here needs Qt: the Help window, the static site and the PDF all read pictures
through it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

IMAGES = Path(__file__).parent / "images"
INDEX = IMAGES / "index.json"
IMAGE = re.compile(r"!\[([^\]]*)\]\(image:([^)\s]+)\)")
ARROW = " → "
# The menu bar's titles, in its order: a chain of arrows starting with one names a menu.
TOP = ("Project", "Edit", "Geometry", "Materials", "Mesh", "Model", "Results", "Window", "Help")
FENCE = re.compile(r"^\s*(```|~~~)")


@lru_cache(maxsize=1)
def index() -> dict:
    """{"menus": {menu path: picture}}, as the picture script last wrote it."""
    if not INDEX.is_file():
        return {"menus": {}}
    return json.loads(INDEX.read_text(encoding="utf-8"))


def path(name: str) -> Path:
    """The file of a picture named as `image:` names it (`menu/model-studies.png`)."""
    return IMAGES / name


def menu_file(menu_path: str) -> str:
    """A menu path's picture name: `menu/model-studies-load-combinations.png`."""
    words = re.sub(r"[^a-z0-9]+", "-", menu_path.lower().replace("…", "")).strip("-")
    return f"menu/{words}.png"


def _blocks(text: str) -> list[tuple[int, str]]:
    """(last line number, text) for each paragraph, list or table outside fenced code,
    its lines joined by spaces, since a menu path may wrap from one line to the next."""
    out, fenced, lines, last = [], False, [], -1
    for i, line in enumerate(text.splitlines()):
        if FENCE.match(line):
            fenced = not fenced
            line = ""
        if fenced or not line.strip() or line.startswith("#"):
            if lines:
                out.append((last, " ".join(lines)))
                lines = []
            if line.startswith("#") and not fenced:
                out.append((i, line))
            continue
        lines.append(line.strip())
        last = i
    if lines:
        out.append((last, " ".join(lines)))
    return out


def _matcher(paths) -> re.Pattern | None:
    names = sorted(paths, key=len, reverse=True)  # the longest path at a place wins
    return re.compile("|".join(re.escape(n) for n in names)) if names else None


def menu_mentions(text: str, paths=None) -> list[tuple[int, str]]:
    """(the line ending its block, menu path) for each menu path the text names, in
    order; `paths` the real ones (the index's when not given)."""
    found = _matcher(index()["menus"] if paths is None else paths)
    if found is None:
        return []
    return [(i, m.group(0)) for i, block in _blocks(text) for m in found.finditer(block)]


def unknown_menus(text: str, paths) -> list[str]:
    """Arrow chains starting with a menu bar title that are no real menu path, or that
    go on past one ("Model → Studies → Nothing"): menu locations the words get wrong.
    Going on past an item that opens a dialog ("Project → Settings… → Units") is a
    place in that dialog, and a chain read from its middle ("Settings → Results → …")
    isn't the menu bar's."""
    found = _matcher(paths)
    starts = re.compile(r"(?<!→ )\b(?:" + "|".join(TOP) + r")" + ARROW)
    bad = []
    for _, block in _blocks(text):
        for m in starts.finditer(block):
            hit = found.match(block, m.start()) if found else None
            if hit is None or (block.startswith(ARROW, hit.end())
                               and not hit.group(0).endswith("…")):  # fmt: skip
                bad.append(block[m.start() : m.start() + 60].strip())
    return bad


def link(menu_path: str) -> str:
    """The `puzzle:` link that opens a menu item from the Help window (ADR-E-1)."""
    return "puzzle:" + menu_file(menu_path)[: -len(".png")]


def with_pictures(text: str, links: bool = False) -> str:
    """The page's Markdown with a picture after the block that first names each menu
    location (a paragraph, a list, a table), never inside fenced code; with `links` (the
    Help window, ADR 0083 decision 2) a link under it that opens the item. The site and
    the PDF have no tools to open, so they leave the links out."""
    menus = index()["menus"]
    after: dict[int, list[str]] = {}
    seen: set[str] = set()
    for i, menu in menu_mentions(text):
        if menu in seen or menu not in menus:
            continue
        seen.add(menu)
        after.setdefault(i, []).append(menu)
    if not after:
        return text
    out = []
    for i, line in enumerate(text.splitlines()):
        out.append(line)
        for menu in after.get(i, []):
            out += ["", f"![{menu}](image:{menus[menu]})", ""]
            if links:
                out += [f"[Open {menu}]({link(menu)})", ""]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def references(text: str) -> list[str]:
    """The pictures a page's Markdown names (`image:` targets), in order."""
    return [m.group(2) for m in IMAGE.finditer(text)]
