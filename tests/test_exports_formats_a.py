"""Exodus II, VTKHDF and UNV exports (ADR 0043 step 3) on a small synthetic mesh, read
back and compared with what was written: coordinates, connectivity, the selections'
names and members, each step's time value and the field values, to the format's digits.

- Exodus: read raw (SciPy's netCDF reader) for the numbering, node order and sets, and
  by VTK's Exodus reader for the values and the side sets' faces (VTK's own face tables,
  so a wrong side number shows as a wrong face).
- VTKHDF: VTK's `vtkHDFReader`, every time step.
- UNV: `export_readers.read_unv`, a reader written from the record layouts, plus the layouts'
  fixed widths.
"""

from __future__ import annotations

import numpy as np
import pytest
from export_readers import fixed_floats, fixed_ints, read_2414, read_exodus, read_mesh, read_unv
from exports_mesh import cube, displacement, results

from puzzle.post.exports.exodus import write_exodus
from puzzle.post.exports.series import step_times
from puzzle.post.exports.unv import write_unv
from puzzle.post.exports.vtkhdf import write_vtkhdf

TET10_EXODUS = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]  # Gmsh -> Exodus (and VTK) tet10 order


def _strings(var) -> list[str]:
    data = np.asarray(var.data)
    return [b"".join(r).decode().rstrip("\x00 ") for r in data.reshape(-1, data.shape[-1])]


@pytest.mark.parametrize("order", [1, 2])
def test_exodus_raw_layout(tmp_path, order):
    from scipy.io import netcdf_file

    mesh = cube(order)
    res = results(mesh)
    files, notes = write_exodus(mesh, res, tmp_path / "cube.exo")
    f = netcdf_file(str(files[0]), "r", mmap=False)
    v = f.variables
    nodes = np.asarray(v["node_num_map"].data)
    assert nodes.tolist() == mesh.node_ids.tolist()  # Puzzle's numbers kept
    xyz = np.column_stack([v[f"coord{a}"].data for a in "xyz"])
    assert np.array_equal(xyz, mesh.coords)
    volume = mesh.blocks[0]
    assert np.asarray(v["elem_num_map"].data).tolist() == volume.ids.tolist()
    assert _strings(v["eb_names"]) == ["Box"]
    assert v["connect1"].elem_type.decode() == ("TETRA4" if order == 1 else "TETRA10")
    conn = nodes[np.asarray(v["connect1"].data) - 1]
    expected = volume.connectivity[:, TET10_EXODUS] if order == 2 else volume.connectivity
    assert np.array_equal(conn, expected)
    # Selections: node sets by name with their members, faces as side sets.
    assert _strings(v["ns_names"]) == ["Box/Bottom", "Box/Top", "Box/Corner"]
    for i, name in enumerate(["Box/Bottom", "Box/Top", "Box/Corner"], 1):
        members = nodes[np.asarray(v[f"node_ns{i}"].data) - 1]
        assert sorted(members) == sorted(mesh.groups[name].node_ids)
    assert _strings(v["ss_names"]) == ["Box/Bottom", "Box/Top"]
    # Steps: the time values; variables named per component.
    assert np.asarray(v["time_whole"].data).tolist() == [0.5, 1.25]
    names = _strings(v["name_nod_var"])
    assert names == ["displacement_x", "displacement_y", "displacement_z", "temperature"]
    for step in (0, 1):
        u = np.column_stack([v[f"vals_nod_var{k}"].data[step] for k in (1, 2, 3)])
        assert np.array_equal(u, displacement(mesh, step + 1.0))
    stress = res.steps[1].fields["stress"].values
    assert _strings(v["name_elem_var"])[0] == "stress_xx"
    assert np.array_equal(v["vals_elem_var6eb1"].data[1], stress[:, 5])
    assert _strings(v["name_glo_var"]) == ["energy"]
    assert np.asarray(v["vals_glo_var"].data)[:, 0].tolist() == [2.5, 5.0]
    assert "SI units" in " ".join(_strings(v["info_records"]))
    assert notes["node sets"] == "3" and notes["side sets"] == "2"
    f.close()


