"""Weak springs (ADR 0080): which bodies get them, where, and the warning when they carry
the load."""

import numpy as np

from puzzle.mesh.mesh import Group, Mesh
from puzzle.results.model import Field, Results, StepResult
from puzzle.weak_springs import Springs, finish, nodes_for


def _box_mesh():
    xyz = np.array([[x, y, z] for x in np.linspace(0, 1, 5) for y in (0, 0.2) for z in (0, 0.1)])
    ids = np.arange(1, len(xyz) + 1)
    end = ids[xyz[:, 0] == 0.0]
    return Mesh(node_ids=ids, coords=xyz, groups={
        "P": Group("P", 3, [(3, 1)], ids), "P/End": Group("P/End", 2, [(2, 1)], end)})  # fmt: skip


def test_three_nodes_spread_out_and_off_the_contact():
    mesh = _box_mesh()
    chosen = nodes_for(mesh, "P", {"P/End"})
    assert len(set(chosen.tolist())) == 3
    assert not set(chosen.tolist()) & set(mesh.groups["P/End"].node_ids.tolist())
    xyz = mesh.node_coords(chosen)
    assert np.ptp(xyz[:, 0]) > 0.4  # one far out along the length


def _results(u_spring, reaction):
    step = StepResult(index=1, kind="stationary", value=1.0, fields={
        "displacement": Field("displacement", "node", ["x", "y", "z"], np.array([1, 2, 3]),
                              np.tile(u_spring, (3, 1))),
        "reaction_force": Field("reaction_force", "node", ["x", "y", "z"], np.array([9]),
                                np.array([reaction])),
    })  # fmt: skip
    return Results(solver="calculix", steps=[step])


def test_springs_that_carry_the_load_are_reported():
    springs = [Springs("Cover", np.array([1, 2, 3]), 1e4, np.array([9]))]
    seated = _results([1e-9, 0, 0], [-100.0, 0, 0])  # 3e-5 N of 100 N
    assert finish(seated, springs) == []
    assert "weak_spring_force" in seated.steps[0].fields
    lifted = _results([1e-3, 0, 0], [0.0, 0, 0])  # all 30 N through the springs
    (said,) = finish(lifted, springs)
    assert "Cover" in said and "100.00%" in said
