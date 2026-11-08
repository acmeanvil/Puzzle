"""A closed winding (no terminal faces, ADR 0047): a ring coil's field at its centre.

A ring of inner radius R1, outer R2 and height 2b carrying N turns of I, uniform over its
section (J = N I / ((R2 − R1) 2b)), in a cylinder of air with insulated faces far away:
    B(0) = μ0 J b ln[(R2 + √(R2² + b²)) / (R1 + √(R1² + b²))]
along the ring's axis, positive for a current right-handed about it (+z here). Elmer's
CoilSolver and FEniCSx's circulating source give the same field.
"""

import math

import gmsh
import numpy as np
import pytest
from solvers import needs_petsc, over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
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

pytest.importorskip("OCP")

MU0 = 4e-7 * math.pi
R1, R2, B, OUT = 0.010, 0.014, 0.003, 0.060  # m
TURNS, CURRENT = 100.0, 1.0


def _ring(tmp_path, solver, frequency=None):
    path = tmp_path / "ring.step"
    with gmsh_session("ring"):
        occ = gmsh.model.occ
        outer = occ.addCylinder(0, 0, -B, 0, 0, 2 * B, R2)
        inner = occ.addCylinder(0, 0, -B, 0, 0, 2 * B, R1)
        ring, _ = occ.cut([(3, outer)], [(3, inner)])
        air = occ.addCylinder(0, 0, -OUT, 0, 0, 2 * OUT, OUT)
        occ.fragment([(3, air)], ring)
        occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    bodies = [e.name for e in sorted(model.of_dim(3), key=lambda e: e.measure)]  # ring first
    outside = [f for f in model.faces_of_kind("Cylinder") if model[f].bbox[3] > OUT * 0.99]
    outside += model.faces_on_plane("z", -OUT) + model.faces_on_plane("z", OUT)
    p = Project(name="ring")
    geo = p.add(Geometry(name="Coil", source=path.name, selections=[
        NamedSelection(name="Ring", entity="body", members=[bodies[0]]),
        NamedSelection(name="Air", entity="body", members=[bodies[1]]),
        NamedSelection(name="Outside", entity="face", members=outside)]))  # fmt: skip
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(MeshRecipe(name="Mesh", element_order=1,
                            global_size=Quantity(value=6, unit="mm"),
                            local_sizes={"Coil/Ring": Quantity(value=1, unit="mm")}))  # fmt: skip
    coil = Feature(type="stranded_coil", selection="Coil/Ring", values={
        "I": Quantity(value=CURRENT, unit="A"), "phase": Quantity(value=0, unit="deg"),
        "turns": Quantity(value=TURNS, unit="dimensionless"),
        "fill": Quantity(value=1, unit="dimensionless")})  # fmt: skip
    insulated = Feature(type="magnetic_insulation", selection="Coil/Outside")
    comp = p.add(Component(
        name="Ring", parts=[PartInstance(name="Coil", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=copper.id, selection="Coil/Ring"),
                   MaterialAssignment(material=air.id, selection="Coil/Air")],
        physics=[Physics(type="magnetic_fields", features=[coil, insulated])]))  # fmt: skip
    step = (SolveStep(type="frequency_domain", settings={"frequencies": f"{frequency:g}"})
            if frequency else SolveStep(type="stationary"))  # fmt: skip
    study = p.add(Study(name="Ring", component=comp.id, solver=solver, steps=[step]))
    return p, study


@over("elmer", "fenicsx")
@pytest.mark.parametrize("frequency", [None, 50.0])
def test_a_closed_ring_coil(tmp_path, solver, frequency):
    # FEniCSx without PETSc (Windows) solves with SciPy's SuperLU, which ran out of
    # memory on this ring's 244,000 equations
    needs_petsc(solver)
    project, study = _ring(tmp_path, solver, frequency)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    b = step.fields["magnetic_flux_density"]
    xyz = run.mesh.node_coords(b.ids)
    near = np.linalg.norm(xyz, axis=1) < 2.5e-3
    j = TURNS * CURRENT / ((R2 - R1) * 2 * B)
    exact = MU0 * j * B * math.log((R2 + math.hypot(R2, B)) / (R1 + math.hypot(R1, B)))
    bz = b.values[near, 2].mean()
    print(f"{solver} {frequency or 'static'}: centre Bz {bz:.5g} T (exact {exact:.5g} T)")
    assert bz == pytest.approx(exact, rel=0.03)  # positive: right-handed about +z
    j_ring = step.fields["current_density_amplitude" if frequency else "current_density"]
    assert np.abs(j_ring.values).max() == pytest.approx(j, rel=0.05)
