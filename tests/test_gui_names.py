"""ADR 0029: the GUI shows names, never raw values, and panels follow their context.

Every tree item of the example and benchmark projects (plus a history geometry and a
derived result) is selected in turn; an equation's own text is the user's code and is
not checked. No text the tree or the properties panel shows
(labels, values, dropdown choices) may be `null`, `None`, a UUID, a snake_case key or an
"N items" placeholder. A field that fails names itself, so the label table can be
completed. The results bar belongs to results: it hides in the Geometry tab.
"""

import os
import re
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402

ROOT = Path(__file__).parents[1]
PROJECTS = [ROOT / "examples" / "cantilever", *sorted((ROOT / "benchmarks").glob("*/"))]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SNAKE = re.compile(r"(?<![\w./])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w.])")
WORDS = re.compile(r"\b(null|None)\b|\b\d+ items?\b")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def texts(window):
    """Every text the properties panel shows: names, values and dropdown choices."""
    out = []
    panel = window.properties
    stack = [panel.invisibleRootItem()]
    while stack:
        item = stack.pop()
        for i in range(item.childCount()):
            child = item.child(i)
            # An equation is the user's own code in the equation language (ADR 0025):
            # its names (von_mises, stress_xx) are that language's, not raw keys.
            shown = "" if child.text(0) in ("Equation", "Expression") else child.text(1)
            out += [child.text(0), shown]
            box = panel.itemWidget(child, 1)
            if isinstance(box, QComboBox):
                out += [box.itemText(j) for j in range(box.count())]
            stack.append(child)
    return [t for t in out if t]


def bad(text: str) -> str | None:
    for pattern, why in ((UUID, "an id"), (WORDS, "a raw value"), (SNAKE, "a key")):
        if pattern.search(text):
            return why
    return None


@pytest.mark.parametrize("source", PROJECTS, ids=lambda p: p.name)
def test_no_raw_values_anywhere_in_tree_or_properties(app, tmp_path, source):
    root = tmp_path / source.name
    shutil.copytree(
        source, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    try:
        found = []
        for item in w.tree._items():
            node = item.data(0, ROLE)
            w.tree.setCurrentItem(item)
            app.processEvents()
            for text in [item.text(0), *texts(w)]:
                why = bad(text)
                if why:
                    found.append(f"{node.kind} '{node.label}': {why}: {text!r}")
        assert not found, "\n".join(found)
    finally:
        w.close()


def test_a_history_geometry_hides_the_file_fields(app, tmp_path):
    from puzzle.commands.model import Create

    w = MainWindow()
    try:
        w.new_project(tmp_path / "p", "P")
        box = {"kind": "box", "name": "Block", "args": {"size": [0.01, 0.02, 0.03]}}
        w.submit(Create(kind="geometry", data={"name": "Block", "history": [box]}))
        for item in w.tree._items():
            if item.data(0, ROLE).label == "Block" and item.data(0, ROLE).kind == "object":
                w.tree.setCurrentItem(item)
        shown = texts(w)
        assert "History" in shown
        for hidden in ("Source", "Source unit", "Facet angle (degrees)"):
            assert hidden not in shown
        assert not [t for t in shown if bad(t)], shown
    finally:
        w.close()


def test_results_bar_belongs_to_results(app, tmp_path):
    """A shown result's bar hides in the Geometry tab and comes back on the run."""
    import numpy as np

    from puzzle.mesh import Mesh
    from puzzle.results.model import Field, Results, StepResult

    root = tmp_path / "cantilever"
    shutil.copytree(
        PROJECTS[0], root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    try:
        ids = np.array([1, 2])
        mesh = Mesh(ids, np.zeros((2, 3)))
        f = Field("temperature", "node", [""], ids, np.array([[1.0], [2.0]]))
        w.result = (
            mesh,
            Results("calculix", [StepResult(1, "stationary", 0.0, {"temperature": f})]),
        )
        w.set_context("results")
        assert w.results_bar.isVisibleTo(w)
        w.left_tabs.setCurrentWidget(w.workbench)
        assert w.context == "geometry" and not w.results_bar.isVisibleTo(w)
        assert w.workbench.ribbon.isVisibleTo(w)
        w.left_tabs.setCurrentIndex(0)
        assert w.context == "model" and not w.workbench.ribbon.isVisibleTo(w)
    finally:
        w.close()
