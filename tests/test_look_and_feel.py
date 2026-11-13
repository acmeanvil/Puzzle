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


CORE = GUI.parent  # the core's drawing code takes the theme's tokens too
TOKENED = [CORE / "post" / "symbols.py", CORE / "post" / "legend.py",
           *sorted((CORE / "schematic").glob("*.py"))]  # fmt: skip


def test_no_colour_literals_outside_the_theme():
    """Every colour the GUI draws is a named token in theme.py (ADR 0045 consequence),
    and so are the view's symbols, the legend's outside colours and the schematic's."""
    found = []
    for path in [*sorted(GUI.glob("*.py")), *TOKENED]:
        if path.name == "theme.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LITERAL.search(line):
                found.append(f"{path.name}:{n}: {line.strip()}")
    assert found == []


def test_the_theme_imports_no_qt():
    """The core reads its colours from theme.py in scripts with no Qt application."""
    import subprocess
    import sys

    code = ("import sys; import puzzle.gui.theme, puzzle.post.symbols, puzzle.post.legend,"
            " puzzle.schematic.svg; print(any(m.startswith('PySide6') for m in sys.modules))"
            )  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)  # fmt: skip
    assert out.stdout.strip() == "False"


def _rgb(colour: str, ground: str | None = None) -> list[float]:
    """A colour's channels 0..1; an rgba() one laid over its ground."""
    m = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", colour)
    if m:
        *rgb, a = (int(v) for v in m.groups())
        under = _rgb(ground or "#ffffff")
        return [a / 255 * v / 255 + (1 - a / 255) * u for v, u in zip(rgb, under, strict=True)]
    c = colour.lstrip("#")
    return [int(c[i : i + 2], 16) / 255 for i in (0, 2, 4)]


