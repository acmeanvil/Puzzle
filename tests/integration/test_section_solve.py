"""A 3D tube's half section solved axisymmetrically (ADR 0048): the loads sit on edges
named after the tube's faces, and the answer is Lamé's (ends held axially)."""

import numpy as np
import pytest
from solvers import over

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
from puzzle.model.schema import HistoryStep
from puzzle.solve import run_study

pytest.importorskip("OCP")

A_IN, B_OUT, P = 0.010, 0.020, 100e6
STEEL = library_material("structural_steel").properties
E, NU = STEEL["youngs_modulus"].to_si(), STEEL["poissons_ratio"].to_si()
CONSTANT = {"temperature_dependent_materials": False}


@over("calculix", "code_aster", "fenicsx", "elmer")
def test_a_tube_section_under_pressure_is_lames(tmp_path, solver):
    p = Project(name="tube")
    tube = p.add(Geometry(name="Tube", history=[
        HistoryStep(kind="cylinder", name="Tube", args={"r": B_OUT, "h": 0.01, "axis": "y"}),
        HistoryStep(kind="hole", name="Bore", args={"r": A_IN, "x": 0, "y": 0, "axis": "y"}),
    ]))  # fmt: skip
    cut = p.add(Geometry(name="Cut", history=[
        HistoryStep(kind="section", name="Cut", args={"part": str(tube.id), "half": True}),
    ], selections=[
        NamedSelection(name="Region", entity="face", members=["Cut/face"]),
        NamedSelection(name="Bore", entity="edge", members=["Cut/Bore/wall"]),
        NamedSelection(name="Outer", entity="edge", members=["Cut/Tube/wall"]),
        NamedSelection(name="Ends", entity="edge", members=["Cut/Tube/start", "Cut/Tube/end"]),
    ]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=Quantity(value=1, unit="mm")))
    zero, pressure = Quantity(value=0, unit="m"), Quantity(value=P, unit="Pa")
    features = [
        Feature(type="displacement", selection="Cut/Ends", values={"y": zero}),
        Feature(type="pressure", selection="Cut/Bore", values={"p": pressure}),
    ]
    c = p.add(Component(name="Ring", dimension="2d_axisymmetric", mesh=mesh.id,
                        parts=[PartInstance(name="Cut", geometry=cut.id)],
                        materials=[MaterialAssignment(material=steel.id, selection="Cut/Region")],
                        physics=[Physics(type="solid_mechanics", features=features,
                                         settings=CONSTANT)]))  # fmt: skip
    steps = [SolveStep(type="stationary")]
    study = p.add(Study(name="s", component=c.id, solver=solver, steps=steps))
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=600)
    a2, b2 = A_IN**2, B_OUT**2
    A, B = P * a2 / (b2 - a2), P * a2 * b2 / (b2 - a2)
    outer = run.results.last("displacement").at(run.mesh.groups["Cut/Outer"].node_ids)
    exact = (1 + NU) / E * ((1 - 2 * NU) * A * B_OUT + B / B_OUT)
    assert np.mean(outer[:, 0]) == pytest.approx(exact, rel=5e-3)
