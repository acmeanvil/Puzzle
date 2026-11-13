"""A result step with an empty field still draws (the maintainer, 2026-11-11: a Code_Aster
run of beam bolts stored its beam forces on helper nodes only, so the field came back
empty and showing the run raised IndexError)."""

from pathlib import Path

import numpy as np
from exports_mesh import cube

from puzzle.adapters.common import drop_helper_nodes
from puzzle.post.vtk import to_grid
from puzzle.results.model import Field, Results, StepResult


def test_an_empty_field_is_skipped():
    mesh = cube()
    u = Field("displacement", "node", ["x", "y", "z"], mesh.node_ids,
              np.zeros((len(mesh.node_ids), 3)))  # fmt: skip
    empty = Field("beam_forces", "node", ["N"], np.zeros(0, np.int64), np.zeros((0, 1)))
    cells = Field("error", "element", [""], np.zeros(0, np.int64), np.zeros((0, 1)))
    step = StepResult(1, "stationary", 1.0, fields={f.name: f for f in (u, empty, cells)})
    grid = to_grid(mesh, step)
    assert "displacement" in grid.point_data and "beam_forces" not in grid.point_data


def test_fields_only_on_helper_nodes_are_dropped(tmp_path: Path):
    mesh = cube()
    top = int(mesh.node_ids.max())
    marker = tmp_path / "job.meshnodes"
    marker.write_text(str(top), encoding="utf-8")
    helpers = Field("beam_forces", "node", ["N"], np.array([top + 1, top + 2]), np.ones((2, 1)))
    mixed = Field("displacement", "node", ["x"], np.array([1, top + 1]), np.ones((2, 1)))
    fields = {"beam_forces": helpers, "displacement": mixed}
    res = Results("code_aster", [StepResult(1, "stationary", 1.0, fields=fields)])
    out = drop_helper_nodes(res, tmp_path / "job.comm")
    assert "beam_forces" not in out.steps[0].fields
    assert out.steps[0].fields["displacement"].ids.tolist() == [1]


def test_the_run_summary_skips_an_empty_field():
    """The summary after a run raised on an empty field, after the run was stored, so
    the GUI showed the run failed and the agent's run calls returned an error."""
    from puzzle.results.query import summary

    empty = Field("beam_forces", "node", ["N"], np.zeros(0, np.int64), np.zeros((0, 1)))
    u = Field("displacement", "node", ["x"], np.array([1]), np.ones((1, 1)))
    fields = {"beam_forces": empty, "displacement": u}
    res = Results("code_aster", [StepResult(1, "stationary", 1.0, fields=fields)])
    assert list(summary(res)[0]["fields"]) == ["displacement"]


def test_lagrange_bricks_draw_by_their_serendipity_nodes():
    """ADR 0082: a 27-node brick is drawn as VTK's 20-node one (its face and centre nodes
    aren't in the cell); the field is still read at all 27."""
    import numpy as np
    import pytest

    from puzzle.mesh.mesh import CellBlock, Mesh
    from puzzle.post.vtk import to_grid
    from puzzle.results.model import Field, StepResult

    corners = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)  # fmt: skip
    edges = [(0, 1), (0, 3), (0, 4), (1, 2), (1, 5), (2, 3), (2, 6), (3, 7), (4, 5), (4, 7),
             (5, 6), (6, 7)]  # fmt: skip
    faces = [(0, 1, 2, 3), (0, 1, 5, 4), (0, 3, 7, 4), (1, 2, 6, 5), (2, 3, 7, 6), (4, 5, 6, 7)]
    mids = [corners[list(e)].mean(axis=0) for e in edges]
    centres = [corners[list(f)].mean(axis=0) for f in faces]
    pts = np.vstack([corners, mids, centres, [corners.mean(axis=0)]])
    ids = np.arange(1, 28)
    mesh = Mesh(node_ids=ids, coords=pts,
                blocks=[CellBlock("hex27", 3, (3, 1), np.array([1]), ids[None, :])])  # fmt: skip
    step = StepResult(1, "stationary", 1.0, fields={
        "temperature": Field("temperature", "node", [""], ids, pts[:, :1])})  # fmt: skip
    grid = to_grid(mesh, step)
    assert grid.n_cells == 1 and int(grid.celltypes[0]) == 25
    assert grid.volume == pytest.approx(1.0)
