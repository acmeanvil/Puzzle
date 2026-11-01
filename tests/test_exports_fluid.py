"""Fluid exports (ADR 0043 step 4) on synthetic meshes and cell results: EnSight Gold,
CGNS and Tecplot read back (by VTK, by the Code_Saturne adapter's own EnSight reader,
by a small Tecplot parser), and wall quantities against Poiseuille flow's exact ones."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import fluid_meshes as fm
import numpy as np
import pytest

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.post.exports import EXPORTS, fluid, wall
from puzzle.post.exports.cgns import write_cgns
from puzzle.post.exports.ensight import write_ensight
from puzzle.post.exports.tecplot import write_tecplot
from puzzle.results.model import Field, Results, StepResult

pytest.importorskip("vtkmodules.vtkIOEnSight")
from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: E402

F32 = 2e-7  # float32's relative precision, with a margin


def _blocks(multiblock):
    """(name, dataset) of every leaf, depth first."""
    out = []
    for i in range(multiblock.GetNumberOfBlocks()):
        b = multiblock.GetBlock(i)
        meta = multiblock.GetMetaData(i)
        name = meta.Get(multiblock.NAME()) if meta else None
        if b is None:
            continue
        if b.IsA("vtkMultiBlockDataSet"):
            out += [(n, d) for n, d in _blocks(b)]
        else:
            out.append((name, b))
    return out


def _centres(data) -> np.ndarray:
    from vtkmodules.vtkFiltersCore import vtkCellCenters

    f = vtkCellCenters()
    f.SetInputData(data)
    f.Update()
    return vtk_to_numpy(f.GetOutput().GetPoints().GetData())


def _match(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Index into `b` of each row of `a` (the same points, any order)."""
    from scipy.spatial import cKDTree

    d, i = cKDTree(b).query(a)
    assert d.max() < 1e-9
    return i


def _cell_values(mesh, step, name, centres):
    ids, c = fm.centroids(mesh)
    f = step.fields[name]
    return f.at(ids)[_match(centres, c)]


@pytest.fixture
def channel():
    mesh = fm.channel()
    return mesh, fm.poiseuille(mesh, steps=2)


# ---- EnSight Gold ------------------------------------------------------------


def _ensight(path: Path, time: float | None = None):
    from vtkmodules.vtkIOEnSight import vtkGenericEnSightReader

    r = vtkGenericEnSightReader()
    r.SetCaseFileName(str(path))
    r.ReadAllVariablesOn()
    r.UpdateInformation()
    if time is None:
        r.Update()
    else:
        r.UpdateTimeStep(time)
    return r


def test_ensight_round_trip_by_vtk(channel, tmp_path):
    mesh, results = channel
    files, notes = write_ensight(mesh, results, tmp_path / "flow.case")
    case = tmp_path / "flow.case"
    assert case in files and all(f.exists() for f in files)
    for k, step in enumerate(results.steps):
        r = _ensight(case, step.value)
        parts = dict(_blocks(r.GetOutput()))
        assert list(parts) == ["volume", "Channel/Walls", "Channel/Inlet", "Channel/Outlet"]
        vol = parts["volume"]
        assert vol.GetNumberOfCells() == 160 and vol.GetNumberOfPoints() == len(mesh.node_ids)
        pts = vtk_to_numpy(vol.GetPoints().GetData())
        order = _match(pts, mesh.coords)
        np.testing.assert_allclose(pts, mesh.coords[order], atol=1e-9)
        centres = _centres(vol)
        for name in ("velocity", "pressure", "stress"):
            got = vtk_to_numpy(vol.GetCellData().GetArray(name)).reshape(len(centres), -1)
            want = _cell_values(mesh, step, name, centres)
            np.testing.assert_allclose(got, want, rtol=F32, atol=1e-12)
        nodal = vtk_to_numpy(vol.GetPointData().GetArray("wall_distance"))
        want = step.fields["wall_distance"].at(mesh.node_ids[order])[:, 0]
        np.testing.assert_allclose(nodal, want, rtol=F32, atol=1e-12)
        # a wall face carries the cell next to it
        walls = parts["Channel/Walls"]
        p = vtk_to_numpy(walls.GetCellData().GetArray("pressure"))
        x = _centres(walls)[:, 0]
        np.testing.assert_allclose(p, (k + 1) * fm.G * (fm.LENGTH - x), rtol=F32)
    times = r.GetTimeSets().GetItem(0)
    assert [times.GetValue(i) for i in range(times.GetNumberOfTuples())] == [1.0, 2.0]
    assert notes["steps"] == "2"


