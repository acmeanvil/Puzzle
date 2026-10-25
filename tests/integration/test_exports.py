"""Exports (ADR 0043) on the cantilever (CalculiX), read back and checked.

- ParaView: one .vtu per step that VTK reads, with the displacement.
- Point cloud: every node's x, y, z and value; mapped back onto the mesh by Puzzle's
  own transfer it returns the field (the tip deflection to 1e-9).
- Deformed shape: the STL's points are the surface nodes moved by u (the tip's lowest
  point is the tip deflection); the tessellated STEP reads back with the same size in
  metres; compensation moves the other way. Each export names its source in a .txt.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study
from puzzle.post.exports import EXPORTS, export
from puzzle.project import save_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

P = 100.0
L = BAR[0]


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exports")
    root = tmp / "p"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-P, unit="N")})
    physics = Physics(
        type="solid_mechanics",
        features=[Feature(type="fixed", selection="Bar/Root"), force],
        settings={"temperature_dependent_materials": False},
    )
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=5.0)
    steps = [SolveStep(type="stationary")]
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    store = ResultStore(root / "results")
    run = run_study(project, study.id, root, tmp / "w", store=store)
    tip = run.results.last("displacement").at(run.mesh.groups["Bar/Tip"].node_ids)[:, 1].min()
    return project, store, run.run_id, tmp, float(tip)


@over("calculix")
def test_paraview_and_point_cloud(solved, solver):
    import pyvista as pv

    project, store, run_id, tmp, tip = solved
    files = export(project, store, run_id, "paraview", tmp / "out" / "bend")
    grid = pv.read(str(files[1]))
    assert "displacement" in grid.point_data and files[-1].suffix == ".txt"
    assert "study: Bend" in files[-1].read_text()
    (csv, note) = export(project, store, run_id, "point_cloud", tmp / "out" / "cloud",
                         quantity="displacement:y")  # fmt: skip
    rows = np.loadtxt(csv, delimiter=",", skiprows=1)
    header = csv.read_text().splitlines()[0]
    assert header == "x [m],y [m],z [m],Deformation Y [m]"
    assert rows[:, 3].min() == pytest.approx(tip, rel=1e-9)
    mesh = store.read_mesh(store.get_run(run_id).mesh_id)
    assert len(rows) == len(mesh.node_ids)
    (tip_only, _) = export(project, store, run_id, "point_cloud", tmp / "out" / "tip",
                           quantity="displacement:y", selection="Bar/Tip")  # fmt: skip
    assert len(np.loadtxt(tip_only, delimiter=",", skiprows=1)) == len(
        mesh.groups["Bar/Tip"].node_ids
    )


@over("calculix")
def test_deformed_shape_as_stl_and_step(solved, solver):
    import pyvista as pv

    project, store, run_id, tmp, tip = solved
    stl, _ = export(project, store, run_id, "deformed_stl", tmp / "out" / "shape")
    surface = pv.read(str(stl))
    assert surface.n_cells > 100
    assert surface.points[:, 1].min() == pytest.approx(tip, rel=1e-6)  # the tip, moved
    bigger, _ = export(project, store, run_id, "deformed_stl", tmp / "out" / "x10", scale=10)
    assert pv.read(str(bigger)).points[:, 1].min() == pytest.approx(10 * tip, rel=1e-6)
    back, _ = export(project, store, run_id, "deformed_stl", tmp / "out" / "comp", compensate=True)
    assert pv.read(str(back)).points[:, 1].max() == pytest.approx(BAR[1] - tip, rel=1e-6)

    step, note = export(project, store, run_id, "deformed_step", tmp / "out" / "shape")
    text = step.read_text()
    assert "TRIANGULATED_SURFACE_SET" in text
    assert "SI_UNIT(.MILLI.,.METRE.)" in text  # the file's unit, as CAD expects
    assert "deformed: moved by +u" in note.read_text()
    # Its coordinates, in millimetres: the bar's length, and the tip moved down.
    import re

    at = text.index("COORDINATES_LIST(")
    listed = text[at : text.index(");", at)].replace("\n", "")
    number = r"(-?[\d.]+(?:E[-+]?\d+)?)"
    xyz = np.array(re.findall(rf"\({number},{number},{number}\)", listed), float)
    assert np.ptp(xyz[:, 0]) == pytest.approx(1e3 * L, rel=1e-3)
    assert xyz[:, 1].min() == pytest.approx(1e3 * tip, rel=1e-5)


def test_formats_are_listed():
    assert set(EXPORTS) == {"paraview", "point_cloud", "deformed_stl", "deformed_step"}


@over("calculix")
def test_cli_and_agents_export(solved, solver, capsys):
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main

    project, store, run_id, tmp, tip = solved
    root = tmp / "p"
    out = tmp / "cli" / "tip.csv"
    args = ["export", str(root), "--study", "Bend", str(out), "--format", "point_cloud",
            "--quantity", "displacement:y", "--selection", "Bar/Tip"]  # fmt: skip
    assert main(args) == 0 and out.exists()
    with pytest.raises(SystemExit):  # argparse names the formats
        main(["export", str(root), "--study", "Bend", str(out), "--format", "nope"])
    server = build_server(root, "test")
    asked = {"study": "Bend", "format": "deformed_stl", "path": "exports/shape"}
    result = asyncio.run(server.call_tool("export", asked))
    files = result.structured_content["files"]
    assert files[0].endswith("exports/shape.stl") and (root / "exports" / "shape.stl").exists()
    with pytest.raises(Exception, match="exports go inside the project folder"):
        asyncio.run(server.call_tool("export", asked | {"path": str(tmp / "away")}))
