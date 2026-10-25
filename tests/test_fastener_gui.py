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
        return True

    window.run_dialog = by_hand
    add_fastener(window, comp)
    names = [c.name for c in window.session.project.of_type(Component)[0].connectors]
    assert names == ["Bolt 1", "Pin"]
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
