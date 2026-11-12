"""A frame made from a grid (ADR 0079 step 6) solves as any frame: an X-braced bay, W14X90
columns and beam, W8X31 braces with their ends released in bending, so the bay acts as a
truss with stiff columns: the lateral load H at the top goes through the tension and
compression diagonals, H / (2 cos θ) each (θ the diagonal's angle from horizontal), less
the columns' share. Equilibrium is exact."""

import math

import pytest
from solvers import over

from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    MaterialAssignment,
    MemberRelease,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _grid_project(tmp_path, bracing="x"):
    pytest.importorskip("OCP")
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Geometry

    root = tmp_path / "grid"
    save_project(Project(name="grid"), root)
    server = build_server(root)
    out = asyncio.run(server.call_tool("frame_grid", {
        "name": "Frame", "bays_x": [6.0], "stories": [4.0], "bracing": bracing,
    })).structured_content  # fmt: skip
    assert out["groups"] == {"Columns": 2, "Beams X": 1, "Braces": 4}
    p = Project(name="grid")
    geo = p.add(Geometry.model_validate(out["command"]["data"]))
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "astm_a992")
    mesh = p.add(MeshRecipe(name="M", global_size=q(0.5, "m"), element_order=1))
    w = lambda d, sel: BeamSection(selection=sel, orientation=(0.0, 1.0, 0.0), shape="library",  # noqa: E731
                                   standard="aisc", designation=d)  # fmt: skip
    releases = []  # pinned at the corners; each diagonal runs through the crossing
    for corner in ("A1-0", "B1-0", "A1-1", "B1-1"):
        releases.append(MemberRelease(selection="F/Braces", at=f"F/{corner}",
                                      released=["ry", "rz"]))  # fmt: skip
    h = 100e3
    features = [Feature(type="fixed", selection="F/A1-0"),
                Feature(type="fixed", selection="F/B1-0"),
                Feature(type="force", selection="F/A1-1", values={"x": q(h, "N")})]  # fmt: skip
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[w("W14X90", "F/Columns"), w("W14X90", "F/Beams X"), w("W8X31", "F/Braces")],
        member_releases=releases,
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="pynite",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, root)
    return p, study, root, h


@over("pynite")
def test_an_x_braced_bay_carries_its_shear_in_the_diagonals(tmp_path, solver):
    p, study, root, h = _grid_project(tmp_path)
    run = run_study(p, study.id, root, tmp_path / "w")
    step = run.results.steps[0]
    r = step.fields["reaction_force"]
    assert float(r.values[:, 0].sum()) == pytest.approx(-h, rel=1e-9)
    theta = math.atan2(4.0, 6.0)
    truss = h / (2 * math.cos(theta))
    forces = step.fields["member_forces"]
    braces = {int(e) for blk in run.mesh.groups["F/Braces"].blocks(run.mesh) if blk.dim == 1
              for e in blk.ids}  # fmt: skip
    n = [abs(v) for e, v in zip(forces.ids, forces.values[:, 0], strict=True) if int(e) in braces]
    largest = max(n)
    print(f"diagonals: {largest:.1f} N against the truss's H/(2 cos theta) = {truss:.1f} N")
    assert largest == pytest.approx(truss, rel=0.02)


def test_new_frame_from_a_grid_in_the_gui(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("OCP")
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.frame_editor import new_frame
    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import Geometry

    QApplication.instance() or QApplication([])
    root = tmp_path / "g"
    save_project(Project(name="g"), root)
    window = MainWindow(root)
    try:
        fill(window, name="Shed", bays_x="2@6", bays_y="5", stories="4", bracing="chevron")
        new_frame(window)
        geo = next(o for o in window.session.project.objects.values() if isinstance(o, Geometry))
        assert geo.name == "Shed" and geo.history[0].kind == "frame"
        names = {s.name for s in geo.selections}
        assert {"Columns", "Beams X", "Beams Y", "Braces", "A1-0", "C2-1"} <= names
    finally:
        window.close()