def _luminance(colour: str, ground: str | None = None) -> float:
    rgb = _rgb(colour, ground)
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str, under: str | None = None) -> float:
    """WCAG contrast of `a` on `b` (`b` an rgba() fill laid over `under`)."""
    la, lb = sorted((_luminance(a), _luminance(b, under)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name", ["light", "dark", "high_contrast"])
def test_text_reads_on_each_theme(name):
    """WCAG 2.1 AA: every text colour 4.5:1 on every ground it is drawn on (body text
    7:1 in high contrast): muted hints, the state roles (labels, toast marks) and the
    banners' words on their tinted fills; the canvases' words on the canvas."""
    t = theme.SETS[name]
    body = 7.0 if name == "high_contrast" else 4.5
    for ground in (t.surface, t.raised, t.sunken):  # windows, buttons/toasts, inputs
        assert contrast(t.text, ground) >= body, (name, ground)
        for words in (t.muted, t.error, t.done, t.needed, t.running):
            assert contrast(words, ground) >= 4.5, (name, words, ground)
        assert contrast(t.banner_error_text, t.banner_error, ground) >= 4.5, (name, ground)
        assert contrast(t.text, t.banner_info, ground) >= 4.5, (name, ground)
    assert contrast(t.accent_text, t.accent) >= 4.5  # selected rows, default buttons
    for words in (t.canvas_ink, t.canvas_text, t.canvas_muted):
        assert contrast(words, t.canvas) >= 4.5, (name, words)
    assert contrast(t.view_text, t.view_plain) >= 4.5


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


def test_tab_rows_hold_controls_a_few_pixels_apart(app):
    """Two controls 2 px apart in height read as one row, wherever they sit: banding
    by round(y / 8) put y = 3 and y = 5 in different rows."""
    box = QWidget()
    box.resize(300, 200)
    right, left, below = QLineEdit(box), QLineEdit(box), QLineEdit(box)
    right.setGeometry(150, 3, 100, 22)
    left.setGeometry(10, 5, 100, 22)
    below.setGeometry(10, 40, 100, 22)
    box.show()
    spin(app, 0.05)
    assert theme.tab_in_order(box) == [left, right, below]


def test_tab_order_follows_rows_shown_later(app):
    """A row shown after the form first showed takes its place on screen in Tab."""
    box = QWidget()
    column = QVBoxLayout(box)
    first, later, last = QLineEdit(), QLineEdit(), QLineEdit()
    column.addWidget(first)
    column.addWidget(last)
    later.hide()
    column.insertWidget(1, later)
    box.show()
    spin(app, 0.1)
    assert theme.tab_in_order(box) == [first, last]
    later.show()
    spin(app, 0.2)
    assert first.nextInFocusChain() is later and later.nextInFocusChain() is last


def test_cancel_stays_for_a_run_while_a_mesh_starts_and_ends(app):
    """Overlapping jobs: the bar and Cancel stay while any job runs, and Cancel stops
    the job the bar names."""
    bar = feedback.StatusProgress()
    stop = threading.Event()
    run, mesh = object(), object()
    bar.start(run, "Running Beam", stop)
    bar.start(mesh, "Meshing Beam")  # can't be stopped part-way
    assert not bar.isHidden() and not bar.cancel_button.isHidden()
    assert "Running Beam" in bar.label.text() and "1 more" in bar.label.text()
    assert bar.cancel_button.accessibleName() == "Cancel Running Beam"
    bar.stop(mesh)
    assert not bar.isHidden() and not bar.cancel_button.isHidden()
    bar.start(mesh, "Meshing Beam")
    bar.stop(run)  # the run ends first: the mesh still shows, without Cancel
    assert not bar.isHidden() and bar.cancel_button.isHidden()
    assert bar.label.text() == "Meshing Beam"
    bar.stop(mesh)
    assert bar.isHidden() and not stop.is_set()


def test_busy_keeps_controls_that_were_disabled(app):
    panel = QWidget()
    column = QVBoxLayout(panel)
    column.addWidget(on := QLineEdit())
    column.addWidget(off := QPushButton("Apply"))
    off.setEnabled(False)
    feedback.busy(panel, True)
    feedback.busy(panel, True, "Still working…")  # twice: still remembers the first state
    assert not on.isEnabled() and not off.isEnabled()
    feedback.busy(panel, False)
    assert on.isEnabled() and not off.isEnabled()


def test_toasts_are_named_for_screen_readers(app):
    w = QMainWindow()
    w.resize(600, 400)
    w.show()
    toasts = feedback.Toasts(w)
    toast = toasts.show("Beam failed", ("Show why", lambda: None), state="error")
    assert toast.accessibleName() == "Failed: Beam failed"
    assert "Show why" in toast.accessibleDescription()
    assert toast.timer.parent() is toast  # goes with the toast
    toast.close_now()
    w.close()


def test_ribbon_titles_follow_the_text_size(app):
    from PySide6.QtGui import QFont

    from puzzle.gui.ribbon import Ribbon

    ribbon = Ribbon()
    ribbon.group("Shapes", [Ribbon.button("Box"), Ribbon.button("Cylinder")])
    ribbon.show()
    spin(app, 0.05)
    title = ribbon.findChild(feedback.QLabel, "ribbon_title")
    before = title.font().pointSizeF()
    font = QFont(app.font())
    try:
        app.setFont(QFont(font.family(), round(font.pointSizeF() * 1.5)))
        spin(app, 0.1)
        assert title.font().pointSizeF() == pytest.approx(ribbon.font().pointSizeF() * 0.85)
        assert title.font().pointSizeF() > before
    finally:
        app.setFont(font)
        spin(app, 0.05)
    ribbon.close()


def test_a_failed_mesh_says_so_in_a_toast(app, window, monkeypatch):
    import puzzle.mesh
    from puzzle.model.schema import Component

    def broken(*_a, **_k):
        raise RuntimeError("no volume to mesh")

    monkeypatch.setattr(puzzle.mesh, "generate_mesh", broken)
    comp = window.session.project.of_type(Component)[0]
    window.start_meshing(comp)
    end = time.monotonic() + 5
    while window.tasks and time.monotonic() < end:
        spin(app, 0.05)
    note = window.toasts.shown[-1]
    assert note.state == "error" and note.text == f"Meshing {comp.name} failed"
    assert note.action == "Show why"
    assert "no volume to mesh" in window.log.toPlainText()  # not the only place it shows


def test_progress_tickers_go_with_their_job(app, window):
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer

    def tickers():
        return [t for t in window.findChildren(QTimer) if t.interval() == 500]

    before = len(tickers())
    seen = []
    task = window.background("Counting…", lambda _emit: 1, seen.append,
                             fraction=lambda: 0.5, lock=False)  # fmt: skip
    end = time.monotonic() + 5
    while not seen and time.monotonic() < end:
        spin(app, 0.05)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert seen == [1] and task.ticker is None and len(tickers()) == before


def test_wizards_follow_the_theme():
    """QWizard's default style on Windows (Aero) paints white pages over the palette,
    so a wizard looked light in dark mode (I6); each sets ModernStyle."""
    import re

    gui = Path(__file__).parents[1] / "src" / "puzzle" / "gui"
    for path in gui.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"class \w+\(QWizard\)", text):
            assert "WizardStyle.ModernStyle" in text, path.name
