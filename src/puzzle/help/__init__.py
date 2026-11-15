"""Puzzle's help (ADR 0083): one set of Markdown pages read by the Help window, F1, the
agent (MCP `help`, the knowledge pack), `puzzle help` and the static site.

The pages live in the package, so the installed app has them (amended 2026-11-15:
`docs/help/` would not reach an install): `pages/` holds the written ones (getting
started, guides, theory, tutorials, verification), `reference/` the ones generated from
the code's own data by `help.reference` (`scripts/build_help.py`; a test fails when one
is stale).

A page starts with front matter between `---` lines:

    ---
    title: Meshing
    section: guide
    order: 30
    keys: kind:mesh, panel:mesh
    summary: From geometry to a mesh: recipes, sizes, controls, quality.
    adrs: 0019, 0066
    ---

`keys` are help keys: what F1 on a panel, a tree item or a Properties row asks for
(`panel:<key>`, `kind:<object kind>`, `feature:<type>`, `field:<name>`, `operator:<kind>`,
...). Inside a page, a line `<!-- key: feature:fixed, feature:solid_mechanics:fixed -->`
just before a heading gives those keys to that heading, so F1 lands on the entry, not
the page's top. Links between pages are `help:<page name>` or `help:<key>`; a link
`puzzle:<action>` opens that tool in the GUI.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

HERE = Path(__file__).parent
PAGES = HERE / "pages"
REFERENCE = HERE / "reference"
SECTIONS = {  # section key -> its heading in the contents, in this order
    "start": "Getting started",
    "guide": "Guides",
    "reference": "Reference",
    "theory": "Theory",
    "tutorial": "Tutorials",
    "verification": "Verification",
}
MARKER = re.compile(r"^<!--\s*key:\s*(.*?)\s*-->\s*$")
COMMENT = re.compile(r"^\s*<!--.*-->\s*$")  # a whole-line comment: never shown
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
WORD = re.compile(r"[a-z0-9_]+")
_STOP = (
    "a an and are as at be by for from has have in is it its of on or that the this to "
    "was were what when which with you your"
)
STOP = frozenset(_STOP.split(" "))


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Page:
    name: str  # its path under pages/ or reference/, without .md: "guides/meshing"
    title: str
    section: str
    order: int
    keys: tuple[str, ...]
    summary: str
    body: str  # as written, markers and all
    headings: tuple[Heading, ...] = field(default_factory=tuple)
    adrs: tuple[str, ...] = ()  # the decisions whose features it explains (WITH_HELP)

    @property
    def text(self) -> str:
        """The page as people read it: the title, then the body without markers."""
        lines = [x for x in self.body.splitlines() if not COMMENT.match(x)]
        return f"# {self.title}\n\n" + "\n".join(lines).strip() + "\n"

    def heading_for(self, key: str) -> Heading | None:
        return next((h for h in self.headings if key in h.keys), None)


def _front(text: str) -> tuple[dict[str, str], str]:
    """(front matter, the rest); no front matter: ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 4 :].lstrip("\n")


def parse(name: str, text: str) -> Page:
    meta, body = _front(text)
    headings, pending = [], ()
    for line in body.splitlines():
        m = MARKER.match(line)
        if m:
            pending = tuple(k.strip() for k in m.group(1).split(",") if k.strip())
            continue
        h = HEADING.match(line)
        if h:
            headings.append(Heading(len(h.group(1)), h.group(2), pending))
            pending = ()
        elif line.strip():
            pending = ()  # a marker belongs to the heading right after it only
    section = meta.get("section", "guide")
    if section not in SECTIONS:
        raise ValueError(f"help page {name}: section '{section}' is not one of {list(SECTIONS)}")
    keys = tuple(k.strip() for k in meta.get("keys", "").split(",") if k.strip())
    adrs = tuple(a.strip() for a in meta.get("adrs", "").split(",") if a.strip())
    return Page(name, meta.get("title", name), section, int(meta.get("order", 100)), keys,
                meta.get("summary", ""), body, tuple(headings), adrs)  # fmt: skip


@cache
def pages() -> dict[str, Page]:
    """Every page by name: the written ones, then the generated reference."""
    out: dict[str, Page] = {}
    for root in (PAGES, REFERENCE):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            name = path.relative_to(root).with_suffix("").as_posix()
            if root is REFERENCE:
                name = f"reference/{name}"
            if name in out:
                raise ValueError(f"two help pages are called {name}")
            out[name] = parse(name, path.read_text(encoding="utf-8"))
    return out


def reload() -> None:
    pages.cache_clear()
    _keys.cache_clear()
    _index.cache_clear()


