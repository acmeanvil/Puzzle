"""The rest of the look and feel (ADR 0045): every colour a token, readable contrast in
each theme, density and text size, engineering icons on the tree and ribbons, toasts,
status-bar progress with Cancel, busy panels, F6 between panels and Tab in screen
order."""

import os
import re
import shutil
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle import settings  # noqa: E402
from puzzle.gui import feedback, icons, theme  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402

GUI = Path(__file__).parents[1] / "src" / "puzzle" / "gui"
EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def spin(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# --- colours -------------------------------------------------------------------------

LITERAL = re.compile(
    r"""['"]#[0-9a-fA-F]{3,8}['"]|rgba?\(|color:\s*(gray|grey|black|white|red)\b"""
    r"""|QColor\(\s*['"]|Qt\.GlobalColor\.(red|green|blue|black|white|gray)"""
)


def test_no_colour_literals_outside_the_theme():
    """Every colour the GUI draws is a named token in theme.py (ADR 0045 consequence)."""
    found = []
    for path in sorted(GUI.glob("*.py")):
        if path.name == "theme.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if LITERAL.search(line):
                found.append(f"{path.name}:{n}: {line.strip()}")
    assert found == []


def _luminance(colour: str) -> float:
    c = colour.lstrip("#")
    rgb = [int(c[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name", ["light", "dark", "high_contrast"])
def test_text_reads_on_each_theme(name):
    """WCAG 2.1: body text 4.5:1 (7:1 in high contrast), hints and state marks 3:1."""
    t = theme.SETS[name]
    body = 7.0 if name == "high_contrast" else 4.5
    for ground in (t.surface, t.sunken):
        assert contrast(t.text, ground) >= body
        for mark in (t.muted, t.error, t.done, t.needed):
            assert contrast(mark, ground) >= 3.0, (name, mark, ground)
    assert contrast(t.accent_text, t.accent) >= 3.0


# --- density and text size ---------------------------------------------------------------


def test_density_and_text_size(app):
    roomy = theme.style_sheet(theme.LIGHT, "comfortable")
    tight = theme.style_sheet(theme.LIGHT, "compact")
    assert "QTreeView::item, QListView::item { padding: 3px 0; }" in roomy
    assert "QTreeView::item, QListView::item { padding: 1px 0; }" in tight
    keys = {p.key: p for p in settings.PREFERENCES}
    assert keys["interface.density"].default == "comfortable"
    assert keys["interface.text_size"].default == "system"
    base = app.font().pointSizeF()
    settings.current().set("interface.text_size", "largest")
    try:
        theme.apply(app, preference="light")
        assert app.font().pointSizeF() == pytest.approx(theme._SYSTEM_FONT["font"].pointSizeF()
                                                        * 1.3)  # fmt: skip
    finally:
        settings.current().set("interface.text_size", "system")
        theme.apply(app, preference="light")
    assert app.font().pointSizeF() == pytest.approx(theme._SYSTEM_FONT["font"].pointSizeF())
    assert base > 0


def test_focus_ring_comes_from_the_tokens():
    sheet = theme.style_sheet(theme.HIGH_CONTRAST)
    assert f"border: 2px solid {theme.HIGH_CONTRAST.focus}" in sheet


# --- icons -------------------------------------------------------------------------------


def test_every_feature_section_and_tool_has_an_icon(app):
    from puzzle.gui.tree import SECTIONS
    from puzzle.model.catalog import CATALOG

    features = {f.type for specs in CATALOG.values() for f in specs}
    assert features <= set(icons.FEATURES)
    assert {title for title, _cls in SECTIONS} | {"Results"} <= set(icons.SECTIONS)
    for name in [*icons.FEATURES.values(), *icons.SECTIONS.values(), *icons.TOOLS.values()]:
        assert not icons.icon(name).isNull(), name
    assert (GUI / "tabler" / "LICENSE").exists()


def test_state_icons_follow_the_theme(app):
    theme.apply(app, preference="dark")
    dark = icons.state_icon("failed")
    theme.apply(app, preference="light")
    light = icons.state_icon("failed")
    assert dark is not light  # drawn again in the light theme's error colour


# --- toasts, progress, busy ----------------------------------------------------------


def test_toasts_stack_act_and_can_be_turned_off(app):
    w = QMainWindow()
    w.resize(800, 600)
    w.show()
    toasts = feedback.Toasts(w)
    hits = []
    first = toasts.show("Meshed Beam", ("Show mesh", lambda: hits.append(1)))
    assert first.isVisible() and first.button.text() == "Show mesh"
    first.button.click()
    assert hits == [1] and first not in toasts.live
    for i in range(5):
        toasts.show(f"note {i}")
    assert len(toasts.live) == feedback.MOST
    a, b = toasts.live[-2], toasts.live[-1]
    assert a.geometry().bottom() < b.geometry().top()  # stacked upward, the newest lowest
    settings.current().set("interface.toasts", False)
    try:
        assert toasts.show("quiet") is None
        assert toasts.shown[-1].text == "quiet"  # still recorded
    finally:
        settings.current().set("interface.toasts", True)
    w.close()


def test_status_progress_shows_fraction_and_cancels(app):
    bar = feedback.StatusProgress()
    stop = threading.Event()
    key = object()
    bar.start(key, "Running Beam", stop)
    assert not bar.isHidden() and bar.bar.maximum() == 0  # busy until it can tell
    bar.update(key, 0.4)
    assert bar.bar.maximum() == 1000 and bar.bar.value() == 400
    assert not bar.cancel_button.isHidden()
    bar.cancel_button.click()
    assert stop.is_set() and bar.label.text() == "Stopping…"
    other = object()
    bar.start(other, "Meshing")
    assert bar.cancel_button.isHidden()  # meshing can't be stopped part-way
    bar.stop(other)
    bar.stop(key)
    assert bar.isHidden()


def test_busy_greys_a_panel_and_says_so(app):
    panel = QWidget()
    QVBoxLayout(panel).addWidget(edit := QLineEdit())
    feedback.busy(panel, True, "Checking the contacts…")
    assert not edit.isEnabled() and panel.property("busy")
    note = panel.findChild(feedback.QLabel, "busy_note")
    assert note.text() == "Checking the contacts…"
    feedback.busy(panel, False)
    assert edit.isEnabled() and note.isHidden()


# --- in the window -----------------------------------------------------------------------


@pytest.fixture
def window(app, tmp_path):
    from puzzle.gui.window import MainWindow

    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE,
        root,
        ignore=shutil.ignore_patterns(
            "results*", ".puzzle", "history.jsonl", "versions"
        ),  # fmt: skip
    )
    w = MainWindow(root)
    w.show()
    spin(app, 0.5)
    yield w
    w.close()


def test_tree_sections_and_ribbons_carry_icons(window):
    top = window.tree.invisibleRootItem().child(0)
    sections = [top.child(i) for i in range(top.childCount())]
    assert sections and all(not s.icon(0).isNull() for s in sections)
    features = [i for i in window.tree._items() if i.data(0, ROLE) and
                getattr(i.data(0, ROLE), "kind", "") == "feature"]  # fmt: skip
    assert features and all(not i.icon(0).isNull() for i in features)
    tools = [b for b in window.findChildren(QToolButton) if b.property("tool_icon")]
    assert len(tools) > 40 and all(not b.icon().isNull() for b in tools)


def test_background_work_shows_progress_and_a_toast(app, window):
    stop = threading.Event()
    started = threading.Event()

    def work(_emit):
        started.set()
        stop.wait(5)
        return "stopped" if stop.is_set() else "ran out"

    seen = []
    window.background("Waiting…", work, seen.append, cancel=stop, fraction=lambda: 0.25,
                      lock=False)  # fmt: skip
    started.wait(5)
    spin(app, 0.7)
    assert not window.progress.isHidden() and window.progress.bar.value() == 250
    window.progress.cancel_button.click()
    end = time.monotonic() + 5
    while not seen and time.monotonic() < end:
        spin(app, 0.05)
    assert seen == ["stopped"] and window.progress.isHidden()


def test_f6_moves_between_panels(app, window):
    panels = window.panels_in_order()
    assert len(panels) >= 3
    reached = []
    for _ in range(len(panels)):
        window.focus_panel(1)
        reached.append(window.focused_panel)
    assert set(map(id, reached)) == set(map(id, panels))
    window.focus_panel(-1)
    assert window.focused_panel is reached[-2]


def test_tab_follows_the_rows_on_screen(app):
    box = QWidget()
    column = QVBoxLayout(box)
    low, high = QPushButton("Second"), QLineEdit()
    column.addWidget(high)
    column.addWidget(low)
    column.insertWidget(0, top := QLineEdit())  # inserted later, shown first
    box.show()
    spin(app, 0.1)
    assert theme.tab_in_order(box) == [top, high, low]
    chain, w = [], top
    for _ in range(50):  # Tab's order: the focus chain's widgets that take Tab
        if w in (top, high, low) and w not in chain:
            chain.append(w)
        w = w.nextInFocusChain()
    assert chain == [top, high, low]
