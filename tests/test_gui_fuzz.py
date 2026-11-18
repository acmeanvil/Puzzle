"""Every menu bar action, triggered: nothing may reach the error hook (ADR 0090 package 10).

The review's fuzz (`gui-core-bugs.md`) found tools that never opened, a tool with no
form at all and actions that raised with no project; this keeps it. Each enabled leaf
action of the menu bar is triggered with no project open, and on both examples with
every dialog cancelled and with every one accepted on its defaults (then undone), and
the tree's right-click actions on each item with dialogs cancelled. File dialogs answer
nothing; actions that solve, mesh, delete, write elsewhere or change the user's own
settings are left out of the accepted run (they are tested where they live).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QColorDialog,
    QDialog,
    QFileDialog,
    QInputDialog,
    QMenu,
    QMessageBox,
)

from puzzle.gui.window import MainWindow  # noqa: E402

EXAMPLES = Path(__file__).parents[1] / "examples"
# Left out when dialogs are accepted: they solve, mesh, delete, leave the project or
# change the user's settings, and each has its own tests.
SKIP_ACCEPTED = ("Run", "Mesh", "Delete", "Optimise", "Export", "archive", "Trust",
                 "Settings", "Layout", "layout", "Save as", "New…", "Open", "Reload",
                 "Quit", "Remote hosts", "Stop", "convergence", "Adapt", "shoot-out",
                 "Build", "Repair", "Version", "version", "Roll back", "known good",
                 "Who may connect", "ParaView", "Import", "Solver output")  # fmt: skip


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def errors(monkeypatch):
    """Exceptions raised in slots, as `sys.excepthook` gets them."""
    caught: list[str] = []

    def hook(kind, value, tb):
        import traceback

        caught.append("".join(traceback.format_exception(kind, value, tb))[-1500:])

    monkeypatch.setattr(sys, "excepthook", hook)

    class Logged(logging.Handler):  # a panel's failure, kept from the rest (`_aside`)
        def emit(self, record):
            caught.append(self.format(record)[-1500:])

    handler = Logged(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("puzzle.gui").addHandler(handler)
    yield caught
    logging.getLogger("puzzle.gui").removeHandler(handler)


def _quiet(monkeypatch, accept: bool) -> None:
    """Every modal question answered without a person."""
    answer = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.Cancel
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: answer))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: answer)
    for name in ("getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (0.0, False)))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False)))
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: None)
    monkeypatch.setattr(QMessageBox, "about", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))

    def run(self) -> int:
        """Cancel, or press OK as a person would: only if it is enabled, and the
        dialog's own handler decides whether it closes."""
        if not accept:
            return 0
        from PySide6.QtWidgets import QDialogButtonBox

        self.setResult(0)
        for box in self.findChildren(QDialogButtonBox):
            ok = box.button(QDialogButtonBox.StandardButton.Ok)
            if ok is not None and ok.isEnabled():
                ok.click()
                return self.result()
        return 0  # no OK to press: as if closed

    monkeypatch.setattr(QDialog, "exec", run)


def _window(app, tmp_path, example: str | None) -> MainWindow:
    if example is None:
        return MainWindow(None)
    root = tmp_path / example
    skip = shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    shutil.copytree(EXAMPLES / example, root, ignore=skip)
    return MainWindow(root)


def _close(app, window) -> None:
    """The window and everything an action left open, deleted before the next test:
    a half-dead widget crashed the next window's style sheet."""
    from conftest import delete_windows

    end = time.monotonic() + 120  # its background jobs report to it: let them end first
    while window.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    window.close()
    delete_windows(app)


def _leaves(window) -> list[tuple[str, object]]:
    """(path, action) for every enabled leaf the menu bar shows, opened as a user would:
    a disabled menu is not walked into."""
    out = []

    def walk(menu: QMenu, names: list[str]) -> None:
        menu.aboutToShow.emit()
        for action in menu.actions():
            if action.isSeparator() or not action.text() or not action.isEnabled():
                continue
            here = [*names, action.text().replace("&", "")]
            if action.menu() is not None:
                if action.menu().isEnabled():
                    walk(action.menu(), here)
            else:
                out.append((" → ".join(here), action))

    for top in window.menuBar().actions():
        walk(top.menu(), [top.text().replace("&", "")])
    return out