@pytest.mark.parametrize("order", [1, 2])
def test_exodus_read_by_vtk(tmp_path, order):
    mesh = cube(order)
    res = results(mesh)
    (path,), _ = write_exodus(mesh, res, tmp_path / "cube.exo")
    found, times = read_exodus(path, 1)
    assert list(times) == [0.5, 1.25]
    box = found[("Element Blocks", "Box")]
    ids = np.asarray(box.point_data["GlobalNodeId"])
    order_ = np.argsort(mesh.node_ids)
    at = order_[np.searchsorted(mesh.node_ids, ids, sorter=order_)]
    assert np.allclose(box.points, mesh.coords[at])
    assert np.array_equal(box.point_data["displacement_"], displacement(mesh, 2.0)[at])
    stress = res.steps[1].fields["stress"]
    elements = np.asarray(box.cell_data["GlobalElementId"])
    assert np.array_equal(box.cell_data["stress_zx"], stress.at(elements)[:, 5])
    assert box.field_data["energy"].ravel().tolist() == [2.5, 5.0]  # its history
    # Side sets: VTK draws each from its element and side number; the faces it draws
    # are the selection's faces, corner for corner.
    for name in ("Box/Bottom", "Box/Top"):
        side = found[("Side Sets", name)]
        faces = np.asarray(side.cells).reshape(side.n_cells, -1)[:, 1:4]
        corners = np.asarray(side.point_data["GlobalNodeId"])[faces]
        want = next(b for b in mesh.blocks if b.entity in mesh.groups[name].entities)
        assert sorted(map(sorted, corners.tolist())) == sorted(
            map(sorted, want.connectivity[:, :3].tolist())
        )
        nodes = found[("Node Sets", name)]
        assert sorted(nodes.point_data["GlobalNodeId"]) == sorted(mesh.groups[name].node_ids)


def test_exodus_read_by_ioss(tmp_path):
    """VTK's IOSS reader (what ParaView uses) sees the blocks and sets by name too."""
    import pyvista as pv
    from vtkmodules.vtkIOIOSS import vtkIOSSReader

    mesh = cube(2)
    (path,), _ = write_exodus(mesh, results(mesh), tmp_path / "cube.exo")
    r = vtkIOSSReader()
    r.SetFileName(str(path))
    r.UpdateInformation()
    for kind in range(10):
        r.GetEntitySelection(kind).EnableAllArrays()
    r.UpdateTimeStep(1.25)
    out = r.GetOutputDataObject(0)
    names = [
        out.GetMetaData(i).Get(out.NAME()) for i in range(out.GetNumberOfPartitionedDataSets())
    ]
    assert names == ["Box", "Box/Bottom", "Box/Top", "Box/Corner", "Box/Bottom", "Box/Top"]
    box = pv.wrap(out.GetPartitionedDataSet(0).GetPartition(0))
    assert box.point_data["displacement"].shape == (len(mesh.node_ids), 3)
    assert box.cell_data["stress"].shape == (6, 6)
    side = pv.wrap(out.GetPartitionedDataSet(4).GetPartition(0))
    assert side.n_cells == 2  # the bottom's two triangles


def test_vtkhdf_round_trip(tmp_path):
    import pyvista as pv
    from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline as SDDP
    from vtkmodules.vtkIOHDF import vtkHDFReader

    mesh = cube(2)
    res = results(mesh)
    (path,), notes = write_vtkhdf(mesh, res, tmp_path / "cube.vtkhdf")
    r = vtkHDFReader()
    r.SetFileName(str(path))
    r.UpdateInformation()
    assert r.GetOutputInformation(0).Get(SDDP.TIME_STEPS()) == (0.5, 1.25)
    for k, t in enumerate((0.5, 1.25)):
        r.UpdateTimeStep(t)
        grid = pv.wrap(r.GetOutputDataObject(0))
        ids = np.asarray(grid.point_data["node_id"])
        o = np.argsort(mesh.node_ids)
        at = o[np.searchsorted(mesh.node_ids, ids, sorter=o)]
        assert np.array_equal(grid.points, mesh.coords[at])
        assert np.array_equal(grid.point_data["displacement"], displacement(mesh, k + 1.0)[at])
        stress = res.steps[k].fields["stress"]
        assert np.array_equal(grid.cell_data["stress"], stress.at(grid.cell_data["element_id"]))
        assert grid.field_data["energy"].ravel().tolist() == [2.5 * (k + 1)]
    assert notes["time values"] == "time (s)"


def test_exodus_planar_model_edges_as_side_sets(tmp_path):
    """A 2D model (ADR 0048): two coordinates, faces as TRI3 blocks, and an edge
    selection as a side set whose sides VTK draws as that edge."""
    from puzzle.mesh.mesh import CellBlock, Group, Mesh

    coords = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    ids = np.array([11, 12, 13, 14])
    tris = CellBlock("tri3", 2, (2, 1), np.array([1, 2]), np.array([[11, 12, 13], [11, 13, 14]]))
    edge = CellBlock("line2", 1, (1, 3), np.array([3]), np.array([[13, 14]]))
    groups = {"Plate": Group("Plate", 2, [(2, 1)], ids),
              "Plate/Top": Group("Plate/Top", 1, [(1, 3)], np.array([13, 14]))}  # fmt: skip
    mesh = Mesh(ids, coords, [tris, edge], groups, planar=True)
    res = results(cube(1), "stationary", (1.0,))
    res.steps[0].fields = {}
    (path,), notes = write_exodus(mesh, res, tmp_path / "plate.exo")
    assert notes["side sets"] == "1"
    found, _ = read_exodus(path, 0)
    side = found[("Side Sets", "Plate/Top")]
    assert side.n_cells == 1
    assert sorted(side.point_data["GlobalNodeId"]) == [13, 14]


