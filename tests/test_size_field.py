"""Sizes from data (ADR 0066 phase 3) on Gmsh: a unit box sized by a table whose size
grows from 0.02 m at x = 0 to 0.2 m at x = 1, through a PostView background field;
the elements near x = 0 come out several times smaller than those near x = 1. And
Netgen's size file written as its reader wants."""

from __future__ import annotations

import numpy as np
import pytest

from puzzle.mesh import size_field


def table():
    g = np.linspace(0, 1, 5)
    pts = np.array([[x, y, z] for x in g for y in g for z in g])
    return pts, 0.02 + 0.18 * pts[:, 0]


def test_a_table_grades_a_gmsh_mesh():
    import gmsh

    from puzzle.geometry import gmsh_session

    sizes = size_field.from_points(*table())
    with gmsh_session():
        gmsh.model.add("box")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        for name in ("MeshSizeFromPoints", "MeshSizeFromCurvature", "MeshSizeExtendFromBoundary"):
            gmsh.option.setNumber(f"Mesh.{name}", 0)
        f = size_field.gmsh_field(sizes)
        gmsh.model.mesh.field.setAsBackgroundMesh(f)
        gmsh.model.mesh.generate(3)
        _, tags, nodes = gmsh.model.mesh.getElements(3)
        _, coords, _ = gmsh.model.mesh.getNodes()
    xyz = coords.reshape(-1, 3)
    conn = nodes[0].reshape(-1, 4) - 1  # node tags are 1..n here
    corners = xyz[conn]
    centre = corners.mean(axis=1)
    edge = np.linalg.norm(corners[:, 1] - corners[:, 0], axis=1)
    near, far = edge[centre[:, 0] < 0.1].mean(), edge[centre[:, 0] > 0.9].mean()
    assert 0.02 < near < 0.04 and far / near > 4  # sizes 0.02..0.038 there, ~0.19 far


def test_bad_tables_are_refused():
    pts, h = table()
    with pytest.raises(Exception, match="positive"):
        size_field.from_points(pts, -h)
    with pytest.raises(Exception, match="four points"):
        size_field.from_points(pts[:3], h[:3])


def test_netgen_file(tmp_path):
    sizes = size_field.from_points(*table())
    text = size_field.netgen_file(sizes, tmp_path / "sizes.msz").read_text().splitlines()
    assert text[0] == "125" and len(text) == 127 and text[-1] == "0"
    assert text[1].split() == ["0", "0", "0", "0.02"]
