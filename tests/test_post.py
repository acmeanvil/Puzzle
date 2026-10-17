import os

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step

from puzzle.mesh import generate_mesh
from puzzle.model import Physics
from puzzle.post import View, export, pick_step, render, to_grid, von_mises
from puzzle.results import Field, Results, StepResult

pv = pytest.importorskip("pyvista")


@pytest.fixture
def bar_mesh(tmp_path):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="solid_mechanics")], mesh_size_mm=4)
    return generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh


def _results(mesh):
    ids = mesh.node_ids
    x = mesh.coords
    disp = Field(
        "displacement",
        "node",
        ["x", "y", "z"],
        ids,
        np.column_stack([x[:, 0], 0 * x[:, 0], 0 * x[:, 0]]),
    )
    stress = np.zeros((len(ids), 6))
    stress[:, 0] = 100.0
    s = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], ids, stress)
    t = Field("temperature", "node", [""], ids, 300 + x[:, 0:1])
    static = StepResult(
        1, "stationary", 1.0, None, {"displacement": disp, "stress": s, "temperature": t}
    )
    mode = StepResult(2, "modal", 204.0, 1, {"displacement": disp})
    return Results("calculix", [static, mode])


def test_grid_volume_matches_the_geometry(bar_mesh):
    grid = to_grid(bar_mesh)
    assert set(np.unique(grid.celltypes)) == {pv.CellType.QUADRATIC_TETRA}
    # A wrong tet10 node order would distort the quadratic cells and change the volume.
    assert grid.volume == pytest.approx(BAR[0] * BAR[1] * BAR[2], rel=1e-9)


def test_fields_land_on_the_right_nodes(bar_mesh):
    grid = to_grid(bar_mesh, _results(bar_mesh).steps[0])
    assert np.allclose(grid.point_data["displacement"][:, 0], grid.points[:, 0])
    assert np.allclose(grid.point_data["displacement_magnitude"], grid.points[:, 0])
    assert np.allclose(grid.point_data["temperature"], 300 + grid.points[:, 0])
    assert np.allclose(grid.point_data["von_mises"], 100.0)


def test_von_mises():
    uniaxial = np.array([[200.0, 0, 0, 0, 0, 0]])
    shear = np.array([[0, 0, 0, 50.0, 0, 0]])
    hydrostatic = np.array([[-10.0, -10.0, -10.0, 0, 0, 0]])
    assert von_mises(uniaxial)[0] == pytest.approx(200.0)
    assert von_mises(shear)[0] == pytest.approx(50.0 * np.sqrt(3))
    assert von_mises(hydrostatic)[0] == pytest.approx(0.0)


def test_pick_step(bar_mesh):
    results = _results(bar_mesh)
    assert pick_step(results, "displacement").mode == 1  # the last step with it
    assert pick_step(results, "displacement", step=1).index == 1
    assert pick_step(results, "stress").index == 1
    with pytest.raises(KeyError):
        pick_step(results, "displacement", mode=7)


def test_export_writes_a_paraview_series(bar_mesh, tmp_path):
    files = export(bar_mesh, _results(bar_mesh), tmp_path / "out" / "bar.pvd")
    assert [f.name for f in files] == ["bar.pvd", "bar_000.vtu", "bar_001.vtu"]
    assert 'name="mode 1"' in files[0].read_text()
    back = pv.read(files[1])
    assert "von_mises" in back.point_data
    assert back.volume == pytest.approx(BAR[0] * BAR[1] * BAR[2], rel=1e-9)


@pytest.mark.skipif(
    not os.environ.get("PUZZLE_RENDER_TESTS"), reason="set PUZZLE_RENDER_TESTS=1 to render"
)
def test_render_to_png(bar_mesh, tmp_path):
    shown = render(
        bar_mesh, _results(bar_mesh), View(field="stress"), screenshot=tmp_path / "s.png"
    )
    assert (tmp_path / "s.png").stat().st_size > 1000
    assert shown["scalar"] == "von_mises" and shown["max"] == pytest.approx(100.0)


def test_render_rejects_unknown_scalars(bar_mesh, tmp_path):
    with pytest.raises(KeyError, match="no 'nope'"):
        render(bar_mesh, _results(bar_mesh), View(scalar="nope"), screenshot=tmp_path / "x.png")