def test_ensight_round_trip_by_the_code_saturne_reader(channel, tmp_path):
    from puzzle.adapters.code_saturne.ensight import read_case, read_variable

    mesh, results = channel
    write_ensight(mesh, results, tmp_path / "flow.case")
    parts, variables = read_case(tmp_path / "flow.case")
    assert len(parts) == 4 and [len(p.blocks[0].centroids) for p in parts] == [160, 32, 20, 20]
    centres = parts[0].blocks[0].centroids
    for name in ("velocity", "pressure"):
        kind, components, file = variables[name]
        assert file.name.endswith("0001")  # the last step
        values = read_variable(file, parts, components)[:160]
        want = _cell_values(mesh, results.steps[-1], name, centres)
        np.testing.assert_allclose(values, want, rtol=F32, atol=1e-12)


def _tet10() -> Mesh:
    """One quadratic tetrahedron in Gmsh's node order (edges 01 12 20 30 32 31)."""
    corners = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    edges = [(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)]
    coords = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in edges]])
    block = CellBlock("tet10", 3, (3, 1), np.array([1]), np.arange(1, 11)[None, :])
    mesh = Mesh(np.arange(1, 11), coords, [block])
    mesh.groups["T"] = Group("T", 3, [(3, 1)], mesh.node_ids)
    return mesh


VTK_TET10_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]


def _check_tet10(cell, points):
    ids = [cell.GetPointId(k) for k in range(10)]
    xyz = points[ids]
    for k, (a, b) in enumerate(VTK_TET10_EDGES):
        np.testing.assert_allclose(xyz[4 + k], (xyz[a] + xyz[b]) / 2, atol=1e-7)


def _volumes(data) -> np.ndarray:
    from vtkmodules.vtkFiltersVerdict import vtkMeshQuality

    q = vtkMeshQuality()
    q.SetInputData(data)
    q.SetTetQualityMeasureToVolume()
    q.SetHexQualityMeasureToVolume()
    q.SetWedgeQualityMeasureToVolume()
    q.SetPyramidQualityMeasureToVolume()
    q.Update()
    return vtk_to_numpy(q.GetOutput().GetCellData().GetArray("Quality"))


def test_ensight_element_orders(tmp_path):
    """Signed volumes come back positive and right for each element type, and a
    quadratic tetrahedron's edge nodes sit on the right edges."""
    empty = Results("x", [StepResult(1, "stationary", 1.0)])
    write_ensight(fm.mixed(), empty, tmp_path / "mixed.case")
    parts = dict(_blocks(_ensight(tmp_path / "mixed.case").GetOutput()))
    np.testing.assert_allclose(_volumes(parts["volume"]), fm.VOLUMES, rtol=1e-6)
    assert parts["Shapes/Floor"].GetNumberOfCells() == 1
    write_ensight(_tet10(), empty, tmp_path / "tet10.case")
    vol = dict(_blocks(_ensight(tmp_path / "tet10.case").GetOutput()))["volume"]
    _check_tet10(vol.GetCell(0), vtk_to_numpy(vol.GetPoints().GetData()))


# ---- CGNS --------------------------------------------------------------------


def _cgns(path: Path, time: float | None = None, patches: bool = True):
    from vtkmodules.vtkIOCGNSReader import vtkCGNSReader

    r = vtkCGNSReader()
    r.SetFileName(str(path))
    r.UpdateInformation()
    r.EnableAllCellArrays()
    r.EnableAllPointArrays()
    r.SetLoadBndPatch(patches)
    r.UpdateTimeStep(time) if time is not None else r.Update()
    return r


