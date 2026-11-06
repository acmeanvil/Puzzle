"""Imported fields in the GUI (ADR 0044): the wizard reads a file, places and checks it,
and adds the field only on Finish (one command); a feature then takes a value from it in
one form, and the tree says where the value comes from."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.field_wizard import FieldWizard  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model import (  # noqa: E402
    Component,
    Feature,
    ImportedField,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
)
from puzzle.model.schema import Geometry  # noqa: E402
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, condition, timeout=60.0):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("the GUI did not finish in time")
        time.sleep(0.02)


def _project(tmp_path):
    p = Project(name="fields")
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=1, unit="mm")))
    film = Feature(type="convection", selection="P/Face",
                   values={"h": Quantity(value=10, unit="W/(m^2*K)"),
                           "T_inf": Quantity(value=20, unit="degC")})  # fmt: skip
    p.add(Component(name="Box", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id,
                    physics=[Physics(type="heat_transfer", features=[film])]))  # fmt: skip
    save_project(p, tmp_path / "proj")
    data = tmp_path / "htc.csv"
    rows = ["x [mm],y [mm],z [mm],h [W/(m^2*K)],T [degC]"]
    rows += [f"{x},{y},0,{100 + x},{25}" for x in range(0, 50, 10) for y in range(0, 50, 10)]
    data.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path / "proj", data


def test_the_wizard_adds_the_field_on_finish_only(app, tmp_path):
    root, data = _project(tmp_path)
    window = MainWindow(root)
    wizard = FieldWizard(window)
    wizard.file.load(data)
    assert wizard.file.length.currentData() == "mm"
    assert [c["role"] for c in wizard.file.columns()] == ["x", "y", "z", "value", "value"]
    assert wizard.file.isComplete()
    wizard.align.move[2].setText("0.001")  # 1 mm up in z
    wizard.verify.check()  # the data alone: no target
    wait(app, lambda: wizard.verify.last is not None)
    assert wizard.verify.last["points"] == 25 and "h" in wizard.verify.last["columns"]
    assert window.session.project.of_type(ImportedField) == []  # nothing yet
    wizard.naming.name.setText("Film coefficients")
    wizard.finish()
    [f] = window.session.project.of_type(ImportedField)
    assert f.name == "Film coefficients" and f.placement.translation == (0.0, 0.0, 0.001)
    assert (root / f.file).exists() and f.value_columns() == ["h", "T"]
    assert window.session.history(limit=1)[0]["command"]["command"] == "create"


def test_a_feature_takes_a_value_from_a_field_in_one_form(app, tmp_path):
    from puzzle.gui import menus

    root, data = _project(tmp_path)
    window = MainWindow(root)
    wizard = FieldWizard(window)
    wizard.file.load(data)
    wizard.naming.name.setText("Film coefficients")
    wizard.finish()
    comp = window.session.project.of_type(Component)[0]
    [f] = window.session.project.of_type(ImportedField)
    fill(window, value="h", field=str(f.id), column="h", scale="1.5", offset="", mapping="nearest")
    menus.value_from_field(window, comp, 0, 0)
    comp = window.session.project.of_type(Component)[0]
    use = comp.physics[0].features[0].fields["h"]
    assert (use.column, use.scale, use.mapping) == ("h", 1.5, "nearest")
    texts = []

    def walk(item):
        texts.append(item.text(0))
        for k in range(item.childCount()):
            walk(item.child(k))

    for k in range(window.tree.topLevelItemCount()):
        walk(window.tree.topLevelItem(k))
    assert any(t.endswith("← Film coefficients ×1.5") for t in texts)
    assert "Imported fields" in texts and "Film coefficients" in texts
    menus.uniform_again(window, comp, 0, 0)
    assert not window.session.project.of_type(Component)[0].physics[0].features[0].fields
