"""Results in a coordinate system (ADR 0049) against Lamé's thick cylinder.

A steel tube, inner radius 20 mm, outer 40 mm, 20 mm long, under 100 MPa inside, its
ends held axially and against turning (in cylindrical axes: u_θ = u_z = 0) is solved on
CalculiX in global axes. Read in a cylindrical system about its axis, the hoop stress
at the bore is p (ro² + ri²) / (ro² − ri²) = 166.7 MPa and the radial stress −p; at the
outside the hoop stress is 2 p ri² / (ro² − ri²) = 66.7 MPa and the radial stress 0.
"""

import numpy as np
import pytest
from solvers import available

from puzzle.model.schema import (
    Component,
    CoordinateSystem,
    Feature,
    Geometry,
    HistoryStep,
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
from puzzle.post.axes import in_axes
from puzzle.project import save_project
from puzzle.solve import run_study

RI, RO, L, P = 0.02, 0.04, 0.02, 100e6
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_lame_cylinder_in_cylindrical_axes(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.materials import add_library_material

    p = Project(name="tube")
    tube = p.add(
        Geometry(
            name="Tube",
            history=[
                HistoryStep(kind="cylinder", name="Shell", args={"r": RO, "h": L}),
                HistoryStep(kind="hole", name="Bore", args={"r": RI, "x": 0.0, "y": 0.0}),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                NamedSelection(name="Bore", entity="face", members=["Bore/wall"]),
                NamedSelection(name="Outside", entity="face", members=["Shell/wall"]),
                NamedSelection(name="Ends", entity="face", members=["Shell/start", "Shell/end"]),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    axis = p.add(CoordinateSystem(name="Axis", type="cylindrical"))
    mesh = p.add(MeshRecipe(name="M", global_size=q(2.5, "mm")))
    held = Feature(type="displacement", selection="Tube/Ends", axes=axis.id,
                   values={"y": q(0, "m"), "z": q(0, "m")})  # fmt: skip
    inside = Feature(type="pressure", selection="Tube/Bore", values={"p": q(P, "Pa")})
    comp = p.add(
        Component(
            name="Tube",
            parts=[PartInstance(name="Tube", geometry=tube.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Tube/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[held, inside],
                    settings={"temperature_dependent_materials": False},
                )
            ],  # fmt: skip
        )
    )
    study = p.add(Study(name="s", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, tmp_path / "tube")
    run = run_study(p, study.id, tmp_path / "tube", tmp_path / "w", timeout_s=300)
    local = in_axes(run.mesh, run.results, axis).last("stress")

    def at(selection):
        ids = run.mesh.groups[selection].node_ids
        xyz = run.mesh.node_coords(ids)
        middle = np.abs(xyz[:, 2] - L / 2) < 0.3 * L  # away from the held ends
        return local.at(ids[middle])

    k = RO**2 - RI**2
    bore, outside = at("Tube/Bore"), at("Tube/Outside")
    assert np.median(bore[:, 1]) == pytest.approx(P * (RO**2 + RI**2) / k, rel=0.03)  # hoop
    assert np.median(bore[:, 0]) == pytest.approx(-P, rel=0.05)  # radial
    assert np.median(outside[:, 1]) == pytest.approx(2 * P * RI**2 / k, rel=0.03)
    assert abs(np.median(outside[:, 0])) < 0.03 * P