def test_cgns_round_trip_by_vtk(channel, tmp_path):
    import h5py

    mesh, results = channel
    bcs = {"Channel/Walls": "BCWall", "Channel/Inlet": "BCInflow"}
    files, notes = write_cgns(mesh, results, tmp_path / "flow.cgns", bcs)
    for step in results.steps:
        blocks = _blocks(_cgns(files[0], step.value).GetOutput())
        names = [n for n, _ in blocks]
        assert names == ["Internal", "Channel_Walls", "Channel_Inlet", "Channel_Outlet"]
        vol = blocks[0][1]
        pts = vtk_to_numpy(vol.GetPoints().GetData())
        np.testing.assert_allclose(pts, mesh.coords[_match(pts, mesh.coords)], atol=0)
        centres = _centres(vol)
        u = vtk_to_numpy(vol.GetCellData().GetArray("Velocity"))
        np.testing.assert_allclose(u, _cell_values(mesh, step, "velocity", centres), rtol=1e-15)
        p = vtk_to_numpy(vol.GetCellData().GetArray("Pressure"))
        np.testing.assert_allclose(p, _cell_values(mesh, step, "pressure", centres)[:, 0])
        szx = vtk_to_numpy(vol.GetCellData().GetArray("StressZX"))
        np.testing.assert_allclose(szx, _cell_values(mesh, step, "stress", centres)[:, 5])
        nodal = vtk_to_numpy(vol.GetPointData().GetArray("WallDistance"))
        order = _match(pts, mesh.coords)
        np.testing.assert_allclose(
            nodal, step.fields["wall_distance"].at(mesh.node_ids[order])[:, 0]
        )
        walls = dict(blocks)["Channel_Walls"]
        assert walls.GetNumberOfCells() == 32
        y = _centres(walls)[:, 1]
        assert set(np.round(y / fm.H, 9)) == {0.0, 1.0}
    with h5py.File(files[0]) as h5:
        bc = h5["Base/Zone/ZoneBC/Channel_Walls"]
        text = bytes(bc["PuzzleSelection/ data"][()]).decode()
        assert text == "Channel/Walls"
        assert bytes(h5["Base/Channel_Walls/FamilyBC/ data"][()]).decode() == "BCWall"
        assert bytes(h5["Base/Channel_Outlet/FamilyBC/ data"][()]).decode() == "BCTypeUserDefined"
        assert bc.attrs["label"] == b"BC_t" and bc.attrs["type"] == b"C1"
        np.testing.assert_allclose(h5["Base/BaseIterativeData/TimeValues/ data"][()], [1, 2])
    assert "Channel_Walls (Channel/Walls)" in notes["boundaries"]


def test_cgns_element_orders(tmp_path):
    empty = Results("x", [StepResult(1, "stationary", 1.0)])
    write_cgns(fm.mixed(), empty, tmp_path / "mixed.cgns")
    vol = _blocks(_cgns(tmp_path / "mixed.cgns", patches=False).GetOutput())[0][1]
    np.testing.assert_allclose(np.sort(_volumes(vol)), np.sort(fm.VOLUMES), rtol=1e-12)
    write_cgns(_tet10(), empty, tmp_path / "tet10.cgns")
    vol = _blocks(_cgns(tmp_path / "tet10.cgns", patches=False).GetOutput())[0][1]
    _check_tet10(vol.GetCell(0), vtk_to_numpy(vol.GetPoints().GetData()))


def test_cgns_names_fit():
    from puzzle.post.exports.cgns import _names

    long = "A very long part name indeed/And a long selection"
    names = _names([long, long + " 2", "P/S"])
    assert all(len(n) <= 32 and "/" not in n for n in names.values())
    assert len(set(names.values())) == 3 and names["P/S"] == "P_S"


# ---- Tecplot -----------------------------------------------------------------


