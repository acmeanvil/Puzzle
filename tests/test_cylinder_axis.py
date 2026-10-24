"""Cylindrical axes picked from a face (ADR 0037): the axis fitted to a cylinder's mesh.

A shaft of radius 7 mm along y, centred at x = 30 mm, z = -5 mm: its wall gives that
axis and radius; its flat end gives none.
"""

import numpy as np
import pytest

from puzzle.mesh.adjacency import cylinder_axis
from puzzle.model.schema import (
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)


def test_axis_of_a_shaft(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.mesh import generate_mesh

    p = Project(name="shaft")
    args = {"r": 0.007, "h": 0.05, "x": 0.03, "y": 0.0, "z": -0.005, "axis": "y"}
    geo = p.add(
        Geometry(
            name="Shaft",
            history=[HistoryStep(kind="cylinder", name="S", args=args)],
            selections=[
                NamedSelection(name="Wall", entity="face", members=["S/wall"]),
                NamedSelection(name="End", entity="face", members=["S/end"]),
            ],
        )
    )
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=3, unit="mm")))
    comp = p.add(Component(name="C", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id))
    meshed = generate_mesh(p, comp.id, tmp_path, tmp_path / "mesh")

    origin, axis, radius = cylinder_axis(meshed.mesh, "P/Wall")
    assert np.allclose(axis, [0, 1, 0], atol=1e-9)
    assert radius == pytest.approx(0.007, rel=1e-3)  # nodes lie on the true circle
    assert np.allclose(origin[[0, 2]], [0.03, -0.005], atol=1e-6)
    assert cylinder_axis(meshed.mesh, "P/End") is None
