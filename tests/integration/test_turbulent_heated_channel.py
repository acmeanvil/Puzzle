"""Turbulent flow of air between heated plates: the energy balance (ADR 0090 OF-1).

Air at 30 m/s between plates 10 mm apart (Re 2e4), 100 mm long, both walls adding
q = 1000 W/m², as a 2D planar region on 0.5 mm quadrilaterals (y+ about 30 at the
first cell), k-epsilon and k-omega SST with wall functions. Whatever the profiles,
energy conservation fixes the bulk (mass flux weighted) temperature's rise:

    d T_bulk / dx = 2 q / (rho cp U h),

with axial conduction negligible at this Péclet number. The bulk is taken over bands of
cells 20 mm wide about x = 40 and 80 mm (T rises linearly, so a band's average is the
value at its middle), each cell weighted by u times its area; the slope between them
against the closed form within the laminar heated channel's 1 % (`test_cfd_2d.py`, on
the same 0.5 mm cells: on 1 mm ones the cell-centre estimate of the bulk is itself 1.5 %
off, either way).

OpenFOAM's temperature is a passive scalar whose diffusivity is nu/Pr + nut/Pr_t, and
under wall functions nut is not zero on the wall face: a gradient of q/k there put
more than q into the fluid (slope 1.56 times the balance with k-epsilon, 1.47 with SST
here; 1.9-2.4 on 1 mm cells). The wall gradient now divides q by the wall face's own
rho cp D (`exprMixed`). Measured 2026-11-17, slope over the balance: OpenFOAM
k-epsilon 0.9993, SST 0.9994; Code_Saturne k-epsilon 1.0010, SST 0.9993.
"""

import numpy as np
import pytest
import test_mesh_layer as base
from solvers import available

from puzzle.materials import library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")

SPEED, Q, LENGTH = 30.0, 1000.0, 0.1


def _cases():
    for solver in ("openfoam", "code_saturne"):
        for model in ("k-epsilon", "k-omega-SST"):
            yield pytest.param(
                solver, model, id=f"{solver}-{model}",
                marks=pytest.mark.skipif(not available(solver), reason=f"{solver} not available"),
            )  # fmt: skip


def _cells(run):
    """Each region cell's id, centre and area (the 2D model's cells, in id order)."""
    ids, centres, areas = [], [], []
    for b in run.mesh.region_blocks():
        xyz = run.mesh.node_coords(b.connectivity.ravel()).reshape(*b.connectivity.shape, 3)
        corners = xyz[:, : 4 if b.element.startswith("quad") else 3, :2]
        x, y = corners[..., 0], corners[..., 1]
        areas.append(0.5 * np.abs((x * np.roll(y, -1, 1) - np.roll(x, -1, 1) * y).sum(1)))
        ids.append(b.ids)
        centres.append(xyz.mean(axis=1))
    ids, centres, areas = np.concatenate(ids), np.vstack(centres), np.concatenate(areas)
    order = np.argsort(ids)
    return ids[order], centres[order], areas[order]


@pytest.mark.parametrize(("solver", "model"), list(_cases()))
def test_turbulent_heated_plates_balance_energy(tmp_path, solver, model):
    air = library_material("air").properties
    rho, cp = (air[n].to_si() for n in ("density", "specific_heat"))
    p, comp = base.cfd_2d("2d_planar", "quadrilaterals", 0.5, length=LENGTH, fluid="air",
                          speed=SPEED)  # fmt: skip
    comp.physics[0].settings["turbulence_model"] = model
    comp.physics.append(Physics(type="heat_transfer", features=[
        Feature(type="temperature", selection="Duct/Inlet",
                values={"T": Quantity(value=293.15, unit="K")}),
        Feature(type="heat_flux", selection="Duct/Walls",
                values={"q": Quantity(value=Q, unit="W/m^2")}),
    ]))  # fmt: skip
    study = p.add(Study(name="heated", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary",
                                         settings={"iterations": 2000})]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    ids, c, area = _cells(run)
    T = run.results.last("temperature").at(ids)[:, 0]
    u = run.results.last("velocity").at(ids)[:, 0]

    def bulk(x0, half=0.01):
        band = np.abs(c[:, 0] - x0) < half
        w = u[band] * area[band]
        return float((w * T[band]).sum() / w.sum())

    slope = (bulk(0.08) - bulk(0.04)) / 0.04
    expected = 2 * Q / (rho * cp * SPEED * base.H)
    print(f"\n{solver} {model}: bulk dT/dx {slope:.4f} K/m against {expected:.4f}, "
          f"ratio {slope / expected:.4f}")  # fmt: skip
    assert slope == pytest.approx(expected, rel=0.01)