def test_steps_with_equal_values_are_numbered():
    steps = results(cube(1), "stationary", (1.0, 1.0)).steps
    assert step_times(steps) == ([1.0, 2.0], "step number (1, 2, ...)")
    assert step_times(results(cube(1), "modal", (12.5, 40.0)).steps)[1] == "frequency (Hz)"


@pytest.mark.parametrize("order", [1, 2])
def test_unv_mesh_and_groups(tmp_path, order):
    mesh = cube(order)
    (path,), notes = write_unv(mesh, results(mesh), tmp_path / "cube.unv")
    sets = read_unv(path)
    assert list(sets) == [164, 2411, 2412, 2467, 2414]
    units = sets[164][0]
    assert fixed_ints(units[0][:10]) == [1] and units[0][10:30].strip() == "SI: Meter (newton)"
    assert fixed_floats(units[1], 25) == [1.0, 1.0, 1.0]
    coords, elements = read_mesh(sets)
    assert list(coords) == mesh.node_ids.tolist()
    assert np.array_equal(np.array(list(coords.values())), mesh.coords)  # all 17 digits
    volume = mesh.blocks[0]
    unv = [0, 4, 1, 5, 2, 6, 7, 9, 8, 3] if order == 2 else [0, 1, 2, 3]
    assert list(elements) == volume.ids.tolist()
    for e, conn in zip(volume.ids, volume.connectivity, strict=True):
        assert elements[e] == (118 if order == 2 else 111, conn[unv].tolist())
    # Groups: the part and every selection by name, their nodes and elements.
    (groups,) = sets[2467]
    k, found = 0, {}
    while k < len(groups):
        number, *_, count = fixed_ints(groups[k])
        name = groups[k + 1].strip()
        k += 2
        entities = []
        while len(entities) < 4 * count:
            entities += fixed_ints(groups[k])
            k += 1
        found[name] = [tuple(entities[j : j + 2]) for j in range(0, len(entities), 4)]
    assert list(found) == ["Box", "Box/Bottom", "Box/Top", "Box/Corner"]
    for name, entities in found.items():
        nodes = sorted(t for kind, t in entities if kind == 7)
        assert nodes == sorted(mesh.groups[name].node_ids)
    assert sorted(t for kind, t in found["Box"] if kind == 8) == sorted(volume.ids)
    assert notes["groups"] == "4"


def test_unv_results_static_and_transient(tmp_path):
    mesh = cube(2)
    res = results(mesh)
    (path,), notes = write_unv(mesh, res, tmp_path / "cube.unv")
    data = read_2414(read_unv(path))
    # Two steps, each displacement, temperature and stress; the global energy is not
    # a UNV result.
    assert [d["ids"][0].strip() for d in data] == ["displacement", "temperature", "stress"] * 2
    assert "energy (a global value)" in notes["not written"]
    u = data[3]
    assert u["record9"] == [1, 4, 2, 8, 2, 3]  # structural, transient, vector, displacement
    assert u["record10"][6] == 2 and u["record12"][0] == pytest.approx(1.25)
    want = displacement(mesh, 2.0)
    for n, row in u["values"].items():
        exact = want[list(mesh.node_ids).index(n)]
        assert np.allclose(row, exact, rtol=5e-6, atol=1e-12)
    t = data[1]
    assert t["record9"][:4] == [2, 4, 1, 5]  # heat transfer, transient, scalar, temperature
    s = data[5]
    assert s["location"] == 2 and s["record9"][2:4] == [4, 2]  # tensor, stress on elements
    stress = res.steps[1].fields["stress"]
    for e, row in s["values"].items():
        xx, yy, zz, xy, yz, zx = stress.at(np.array([e]))[0]
        assert np.allclose(row, [xx, xy, yy, zx, yz, zz], rtol=5e-6)  # I-DEAS order


def test_unv_modes_say_normal_mode_and_frequency(tmp_path):
    mesh = cube(1)
    res = results(mesh, "modal", (123.456789, 456.7))
    (path,), _ = write_unv(mesh, res, tmp_path / "modes.unv")
    data = [d for d in read_2414(read_unv(path)) if d["ids"][0].strip() == "displacement"]
    for mode, d in enumerate(data, 1):
        assert d["record9"][:2] == [1, 2]  # structural, normal mode
        assert d["record10"][5] == mode
        assert d["record12"][1] == pytest.approx(res.steps[mode - 1].value, rel=5e-6)


def test_formats_are_registered():
    from puzzle.post.exports import EXPORTS, writer

    for name in ("exodus", "vtkhdf", "unv"):
        assert callable(writer(name)) and not EXPORTS[name].options
