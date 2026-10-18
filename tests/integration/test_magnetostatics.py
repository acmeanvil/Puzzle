"""A straight copper wire carrying a steady current inside a tube of air.

The air tube's end and outer faces are magnetically insulated (n x A = 0, so B.n = 0) and
the wire's ends are its coil terminals. The field is then exactly that of an infinite wire:
    |B| = mu0 I r / (2 pi a^2) inside the wire and mu0 I / (2 pi r) outside.
Wire and air are separate parts joined by a conformal bonded interface.
"""

import gmsh
import numpy as np
import pytest
from solvers import over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
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

MU0 = 1.25663706212e-6
A, R, L = 0.001, 0.005, 0.01  # m: wire radius, air tube radius, length
CURRENT = 100.0  # A

solver = over("elmer", "fenicsx")


def _cylinder(path, radius, bore=None):
    with gmsh_session("cylinder"):
        outer = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, L, radius)
        if bore:
            inner = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, L, bore)
            gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    sides = {
        round(model[f].bbox[3], 6): f for f in model.faces_of_kind("Cylinder")
    }  # lateral faces by radius
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Start", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="End", entity="face", members=model.faces_on_plane("z", L)),
        NamedSelection(name="Outer", entity="face", members=[sides[round(radius, 6)]]),
        *(
            [NamedSelection(name="Inner", entity="face", members=[sides[round(bore, 6)]])]
            if bore
            else []
        ),
    ]


def _wire(tmp_path, solver_name):
    p = Project(name="wire")
    wire_geo = p.add(
        Geometry(name="Wire", source="wire.step", selections=_cylinder(tmp_path / "wire.step", A))
    )
    air_geo = p.add(
        Geometry(
            name="Air", source="air.step", selections=_cylinder(tmp_path / "air.step", R, bore=A)
        )
    )
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Tets",
            element_order=1,  # lowest-order edge elements
            global_size=Quantity(value=0.6, unit="mm"),
            local_sizes={"Conductor/Body": Quantity(value=0.4, unit="mm")},
        )
    )
    insulated = [
        Feature(type="magnetic_insulation", selection=f"Gap/{s}") for s in ("Start", "End", "Outer")
    ]
    comp = p.add(
        Component(
            name="Wire in air",
            parts=[
                PartInstance(name="Conductor", geometry=wire_geo.id),
                PartInstance(name="Gap", geometry=air_geo.id),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=copper.id, selection="Conductor/Body"),
                MaterialAssignment(material=air.id, selection="Gap/Body"),
            ],
            contacts=[
                Contact(name="Surface", type="bonded", source="Conductor/Outer", target="Gap/Inner")
            ],
            physics=[
                Physics(
                    type="magnetic_fields",
                    features=[
                        Feature(
                            type="coil_current",
                            selection="Conductor/Body",
                            values={"I": Quantity(value=CURRENT, unit="A")},
                        ),
                        Feature(type="coil_start", selection="Conductor/Start"),
                        Feature(type="coil_end", selection="Conductor/End"),
                        *insulated,
                    ],
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="Magnetostatics",
            component=comp.id,
            solver=solver_name,
            steps=[SolveStep(type="stationary")],
        )
    )
    return p, study


@solver
def test_wire_field(tmp_path, solver):
    project, study = _wire(tmp_path, solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)

    b = run.results.last("magnetic_flux_density")
    xyz = run.mesh.node_coords(b.ids)
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    exact = np.where(
        r <= A, MU0 * CURRENT * r / (2 * np.pi * A**2), MU0 * CURRENT / (2 * np.pi * r)
    )
    magnitude = np.linalg.norm(b.values, axis=1)

    # Lowest-order edge elements give B constant per element; Elmer averages it to the
    # nodes. Check the air gap, away from the terminals, the wire's kink in |B| at r = a and
    # the outer wall, where that average is one-sided.
    middle = (np.abs(xyz[:, 2] - L / 2) < L / 4) & (r > 1.5 * A) & (r < 0.8 * R)
    error = np.abs(magnitude[middle] - exact[middle]) / exact[middle]
    print(f"|B| error in the gap: mean {error.mean():.2%}, max {error.max():.2%}")
    assert error.mean() < 0.02 and error.max() < 0.12

    # Stored energy per unit length of a wire in a coaxial return: (mu0 I^2 / 4 pi)(1/4 + ln R/a).
    energy = run.results.last("magnetic_energy").values[0, 0]
    exact_energy = MU0 * CURRENT**2 * L / (4 * np.pi) * (0.25 + np.log(R / A))
    print(f"energy error {energy / exact_energy - 1:+.2%}")
    assert energy == pytest.approx(exact_energy, rel=0.02)  # the whole field, wire included

    # The field circles the wire: no axial or radial component to speak of.
    tangent = np.column_stack([-xyz[:, 1], xyz[:, 0], 0 * r])[middle] / r[middle, None]
    along = np.einsum("ij,ij->i", b.values[middle], tangent)
    assert np.all(along > 0.9 * magnitude[middle])  # current flows +z: B is anticlockwise
