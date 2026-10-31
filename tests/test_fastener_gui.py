"""Beam fasteners in the model and the GUI (ADR 0027); the solver benchmarks are in
tests/integration/test_fasteners.py.

Two 10 mm plates, each with an 8 mm hole, the upper one placed over the lower so the
holes line up: "Fasteners from holes…" finds the pair and makes a hole selection on
each plate and one fastener, in one batch. The tree lists it, the outline names it,
the view draws it as a tube, Elmer refuses it, and Add fastener… makes one by hand.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.adapters.elmer import ElmerAdapter  # noqa: E402
from puzzle.commands.outline import outline  # noqa: E402
from puzzle.gui.fastener_dialog import add_fastener, fasteners_from_holes  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _plates(tmp_path):
    from puzzle.materials import add_library_material

    p = Project(name="plates")
    geo = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(kind="box", name="P", args={"dx": 0.04, "dy": 0.04, "dz": 0.01}),
                HistoryStep(kind="hole", name="H", args={"r": 0.004, "x": 0.02, "y": 0.02}),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                NamedSelection(name="Top", entity="face", members=["P/+z"]),
                NamedSelection(name="Bottom", entity="face", members=["P/-z"]),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=4, unit="mm")))
    comp = p.add(
        Component(
            name="Joint",
            parts=[
                PartInstance(name="Lower", geometry=geo.id),
                PartInstance(name="Upper", geometry=geo.id, translation=(0.0, 0.0, 0.015)),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                for n in ("Lower", "Upper")
            ],
        )
    )
    p.add(Study(name="S", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")]))
    root = tmp_path / "plates"
    save_project(p, root)
    return root


def test_fasteners_from_holes(app, tmp_path):
    window = MainWindow(_plates(tmp_path))
    window.run_dialog = lambda dialog: True
    comp = window.session.project.of_type(Component)[0]
    fasteners_from_holes(window, comp)
    project = window.session.project
    comp = project.of_type(Component)[0]
    (bolt,) = comp.connectors
    assert bolt.name == "Bolt 1" and bolt.diameter.value == pytest.approx(8.0)
    assert {bolt.source, bolt.target} == {"Lower/Hole 1", "Upper/Hole 1"}
    plate = project.of_type(Geometry)[0]
    assert [s.members for s in plate.selections if s.name == "Hole 1"] == [["H/wall"]]
    assert window.session.history(limit=1)[0]["command"]["command"] == "batch"
    labels = [i.text(0) for i in window.tree._items()]
    assert any(t.startswith("Fastener Bolt 1 (Ø 8 mm)") for t in labels)
    assert "fastener Bolt 1: " in outline(project)
    study = project.of_type(Study)[0]
    assert any("beam fasteners" in p for p in ElmerAdapter().check(project, study))

    def by_hand(dialog):
        dialog.name.setText("Pin")
        dialog.diameter.setText("0.006")
        dialog.pretension.setText("2000")
        return True

    window.run_dialog = by_hand
    add_fastener(window, comp)
    names = [c.name for c in window.session.project.of_type(Component)[0].connectors]
    assert names == ["Bolt 1", "Pin"]
    pin = window.session.project.of_type(Component)[0].connectors[1]
    assert pin.pretension.to_si() == pytest.approx(2000.0)
    window.close()


def test_fasteners_drawn_as_tubes(tmp_path):
    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import BeamFastener
    from puzzle.post.symbols import symbols

    root = _plates(tmp_path)
    from puzzle.project import load_project

    project = load_project(root)
    comp = project.of_type(Component)[0]
    comp.connectors.append(
        BeamFastener(
            name="Bolt",
            source="Lower/Top",
            target="Upper/Top",
            diameter=Quantity(value=6, unit="mm"),
            material=comp.materials[0].material,
        )
    )
    mesh = generate_mesh(project, comp.id, root, tmp_path / "m").mesh
    (tube,) = [g for g in symbols(comp, mesh, lambda s: []) if g.kind == "tube"]
    assert tube.radius == pytest.approx(0.003)
    assert tube.points[1][2] - tube.points[0][2] == pytest.approx(0.015, abs=1e-6)


def test_fastener_forces_over_results(app, tmp_path):
    """A run's fasteners are labelled with their axial force over its results."""
    from solvers import available

    if not available("calculix"):
        pytest.skip("needs CalculiX")
    from puzzle.model.schema import BeamFastener, Feature, Physics

    root = _plates(tmp_path)
    from puzzle.project import load_project

    project = load_project(root)
    comp = project.of_type(Component)[0]
    comp.connectors.append(
        BeamFastener(
            name="Bolt",
            source="Lower/Top",
            target="Upper/Top",
            diameter=Quantity(value=6, unit="mm"),
            material=comp.materials[0].material,
        )
    )
    comp.physics = [
        Physics(
            type="solid_mechanics",
            features=[
                Feature(type="fixed", selection="Lower/Bottom"),
                Feature(
                    type="gravity",
                    selection="Upper/Body",
                    values={"gz": Quantity(value=-9.81e3, unit="m/s^2")},
                ),
            ],
        )
    ]
    study = project.of_type(Study)[0]
    study.solver = "calculix"
    save_project(project, root)
    window = MainWindow(root)
    from puzzle.commands.model import RunStudy

    run = window.session.submit(RunStudy(study=str(study.id))).run
    window._select_run(run.run_id)
    (label,) = window.viewport.shown["fasteners"]
    assert label.startswith("Bolt: -") and label.endswith("compression")  # it holds up the plate
    window.close()