@cache
def _keys() -> dict[str, tuple[str, str | None]]:
    """Help key -> (page name, heading text or None for the page's top)."""
    out: dict[str, tuple[str, str | None]] = {}
    for p in pages().values():
        out.setdefault(f"page:{p.name}", (p.name, None))
        for k in p.keys:
            out.setdefault(k, (p.name, None))
        for h in p.headings:
            for k in h.keys:
                out.setdefault(k, (p.name, h.text))
    return out


def keys() -> set[str]:
    return set(_keys())


def lookup(key: str) -> tuple[Page, str | None] | None:
    """The page (and the heading in it) a help key opens; a more general key when the
    exact one has no page: `setting:calculix:solver` → `setting:calculix`, then
    `setting`. A page's own name works too."""
    if key in pages():
        return pages()[key], None
    table = _keys()
    parts = key.split(":")
    while parts:
        found = table.get(":".join(parts))
        if found is not None:
            return pages()[found[0]], found[1]
        parts.pop()
    return None


def contents() -> list[tuple[str, list[Page]]]:
    """(section heading, its pages in order) for every section that has pages."""
    out = []
    for key, heading in SECTIONS.items():
        listed = sorted((p for p in pages().values() if p.section == key),
                        key=lambda p: (p.order, p.title))  # fmt: skip
        if listed:
            out.append((heading, listed))
    return out


# --- search: BM25 over every heading's passage ---------------------------------------


@dataclass(frozen=True)
class Passage:
    page: str
    heading: str | None
    text: str


def passages() -> list[Passage]:
    """Each page cut at its headings: (page, heading, the text under it)."""
    out = []
    for p in pages().values():
        heading, lines = None, [p.title, p.summary]
        for line in p.body.splitlines():
            if COMMENT.match(line):
                continue
            h = HEADING.match(line)
            if h:
                if any(x.strip() for x in lines):
                    out.append(Passage(p.name, heading, "\n".join(lines).strip()))
                heading, lines = h.group(2), [p.title, h.group(2)]
            else:
                lines.append(line)
        if any(x.strip() for x in lines):
            out.append(Passage(p.name, heading, "\n".join(lines).strip()))
    return out


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


@cache
def _index():
    found = passages()
    docs = [_words(p.text) for p in found]
    df: dict[str, int] = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    mean = sum(map(len, docs)) / max(1, len(docs))
    return found, docs, df, mean


def search(query: str, limit: int = 8) -> list[dict]:
    """The passages that answer `query` best: page, heading, title, an excerpt."""
    found, docs, df, mean = _index()
    words = _words(query)
    if not words or not docs:
        return []
    n = len(docs)
    scores = []
    for i, d in enumerate(docs):
        s = 0.0
        for w in words:
            tf = d.count(w)
            if tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf * 2.4 / (tf + 1.4 * (0.25 + 0.75 * len(d) / mean))
        title = pages()[found[i].page].title.lower()
        s *= 1.0 + 0.5 * sum(w in title for w in words)  # a page about it, first
        if s > 0:
            scores.append((s, i))
    scores.sort(reverse=True)
    out = []
    for s, i in scores[:limit]:
        p = found[i]
        out.append({"page": p.page, "title": pages()[p.page].title, "heading": p.heading,
                    "score": round(s, 3), "excerpt": _excerpt(p.text)})  # fmt: skip
    return out


def _excerpt(text: str, limit: int = 300) -> str:
    flat = " ".join(x.strip() for x in text.splitlines()[1:] if x.strip())
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0] + "…"


def answer(topic: str = "") -> dict:
    """What MCP `help` and `puzzle help` give: no topic, the contents; a page name or a
    help key, that page (from its heading when the key names one); anything else, the
    pages that answer it best."""
    topic = topic.strip()
    if not topic:
        return {"contents": [{"section": s, "pages": [{"name": p.name, "title": p.title,
                                                       "summary": p.summary} for p in ps]}
                             for s, ps in contents()]}  # fmt: skip
    hit = lookup(topic)
    if hit is not None:
        page, heading = hit
        return {"page": page.name, "title": page.title, "heading": heading,
                "text": section_text(page, heading)}  # fmt: skip
    return {"search": topic, "results": search(topic)}


def section_text(page: Page, heading: str | None) -> str:
    """A page's text, or from `heading` to the next heading of its level or above."""
    text = page.text
    if heading is None:
        return text
    lines, out, level = text.splitlines(), [], None
    for line in lines:
        h = HEADING.match(line)
        if level is None:
            if h and h.group(2) == heading:
                level = len(h.group(1))
                out.append(line)
            continue
        if h and len(h.group(1)) <= level:
            break
        out.append(line)
    return "\n".join(out).strip() + "\n" if out else text
