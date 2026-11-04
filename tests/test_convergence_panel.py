"""The Convergence panel (ADR 0066) on the stand-in study whose answer converges as
f∞ (1 − c h²): the meshes' table, the plot with the extrapolated line, the verdict, and
Run the rest offered only while a mesh is missing. No solver."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_convergence import Store, project, stand_in  # noqa: E402

from puzzle import convergence  # noqa: E402
from puzzle.gui.convergence_panel import ConvergencePanel  # noqa: E402
from puzzle.model.schema import Convergence  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_panel_shows_meshes_plot_and_verdict(app, tmp_path):
    p, study = project()
    study.convergence = Convergence(levels=3, ratio=1.5, target=0.2)
    store = Store()
    window = SimpleNamespace(session=SimpleNamespace(project=p, root=tmp_path),
                             _store=lambda: store)  # fmt: skip
    panel = ConvergencePanel(window)
    panel.show(str(study.id))
    assert panel.run_button.isEnabled()  # nothing run yet
    assert "0 of 3 meshes run" in panel.summary.text()
    convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=3, ratio=1.5,
                    solve=stand_in(store))  # fmt: skip
    panel.refresh()
    heads = [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())]
    assert heads == ["Mesh", "Elements", "Nodes", "Largest total deformation"]
    assert [panel.table.item(r, 0).text() for r in range(3)] == ["6 mm", "4 mm", "2.667 mm"]
    assert panel.table.item(0, 1).text() == "1000"
    assert panel.table.item(0, 3).text().endswith(" m")  # SI by default: a length
    names = [name for name, _ in panel.plot.series]
    assert names == ["Largest total deformation", "Largest total deformation: extrapolated"]
    assert "converged within" in panel.summary.text()
    assert not panel.run_button.isEnabled()


def test_the_error_estimate_and_convergence_forms(app, tmp_path, monkeypatch):
    from forms import fill

    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import ResultDefinition, Study
    from puzzle.project import save_project

    p, _ = project()
    save_project(p, tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    try:
        study = next(iter(w.session.project.of_type(Study)))
        fill(w, source=str(study.id), target="5", name="How far off")
        w.new_derived("error_estimate", str(study.id))
        (d,) = w.session.project.of_type(ResultDefinition)
        assert d.method == "error_estimate" and d.error_target == 0.05 and d.quantity is None
        box = w.last_form.widgets["quantity"]
        offered = [box.itemText(i) for i in range(box.count())]
        assert offered == ["Displacement (structural) or temperature (heat)"]  # no run yet
        fill(w, target="10")
        w.new_derived("error_estimate", definition=d)
        (d,) = w.session.project.of_type(ResultDefinition)
        assert d.error_target == 0.1
        fill(w, levels=4, ratio=2.0, target="1")
        from puzzle.gui import convergence_panel

        ran = []
        monkeypatch.setattr(convergence_panel, "run", lambda window, sid: ran.append(sid))
        convergence_panel.check(w, str(study.id))
        assert ran == [str(study.id)]
        study = next(iter(w.session.project.of_type(Study)))
        assert (study.convergence.levels, study.convergence.ratio) == (4, 2.0)
        assert study.convergence.target == 0.01
    finally:
        w.close()
