"""Fully developed turbulent channel flow (Code_Saturne, RANS with wall functions).

Water at 5 m/s mean in a 20 mm channel (Re = 100,000 on the full height), 1 m long,
side faces symmetry planes; the first cells sit at y+ ~ 200, where wall functions
belong. Against Dean's correlations for plane channels (1978):

    skin friction   Cf = 0.073 Re^-1/4,  so  dp/dx = -Cf rho U^2 / H
    centreline      Uc / U = 1.28 Re^-0.0116

RANS with wall functions is good to a few per cent on these; the test allows 10% on
the friction and 3% on the velocity ratio. Measured (Code_Saturne 9.2, 2 mm cells):
k-epsilon -7.0% / -0.3%, k-omega SST -3.2% / -1.9%. At Re = 13,300 (Re_tau ~ 390, air)
both under-predicted the friction by 13-16% on 2 mm and 1.25 mm meshes alike: wall
functions need a developed log layer.
"""

import gmsh
import numpy as np
import pytest
from solvers import available

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

pytestmark = pytest.mark.skipif(not available("code_saturne"), reason="code_saturne not available")

H, LENGTH, WIDTH, U = 0.02, 1.0, 0.004, 5.0
WATER = library_material("water").properties
RHO, MU = WATER["density"].to_si(), WATER["dynamic_viscosity"].to_si()
RE = RHO * U * H / MU


def _project(tmp_path, model):
    step = tmp_path / "channel.step"
    with gmsh_session("channel"):
        gmsh.model.occ.addBox(0, 0, 0, LENGTH, H, WIDTH)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    m = import_step(step)
    p = Project(name="turbulent channel")
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
    water = add_library_material(p, "water")
    mesh = p.add(
        MeshRecipe(
            name="Hexahedra",
            global_size=Quantity(value=2, unit="mm"),
            element_order=1,
            element_shape="hexahedra",
        )
    )
    flow = Physics(
        type="fluid_flow",
        settings={"turbulence_model": model},
        features=[
            Feature(
                type="velocity_inlet",
                selection="Channel/Inlet",
                values={
                    "U": Quantity(value=U, unit="m/s"),
                    "intensity": Quantity(value=0.05),
                    "hydraulic_diameter": Quantity(value=2 * H, unit="m"),  # plane channel: 2H
                },
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
    comp = p.add(
        Component(
            name="Channel",
            parts=[PartInstance(name="Channel", geometry=geo.id)],
            mesh=mesh.id,
            physics=[flow],
            materials=[MaterialAssignment(material=water.id, selection="Channel/Fluid")],
        )
    )
    study = p.add(
        Study(
            name="Turbulent",
            component=comp.id,
            solver="code_saturne",
            steps=[SolveStep(type="stationary", settings={"iterations": 1500})],
        )
    )
    return p, study


@pytest.mark.parametrize("model", ["k-epsilon", "k-omega-SST"])
def test_turbulent_channel(tmp_path, model):
    project, study = _project(tmp_path, model)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    velocity, pressure = run.results.last("velocity"), run.results.last("pressure")
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
    ids, x, y = ids[order], centres[order, 0], centres[order, 1]

    def mean_pressure(x0):
        return pressure.at(ids[np.abs(x - x0) < 1.1e-3])[:, 0].mean()

    dpdx = (mean_pressure(0.9) - mean_pressure(0.6)) / 0.3
    dean = -0.073 * RE**-0.25 * RHO * U**2 / H
    at = np.abs(x - 0.85) < 1.1e-3
    u = velocity.at(ids[at])[:, 0]
    ratio, dean_ratio = u.max() / u.mean(), 1.28 * RE**-0.0116
    print(
        f"{model}: Re {RE:.0f}, dp/dx {dpdx:.2f} Pa/m (Dean {dean:.2f}), "
        f"Uc/U {ratio:.4f} (Dean {dean_ratio:.4f})"
    )
    assert dpdx == pytest.approx(dean, rel=0.10)
    assert ratio == pytest.approx(dean_ratio, rel=0.03)
