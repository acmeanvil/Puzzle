"""Ways in for what only agents reached (ADR 0090 package 16): a threaded contact's
results in the Contact tool's Threads tab (ADR-16, ADR 0076 decision 5), and a weld's
bead drawn or taken back from the tree and the menu bar (ADR-20, ADR 0078 step 4); a
weld set to "modelled" by hand with no bead, or on a side not modelled, is refused
(ADR-20, ADR-19)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.model.schema import Component, Quantity, Study, Weld  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402
from puzzle.results.model import Field, Results, StepResult  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


def _with_weld(root: Path, **weld) -> None:
    p = load_project(root)
    comp = p.of_type(Component)[0]
    comp.welds.append(Weld(name="W", leg=Quantity(value=5, unit="mm"), path="Bar/Tip",
                           faces=["Bar/Tip", "Bar/Root"], **weld))  # fmt: skip
    save_project(p, root)


def _window(app, root):
    from puzzle.gui.window import MainWindow

    return MainWindow(root)


def _close(app, w) -> None:
    from conftest import delete_windows

    end = time.monotonic() + 30
    while w.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    delete_windows(app)


# --- refusals (ADR-20, ADR-19) --------------------------------------------------------


@pytest.mark.parametrize("weld, said", [({"formulation": "modelled"}, "no bead part 'W bead'"),
                                        ({"sides": "both"}, "only the arrow side")])  # fmt: skip
def test_a_weld_the_model_cannot_hold_is_refused(root, weld, said):
    from puzzle.adapters import available_adapters

    _with_weld(root, **weld)
    p = load_project(root)
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    problems = available_adapters()["calculix"]().check(p, study)
    assert any(said in x for x in problems), problems


# --- the bead from the tree and the menu bar (ADR-20) ---------------------------------


def test_the_bead_is_drawn_and_taken_back_from_the_gui(app, root, monkeypatch):
    from puzzle import weld_generator
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui import menus, weld_forms
    from puzzle.mesh import generate

    _with_weld(root)
    asked = []

    def modelled(project, comp, mesh, name):  # the MCP tool's batch, as it would be
        asked.append(name)
        return [Edit(object=str(comp.id), ops=[EditOp(op="set", path="welds.0.formulation",
                                                      value="modelled")])]  # fmt: skip

    monkeypatch.setattr(generate, "generate_mesh",
                        lambda *a, **k: SimpleNamespace(mesh=None))  # fmt: skip
    monkeypatch.setattr(weld_generator, "modelled_commands", modelled)
    w = _window(app, root)
    try:
        comp = w.session.project.of_type(Component)[0]
        from puzzle.gui.tree import Node

        node = Node("weld", str(comp.id), "welds.0", "W", "weld:W")
        menu = menus.build_menu(w, node)
        texts = [a.text() for a in menu.actions()]
        assert "Model the bead" in texts and "Back to a light weld" not in texts
        fill(w, weld="W", how="modelled")
        weld_forms.weld_bead(w, comp)
        end = time.monotonic() + 30
        while w.tasks and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert asked == ["W"]
        comp = w.session.project.of_type(Component)[0]
        assert comp.welds[0].formulation == "modelled"
        texts = [a.text() for a in menus.build_menu(w, node).actions()]
        assert "Back to a light weld" in texts
        weld_forms.light_weld(w, comp, "W")  # a groove-less fillet with no bead part
        assert w.session.project.of_type(Component)[0].welds[0].formulation == "line"
    finally:
        _close(app, w)


def test_weld_bead_is_on_the_menu_bar(app, root):
    from puzzle.gui import main_menu

    w = _window(app, root)
    try:
        paths = main_menu.paths(w)
        assert "Model → Bolts and fasteners → Weld bead…" in paths
        assert "Results → Result tools → Thread results" in paths
        assert "Model → Run → Run shoot-out…" in paths
    finally:
        _close(app, w)


# --- the Threads tab (ADR-16) -----------------------------------------------------------


def test_the_threads_tab_shows_each_threads_share(app, root, monkeypatch):
    """A run's thread results as MCP `thread_results` gives them, in the Contact tool."""
    w = _window(app, root)
    try:
        loads = np.array([[600.0, 300.0, 100.0]])
        load = Field("thread_load", "global", ["Nut: 1", "Nut: 2", "Nut: 3"], np.array([0]),
                     loads)  # fmt: skip
        wedge = Field("thread_wedge", "global", ["Nut"], np.array([0]), np.array([[250.0]]))
        res = Results("calculix", [StepResult(1, "stationary", 1.0, fields={
            "thread_load": load, "thread_wedge": wedge})])  # fmt: skip
        record = SimpleNamespace(run_id="r1", status="completed", mesh_id="m1",
                                 recorded_at=__import__("datetime").datetime.now(
                                     __import__("datetime").UTC), study_name="Tip load",
                                 solver="calculix")  # fmt: skip

        class Store:
            def runs(self):
                return [record]

            def field_catalog(self, run_id):
                return [("thread_load", "global", 3)]

            def get_run(self, run_id):
                return record

            def read_mesh(self, mid):
                return None

            def read_results(self, run_id):
                return res

        monkeypatch.setattr(w, "_store", lambda: Store())
        w.contacts.show_threads()
        tab = w.contacts.threads
        assert w.contacts.tabs.currentWidget() is tab
        cells = [tab.table.item(0, c).text() for c in range(tab.table.columnCount())]
        assert cells[0] == "Nut" and cells[2] == "3" and cells[3] == "60%"
        assert cells[4] == "60%, 30%, 10%"
        assert "kN" in cells[1] or "N" in cells[1]
    finally:
        _close(app, w)
