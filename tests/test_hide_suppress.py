"""Hiding and suppressing geometry (as Ansys's bodies): hidden is not drawn but still
solved; suppressed is left out of meshing and solving, with what hangs on it."""

from __future__ import annotations

import os

import pytest

from puzzle.model.schema import (
    BeamFastener,
    Component,
    Contact,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project.graph import content_hashes
from puzzle.suppress import applied

q = lambda v, u="mm": Quantity(value=v, unit=u)  # noqa: E731


def _two_plates() -> Project:
    from puzzle.materials import add_library_material

    p = Project(name="two")
    steel = add_library_material(p, "structural_steel")
    geos = {}
    for name in ("Lower plate", "Upper plate"):
        geos[name] = p.add(Geometry(
            name=name,
            history=[HistoryStep(kind="box", name="P", args={"dx": 0.04, "dy": 0.04, "dz": 0.01}),
                     HistoryStep(kind="hole", name="H", args={"r": 0.004, "x": 0.02, "y": 0.02})],
            selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                        NamedSelection(name="Hole", entity="face", members=["H/wall"]),
                        NamedSelection(name="Top", entity="face", members=["P/+z"]),
                        NamedSelection(name="Bottom", entity="face", members=["P/-z"])],
        ))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=q(4)))
    comp = p.add(Component(
        name="Joint",
        parts=[PartInstance(name="Lower", geometry=geos["Lower plate"].id),
               PartInstance(name="Upper", geometry=geos["Upper plate"].id,
                            translation=(0.0, 0.0, 0.01))],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Lower", "Upper")],
        contacts=[Contact(name="Seat", type="bonded", source="Upper/Bottom", target="Lower/Top")],
        connectors=[BeamFastener(name="Bolt", source="Lower/Hole", target="Upper/Hole",
                                 diameter=q(8), material=steel.id)],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Lower/Bottom"),
            Feature(type="force", selection="Upper/Top", values={"z": q(100, "N")}),
        ])],
    ))  # fmt: skip
    p.add(Study(name="S", component=comp.id, solver="calculix",
                steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p


def test_suppressing_leaves_a_part_and_what_hangs_on_it_out():
    p = _two_plates()
    upper = next(g for g in p.of_type(Geometry) if g.name == "Upper plate")
    p.objects[upper.id] = upper.model_copy(update={"suppressed": True})
    comp = applied(p).of_type(Component)[0]
    assert [x.name for x in comp.parts] == ["Lower"]
    assert comp.contacts == [] and comp.connectors == []
    assert [m.selection for m in comp.materials] == ["Lower/Body"]
    assert [f.type for f in comp.physics[0].features] == ["fixed"]
    assert p.of_type(Component)[0].parts[1].name == "Upper"  # the project itself is kept


def test_hiding_never_stales_a_study_but_suppressing_does():
    p = _two_plates()
    study = p.of_type(Study)[0]
    before = content_hashes(p)[study.id]
    upper = next(g for g in p.of_type(Geometry) if g.name == "Upper plate")
    p.objects[upper.id] = upper.model_copy(update={"hidden": True})
    assert content_hashes(p)[study.id] == before
    p.objects[upper.id] = upper.model_copy(update={"suppressed": True})
    assert content_hashes(p)[study.id] != before


def test_every_part_suppressed_is_a_problem():
    p = _two_plates()
    for g in p.of_type(Geometry):
        p.objects[g.id] = g.model_copy(update={"suppressed": True})
    assert "suppressed" in {x.code for x in p.problems()}


def test_the_menus_hide_show_swap_and_suppress(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("OCP")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui import menus
    from puzzle.gui.viewport import without_parts
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])  # noqa: F841
    root = tmp_path / "two"
    save_project(_two_plates(), root)
    window = MainWindow(root)

    def geo(name):
        return next(g for g in window.session.project.of_type(Geometry) if g.name == name)

    try:
        lower, upper = geo("Lower plate"), geo("Upper plate")
        menus.hide_others(window, lower)
        assert (geo("Lower plate").hidden, geo("Upper plate").hidden) == (False, True)
        menus.swap_hidden(window)
        assert (geo("Lower plate").hidden, geo("Upper plate").hidden) == (True, False)
        menus.show_all(window)
        assert not geo("Lower plate").hidden and not geo("Upper plate").hidden
        menus.show_geometry(window, [upper], False)
        assert geo("Upper plate").hidden
        window.session.undo()  # each is one command: it undoes
        assert not geo("Upper plate").hidden
        menus.suppress_geometry(window, upper, True)
        assert geo("Upper plate").suppressed
        item = next(i for i in window.tree._items() if i.text(0) == "Upper plate")
        assert item.font(0).strikeOut() and "suppressed" in item.toolTip(0)

        from puzzle.mesh import generate_mesh

        project = window.session.project
        comp_id = project.of_type(Component)[0].id
        mesh = generate_mesh(project, comp_id, root, tmp_path / "m").mesh
        assert window._hidden_parts(mesh) == {"Upper"}
        shown = without_parts(mesh, {"Upper"})
        upper_nodes = set(mesh.groups["Upper"].node_ids)
        assert not any(set(b.connectivity.ravel()) <= upper_nodes for b in shown.blocks)
        assert any(b.dim == 3 for b in shown.blocks)  # the lower plate is still drawn
    finally:
        window.close()


def test_a_study_runs_without_its_suppressed_part(tmp_path):
    """The upper plate suppressed: CalculiX solves the lower plate alone (its mesh has
    no upper nodes), with the bolt, contact and the upper plate's load left out."""
    pytest.importorskip("OCP")
    from solvers import available

    if not available("calculix"):
        pytest.skip("needs CalculiX")
    import numpy as np

    from puzzle.project import save_project
    from puzzle.solve import run_study

    p = _two_plates()
    upper = next(g for g in p.of_type(Geometry) if g.name == "Upper plate")
    p.objects[upper.id] = upper.model_copy(update={"suppressed": True})
    lower = p.of_type(Component)[0]
    lower.physics[0].features.append(
        Feature(type="force", selection="Lower/Top", values={"x": q(50, "N")})
    )
    root = tmp_path / "two"
    save_project(p, root)
    run = run_study(p, p.of_type(Study)[0].id, root, tmp_path / "w")
    assert "Upper" not in run.mesh.groups and "Lower" in run.mesh.groups
    assert float(np.abs(run.results.last("displacement").values).max()) > 0


def test_hiding_redraws_the_view(tmp_path):
    """With the component in the view, hiding a plate redraws it at once, fewer cells;
    showing it again brings them back."""
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("OCP")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui import menus
    from puzzle.gui.tree import Node
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "two"
    save_project(_two_plates(), root)
    window = MainWindow(root)
    try:
        comp = window.session.project.of_type(Component)[0]
        window.tree.select(Node("object", str(comp.id), "", comp.name))
        end = time.time() + 120
        while window.viewport.shown.get("kind") != "preview" and time.time() < end:
            app.processEvents()
            time.sleep(0.05)
        both = window.viewport.shown["cells"]
        upper = next(g for g in window.session.project.of_type(Geometry) if g.name == "Upper plate")
        menus.show_geometry(window, [upper], False)
        app.processEvents()
        one = window.viewport.shown["cells"]
        assert 0 < one < both
        menus.show_all(window)
        app.processEvents()
        assert window.viewport.shown["cells"] == both
    finally:
        window.close()
