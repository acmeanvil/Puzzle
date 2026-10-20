"""Laminar flow of air between uniformly heated plates (Code_Saturne, OpenFOAM).

Air enters the 10 mm channel at 15 mm/s and 20 C (Re = 10 on the gap); both plates
add q = 10 W/m^2, the side faces are symmetry planes. Two exact results:

- energy balance: where the flow is fully developed the axial gradient is uniform, so
  axial conduction into and out of a slice cancels and the bulk (velocity-weighted)
  temperature rises at exactly dTb/dx = 2 q / (rho cp U h). (Near the inlet, where heat
  conducts back out through the fixed-temperature inlet at this Peclet number, ~7, and
  near the outlet's zero-gradient condition it does not.)
- fully developed profile (Poiseuille flow, uniform wall flux; Nu = 8.235 on 2h):
  T(eta) - T(1/2) = (12 q h / k) (eta^3/6 - eta^4/12 - eta/12 + 5/192), eta = y/h.

Air's low Prandtl number develops the profile within a few millimetres.
"""

import gmsh
import numpy as np
import pytest
from solvers import over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material, library_material
from puzzle.model import (
    Component,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.solve import run_study

H, LENGTH, WIDTH = 0.01, 0.1, 0.004
U, Q, T_IN = 0.015, 10.0, 293.15
AIR = library_material("air").properties
RHO, CP, K = (AIR[p].to_si() for p in ("density", "specific_heat", "thermal_conductivity"))


def _channel(tmp_path, solver):
    step = tmp_path / "channel.step"
    with gmsh_session("channel"):
        gmsh.model.occ.addBox(0, 0, 0, LENGTH, H, WIDTH)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    m = import_step(step)
    p = Project(name="heated channel")
    geo = p.add(
        Geometry(
            name="Channel",
            source=step.name,
            selections=[
                NamedSelection(name="Fluid", entity="body", members=["body-1"]),
                NamedSelection(name="Inlet", entity="face", members=m.faces_on_plane("x", 0.0)),
                NamedSelection(name="Outlet", entity="face", members=m.faces_on_plane("x", LENGTH)),
                NamedSelection(
                    name="Walls",
                    entity="face",
                    members=m.faces_on_plane("y", 0.0) + m.faces_on_plane("y", H),
                ),
                NamedSelection(
                    name="Sides",
                    entity="face",
                    members=m.faces_on_plane("z", 0.0) + m.faces_on_plane("z", WIDTH),
                ),
            ],
        )
    )
    air = add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Hexahedra",
            global_size=Quantity(value=0.5, unit="mm"),
            element_order=1,
            element_shape="hexahedra",
        )
    )
    flow = Physics(
        type="fluid_flow",
        features=[
            Feature(
                type="velocity_inlet",
                selection="Channel/Inlet",
                values={"U": Quantity(value=U, unit="m/s")},
            ),
            Feature(
                type="pressure_outlet",
                selection="Channel/Outlet",
                values={"p": Quantity(value=0, unit="Pa")},
            ),
            Feature(type="wall", selection="Channel/Walls"),
            Feature(type="symmetry", selection="Channel/Sides"),
        ],
    )
    heat = Physics(
        type="heat_transfer",
        features=[
            Feature(
                type="temperature",
                selection="Channel/Inlet",
                values={"T": Quantity(value=T_IN, unit="K")},
            ),
            Feature(
                type="heat_flux",
                selection="Channel/Walls",
                values={"q": Quantity(value=Q, unit="W/m^2")},
            ),
        ],
    )
    comp = p.add(
        Component(
            name="Channel",
            parts=[PartInstance(name="Channel", geometry=geo.id)],
            mesh=mesh.id,
            physics=[flow, heat],
            materials=[MaterialAssignment(material=air.id, selection="Channel/Fluid")],
        )
    )
    study = p.add(
        Study(
            name="Heated",
            component=comp.id,
            solver=solver,
            steps=[
                SolveStep(
                    type="stationary",
                    settings={
                        "iterations": 300,
                        # Time-march to steady: axial conduction relaxes over L^2/alpha
                        # ~ 470 s; the steady algorithm needs ~4000 iterations (same answer).
                        "pseudo_time_step": Quantity(value=2, unit="s"),
                    },
                )
            ],
        )
    )
    return p, study


@over("code_saturne", "openfoam")
def test_heated_channel(tmp_path, solver):
    project, study = _channel(tmp_path, solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    T, u = run.results.last("temperature"), run.results.last("velocity")
    blocks = [b for b in run.mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    centres = np.vstack(
        [
            run.mesh.node_coords(b.connectivity.ravel())
            .reshape(*b.connectivity.shape, 3)
            .mean(axis=1)
            for b in blocks
        ]
    )
    order = np.argsort(ids)
    ids, centres = ids[order], centres[order]
    x, y = centres[:, 0], centres[:, 1]

    gradient = 2 * Q / (RHO * CP * U * H)

    def bulk(x0):
        at = np.abs(x - x0) < 0.3e-3
        temp, speed = T.at(ids[at])[:, 0], u.at(ids[at])[:, 0]
        return (temp * speed).sum() / speed.sum()

    slope = (bulk(0.08) - bulk(0.04)) / 0.04
    print(f"{solver}: bulk dT/dx, 40 to 80 mm: {slope:.4f} K/m (balance {gradient:.4f})")
    assert slope == pytest.approx(gradient, rel=0.01)

    at = np.abs(x - 0.06) < 0.3e-3
    eta, temp = y[at] / H, T.at(ids[at])[:, 0]
    shape = 12 * Q * H / K * (eta**3 / 6 - eta**4 / 12 - eta / 12 + 5 / 192)
    centre = temp[np.argmin(np.abs(eta - 0.5))] if np.any(np.abs(eta - 0.5) < 1e-9) else None
    measured = temp - (temp.mean() if centre is None else centre)
    expected = shape - (shape.mean() if centre is None else 0.0)
    error = np.abs(measured - expected).max() / np.ptp(expected)
    print(f"{solver}: profile at 60 mm: wall-centre {np.ptp(expected):.4f} K, error {error:.2%}")
    assert error < 0.02