def _menu_leaves(menu: QMenu, names: list[str]) -> list[tuple[str, object]]:
    out = []
    menu.aboutToShow.emit()
    for a in menu.actions():
        if a.isSeparator() or not a.text() or not a.isEnabled():
            continue
        if a.menu() is not None:
            out += _menu_leaves(a.menu(), [*names, a.text()])
        else:
            out.append((" → ".join([*names, a.text()]), a))
    return out


def _settle(app, window) -> None:
    for _ in range(3):
        app.processEvents()
    if window.workbench.tool is not None:
        window.workbench.close_tool()
    if getattr(window.workbench, "sketching", None) is not None:
        window.workbench.cancel_sketch()


def _walk(app, window, errors, *, undo: bool, skip=()) -> list[str]:
    failed = []
    for path, action in _leaves(window):
        if any(word in path for word in skip):
            continue
        before = len(errors)
        history = len(window.session.history(limit=1000)) if window.session else 0
        try:
            action.trigger()
            _settle(app, window)
        except Exception as exc:  # noqa: BLE001 - a raise out of trigger is a failure too
            errors.append(f"{type(exc).__name__}: {exc}")
        if undo and window.session is not None:
            added = len(window.session.history(limit=1000)) - history
            for _ in range(max(added, 0)):
                window.session.undo()
            if added:
                window.refresh()
        if len(errors) > before:
            failed.append(f"{path}:\n{errors[-1]}")
    return failed


def test_no_project(app, tmp_path, monkeypatch, errors):
    _quiet(monkeypatch, accept=False)
    window = _window(app, tmp_path, None)
    try:
        failed = _walk(app, window, errors, undo=False)
        for name in (
            "undo",
            "redo",
            "import",
            "mesh",
            "run",
            "delete",
            "pick",
        ):  # the toolbar's own
            window.actions_by_name[name].trigger()
        assert not failed and not errors, "\n\n".join(failed or errors)
    finally:
        _close(app, window)


@pytest.mark.parametrize("example", ["cantilever", "bracket_assembly"])
def test_every_menu_action_cancelled(app, tmp_path, monkeypatch, errors, example):
    _quiet(monkeypatch, accept=False)
    window = _window(app, tmp_path, example)
    try:
        failed = _walk(app, window, errors, undo=False, skip=("Quit", "Open", "New…"))
        assert not failed, "\n\n".join(failed)
    finally:
        _close(app, window)


@pytest.mark.parametrize("example", ["cantilever", "bracket_assembly"])
def test_every_menu_action_accepted(app, tmp_path, monkeypatch, errors, example):
    _quiet(monkeypatch, accept=True)
    window = _window(app, tmp_path, example)
    window.ask_yes = lambda *a, **k: True
    try:
        failed = _walk(app, window, errors, undo=True, skip=SKIP_ACCEPTED)
        assert not failed, "\n\n".join(failed)
    finally:
        _close(app, window)


@pytest.mark.parametrize("accept", [False, True], ids=["cancelled", "accepted"])
@pytest.mark.parametrize("example", ["cantilever", "bracket_assembly"])
def test_every_tree_action(app, tmp_path, monkeypatch, errors, example, accept):
    """Each tree item's right-click actions; accepted ones are undone after."""
    from puzzle.gui.menus import build_menu
    from puzzle.gui.tree import ROLE

    _quiet(monkeypatch, accept=accept)
    window = _window(app, tmp_path, example)
    window.ask_yes = lambda *a, **k: accept
    try:
        failed = []
        nodes = [i.data(0, ROLE) for i in window.tree._items()]
        for node in nodes:
            window.tree.select(node)
            if window.tree.current() is None:
                continue
            menu = build_menu(window, window.tree.current())
            if menu is None:
                continue
            for path, action in _menu_leaves(menu, [node.label]):
                if any(w in path for w in ("Run", "Mesh", "Open", "ParaView")):
                    continue
                if accept and any(w in path for w in SKIP_ACCEPTED):
                    continue
                before = len(errors)
                history = len(window.session.history(limit=1000))
                window.tree.select(node)
                action.trigger()
                _settle(app, window)
                added = len(window.session.history(limit=1000)) - history
                for _ in range(max(added, 0)):
                    window.session.undo()
                if added:
                    window.refresh()
                if len(errors) > before:
                    failed.append(f"{path}:\n{errors[-1]}")
        assert not failed, "\n\n".join(failed)
    finally:
        _close(app, window)
