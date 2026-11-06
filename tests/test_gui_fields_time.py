"""The Import field wizard's File page for other tools' formats and time (ADR 0044): an
OpenFOAM case lists its patches and times by name and varies over every step; two CSV
files with their times become one field varying in time, both copied in on Finish."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.field_wizard import FieldWizard  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model import ImportedField, Project  # noqa: E402
from puzzle.project import save_project  # noqa: E402

DATA = Path(__file__).parent / "data" / "fields"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(tmp_path):
    save_project(Project(name="fields"), tmp_path / "proj")
    return MainWindow(tmp_path / "proj")


def _items(box):
    return [box.itemText(i) for i in range(box.count())]


def test_an_openfoam_case_varies_over_its_times(app, tmp_path):
    window = _window(tmp_path)
    wizard = FieldWizard(window)
    wizard.file.load(DATA / "channel")
    assert _items(wizard.file.region) == ["The cells", "inlet", "outlet", "walls"]
    assert _items(wizard.file.step) == ["The last", "0: 0", "1: 1"]
    assert wizard.file.isComplete()  # the points come from the case, not columns
    wizard.file.timing.setCurrentIndex(wizard.file.timing.findData("every_step"))
    wizard.naming.name.setText("Channel")
    wizard.finish()
    [f] = window.session.project.of_type(ImportedField)
    assert f.format == "openfoam" and [(t.time, t.index) for t in f.times] == [(0, "0"), (1, "1")]
    assert (window.session.root / f.file / "constant" / "polyMesh" / "points").exists()


def test_two_files_with_their_times(app, tmp_path):
    for t in (0, 10):
        (tmp_path / f"t{t}.csv").write_text(
            "x,y,z,T [K]\n0,0,0,300\n1,0,0,400\n0,1,0,300\n", encoding="utf-8"
        )
    window = _window(tmp_path)
    wizard = FieldWizard(window)
    wizard.file.load(tmp_path / "t0.csv")
    wizard.file.set_more([tmp_path / "t10.csv"])
    wizard.file.times.setText("0, 10")
    wizard.finish()
    [f] = window.session.project.of_type(ImportedField)
    assert [(t.time, t.file) for t in f.times] == [(0, "fields/t0.csv"), (10, "fields/t10.csv")]
    assert all(len(t.file_hash) == 64 for t in f.times) and f.varies()
