"""ADR 0036: long editors as a property grid in folding sections, a description box
for the focused row, and the blocking message on top."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QTableWidget,
)

from puzzle.gui.form import DESCRIPTIONS, FormPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_rows_sections_descriptions_and_message(app):
    form = FormPanel("What this form does.")
    how = form.section("Stress and life")
    life = how.row("design_life", "Design life", QLineEdit("1e6"), "duties")
    box = how.row("surface", "Surface finish", QComboBox())
    table = how.add_widget(QTableWidget(0, 2))
    assert box.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents

    assert form.description.text() == "What this form does."
    app.sendEvent(life, QEvent(QEvent.Type.FocusIn))
    assert form.description.text() == DESCRIPTIONS["design_life"]
    assert life.toolTip() == DESCRIPTIONS["design_life"]

    form.set_visible("design_life", False)
    name, field, unit = form.rows["design_life"]
    assert name.isHidden() and field.isHidden() and unit.isHidden()

    form.set_message("Steel needs its tensile strength", error=True)
    assert form.message.isVisibleTo(form) and "tensile" in form.message.text()
    form.set_message(None)
    assert not form.message.isVisibleTo(form)

    how.set_open(False)
    assert not how.body.isVisibleTo(form)


def test_the_fatigue_dialog_fits_and_folds_the_notch(app):
    from puzzle.gui.derived_dialog import FatigueDialog
    from puzzle.model.schema import Project

    class NoRuns:
        def runs(self):
            return []

    d = FatigueDialog(Project("p"), NoRuns())
    screen = d.screen().availableGeometry()
    assert d.height() <= screen.height() * 0.85 + 1
    sections = [s.header.text() for s in d.form.sections]
    assert sections[0] == "Fatigue tool" and sections[-1].startswith("Notch")
    assert not d.form.sections[-1].body.isVisibleTo(d)  # Kf rarely applies: folded
    assert d.status is d.form.message and "No study has been run" in d.status.text()
