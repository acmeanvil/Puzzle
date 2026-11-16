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


def test_a_help_key_link_goes_to_its_heading():
    """A link by help key (help:feature:pressure) resolves through `help.lookup` to the
    page and heading the key marks, as F1 does."""
    key = next(k for k in sorted(manual.keys()) if manual.lookup(k)[1])
    page, heading = manual.lookup(key)
    assert site.resolve(key) == (page.name, site.slug(key))
    assert site.resolve(f"{page.name}#{heading}") == (page.name, site.slug(heading))
    assert site.resolve("no-such-page") is None


def test_the_pdf_holds_the_whole_manual(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = tmp_path / "manual.pdf"
    done = site.build_pdf(path)
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and done["pages"] == len(manual.pages())
    assert len(re.findall(rb"/Type\s*/Page\b", data)) > 2 * done["pages"]