def read_tecplot(path: Path):
    """A small reader for what the writer writes: zones of BLOCK data with
    VARLOCATION, PASSIVEVARLIST, VARSHARELIST and CONNECTIVITYSHAREZONE."""
    text = path.read_text()
    names = re.findall(r'"([^"]*)"', text.split("\n")[1])
    zones, tokens = [], None
    chunks = re.split(r"^ZONE ", text, flags=re.M)[1:]
    for chunk in chunks:
        header, _, body = chunk.partition("\n")
        keys = dict(re.findall(r'(\w+)=("[^"]*"|\([^)]*\)|[^,\s]+)', "ZONE " + header))
        n, e = int(keys["NODES"]), int(keys["ELEMENTS"])
        cells = {int(v) for v in re.findall(r"\[(\d+)\]=CELLCENTERED", keys.get("VARLOCATION", ""))}
        passive = {int(v) for v in re.findall(r"\[(\d+)\]", keys.get("PASSIVEVARLIST", ""))}
        shared = "VARSHARELIST" in keys
        tokens = body.split()
        pos, data = 0, {}
        for v, name in enumerate(names, start=1):
            if v in passive or (shared and v <= 3):
                continue
            size = e if v in cells else n
            data[name] = np.array(tokens[pos : pos + size], float)
            pos += size
        npe = 8 if keys["ZONETYPE"] == "FEBRICK" else 4
        conn = None
        if not shared:
            conn = np.array(tokens[pos : pos + e * npe], int).reshape(e, npe)
            pos += e * npe
        assert pos == len(tokens)
        zones.append(
            {
                "title": keys["T"].strip('"'),
                "time": float(keys["SOLUTIONTIME"]),
                "data": data,
                "conn": conn,
                "share": keys.get("CONNECTIVITYSHAREZONE"),
            }
        )
    return names, zones


def test_tecplot_round_trip(channel, tmp_path):
    mesh, results = channel
    files, notes = write_tecplot(mesh, results, tmp_path / "flow.dat")
    names, zones = read_tecplot(files[0])
    assert names[:3] == ["X [m]", "Y [m]", "Z [m]"] and "pressure [Pa]" in names
    assert "velocity x [m/s]" in names and "wall_distance" in names
    assert [z["title"] for z in zones[:4]] == ["volume", "Channel/Walls", "Channel/Inlet",
                                               "Channel/Outlet"]  # fmt: skip
    assert [z["time"] for z in zones] == [1.0] * 4 + [2.0] * 4
    first = zones[0]
    xyz = np.column_stack([first["data"][n] for n in names[:3]])
    centres = xyz[first["conn"] - 1].mean(axis=1)
    for k, step in enumerate(results.steps):
        z = zones[4 * k]
        np.testing.assert_allclose(z["data"]["pressure [Pa]"],
                                   _cell_values(mesh, step, "pressure", centres)[:, 0],
                                   rtol=1e-8)  # fmt: skip
        np.testing.assert_allclose(z["data"]["velocity x [m/s]"],
                                   _cell_values(mesh, step, "velocity", centres)[:, 0],
                                   rtol=1e-8, atol=1e-15)  # fmt: skip
        order = _match(xyz, mesh.coords)
        np.testing.assert_allclose(z["data"]["wall_distance"],
                                   step.fields["wall_distance"].at(mesh.node_ids[order])[:, 0],
                                   rtol=1e-8, atol=1e-15)  # fmt: skip
    assert zones[4]["share"] == "1" and zones[4]["conn"] is None


def test_tecplot_bricks_of_mixed_cells(tmp_path):
    empty = Results("x", [StepResult(1, "stationary", 1.0)])
    write_tecplot(fm.mixed(), empty, tmp_path / "mixed.dat")
    names, zones = read_tecplot(tmp_path / "mixed.dat")
    conn = zones[0]["conn"]
    assert conn.shape == (4, 8)
    assert [len(set(row)) for row in conn] == [4, 5, 6, 8]  # tet, pyramid, wedge, hex


# ---- wall quantities ---------------------------------------------------------


def _job(mesh, results, path, **extra):
    props = {"mu": fm.MU, "rho": fm.RHO, "k": fm.K}
    return SimpleNamespace(
        project=None, record=None, mesh=mesh, results=results, step=results.steps[0],
        path=path, quantity=extra.pop("quantity", None), selection="Channel/Walls",
        units=extra.pop("units", "si"), extra={"properties": props, **extra},
    )  # fmt: skip


def _read_csv(path):
    header = path.read_text().split("\n")[0].split(",")
    return header, np.loadtxt(path, delimiter=",", skiprows=1)


D1 = fm.H / 10 / 2  # the first cells' centres from the wall
Q = -fm.K * (fm.T_SLOPE + fm.T_CURVE * D1)  # k (T_w - T_1) / d of the synthetic field


