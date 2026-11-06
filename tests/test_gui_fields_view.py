"""Imported fields' connections in the GUI (ADR 0044 decisions 2.2, 2.3 and 8): the
wizard's alignment helpers set the placement, its verification draws the field on the
target, selecting a field-fed feature or the field colours the mapped values (with the
extrapolated nodes counted), Settings > Connections switches it off, and Properties
lists a field's uses and a feature's chain in words."""

import os
import re
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from conftest_geometry import BAR, bar_project, write_bar_step  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.field_wizard import FieldWizard  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Feature,
    FieldColumn,
    FieldUse,
    ImportedField,
    Physics,
    Quantity,
)
from puzzle.project import save_project  # noqa: E402

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(window, condition=lambda: True, timeout=120.0):
    end = time.monotonic() + timeout
    app = QApplication.instance()
    while window.tasks or not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("the GUI did not finish in time")
        time.sleep(0.02)
    app.processEvents()


def _film_csv(path, shift=(0.0, 0.0, 0.0), half=True):
    """Film coefficients on the bar's tip (x = 200 mm), in mm: over half its height
    only when `half`, so the other half is extrapolated."""
    rows = ["x [mm],y [mm],z [mm],h [W/(m^2*K)]"]
    zs = np.linspace(0, 5 if half else 10, 6)
    for y in np.linspace(0, 10, 6):
        for z in zs:
            p = np.array([200.0, y, z]) + np.asarray(shift) * 1e3
            rows.append(f"{p[0]:.6f},{p[1]:.6f},{p[2]:.6f},{100 + 10 * y:.3f}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _project(tmp_path, used=True):
    root = tmp_path / "bar"
    root.mkdir()
    step = write_bar_step(root / "bar.step")
    film = Feature(type="convection", selection="Bar/Tip",
                   values={"h": Quantity(value=10, unit="W/(m^2*K)"),
                           "T_inf": Quantity(value=20, unit="degC")})  # fmt: skip
    held = Feature(type="temperature", selection="Bar/Root",
                   values={"T": Quantity(value=100, unit="degC")})  # fmt: skip
    project, comp = bar_project(step, [Physics(type="heat_transfer", features=[held, film])],
                                mesh_size_mm=5.0)  # fmt: skip
    (root / "fields").mkdir()
    _film_csv(root / "fields" / "film.csv")
    f = project.add(ImportedField(
        name="CFD film", file="fields/film.csv", length_unit="mm",
        columns=[FieldColumn(name=n, role=n) for n in "xyz"]
        + [FieldColumn(name="h", role="value", unit="W/(m^2*K)")],
    ))  # fmt: skip
    if used:
        film.fields["h"] = FieldUse(field=f.id, column="h", scale=1.5)
    save_project(project, root)
    return root


def _select(window, label):
    for item in window.tree._items():
        if item.data(0, ROLE).label == label or item.text(0) == label:
            window.tree.setCurrentItem(item)
            return item
    raise AssertionError(f"no {label} in the tree")


def _item(window, text_start):
    for item in window.tree._items():
        if item.text(0).startswith(text_start):
            return item
    raise AssertionError(f"no item starting {text_start!r}")


def _rows(window) -> dict[str, dict[str, str]]:
    """The properties panel's top-level groups and their rows."""
    panel, out = window.properties, {}
    for i in range(panel.topLevelItemCount()):
        item = panel.topLevelItem(i)
        out[item.text(0)] = {item.child(k).text(0): item.child(k).text(1)
                             for k in range(item.childCount())}  # fmt: skip
    return out


def _meshed(app, root):
    window = MainWindow(root)
    comp = window.session.project.of_type(Component)[0]
    window.start_meshing(comp)
    settle(window, lambda: str(comp.id) in window.meshes)
    return window, comp


def test_selecting_a_field_fed_feature_draws_its_mapped_values(app, tmp_path):
    root = _project(tmp_path)
    window, _ = _meshed(app, root)
    window.tree.setCurrentItem(_item(window, "Convection on Bar/Tip"))
    settle(window, lambda: "nodes" in window.viewport.shown.get("field_values", {}))
    drawn = window.viewport.shown["field_values"]
    assert drawn["selection"] == "Bar/Tip" and drawn["nodes"] > 6
    # Half the tip has no data: those nodes are extrapolated (counted, marked).
    assert 0 < drawn["extrapolated"] < drawn["nodes"]
    assert drawn["min"] == pytest.approx(150, rel=1e-6)  # ×1.5 of 100 at y = 0
    assert drawn["max"] == pytest.approx(300, rel=1e-6)  # ×1.5 of 200 at y = 10 mm
    # Properties: the chain from the file to the solver, in words.
    chain = _rows(window)["Where h comes from"]
    assert chain["File"].startswith("fields/film.csv")
    assert chain["Imported field"].startswith("CFD film")
    assert chain["Scaling"] == "×1.5"
    assert chain["Mapping"] == "linear between the points"
    assert "CalculiX" in chain["Solvers"]
    assert not any(re.search(UUID, v) for v in chain.values())

    # Selecting the field itself: its uses listed, and the same values drawn.
    window.viewport.shown.pop("field_values", None)
    _select(window, "CFD film")
    settle(window, lambda: "nodes" in window.viewport.shown.get("field_values", {}))
    used = _rows(window)["Used by"]
    [(where, how)] = used.items()
    assert where == "Bar: Convection on Bar/Tip (h)"
    assert how == "column h, ×1.5, linear between the points"
    assert window.viewport.shown["field_values"]["extrapolated"] > 0


def test_settings_switch_the_connections_off(app, tmp_path):
    from puzzle.settings import current

    root = _project(tmp_path)
    current().set("connections.view", False)
    current().set("connections.tree", False)
    window, _ = _meshed(app, root)
    assert not any("←" in i.text(0) for i in window.tree._items())  # no source named
    window.tree.setCurrentItem(_item(window, "Convection on Bar/Tip"))
    settle(window)
    assert "field_values" not in window.viewport.shown
    assert "Where h comes from" not in _rows(window)
    current().set("connections.tree", True)
    window.refresh()
    assert any(i.text(0).endswith("← CFD film ×1.5") for i in window.tree._items())


def test_an_unused_field_shows_its_points_and_says_so(app, tmp_path):
    root = _project(tmp_path, used=False)
    window, _ = _meshed(app, root)
    _select(window, "CFD film")
    settle(window, lambda: "field_points" in window.viewport.shown)
    assert window.viewport.shown["field_points"] == 36
    assert "Not used yet" in _rows(window)["Used by"]


def test_the_wizards_helpers_set_the_placement(app, tmp_path):
    root = _project(tmp_path, used=False)
    window, comp = _meshed(app, root)
    data = _film_csv(tmp_path / "moved.csv", shift=(0.05, -0.02, 0.01), half=False)
    wizard = FieldWizard(window)
    wizard.file.load(data)
    helpers = wizard.align.helpers
    helpers.target.setCurrentIndex(helpers.target.findData("Bar/Bar/Tip"))
    wizard.align.initializePage()
    assert window.viewport.shown["field_points"] == 36  # drawn over the model

    # Bounding boxes: the data's centre onto the tip's.
    helpers.match_boxes()
    settle(window)
    move = [float(e.text()) for e in wizard.align.move]
    assert np.allclose(move, (-0.05, 0.02, -0.01), atol=1e-9)

    # Point pairs: three typed pairs, each data corner and the tip's corner it belongs at.
    for e in (*wizard.align.move, *wizard.align.turn):
        e.setText("0")
    for model in ((0.2, 0.0, 0.0), (0.2, 0.01, 0.0), (0.2, 0.0, 0.01)):
        helpers.add_pair(np.add(model, (0.05, -0.02, 0.01)), model)
    fit = helpers.from_pairs()
    assert fit.rms < 1e-12
    move = [float(e.text()) for e in wizard.align.move]
    assert np.allclose(move, (-0.05, 0.02, -0.01), atol=1e-9)
    assert np.allclose([float(e.text()) for e in wizard.align.turn], 0, atol=1e-9)

    # A best fit from 1.5 mm off the face and tilted 2° about y: back onto the tip's
    # face (x = 200 mm). A flat face holds only that: sliding along it is left alone.
    wizard.align.move[0].setText("-0.0485")
    wizard.align.turn[1].setText("2")
    wizard.align.about[0].setText("0.25")
    found = []
    helpers.best_fit(then=found.append)
    settle(window, lambda: found)
    # Turning about another point than the one tilted about slides the points along the
    # face a little (9 µm), which a flat face can't see: only the few past its edge
    # are off it.
    assert found[0].before > 1e-3 and found[0].rms < 2e-5
    placed = helpers.placed()
    assert np.abs(placed[:, 0] - 0.2).max() < 1e-9  # every point on the face
    assert float(wizard.align.turn[1].text()) == pytest.approx(0, abs=1e-6)

    # Picking a pair in the view: a data point, then the model point (the callbacks a
    # double-click would call).
    rows = helpers.pairs.rowCount()
    helpers.pick_pair()
    assert window.viewport.shown.get("capturing")
    helpers._data_picked((0.2, 0.0, 0.0))
    helpers._model_picked((0.2, 0.0, 0.0))
    assert helpers.pairs.rowCount() == rows + 1

    # Verification: the field mapped and coloured on the target, nothing extrapolated.
    wizard.verify.initializePage()
    assert wizard.verify.target.currentData() == "Bar/Bar/Tip"
    wizard.verify.check()
    settle(window, lambda: wizard.verify.view.last is not None)
    drawn = window.viewport.shown["field_values"]
    assert drawn["extrapolated"] == 0 and drawn["nodes"] > 6
    assert wizard.verify.last["coverage"] == pytest.approx(1.0)
    assert BAR[0] == 0.2
