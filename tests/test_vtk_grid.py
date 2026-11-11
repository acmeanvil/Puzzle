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
