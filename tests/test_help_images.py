"""The manual's pictures (ADR 0083 decision 2, amended 2026-11-16): every picture a page
names is drawn, every menu location a page names is a real one with its picture, and
every operator has its node drawn. When one fails, rerun
`pixi run -e dev python scripts/build_help_images.py` (and `scripts/build_help.py` for
the reference)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from puzzle import help as manual
from puzzle.help import pictures

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def test_every_picture_a_page_names_is_drawn():
    missing = [
        f"{name}: {ref}"
        for name, page in manual.pages().items()
        for ref in pictures.references(pictures.with_pictures(page.body))
        if not pictures.path(ref).is_file()
    ]
    assert missing == [], "rerun scripts/build_help_images.py"


def test_no_picture_is_left_over():
    menus = set(pictures.index()["menus"].values())
    drawn = {f"menu/{p.name}" for p in (pictures.IMAGES / "menu").glob("*.png")}
    assert drawn == menus
    from puzzle.operators import TYPES

    operators = {p.stem for p in (pictures.IMAGES / "operator").glob("*.png")}
    assert operators == set(TYPES)
    named = {ref for page in manual.pages().values() for ref in pictures.references(page.body)}
    screens = {f"screens/{p.name}" for p in (pictures.IMAGES / "screens").glob("*.png")}
    assert screens <= named, "a screen no page shows"


def test_a_menu_location_gets_its_picture_after_its_paragraph():
    menu = next(iter(pictures.index()["menus"]))
    first, rest = menu.split(" → ", 1)
    text = (f"Open {first} →\n{rest} to start.\nThen carry on.\n\nNext paragraph.\n\n"
            f"```\n{menu}\n```\n")  # fmt: skip
    lines = pictures.with_pictures(text).splitlines()
    picture = f"![{menu}](image:{pictures.menu_file(menu)})"
    assert lines.count(picture) == 1  # once, though named again (in code, not read)
    assert lines.index(picture) == 4 and lines[2] == "Then carry on."
    assert pictures.unknown_menus(f"{first} → No such item", [menu]) != []
    assert pictures.unknown_menus("Settings → Results → Symmetric models", [menu]) == []


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from puzzle.gui.window import MainWindow

    root = tmp_path_factory.mktemp("images") / "c"
    skip = shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    shutil.copytree(EXAMPLE, root, ignore=skip)
    w = MainWindow(root)
    yield w
    w.close()


def test_every_menu_location_a_page_names_is_real_and_drawn(window):
    """Words that name a menu location name one the menu bar has, and the pictures are
    of the locations the pages name now (menus renamed or pages changed: redraw)."""
    from puzzle.gui.main_menu import paths

    real = [p for p in paths(window) if pictures.ARROW in p]
    named, wrong = set(), []
    for name, page in manual.pages().items():
        named |= {m for _, m in pictures.menu_mentions(page.body, real)}
        wrong += [f"{name}: {b}" for b in pictures.unknown_menus(page.body, real)]
    assert wrong == []
    assert named == set(pictures.index()["menus"]), "rerun scripts/build_help_images.py"