def test_wall_shear_and_pressure_of_poiseuille_flow(tmp_path):
    """tau = mu u_1 / d, the solvers' own wall gradient: for Poiseuille flow sampled at
    the cell centres, 6 mu U / h (1 - d / h), along the flow; the wall pressure falls at
    dp/dx = -12 mu U / h^2; y+ = rho u_tau d / mu."""
    mesh = fm.channel()
    files, notes = wall.write(_job(mesh, fm.poiseuille(mesh), tmp_path / "walls.csv"))
    header, table = _read_csv(files[0])
    col = {h: table[:, i] for i, h in enumerate(header)}
    assert len(table) == 32  # both walls' faces
    tau = col["wall shear stress magnitude [Pa]"]
    exact = 6 * fm.MU * fm.U / fm.H * (1 - D1 / fm.H)
    np.testing.assert_allclose(tau, exact, rtol=5e-9)  # 9 digits written
    np.testing.assert_allclose(col["wall shear stress x [Pa]"], tau, rtol=1e-12)
    np.testing.assert_allclose(col["wall shear stress y [Pa]"], 0, atol=1e-15)
    x, p = col["x [m]"], col["wall pressure [Pa]"]
    assert np.polyfit(x, p, 1)[0] == pytest.approx(-12 * fm.MU * fm.U / fm.H**2, rel=1e-9)
    u_tau = np.sqrt(exact / fm.RHO)
    np.testing.assert_allclose(col["y+"], fm.RHO * u_tau * D1 / fm.MU, rtol=5e-9)
    assert "Heat transfer coefficient" in " ".join(notes)  # skipped: adiabatic, said
    assert set(np.round(col["y [m]"] / fm.H, 9)) == {0.0, 1.0}


@pytest.mark.parametrize(
    "condition",
    [("temperature", fm.T_WALL), ("heat_flux", Q), ("convection", 50.0, fm.T_WALL + Q / 50.0)],
)
def test_wall_temperature_and_flux(condition):
    """The same wall temperature and flux into the fluid from any wall condition that
    the cells' temperatures agree with."""
    mesh = fm.channel()
    results = fm.poiseuille(mesh)
    vol = fluid.volume(mesh)
    w = wall.wall_faces(mesh, vol, fluid.cell_faces(vol), fluid.boundaries(mesh)["Channel/Walls"])
    t = wall._cells(results.steps[0], "temperature", vol, mesh)
    t_wall, q = wall.thermal(w, t, condition, fm.K)
    np.testing.assert_allclose(t_wall, fm.T_WALL, rtol=1e-12)
    np.testing.assert_allclose(q, Q, rtol=1e-9)


def test_wall_cloud_in_display_units(tmp_path, engineering_units):
    mesh = fm.channel()
    files, _ = wall.write(_job(mesh, fm.poiseuille(mesh), tmp_path / "w.csv",
                               quantity="wall_pressure", units="display"))  # fmt: skip
    header, table = _read_csv(files[0])
    # the faces' areas last (ADR 0044: a conservative import's weights)
    assert header == ["x [mm]", "y [mm]", "z [mm]", "wall pressure [MPa]", "area [mm²]"]
    assert table[:, 0].max() < fm.LENGTH * 1000 and table[:, 0].max() > fm.LENGTH * 500
    assert (table[:, -1] > 0).all()


def test_wall_cloud_refuses_unknowns(tmp_path):
    from puzzle.errors import PuzzleError

    mesh = fm.channel()
    with pytest.raises(PuzzleError, match="no wall quantity 'velocity'"):
        wall.write(_job(mesh, fm.poiseuille(mesh), tmp_path / "w.csv", quantity="velocity"))
    job = _job(mesh, fm.poiseuille(mesh), tmp_path / "w.csv")
    job.selection = "Channel/Nowhere"
    with pytest.raises(PuzzleError, match="no face selection"):
        wall.write(job)


def test_formats_are_listed():
    for key in ("ensight", "cgns", "tecplot", "wall_cloud"):
        assert key in EXPORTS
    assert EXPORTS["wall_cloud"].takes("selection") and not EXPORTS["cgns"].takes("step")


def test_element_fields_missing_cells_are_nan():
    f = Field("p", "element", [""], np.array([3, 1]), np.array([[30.0], [10.0]]))
    np.testing.assert_array_equal(fluid.lookup(f, np.array([1, 2, 3]))[:, 0], [10, np.nan, 30])
