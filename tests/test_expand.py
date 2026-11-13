"""Results of a part model drawn whole (ADR 0081 step 6): copies mirrored across symmetry
planes, turned into every sector, with vectors and tensors carried."""

import numpy as np
import pytest

from puzzle.mesh.mesh import Group, Mesh
from puzzle.model.schema import Feature, Physics
from puzzle.post.expand import _turn, transforms


class _Comp:
    def __init__(self, features=(), cyclic=None, periodic=None):
        self.physics = [Physics(type="solid_mechanics", features=list(features))]
        self.cyclic, self.periodic = cyclic, periodic


def _mesh():
    pts = np.array([[0.0, y, z] for y in (0, 1) for z in (0, 1)] +
                   [[x, 0.0, z] for x in (1, 2) for z in (0, 1)], float)  # fmt: skip
    ids = np.arange(1, len(pts) + 1)
    return Mesh(node_ids=ids, coords=pts, groups={
        "P/X0": Group("P/X0", 2, [(2, 1)], ids[:4]),
        "P/Y0": Group("P/Y0", 2, [(2, 2)], ids[4:]),
    })  # fmt: skip


def test_two_symmetry_planes_make_four_copies():
    comp = _Comp([Feature(type="symmetry", selection="P/X0"),
                  Feature(type="symmetry", selection="P/Y0")])  # fmt: skip
    copies = transforms(_mesh(), comp)
    assert len(copies) == 4
    signs = sorted(np.round(np.linalg.det(m)) for m, _s, _g in copies)
    assert signs == [-1, -1, 1, 1]  # two mirrors and the double mirror (a half turn)
    point = np.array([1.0, 2.0, 3.0])
    landed = {tuple(np.round(m @ point + s, 9)) for m, s, _g in copies}
    assert landed == {(1, 2, 3), (-1, 2, 3), (1, -2, 3), (-1, -2, 3)}


def test_antisymmetry_reverses_the_copy():
    comp = _Comp([Feature(type="antisymmetry", selection="P/X0")])
    (_m0, _s0, g0), (m1, _s1, g1) = transforms(_mesh(), comp)
    assert (g0, g1) == (1.0, -1.0)
    u = np.array([[0.1, 0.2, 0.3]])
    assert np.allclose(_turn(u, m1, g1), [[0.1, -0.2, -0.3]])  # -M u, M flipping x


def test_a_cyclic_sector_turns_into_every_sector():
    from puzzle.model.schema import CyclicSymmetry

    comp = _Comp(cyclic=CyclicSymmetry(source="a", target="b", sectors=6))
    copies = transforms(_mesh(), comp)
    assert len(copies) == 6
    turned = [m @ np.array([1.0, 0.0, 0.0]) for m, _s, _g in copies]
    angles = sorted(np.degrees(np.arctan2(v[1], v[0])) % 360 for v in turned)
    assert np.allclose(angles, [0, 60, 120, 180, 240, 300])


def test_tensors_turn_with_the_copy():
    t = np.radians(90)
    r = np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]])
    sigma = np.array([[100.0, 0, 0, 0, 0, 0]])  # xx only
    assert np.allclose(_turn(sigma, r, 1.0), [[0, 100.0, 0, 0, 0, 0]], atol=1e-9)


def test_the_view_draws_the_whole(tmp_path):
    pytest.importorskip("pyvista")
    from puzzle.mesh.mesh import CellBlock
    from puzzle.post.expand import expand
    from puzzle.post.vtk import to_grid
    from puzzle.results.model import Field, StepResult

    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float) + 0.5
    mesh = Mesh(
        node_ids=np.arange(1, 5),
        coords=pts,
        blocks=[CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))],
    )
    step = StepResult(index=1, kind="static", value=1.0, fields={
        "displacement": Field("displacement", "node", ["x", "y", "z"], np.arange(1, 5),
                              np.tile([1.0, 0.0, 0.0], (4, 1)))})  # fmt: skip
    grid = to_grid(mesh, step)
    mirror = np.diag([-1.0, 1.0, 1.0])
    whole = expand(grid, [(np.eye(3), np.zeros(3), 1.0), (mirror, np.zeros(3), 1.0)])
    assert whole.n_cells == 2 and whole.n_points == 8
    u = np.asarray(whole.point_data["displacement"])
    assert np.allclose(sorted(set(u[:, 0])), [-1.0, 1.0])  # mirrored: x flips
