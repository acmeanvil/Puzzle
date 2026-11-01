"""Fluid exports (ADR 0043 step 4) of real CFD runs: the laminar channel benchmark and
the heated channel, exported as EnSight Gold, CGNS, Tecplot and wall clouds, read back,
and the wall quantities checked against plane Poiseuille flow:

    tau_w = 6 mu U / h,   dp/dx = -12 mu U / h^2,   Nu = h_c (2 h) / k = 8.235
    (uniform wall flux, the bulk temperature the reference),
    and in RANS (k-epsilon, wall functions) tau_w = -(H / 2) dp/dx of the same run.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from solvers import over
from test_exports_fluid import _blocks, _centres, _cgns, _ensight, read_tecplot
from vtkmodules.util.numpy_support import vtk_to_numpy

from puzzle.model import Material, Study
from puzzle.post.exports import export
from puzzle.project import load_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "channel_flow"
HERE = Path(__file__).parent


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ch = _module(BENCHMARK / "build.py", "channel_build")


def _cells(mesh):
    blocks = [b for b in mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    centres = np.vstack([
        mesh.node_coords(b.connectivity.ravel()).reshape(*b.connectivity.shape, 3).mean(1)
        for b in blocks
    ])  # fmt: skip
    return ids, centres


def _nearest(points, centres):
    from scipy.spatial import cKDTree

    d, i = cKDTree(centres).query(points)
    assert d.max() < 1e-6  # float32 coordinates in EnSight
    return i


@pytest.fixture(scope="module", params=["code_saturne", "openfoam"])
def channel(request, tmp_path_factory):
    from solvers import available

    solver = request.param
    if not available(solver):
        pytest.skip(f"{solver} not available")
    tmp = tmp_path_factory.mktemp(f"fluid-{solver}")
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    study.solver, study.shootout = solver, None
    store = ResultStore(tmp / "results")
    run = run_study(project, study.id, BENCHMARK, tmp / "run", store=store, timeout_s=1800)
    return project, store, run, tmp, solver


def test_channel_exports_read_back(channel):
    project, store, run, tmp, solver = channel
    mesh, results = run.mesh, run.results
    ids, centres = _cells(mesh)
    pressure, velocity = results.last("pressure"), results.last("velocity")

    files = export(project, store, run.run_id, "ensight", tmp / "out" / "flow")
    assert files[0].suffix == ".case" and files[-1].suffix == ".txt"
    parts = dict(_blocks(_ensight(files[0]).GetOutput()))
    assert {"Channel/Walls", "Channel/Inlet", "Channel/Outlet", "Channel/Sides"} <= set(parts)
    vol = parts["volume"]
    at = _nearest(_centres(vol), centres)
    got = vtk_to_numpy(vol.GetCellData().GetArray("pressure"))
    np.testing.assert_allclose(got, pressure.at(ids[at])[:, 0], rtol=2e-7, atol=1e-9)
    from puzzle.adapters.code_saturne.ensight import read_case, read_variable

    cs_parts, variables = read_case(files[0])
    _, n, path = variables["velocity"]
    u = read_variable(path, cs_parts, n)[: len(ids)]
    at = _nearest(cs_parts[0].blocks[0].centroids, centres)
    np.testing.assert_allclose(u, velocity.at(ids[at]), rtol=2e-7, atol=1e-10)

    files = export(project, store, run.run_id, "cgns", tmp / "out" / "flow")
    blocks = _blocks(_cgns(files[0]).GetOutput())
    assert {"Channel_Walls", "Channel_Inlet", "Channel_Outlet"} <= {n for n, _ in blocks}
    vol = blocks[0][1]
    at = _nearest(_centres(vol), centres)
    got = vtk_to_numpy(vol.GetCellData().GetArray("Velocity"))
    np.testing.assert_allclose(got, velocity.at(ids[at]), rtol=1e-12, atol=1e-15)
    walls = dict(blocks)["Channel_Walls"]
    assert set(np.round(_centres(walls)[:, 1] / ch.H, 6)) == {0.0, 1.0}

    files = export(project, store, run.run_id, "tecplot", tmp / "out" / "flow")
    names, zones = read_tecplot(files[0])
    assert zones[0]["title"] == "volume" and "Channel/Walls" in [z["title"] for z in zones]
    z = zones[0]
    xyz = np.column_stack([z["data"][n] for n in names[:3]])
    at = _nearest(xyz[z["conn"] - 1].mean(axis=1), centres)  # hexahedra: 8 corners
    np.testing.assert_allclose(z["data"]["pressure [Pa]"], pressure.at(ids[at])[:, 0],
                               rtol=1e-8, atol=1e-12)  # fmt: skip


def test_channel_wall_quantities(channel):
    project, store, run, tmp, solver = channel
    mu = project.of_type(Material)[0].properties["dynamic_viscosity"].to_si()
    files = export(project, store, run.run_id, "wall_cloud", tmp / "out" / "walls")
    csv = files[0]
    header = csv.read_text().splitlines()[0].split(",")
    table = np.loadtxt(csv, delimiter=",", skiprows=1)
    col = {h: table[:, i] for i, h in enumerate(header)}
    x = col["x [m]"]
    developed = (x > 0.03) & (x < 0.09)
    tau = col["wall shear stress magnitude [Pa]"][developed]
    exact = 6 * mu * ch.U / ch.H
    print(f"{solver}: wall shear {tau.mean():.6g} Pa (exact {exact:.6g}), "
          f"spread {np.ptp(tau) / exact:.2%}")  # fmt: skip
    assert tau.mean() == pytest.approx(exact, rel=0.01)
    assert np.abs(tau / exact - 1).max() < 0.02
    assert (col["wall shear stress x [Pa]"][developed] > 0).all()  # along the flow
    slope = np.polyfit(x[developed], col["wall pressure [Pa]"][developed], 1)[0]
    print(f"{solver}: wall dp/dx {slope:.6g} Pa/m (exact {-12 * mu * ch.U / ch.H**2:.6g})")
    assert slope == pytest.approx(-12 * mu * ch.U / ch.H**2, rel=0.02)
    assert "Heat transfer coefficient" in files[-1].read_text()  # said why not written


@over("code_saturne")
def test_heated_channel_heat_transfer_coefficient(tmp_path, solver):
    heated = _module(HERE / "test_heated_channel.py", "heated_channel")
    project, study = heated._channel(tmp_path, solver)
    store = ResultStore(tmp_path / "results")
    run = run_study(project, study.id, tmp_path, tmp_path / "run", store=store, timeout_s=3600)
    ids, centres = _cells(run.mesh)
    T, u = run.results.last("temperature"), run.results.last("velocity")
    at = np.abs(centres[:, 0] - 0.06) < 0.3e-3
    temp, speed = T.at(ids[at])[:, 0], u.at(ids[at])[:, 0]
    bulk = float((temp * speed).sum() / speed.sum())
    files = export(project, store, run.run_id, "wall_cloud", tmp_path / "walls",
                   reference_temperature=bulk)  # fmt: skip
    header = files[0].read_text().splitlines()[0].split(",")
    table = np.loadtxt(files[0], delimiter=",", skiprows=1)
    col = {h: table[:, i] for i, h in enumerate(header)}
    near = np.abs(col["x [m]"] - 0.06) < 0.3e-3
    np.testing.assert_allclose(col["wall heat flux [W/m^2]"], heated.Q, rtol=1e-9)
    h = col["heat transfer coefficient [W/(m^2*K)]"][near]
    exact = 8.235 * heated.K / (2 * heated.H)
    print(f"{solver}: h at 60 mm {h.mean():.5g} W/m2K (Nu 8.235: {exact:.5g})")
    assert h.mean() == pytest.approx(exact, rel=0.03)
    assert col["reference temperature [K]"] == pytest.approx(bulk)


@over("code_saturne", "openfoam")
def test_turbulent_wall_shear_balances_the_pressure_drop(tmp_path, solver):
    """RANS k-epsilon at Re 1e5 (first cells at y+ ~ 200): the wall-function shear
    stress against the run's own force balance, tau_w = -(H / 2) dp/dx."""
    turbulent = _module(HERE / "test_turbulent_channel.py", "turbulent_channel")
    project, study = turbulent._project(tmp_path, "k-epsilon", solver)
    store = ResultStore(tmp_path / "results")
    run = run_study(project, study.id, tmp_path, tmp_path / "run", store=store, timeout_s=3600)
    files = export(project, store, run.run_id, "wall_cloud", tmp_path / "walls")
    header = files[0].read_text().splitlines()[0].split(",")
    table = np.loadtxt(files[0], delimiter=",", skiprows=1)
    col = {h: table[:, i] for i, h in enumerate(header)}
    x = col["x [m]"]
    developed = (x > 0.6) & (x < 0.9)
    dpdx = np.polyfit(x[developed], col["wall pressure [Pa]"][developed], 1)[0]
    balance = -turbulent.H / 2 * dpdx
    tau = col["wall shear stress magnitude [Pa]"][developed].mean()
    y_plus = col["y+"][developed].mean()
    print(f"{solver}: wall-function tau {tau:.4g} Pa, balance {balance:.4g} Pa "
          f"({tau / balance - 1:+.1%}), y+ {y_plus:.0f}")  # fmt: skip
    assert tau == pytest.approx(balance, rel=0.05)  # measured: CS -2.4 %, OpenFOAM +0.2 %
    assert 30 < y_plus < 500