def test_bolt_types_from_the_menu_and_typed_fasteners(app, tmp_path):
    """ADR 0050: New bolt type… copies a library grade in with the type (one undo), the
    tree lists it, and a fastener of that type takes its size, grade and preload."""
    from forms import fill

    from puzzle import bolts
    from puzzle.gui.bolt_types import new_bolt_type
    from puzzle.model.schema import BoltType, Material

    window = MainWindow(_plates(tmp_path))
    fill(window, standard="iso_coarse", size="M8", material="library:iso_898_1_class_10_9",
         preload="torque", torque="25", nut_factor="0.2")  # fmt: skip
    new_bolt_type(window)
    project = window.session.project
    (btype,) = project.of_type(BoltType)
    assert btype.name == "M8 10.9" and btype.diameter.to_si() == pytest.approx(0.008)
    assert project.objects[btype.material].name == "ISO 898-1 Class 10.9"
    assert window.session.history(limit=1)[0]["command"]["command"] == "batch"
    labels = [i.text(0) for i in window.tree._items()]
    assert "Bolt types" in labels and "M8 10.9" in labels
    box = window.last_form.widgets["material"]
    offered = [box.itemText(i) for i in range(box.count())]
    assert "ISO 898-1 Class 8.8 (from the library)" in offered

    def typed(dialog):
        dialog.btype.setCurrentIndex(dialog.btype.findData(str(btype.id)))
        assert dialog.diameter.text() == "" and dialog.material.currentData() is None
        return True

    window.run_dialog = typed
    comp = project.of_type(Component)[0]
    add_fastener(window, comp)
    bolt = window.session.project.of_type(Component)[0].connectors[-1]
    assert bolt.type == btype.id and bolt.diameter is None and bolt.material is None
    solved = bolts.resolve(window.session.project, bolt)
    assert solved.diameter.to_si() == pytest.approx(0.008)
    assert solved.pretension.to_si() == pytest.approx(25 / (0.2 * 0.008))
    assert any(t.startswith("Fastener Bolt 1 (M8 10.9)") for t in
               [i.text(0) for i in window.tree._items()])  # fmt: skip
    assert len(window.session.project.of_type(Material)) == 2
    window.close()


