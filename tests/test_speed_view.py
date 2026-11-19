"""What the view keeps per mesh so a click doesn't work it out again (2026-11-19: on a
200k-node vessel with ten bolts each click on a result held the window 4 s)."""

import os

import numpy as np
import pytest

from puzzle.mesh.mesh import CellBlock, Group, Mesh


def _two_tets() -> Mesh:
    ids = np.arange(1, 6)
    xyz = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1.0]])
    block = CellBlock(element="tet4", dim=3, entity=(3, 1), ids=np.array([11, 12]),
                      connectivity=np.array([[1, 2, 3, 4], [2, 3, 4, 5]]))  # fmt: skip
    return Mesh(ids, xyz, [block], {"Part": Group("Part", 3, [(3, 1)], ids.copy())})


def test_a_mesh_keeps_a_value_for_the_same_arrays_only():
    mesh = _two_tets()
    made = []

    def make():
        made.append(1)
        return object()

    first = mesh.kept("x", (mesh.coords,), make)
    assert mesh.kept("x", (mesh.coords,), make) is first and len(made) == 1
    mesh.coords = mesh.coords.copy()  # other arrays: worked out again
    assert mesh.kept("x", (mesh.coords,), make) is not first and len(made) == 2


def test_node_coordinates_follow_a_replaced_id_array():
    mesh = _two_tets()
    assert mesh.node_coords(np.array([5]))[0].tolist() == [1, 1, 1]
    mesh.node_ids = np.array([5, 4, 3, 2, 1])
    assert mesh.node_coords(np.array([5]))[0].tolist() == [0, 0, 0]


def test_a_grid_handed_out_never_changes_the_kept_one():
    pytest.importorskip("pyvista")
    from puzzle.post.vtk import to_grid

    mesh = _two_tets()
    mine = to_grid(mesh)
    mine.points = mine.points + 5.0
    mine.point_data["extra"] = np.ones(5)
    again = to_grid(mesh)
    assert float(np.asarray(again.points).max()) == 1.0 and "extra" not in again.point_data
    assert again.n_cells == 2 and list(again.cell_data["element_id"]) == [11, 12]


def test_the_kept_surface_carries_the_grids_values_and_points():
    pytest.importorskip("pyvista")
    from puzzle.post.vtk import skin, to_grid

    mesh = _two_tets()
    grid = to_grid(mesh)
    grid.point_data["v"] = np.asarray(grid.points)[:, 0] * 10 + np.asarray(grid.points)[:, 2]
    grid.cell_data["c"] = np.array([7.0, 9.0])
    moved = grid.copy()
    moved.points = np.asarray(grid.points) + [0.0, 0.0, 2.0]
    outer = skin(mesh, moved, "v")
    assert outer.n_cells == 6  # two tetrahedra's eight faces less the shared one, twice
    points = np.asarray(outer.points)
    assert np.allclose(outer.point_data["v"], points[:, 0] * 10 + points[:, 2] - 2.0)
    cells = skin(mesh, grid, "c")
    centres = np.asarray(cells.cell_centers().points)
    # a face of the second tetrahedron touches node 5 at (1, 1, 1): beyond x + y + z = 1
    assert np.array_equal(np.asarray(cells.cell_data["c"]) == 9.0, centres.sum(axis=1) > 1.0001)
    assert skin(mesh, grid.extract_cells([0]), "v") is None  # not the mesh's own grid


def test_physical_cores_come_from_windows_without_a_subprocess(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows only")
    import subprocess

    from puzzle import machine

    def refuse(*_a, **_k):
        raise AssertionError("no subprocess is needed for the core count")

    monkeypatch.setattr(subprocess, "run", refuse)
    machine.physical_cores.cache_clear()
    try:
        assert 1 <= machine.physical_cores() <= machine.logical_cores()
    finally:
        machine.physical_cores.cache_clear()
