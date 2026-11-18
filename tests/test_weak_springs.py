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


def _seated_cover(pressure: float, u_spring):
    """A cover (nodes 1-4) on a 1 m × 1 m contact face (two triangles), the rest of the
    model held at node 9 with a 100 kN reaction: the contact passes pressure × 1 m²."""
    from uuid import uuid4

    from puzzle.mesh.mesh import CellBlock
    from puzzle.model.schema import Component, Contact, PartInstance

    xyz = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [5, 5, 5.0]])
    ids = np.array([1, 2, 3, 4, 9])
    face = CellBlock("tri3", 2, (2, 7), np.array([1, 2]), np.array([[1, 2, 3], [1, 3, 4]]))
    mesh = Mesh(node_ids=ids, coords=xyz, blocks=[face], groups={
        "Cover": Group("Cover", 3, [(3, 1)], ids[:4]),
        "Cover/Bottom": Group("Cover/Bottom", 2, [(2, 7)], ids[:4]),
        "Base/Top": Group("Base/Top", 2, [(2, 8)], np.array([9]))})  # fmt: skip
    comp = Component(name="C", mesh=uuid4(),
                     parts=[PartInstance(name=n, geometry=uuid4()) for n in ("Cover", "Base")],
                     contacts=[Contact(name="Seat", type="frictionless", source="Cover/Bottom",
                                       target="Base/Top")])  # fmt: skip
    n = len(ids)
    nodal = lambda v: Field(v, "node", ["value"], ids, np.full((n, 1), 0.0))  # noqa: E731
    fields = {
        "displacement": Field("displacement", "node", ["x", "y", "z"], ids,
                              np.tile(u_spring, (n, 1))),
        "reaction_force": Field("reaction_force", "node", ["x", "y", "z"], np.array([9]),
                                np.array([[0.0, 0.0, -1e5]])),
        "contact_pressure": Field("contact_pressure", "node", ["value"], ids,
                                  np.full((n, 1), pressure)),
        "contact_status": nodal("contact_status"),
        "contact_gap": nodal("contact_gap"),
        "contact_sliding": nodal("contact_sliding"),
    }  # fmt: skip
    springs = [Springs("Cover", np.array([1, 2, 3]), 1e4, np.array([9]))]
    return (
        Results("calculix", [StepResult(1, "stationary", 1.0, fields=fields)]),
        springs,
        comp,
        mesh,
    )


def test_a_small_unseated_body_is_not_diluted_by_the_models_reactions():
    """ADR-2 (ADR 0080 decision 5: the body's own load): 30 N on the cover's springs and
    nothing through its contact, beside 100 kN of reactions elsewhere (3e-4 of those, under
    the 1e-3 limit): the cover's springs carry all of its load, so it is reported."""
    res, springs, comp, mesh = _seated_cover(0.0, [1e-3, 0, 0])
    (said,) = finish(res, springs, 1e-3, None, comp, mesh)
    assert "Cover" in said and "100.00%" in said
    # seated: 200 N through the contact (200 Pa over 1 m²) against 3e-5 N, 1.5e-7
    res, springs, comp, mesh = _seated_cover(200.0, [1e-9, 0, 0])
    assert finish(res, springs, 1e-3, None, comp, mesh) == []


def test_springs_resisting_a_couple_do_not_cancel():
    """Spring forces of +f and -f (a couple) sum to nothing as vectors; node by node they
    are 2 f, against no force through the contact: reported."""
    res, springs, comp, mesh = _seated_cover(0.0, [0.0, 0.0, 0.0])
    u = res.steps[0].fields["displacement"].values
    u[0], u[1] = [1e-3, 0, 0], [-1e-3, 0, 0]
    (said,) = finish(res, springs, 1e-3, None, comp, mesh)
    assert "(20 N)" in said
