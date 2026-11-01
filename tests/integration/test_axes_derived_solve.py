"""A force given in axes taken from a face (ADR 0049 step 3): the bar's tip face's z is
its outward normal (+x), so 1 kN along that z stretches the bar by F L / (E A)."""

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
from puzzle.model.schema import CoordinateSystem, HistoryStep
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B = 0.2, 0.01


@over("calculix", "code_aster")
def test_a_force_in_axes_from_the_tip_face(tmp_path, solver):
    p = Project(name="axes")
    geo = p.add(Geometry(name="Bar", history=[
        HistoryStep(kind="box", name="Bar", args={"dx": L, "dy": B, "dz": B})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    NamedSelection(name="Root", entity="face", members=["Bar/-x"]),
                    NamedSelection(name="Tip", entity="face", members=["Bar/+x"])]))  # fmt: skip
    tip = p.add(CoordinateSystem(name="Tip normal", geometry=geo.id, entity="face",
                                 entity_name="Bar/+x"))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=4, unit="mm")))
    force = Feature(type="force", selection="Bar/Tip", axes=tip.id,
                    values={"z": Quantity(value=1000, unit="N")})  # fmt: skip
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[Feature(type="fixed", selection="Bar/Root"), force])  # fmt: skip
    parts = [PartInstance(name="Bar", geometry=geo.id)]
    steelwork = [MaterialAssignment(material=steel.id, selection="Bar/Body")]
    comp = p.add(Component(name="Bar", mesh=mesh.id, parts=parts, materials=steelwork,
                           physics=[physics]))  # fmt: skip
    steps = [SolveStep(type="stationary")]
    study = p.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=600)
    E = library_material("structural_steel").properties["youngs_modulus"].to_si()
    ux = run.results.last("displacement").at(run.mesh.groups["Bar/Tip"].node_ids)[:, 0].mean()
    assert ux == pytest.approx(1000 * L / (E * B * B), rel=0.01)
