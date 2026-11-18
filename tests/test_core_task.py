"""The core task, counted (ADR 0090 package 11): a STEP file to a probe and an export,
through the GUI's own actions, as a user would do it. `gui-usability.md` counted 16
steps; each step here is one thing a user does (a toolbar button, a form, a pick), and
the test fails if a step needs more than its one form.

1. New project (toolbar New…: one form)
2. Import geometry (toolbar Import geometry…: the file)
3. New analysis (the import's note, or the toolbar: one form)
4. Pick the fixed face, Add load or support… in the pick bar (one form)
5. Pick the loaded face, Add load or support…, the force in the same form
6. Run (toolbar; the only study: nothing asked)
7. Probe: double-click the part where the result is wanted (results show on their own)
8. Export… on the Results ribbon (one form, then where to save)
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from conftest_geometry import write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.geometry import import_step  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Study  # noqa: E402

STEPS = 8  # the count the review asked to be under ten


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(app, window, seconds: float = 600) -> None:
    end = time.monotonic() + seconds
    while window.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def _pick_face(window, comp, name: str) -> None:
    """What Pick and a double-click on that face leave: the face picked in the view."""
    names = window.mesh_names[str(comp.id)]
    entity = next(e for e, (part, n) in names.items() if e[0] == 2 and n == name)
    window.toggle_picking(True)
    window.viewport.set_pick_kind("face")
    window.viewport.pick([entity], "replace")


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_a_step_file_to_a_probe_and_an_export_in_eight_steps(app, tmp_path):
    steps = []
    window = MainWindow()
    try:
        # 1. New project
        fill(window, folder=str(tmp_path / "beam"), name="Beam", units="engineering")
        window.actions_by_name["new"].trigger()
        steps.append("new project")
        assert window.session is not None

        # 2. Import geometry (the file dialog answered)
        step = write_bar_step(tmp_path / "bar.step")
        window.drop_cad([step])  # the same as Import geometry… with the file chosen
        steps.append("import geometry")
        note = window.toasts.shown[-1]
        assert note.action == "New analysis…"

        # 3. New analysis: material, mesh, physics and study in one form
        fill(window, geometry="bar", analysis="static", material="structural_steel",
             mesh="medium")  # fmt: skip
        window.actions_by_name["new_analysis"].trigger()
        steps.append("new analysis")
        (comp,) = window.session.project.of_type(Component)
        assert window.tree.current().object == str(comp.id)
        _settle(app, window)  # its surface preview, to pick on
        assert str(comp.id) in window.mesh_names, "the component is shown to pick on"

        model = import_step(window.session.root / "bar.step")
        (root,) = model.faces_on_plane("x", 0.0)
        (tip,) = model.faces_on_plane("x", 0.2)

        # 4. The fixed support on the picked face
        _pick_face(window, comp, root)
        fill(window, type="fixed", selection="\x00picked")
        window.add_load_on_picks()
        steps.append("fixed support")

        # 5. The force on the picked face, its value in the same form
        _pick_face(window, comp, tip)
        fill(window, type="force", selection="\x00picked", **{"force:y": "-100 N"})
        window.add_load_on_picks()
        steps.append("force")
        assert window.session.project.problems() == []

        # 6. Run
        window.actions_by_name["run"].trigger()
        steps.append("run")
        _settle(app, window)
        assert window.viewport.shown.get("kind") == "result", window.log.toPlainText()[-2000:]

        # 7. Probe at the tip (the view shows the deformation once the run ends)
        found = window.viewport.probe_undeformed([0.2, 0.005, 0.005])
        steps.append("probe")
        E, L, I = 210e9, 0.2, 0.01**4 / 12  # noqa: E741 - Euler-Bernoulli F L^3 / 3 E I
        assert found["shown"] == pytest.approx(1e3 * 100 * L**3 / (3 * E * I), rel=0.05)

        # 8. Export
        assert window.results_ribbon.isVisibleTo(window)
        out = tmp_path / "beam.pvd"
        window.ask_save_path = lambda *a: str(out)
        fill(window, format="paraview")
        window.results_ribbon.buttons["export"].click()
        steps.append("export")
        assert out.exists()

        assert len(steps) == STEPS < 10
        (study,) = window.session.project.of_type(Study)
        assert study.name == "bar static structural"
    finally:
        while window.tasks:
            app.processEvents()
        window.close()