def test_the_bolt_table(app, tmp_path):
    """ADR 0050: the bolt table lists every bolt with its type's values faded, edits
    several rows as one undo, exports CSV, and its Results tab gives each bolt's loads
    and its use of its grade's proof and yield, worst first, against a hand calculation."""
    import csv
    import math

    from solvers import available

    from puzzle.commands.model import RunStudy
    from puzzle.gui.bolt_table import COLUMNS
    from puzzle.materials import add_library_material
    from puzzle.model.schema import BeamFastener, BoltType, Feature, Physics
    from puzzle.project import load_project

    root = _plates(tmp_path)
    project = load_project(root)
    plate = project.of_type(Geometry)[0]  # a second hole, so the bolts share no nodes
    plate.history[1].args.update({"x": 0.01, "y": 0.02})
    plate.history.append(
        HistoryStep(kind="hole", name="K", args={"r": 0.004, "x": 0.03, "y": 0.02})
    )
    plate.selections += [
        NamedSelection(name="Hole 1", entity="face", members=["H/wall"]),
        NamedSelection(name="Hole 2", entity="face", members=["K/wall"]),
    ]
    comp = project.of_type(Component)[0]
    grade = add_library_material(project, "iso_898_1_class_8_8")
    btype = project.add(BoltType(name="M8 8.8", size="M8", material=grade.id, preload="none"))
    comp.connectors += [
        BeamFastener(name="Pin", source="Lower/Hole 1", target="Upper/Hole 1",
                     diameter=Quantity(value=6, unit="mm"), material=comp.materials[0].material,
                     ends="distributing"),
        BeamFastener(name="Bolt", source="Lower/Hole 2", target="Upper/Hole 2", type=btype.id),
    ]  # fmt: skip
    comp.physics = [
        Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Lower/Bottom"),
            Feature(type="gravity", selection="Upper/Body",
                    values={"gx": Quantity(value=5e3, unit="m/s^2"),
                            "gz": Quantity(value=-9.81e3, unit="m/s^2")}),
        ])
    ]  # fmt: skip
    study = project.of_type(Study)[0]
    study.solver = "calculix"
    save_project(project, root)
    window = MainWindow(root)
    window.open_bolts()
    table = window.bolts.properties
    assert table.table.rowCount() == 2 and table.count.text() == "2 bolts"
    col = {c.title: i for i, c in enumerate(COLUMNS)}
    rows = {table.table.item(r, 0).text(): r for r in range(2)}
    diameter = table.table.item(rows["Bolt"], col["Diameter"])
    assert diameter.text() == "0.008 m" and "M8 8.8" in diameter.toolTip()  # the type's
    assert table.table.item(rows["Pin"], col["Diameter"]).toolTip() == ""  # its own

    # Both rows selected, one edit: the group set on each, one command (one undo).
    table.table.selectAll()
    table.edit(rows["Pin"], COLUMNS[col["Group"]], "Flange")
    comp = window.session.project.of_type(Component)[0]
    assert [c.group for c in comp.connectors] == ["Flange", "Flange"]
    assert window.session.history(limit=1)[0]["command"]["command"] == "edit"
    table.filter.setText("pin")
    assert table.count.text() == "1 of 2 bolts"
    path = table.export(str(tmp_path / "bolts.csv"))
    with open(path) as f:
        exported = list(csv.reader(f))
    assert exported[0][0] == "Name" and [r[0] for r in exported[1:]] == ["Pin"]
    table.filter.setText("")

    if not available("calculix"):
        window.close()
        pytest.skip("the Results tab needs CalculiX")
    window.session.submit(RunStudy(study=str(study.id)))
    window.open_bolts("results")
    results = window.bolts.results
    assert results.table.rowCount() == 2
    worst, other = results.found
    assert worst.name == "Bolt" and other.proof is None  # the pin's steel has no proof
    assert results.table.item(0, 0).font().bold()
    # By hand: σ = |N|/As + M/Z, Z = π d³/32 on the stress area's diameter; As of M8.
    area = 36.6e-6
    d = math.sqrt(4 * area / math.pi)
    b = next(r for r in results.found if r.name == "Bolt")
    by_hand = abs(b.loads.axial[0]) / area + max(b.loads.bending) / (math.pi * d**3 / 32)
    # (the published 36.6 mm² against the formula's 36.61 mm²)
    assert b.stress == pytest.approx(by_hand, rel=2e-3)
    assert b.proof == pytest.approx(by_hand / 580e6, rel=2e-3)
    assert b.yielding == pytest.approx(by_hand / 640e6, rel=2e-3)
    assert results.plot_title.text().startswith(worst.name)
    assert len(results.moments.series[0][1]) == 11
    window.close()


def test_fastener_dialogs_offer_the_library(app, tmp_path):
    """A fastener's material may be a shipped Fastener grade or any library material
    (the picker): it is copied into the project with the fastener, one undo."""
    from puzzle.model.schema import Material

    window = MainWindow(_plates(tmp_path))
    window.pick_material = lambda: "titanium_ti6al4v"

    def grade(dialog):
        dialog.diameter.setText("0.008")
        box = dialog.material
        texts = [box.itemText(i) for i in range(box.count())]
        assert "SAE J429 Grade 7 (from the library)" in texts  # from Lacewing Lab
        assert texts[-1] == "More from the library…"
        box.setCurrentIndex(box.count() - 1)  # the picker gives titanium
        assert box.currentText() == "Titanium Ti-6Al-4V (from the library)"
        return True

    window.run_dialog = grade
    comp = window.session.project.of_type(Component)[0]
    add_fastener(window, comp)
    project = window.session.project
    (bolt,) = project.of_type(Component)[0].connectors
    assert project.objects[bolt.material].name == "Titanium Ti-6Al-4V"
    assert len(project.of_type(Material)) == 2
    assert window.session.history(limit=1)[0]["command"]["command"] == "batch"
    window.session.undo()
    assert len(window.session.project.of_type(Material)) == 1
    window.close()


def test_the_bolt_panel_fits_a_short_dock(app):
    """Docked under or beside another panel, the Bolts panel asks for little height,
    so it never spills over its neighbours (the plots shrink and scroll)."""
    window = MainWindow()
    try:
        assert window.bolts.layout().minimumSize().height() <= 220
        assert window.bolts.results.layout().minimumSize().height() <= 200
    finally:
        window.close()
