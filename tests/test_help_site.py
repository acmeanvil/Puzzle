"""The manual as a static site and a PDF (ADR 0083 step 5): built from the same pages as
the Help window, every link and search entry landing on a page and heading that exist,
every equation drawn, nothing of TeX or Puzzle's own link schemes left in the HTML."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("markdown_it")

from puzzle import help as manual  # noqa: E402
from puzzle.help import site  # noqa: E402

HREF = re.compile(r'href="([^"]+)"')
ID = re.compile(r'id="([^"]+)"')


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    return out, site.build_site(out)


def test_every_page_is_written_and_every_link_lands(built):
    out, done = built
    assert done["missing"] == [] and done["pages"] == len(manual.pages())
    ids: dict[Path, set[str]] = {}
    for name in manual.pages():
        path = out / f"{name}.html"
        text = path.read_text(encoding="utf-8")
        assert "help:" not in text and "puzzle:" not in text, name
        assert "$$" not in text, name  # every display equation became an image
        ids[path.resolve()] = set(ID.findall(text))
    for path in [out / "index.html", *(out / f"{n}.html" for n in manual.pages())]:
        for href in HREF.findall(path.read_text(encoding="utf-8")):
            if href.startswith(("http:", "https:", "mailto:")):
                continue
            file, _, anchor = href.partition("#")
            target = (path.parent / file).resolve() if file else path.resolve()
            assert target.exists(), f"{path.name}: {href}"
            if anchor and target in ids:
                assert anchor in ids[target], f"{path.name}: {href}"


def test_equations_are_drawn_and_the_search_index_lands(built):
    import json

    out, _ = built
    page = (out / "theory" / "mesh-convergence.html").read_text(encoding="utf-8")
    assert page.count('class="tex display"') >= 5 and "data:image/svg+xml;base64," in page
    script = (out / "search.js").read_text(encoding="utf-8")
    index = json.loads(script[len("var INDEX = ") : script.index(";\n")])
    assert {e["url"].split("#")[0] for e in index} == {f"{n}.html" for n in manual.pages()}
    assert any(e["heading"] == "The verdicts" for e in index)
    for e in index:
        assert (out / e["url"].split("#")[0]).exists()


def test_pictures_are_in_the_pages(built):
    """Screens, operators' nodes and the menu locations a page names, as PNGs within the
    page and no wider than the PDF's text."""
    out, _ = built
    for name in ("guides/agent-and-advisor", "reference/operators"):
        page = (out / f"{name}.html").read_text(encoding="utf-8")
        assert 'class="picture"' in page and "image:" not in page, name
    for width in re.findall(r'class="picture"[^>]* width="(\d+)"', page):
        assert int(width) <= site.PICTURE_WIDTH


def test_a_help_key_link_goes_to_its_heading():
    """A link by help key (help:feature:pressure) resolves through `help.lookup` to the
    page and heading the key marks, as F1 does."""
    key = next(k for k in sorted(manual.keys()) if manual.lookup(k)[1])
    page, heading = manual.lookup(key)
    assert site.resolve(key) == (page.name, site.slug(key))
    assert site.resolve(f"{page.name}#{heading}") == (page.name, site.slug(heading))
    assert site.resolve("no-such-page") is None


LITERAL = rb"\((?:\\.|[^\\)])*\)"  # a PDF literal string, escapes and all


def _text(token: bytes) -> str:
    """A PDF string token, literal (Qt's) or hex (the bookmarks'), as text."""
    if token.startswith(b"<"):
        raw = bytes.fromhex(token[1:-1].decode())
    else:
        raw = re.sub(rb"\\([0-7]{1,3}|.)", lambda m: _escape(m.group(1)), token[1:-1], flags=re.S)
    return raw[2:].decode("utf-16-be") if raw[:2] == b"\xfe\xff" else raw.decode("latin-1")


def _escape(code: bytes) -> bytes:
    if code[:1].isdigit():
        return bytes([int(code, 8)])
    return {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}.get(code, code)


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = tmp_path_factory.mktemp("pdf") / "manual.pdf"
    return path, site.build_pdf(path)


def test_the_pdf_holds_the_whole_manual(pdf):
    path, done = pdf
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and done["pages"] == len(manual.pages())
    assert done["missing"] == []
    assert len(re.findall(rb"/Type\s*/Page\b", data)) > 2 * done["pages"]


def test_the_pdf_links_and_bookmarks_land(pdf):
    """Every link and bookmark names a destination the document holds: a page, a heading
    or a help key's heading, as the site's links do."""
    path, _ = pdf
    data = path.read_bytes()
    names = {_text(m) for m in re.findall(rb"(" + LITERAL + rb")\s+\d+ 0 R", data)}
    links = [_text(m) for m in re.findall(rb"/Subtype /Link\s.*?/Dest (" + LITERAL + rb")",
                                          data, re.S)]  # fmt: skip
    marks = [_text(m) for m in re.findall(rb"/Dest (<FEFF[0-9A-F]*>)", data)]
    assert len(links) > 100 and set(links) <= names
    assert len(marks) > len(manual.pages()) and set(marks) <= names
    assert {p for p in manual.pages()} <= names  # each page a bookmark's target
    key = next(k for k in sorted(manual.keys()) if manual.lookup(k)[1] and ":" in k)
    page, _ = manual.lookup(key)
    assert f"{page.name}~{site.slug(key)}" in names  # a help key's heading, as F1 finds
    assert b"/PageMode /UseOutlines" in data and data.rstrip().endswith(b"%%EOF")
