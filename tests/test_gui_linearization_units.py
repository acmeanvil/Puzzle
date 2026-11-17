"""The linearization panel's allowable across a change of "Stresses in" (ADR 0090
PANEL-1): switching the unit shows the stored allowable again in the new unit, and an
edit of another field writes that field alone. The field once kept the old unit's
number and the next edit read it in the new one: 138 MPa became 138 ksi (951 MPa)."""

import os
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from puzzle.gui.linearization_panel import LinearizationPanel  # noqa: E402
from puzzle.model.schema import ResultProbe  # noqa: E402
from puzzle.post.linearize import report  # noqa: E402

KSI = 6.894757293168361e6  # Pa


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _panel(allowable, unit):
    x = np.linspace(0.0, 0.01, 11)
    tensors = np.zeros((11, 6))
    tensors[:, 0] = 1e8 * (1 - 2 * x / 0.01)  # pure bending through the wall
    probe = ResultProbe(id=uuid.uuid4(), name="Wall", source=uuid.uuid4(), type="linearized",
                        quantity="stress", point=(0.0, 0.0, 0.0), end=(0.01, 0.0, 0.0),
                        samples=11, allowable=allowable)  # fmt: skip
    edits = []

    def submit(edit):  # as the session would: the tool takes the edit
        edits.append(edit)
        for op in edit.ops:
            setattr(probe, op.path, op.value)

    window = SimpleNamespace(
        session=SimpleNamespace(project=SimpleNamespace(objects={})),
        submit=submit, output_tabs=QTabWidget(), plots=None, linearization_unit=unit,
    )  # fmt: skip
    return LinearizationPanel(window, probe, report(x, tensors)), edits


def _ops(edits):
    return [(op.path, op.value) for e in edits for op in e.ops]


def test_switching_to_ksi_keeps_the_stored_allowable(app):
    panel, edits = _panel(138e6, "MPa")
    assert panel.allowable.text() == "138"
    panel.units.setCurrentIndex(panel.units.findData("ksi"))
    assert float(panel.allowable.text()) == pytest.approx(20.0152, abs=1e-4)
    panel.membrane.setCurrentIndex(panel.membrane.findData("local"))  # another field
    assert _ops(edits) == [("membrane", "local")]  # one op: the allowable untouched
    panel.allowable.editingFinished.emit()  # focus leaving the field: nothing typed
    assert len(_ops(edits)) == 1


def test_switching_to_mpa_keeps_the_stored_allowable(app):
    panel, edits = _panel(20 * KSI, "ksi")
    assert panel.allowable.text() == "20"
    panel.units.setCurrentIndex(panel.units.findData("MPa"))
    assert float(panel.allowable.text()) == pytest.approx(137.895, abs=1e-3)
    panel.equivalent.setCurrentIndex(panel.equivalent.findData("tresca"))
    assert _ops(edits) == [("equivalent", "tresca")]


def test_a_value_typed_then_the_unit_switched_is_kept_whole(app):
    panel, edits = _panel(138e6, "MPa")
    panel.allowable.setText("150")  # typed, not yet taken
    panel.units.setCurrentIndex(panel.units.findData("ksi"))
    assert float(panel.allowable.text()) == pytest.approx(150e6 / KSI, rel=1e-5)
    panel.edited()
    ((path, value),) = _ops(edits)
    assert path == "allowable" and value == pytest.approx(150e6, rel=1e-12)


def test_a_new_allowable_in_the_unit_shown(app):
    panel, edits = _panel(None, "ksi")
    assert panel.allowable.text() == ""
    panel.allowable.setText("20")
    panel.edited()
    ((path, value),) = _ops(edits)
    assert path == "allowable" and value == pytest.approx(20 * KSI)
