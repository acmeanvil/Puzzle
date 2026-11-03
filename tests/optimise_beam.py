"""A steel cantilever whose thickness is a parameter, with a design of experiments over
it (ADR 0063): used by the benchmark and the GUI test."""

from __future__ import annotations

from puzzle.model.schema import (
    Component,
    DesignOutput,
    DesignVariable,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    Operator,
    OperatorInput,
    Optimisation,
    ParameterSet,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)

L, W, F, LIMIT = 0.2, 0.02, 100.0, 0.002


def beam_project() -> tuple[Project, Optimisation]:
    from puzzle.materials import add_library_material

    p = Project(name="beam")
    p.add(ParameterSet(name="Section", values={"t": Quantity(value=6, unit="mm")}))
    geo = p.add(Geometry(name="Beam", history=[
        HistoryStep(kind="box", name="B", args={"dx": L, "dy": W, "dz": "t"})],
        selections=[
            NamedSelection(name="Body", entity="body", members=["body-1"]),
            NamedSelection(name="Root", entity="face", rule={"plane": {"axis": "x", "at": 0.0}}),
            NamedSelection(name="Tip", entity="face", rule={"plane": {"axis": "x", "at": L}}),
        ]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=4, unit="mm")))
    comp = p.add(Component(
        name="Beam", parts=[PartInstance(name="B", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="B/Body")],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="B/Root"),
            Feature(type="force", selection="B/Tip",
                    values={"z": Quantity(value=-F, unit="N")}),
        ], settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="Bend", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    result = {"result": OperatorInput(source=study.id, port="result")}
    tip = p.add(Operator(name="Tip deflection", type="reduce", quantity="displacement:total",
                         selection="B/Tip", inputs=result))  # fmt: skip
    thick = p.add(Operator(name="Thickness", type="expression", expression="t"))
    opt = p.add(Optimisation(
        name="Thinnest", study=study.id, method="full_factorial", levels=7,
        variables=[DesignVariable(name="t", low=Quantity(value=4, unit="mm"),
                                  high=Quantity(value=10, unit="mm"))],
        objective=DesignOutput(source=thick.id),
        constraints=[DesignOutput(source=tip.id, upper=Quantity(value=2, unit="mm"))]))  # fmt: skip
    return p, opt
