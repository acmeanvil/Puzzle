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
    """Select a library material by key, wherever the grouped tree files it."""
    stack = [builder.tree.invisibleRootItem()]
    while stack:
        item = stack.pop()
        if item.data(0, 256) == key:
            builder.tree.setCurrentItem(item)
            return
        stack += [item.child(i) for i in range(item.childCount())]
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
    assert "(mine)" in b.tree.currentItem().text(0)
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


def test_the_library_is_grouped_by_type_group_and_alloy(app):
    """Headings to open (type, group, ...), with counts; a search opens what matches."""
    from puzzle.gui.material_builder import MaterialPicker

    p = MaterialPicker()
    tops = [p.tree.topLevelItem(i).text(0) for i in range(p.tree.topLevelItemCount())]
    assert tops[0].startswith("Metal  (") and any(t.startswith("Fluid") for t in tops)
    metal = p.tree.topLevelItem(0)
    assert metal.isExpanded() and not metal.child(0).isExpanded()
    groups = [metal.child(i).text(0) for i in range(metal.childCount())]
    assert any(g.startswith("Aluminum  (") for g in groups)
    p.search.setText("PTFE")
    hits = []
    stack = [p.tree.invisibleRootItem()]
    while stack:
        item = stack.pop()
        if item.data(0, 256):
            hits.append(item)
        stack += [item.child(i) for i in range(item.childCount())]
    assert [h.data(0, 256) for h in hits] == ["ptfe"] and hits[0].parent().isExpanded()
    p.tree.setCurrentItem(hits[0])
    p._accept()
    assert p.key == "ptfe"


def test_the_curve_builder_makes_a_hardening_table(app):
    """Ramberg-Osgood from E, yield and n (MPa in the dialog), used as the material's
    hardening curve; the ASME model refuses a class it doesn't fit."""
    from puzzle.gui.material_builder import CurveBuilder

    d = CurveBuilder(200e9, 250e6, 400e6)
    assert d.fields["modulus"].text() == "200000" and d.fields["yield"].text() == "250"
    d.fields["n"].setText("8")
    assert d.error.text() == "" and "n = 8" in d.info.text()
    table = d.result.table
    assert table[0][0] == 0 and table[0][1] > 250e6  # engineering yield read as true
    d.model.setCurrentIndex(d.model.findData("asme_3d"))
    d.asme_class.setCurrentIndex(d.asme_class.findData("ALUMINUM"))
    d.fields["yield"].setText("399")
    assert "below" in d.error.text() and d.result is None

    b = MaterialBuilder()
    choose(b, "structural_steel")
    b.editor.values["yield_strength"].setText("250")
    b.editor.values["tensile_strength"].setText("400")
    seen = {}

    def ask(modulus, yield_strength, ultimate):
        seen["args"] = (modulus, yield_strength, ultimate)
        c = CurveBuilder(modulus, yield_strength, ultimate)
        c.fields["n"].setText("8")
        return c.result

    b.editor.ask_curve = ask
    b.editor.build_curve()
    assert seen["args"] == (pytest.approx(210e9), pytest.approx(250e6), pytest.approx(400e6))
    assert b.editor.model_on["plasticity"].isChecked()
    assert "Ramberg-Osgood, n = 8" in b.editor.sources.toPlainText()
    m = b.editor.material()
    assert m.models.plasticity.curves[0].points[0][0] == 0.0


def test_fatigue_tab_holds_sn_curves_and_strain_life(app):
    """An S-N curve typed in MPa and a Langer model; saved and read back; Completeness
    says which life models the fatigue tool can use."""
    b = MaterialBuilder()
    choose(b, "structural_steel")
    e = b.editor
    e.ask_table = lambda *a: [(1e6, 200.0), (1e3, 400.0)]  # any order; sorted by cycles
    e.new_sn_curve()
    e.sn_fields["name"].setText("R=-1 polished")
    e._keep_sn()
    assert e.sn_pick.currentText() == "R=-1 polished  (2 points)"
    assert e.sn_plot.log and len(e.sn_plot.points) == 2
    assert report(b)[("Fatigue tool", "Strain life")] == "Needs a strain-life model (Fatigue tab)"
    e.sl_model.setCurrentIndex(e.sl_model.findData("langer"))
    for key, value in (("langer_a", "6.891"), ("langer_b", "1.920"), ("langer_c", "0.112")):
        e.sl_fields[key].setText(value)
    assert e.sl_fields["fatigue_strength"].isHidden() and not e.sl_fields["langer_a"].isHidden()
    assert len(e.sl_plot.points) > 10
    rows = report(b)
    assert rows[("Fatigue tool", "Strain life")] == "Complete"
    assert rows[("Fatigue tool", "Measured S-N curve")] == "Complete"
    key = b.save()
    m = library_material(key)
    (curve,) = m.fatigue.sn_curves
    assert curve.name == "R=-1 polished" and curve.points == [(1e3, 400e6), (1e6, 200e6)]
    assert m.fatigue.strain_life.langer_b == pytest.approx(1.92)
    choose(b, key)
    assert b.editor.sn_pick.count() == 1 and b.editor.sl_fields["langer_a"].text() == "6.891"
    e.ask_table = lambda *a: [(1e3, 400.0)]
    e.edit_sn_points()
    assert b.save() is None and "two points or more" in b.status.text()
