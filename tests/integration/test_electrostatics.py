"""Two-layer parallel-plate capacitor: PTFE and air between electrodes.

The sides are insulated (natural boundary), so the field is exactly one-dimensional:
the potential is linear in each layer and the capacitance is
    C = eps0 A / (d1 / eps_r1 + d2 / eps_r2).
The PTFE and air are separate parts joined by a conformal bonded interface, each with
its own material.
"""

import gmsh
import numpy as np
import pytest

from puzzle.geometry import gmsh_session, import_step
from puzzle.jobs import runtime_for
from puzzle.materials import add_library_material, library_material
from puzzle.model import (
    Component,
    Contact,
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

EPS0 = 8.8541878128e-12
SIDE, D1, D2 = 0.05, 0.002, 0.003  # m: plate side, PTFE thickness, air gap
VOLTS = 100.0

SOLVERS = {"elmer": "ElmerSolver"}
solver = pytest.mark.parametrize(
    "solver",
    [
        pytest.param(
            n,
            marks=pytest.mark.skipif(not runtime_for(n).available(e), reason=f"{n} not available"),
        )
        for n, e in SOLVERS.items()
    ],
)


def _slab(path, thickness):
    with gmsh_session("slab"):
        gmsh.model.occ.addBox(0, 0, 0, SIDE, SIDE, thickness)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    return model, [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("z", thickness)),
    ]


def _capacitor(tmp_path, solver_name):
    _, ptfe_sel = _slab(tmp_path / "ptfe.step", D1)
    _, air_sel = _slab(tmp_path / "air.step", D2)
    p = Project(name="capacitor")
    ptfe_geo = p.add(Geometry(name="PTFE", source="ptfe.step", selections=ptfe_sel))
    air_geo = p.add(Geometry(name="Air", source="air.step", selections=air_sel))
    ptfe, air = add_library_material(p, "ptfe"), add_library_material(p, "air")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=4, unit="mm")))
    comp = p.add(
        Component(
            name="Capacitor",
            parts=[
                PartInstance(name="Dielectric", geometry=ptfe_geo.id),
                PartInstance(name="Gap", geometry=air_geo.id, translation=(0.0, 0.0, D1)),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=ptfe.id, selection="Dielectric/Body"),
                MaterialAssignment(material=air.id, selection="Gap/Body"),
            ],
            contacts=[
                Contact(
                    name="Interface", type="bonded", source="Dielectric/Top", target="Gap/Bottom"
                )
            ],
            physics=[
                Physics(
                    type="electrostatics",
                    features=[
                        Feature(type="ground", selection="Dielectric/Bottom"),
                        Feature(
                            type="electric_potential",
                            selection="Gap/Top",
                            values={"V": Quantity(value=VOLTS, unit="V")},
                        ),
                    ],
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="Electrostatics",
            component=comp.id,
            solver=solver_name,
            steps=[SolveStep(type="stationary")],
        )
    )
    return p, study


@solver
def test_two_layer_capacitor(tmp_path, solver):
    project, study = _capacitor(tmp_path, solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)

    eps1 = library_material("ptfe").properties["relative_permittivity"].to_si()
    eps2 = library_material("air").properties["relative_permittivity"].to_si()
    series = D1 / eps1 + D2 / eps2
    interface = VOLTS * (D1 / eps1) / series  # potential at the PTFE/air interface

    phi = run.results.last("electric_potential")
    z = run.mesh.node_coords(phi.ids)[:, 2]
    exact = np.where(z <= D1, interface * z / D1, interface + (VOLTS - interface) * (z - D1) / D2)
    assert np.abs(phi.values[:, 0] - exact).max() < 1e-6 * VOLTS

    capacitance = EPS0 * SIDE**2 / series
    energy = run.results.last("electric_energy").values[0, 0]
    assert 2 * energy / VOLTS**2 == pytest.approx(capacitance, rel=1e-6)
