"""The material builder in the GUI (ADR 0020): browse, make, vary and check materials."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.material_builder import MaterialBuilder  # noqa: E402
from puzzle.materials import library_keys, library_material  # noqa: E402
from puzzle.materials.store import LibraryStore  # noqa: E402

# Values typed in MPa and mm (Settings > Units: engineering).
pytestmark = pytest.mark.usefixtures("engineering_units")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def choose(builder, key):
    for i in range(builder.tree.topLevelItemCount()):
        top = builder.tree.topLevelItem(i)
        for item in [top, *(top.child(j) for j in range(top.childCount()))]:
            if item.data(0, 256) == key:
                builder.tree.setCurrentItem(item)
                return
    raise LookupError(key)


def report(builder):
    t = builder.editor.report
    return {
        (t.item(r, 0).text(), t.item(r, 1).text()): t.item(r, 2).text() for r in range(t.rowCount())
    }


def test_a_library_material_shows_in_display_units_with_its_completeness(app):
    b = MaterialBuilder()
    choose(b, "structural_steel")
    e = b.editor
    assert (
        e.values["youngs_modulus"].text() == "210000" and e.units["youngs_modulus"].text() == "MPa"
    )
    assert e.table_buttons["youngs_modulus"].text().startswith("Table (")
    rows = report(b)
    assert rows[("CalculiX", "Static structural")] == "Complete"
    assert rows[("Code_Saturne", "Steady flow")] == "Needs dynamic viscosity"


def test_make_a_plastic_material_with_a_table_and_a_variant(app):
    b = MaterialBuilder()
    b.new()
    e = b.editor
    e.name.setText("Bilinear steel")
    e.values["youngs_modulus"].setText("200000")
    e.values["poissons_ratio"].setText("0.3")
    assert report(b)[("CalculiX", "Modal")] == "Needs density"
    e.values["density"].setText("7850")
    # A conductivity table typed in °C; stored in kelvin.
    e.ask_table = lambda *a: [(20.0, 50.0), (400.0, 40.0)]
    e.edit_table("thermal_conductivity")
    assert e.table_buttons["thermal_conductivity"].text() == "Table (2)…"
    # A hardening curve in MPa: yield at 250, then 300 at 10 % plastic strain.
    e.ask_table = lambda *a: [(0.0, 250.0), (0.1, 300.0)]
    e.edit_curve()
    assert e.model_on["plasticity"].isChecked()
    assert "Yield 250 MPa" in e.curve_list.text()
    rows = report(b)
    assert rows[("CalculiX", "Static structural")] == "Complete"  # it writes plasticity
    assert rows[("Elmer", "Static structural")] == "Refuses plasticity (isotropic hardening)"
    key = b.save()
    assert key == "bilinear_steel" and key in library_keys()
    m = library_material(key)
    assert m.models.plasticity.curves[0].points == [(0.0, 250e6), (0.1, 300e6)]
    assert m.tables["thermal_conductivity"].curve()[0] == pytest.approx((293.15, 50.0))

    # A variant: only its hardening differs, and only that is stored for it.
    b.new_variant()
    e.name.setText("Bilinear steel, hardened")
    e.ask_table = lambda *a: [(0.0, 400.0), (0.05, 450.0)]
    e.edit_curve()
    vkey = b.save()
    stored = LibraryStore().entries()[vkey]
    assert stored["parent"] == "bilinear_steel"
    assert set(stored) <= {"name", "parent", "models", "modified"}
    v = library_material(vkey)
    assert v.properties["youngs_modulus"].to_si() == pytest.approx(200e9)  # inherited
    assert v.models.plasticity.curves[0].points[0] == (0.0, 400e6)


def test_editing_a_shipped_material_saves_your_copy(app):
    b = MaterialBuilder()
    choose(b, "structural_steel")
    b.editor.values["youngs_modulus"].setText("205000")
    assert b.save() == "structural_steel"
    assert library_material("structural_steel").properties["youngs_modulus"].to_si() == 205e9
    assert any(
        "(mine)" in b.tree.topLevelItem(i).text(0) for i in range(b.tree.topLevelItemCount())
    )
    b.remove()
    assert library_material("structural_steel").properties["youngs_modulus"].to_si() == 210e9
    b.undo()
    assert library_material("structural_steel").properties["youngs_modulus"].to_si() == 205e9


def test_mistakes_are_said_in_words(app):
    b = MaterialBuilder()
    b.new()
    b.editor.values["density"].setText("heavy")
    assert "Density: 'heavy' is not a number" in b.status.text()
    assert b.save() is None
    b.editor.values["density"].setText("7850")
    b.editor.model_on["plasticity"].setChecked(True)
    assert b.save() is None and "hardening curve" in b.status.text()


def test_add_a_built_material_to_the_project(app, tmp_path):
    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import Material

    w = MainWindow()
    try:
        w.new_project(tmp_path / "p", "P")
        b = MaterialBuilder(w)
        choose(b, "copper")
        b.add_to_project()
        (m,) = w.session.project.of_type(Material)
        assert m.name == "Copper" and m.properties["thermal_conductivity"].value > 300
        assert w.session.history(limit=1)[0]["command"]["command"] == "create"  # one command
    finally:
        w.close()
