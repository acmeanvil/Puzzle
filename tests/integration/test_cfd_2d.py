"""2D CFD (ADR 0048 round 5): the 2D mesh solved one cell thick, against closed forms.

- **plane Poiseuille:** the channel benchmark's 10 mm gap, 50 mm long, water at a mean
  1 mm/s (Re 10), as a 2D planar region: u(y) = 6 U y (h - y) / h², peak 1.5 U,
  dp/dx = -12 μ U / h², with the 3D benchmark's tolerances (1 %, 2 %);
- **heated plates:** air at 15 mm/s and 20 °C between plates adding 10 W/m² each, 100
  mm long, as the 3D heated-channel benchmark: the bulk temperature rises at
  2 q / (ρ cp U h), and the developed profile is Nu = 8.235's (1 %, 2 %);
- **Hagen–Poiseuille:** a pipe R = 5 mm, 50 mm long, axisymmetric (Y the axis), the
  same water and speed: u(r) = 2 U (1 - r² / R²), dp/dy = -8 μ U / R².
"""

import numpy as np
import pytest
import test_mesh_layer as base
from scipy.spatial import cKDTree
from solvers import over

from puzzle.model import Material, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")

U = 0.001


def _project(dimension, size_mm):
    return base.cfd_2d(dimension, "quadrilaterals", size_mm)


def _solve(tmp_path, p, comp, solver):
    study = p.add(
        Study(
            name="flow",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="stationary", settings={"iterations": 1000})],
        )
    )
    return run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)


def _centres(run):
    blocks = run.mesh.region_blocks()
    ids = np.concatenate([b.ids for b in blocks])
    c = np.vstack([run.mesh.node_coords(b.connectivity.ravel())
                   .reshape(*b.connectivity.shape, 3).mean(axis=1) for b in blocks])  # fmt: skip
    order = np.argsort(ids)
    return ids[order], c[order]


@over("openfoam", "code_saturne")
def test_plane_poiseuille(tmp_path, solver):
    p, comp = _project("2d_planar", 0.5)
    mu = p.of_type(Material)[0].properties["dynamic_viscosity"].to_si()
    run = _solve(tmp_path, p, comp, solver)
    ids, c = _centres(run)
    velocity, pressure = run.results.last("velocity"), run.results.last("pressure")
    assert velocity.location == "element" and set(velocity.ids) == set(ids)
    H, x, y = base.H, c[:, 0], c[:, 1]
    at = np.abs(x - 0.035) < 0.3e-3
    u = velocity.at(ids[at])[:, 0]
    exact = 6 * U * y[at] * (H - y[at]) / H**2
    peak = u.max() / U
    l2 = np.linalg.norm(u - exact) / np.linalg.norm(exact)

    def mean_p(x0):
        return pressure.at(ids[np.abs(x - x0) < 0.3e-3])[:, 0].mean()

    dpdx = (mean_p(0.045) - mean_p(0.025)) / 0.02
    print(f"\n{solver} plane Poiseuille: peak {peak:.5f} U, profile L2 {l2:.5f}, "
          f"dp/dx {dpdx:.6g} vs {-12 * mu * U / H**2:.6g}")  # fmt: skip
    assert peak == pytest.approx(1.5, rel=0.01)
    assert l2 < 0.01
    assert dpdx == pytest.approx(-12 * mu * U / H**2, rel=0.02)
    assert np.abs(velocity.values[:, 2]).max() < 1e-6 * U  # nothing across the layer


@over("openfoam", "code_saturne")
def test_hagen_poiseuille_axisymmetric(tmp_path, solver):
    p, comp = _project("2d_axisymmetric", 0.25)
    mu = p.of_type(Material)[0].properties["dynamic_viscosity"].to_si()
    run = _solve(tmp_path, p, comp, solver)
    ids, c = _centres(run)
    velocity, pressure = run.results.last("velocity"), run.results.last("pressure")
    R, r, y = base.R, c[:, 0], c[:, 1]
    at = np.abs(y - 0.035) < 0.15e-3
    u = velocity.at(ids[at])[:, 1]
    exact = 2 * U * (1 - r[at] ** 2 / R**2)
    peak = u.max() / U
    l2 = np.linalg.norm(u - exact) / np.linalg.norm(exact)

    def mean_p(y0):
        return pressure.at(ids[np.abs(y - y0) < 0.15e-3])[:, 0].mean()

    dpdy = (mean_p(0.045) - mean_p(0.025)) / 0.02
    print(f"\n{solver} Hagen-Poiseuille: peak {peak:.5f} U, profile L2 {l2:.5f}, "
          f"dp/dy {dpdy:.6g} vs {-8 * mu * U / R**2:.6g}")  # fmt: skip
    assert peak == pytest.approx(2.0, rel=0.01)
    assert l2 < 0.01
    assert dpdy == pytest.approx(-8 * mu * U / R**2, rel=0.02)


@over("openfoam", "code_saturne")
def test_heated_plates_2d(tmp_path, solver):
    from puzzle.materials import library_material
    from puzzle.model import Feature, Physics, Quantity

    q, t_in, speed = 10.0, 293.15, 0.015
    air = library_material("air").properties
    rho, cp, k = (air[n].to_si() for n in ("density", "specific_heat", "thermal_conductivity"))
    p, comp = base.cfd_2d("2d_planar", "quadrilaterals", 0.5, length=0.1, fluid="air",
                          speed=speed)  # fmt: skip
    comp.physics.append(Physics(type="heat_transfer", features=[
        Feature(type="temperature", selection="Duct/Inlet",
                values={"T": Quantity(value=t_in, unit="K")}),
        Feature(type="heat_flux", selection="Duct/Walls",
                values={"q": Quantity(value=q, unit="W/m^2")}),
    ]))  # fmt: skip
    # As the 3D benchmark: time-marched to steady (axial conduction relaxes slowly).
    settings = {"iterations": 300, "pseudo_time_step": Quantity(value=2, unit="s")}
    study = p.add(Study(name="heated", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary", settings=settings)]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    ids, c = _centres(run)
    T, u = run.results.last("temperature"), run.results.last("velocity")
    H = base.H

    tree = cKDTree(c[:, :2])
    eta = (np.arange(200) + 0.5) / 200  # points evenly across the gap: the cell at each

    def line(field, x0, comp=0):
        _d, i = tree.query(np.column_stack([np.full_like(eta, x0), eta * H]))
        return field.at(ids[i])[:, comp]

    def bulk(x0):
        return (line(T, x0) * line(u, x0)).sum() / line(u, x0).sum()

    slope = (bulk(0.08) - bulk(0.04)) / 0.04
    gradient = 2 * q / (rho * cp * speed * H)
    # The profile: each cell near x = 60 mm against the closed form at its own centre,
    # up to a constant
    at = np.abs(c[:, 0] - 0.06) < 0.3e-3
    y = c[at, 1] / H
    shape = 12 * q * H / k * (y**3 / 6 - y**4 / 12 - y / 12 + 5 / 192)
    # (each less the axial rise from 60 mm: the band is 0.6 mm wide)
    d = T.at(ids[at])[:, 0] - gradient * (c[at, 0] - 0.06) - shape
    error = np.abs(d - d.mean()).max() / np.ptp(shape)
    print(f"\n{solver} heated plates 2D: bulk dT/dx {slope:.5f} vs {gradient:.5f}, "
          f"profile error {error:.4f}")  # fmt: skip
    assert slope == pytest.approx(gradient, rel=0.01)
    assert error < 0.02
