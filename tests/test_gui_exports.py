"""Export… in the GUI (ADR 0043): one form, fields for the chosen format only, then
where to save. A point cloud of the tip's deflection and the deformed shape as STEP."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.gui.export_dialog import export_results  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_export_form(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    physics = Physics(
        type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"), force]
    )
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=5.0)
    study = project.add(
        Study(
            name="Bend", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")]
        )
    )
    save_project(project, root)
    run_study(project, study.id, root, tmp_path / "w", store=ResultStore(root / "results"))
    window = MainWindow(root)
    try:
        window.ask_save_path = lambda *a: str(tmp_path / "out" / "tip")
        fill(window, format="point_cloud", quantity="displacement:y", selection="Bar/Tip")
        files = export_results(window, str(study.id))
        form = window.last_form
        assert form.form.isRowVisible(form.widgets["quantity"])
        assert not form.form.isRowVisible(form.widgets["scale"])  # a shape's only
        assert files[0].name == "tip.csv" and files[-1].name == "tip.txt"
        fill(window, format="deformed_step", scale="10", compensate=True)
        files = export_results(window, str(study.id))
        assert files[0].suffix == ".step" and "x 10 (exaggerated)" in files[-1].read_text()
        assert "compensated" in files[-1].read_text()
        fill(window, format="deformed_stl", scale="0")
        assert export_results(window, str(study.id)) is None
        assert "above zero" in window.last_form.problem()
    finally:
        window.close()
