"""Build the cantilever example project: a 200 x 10 x 10 mm steel bar fixed at one end.

    python examples/cantilever/build.py            # writes the project next to this file
    puzzle studies examples/cantilever
    puzzle run examples/cantilever --study "Tip load"
    puzzle show examples/cantilever --study "Tip load" --field stress

Studies:
- Tip load: 100 N down at the free end (tip deflection about 1.60 mm by beam theory)
- Modes: the first six natural frequencies (first bending about 204 Hz)
- Conduction: 100 °C at the fixed end, 20 °C at the free end
"""

from pathlib import Path

import gmsh

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
from puzzle.project import save_project

HERE = Path(__file__).parent
L, B, H = 0.2, 0.01, 0.01


def main() -> None:
    step = HERE / "bar.step"
    with gmsh_session("cantilever"):
        gmsh.model.occ.addBox(0, 0, 0, L, B, H)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    model = import_step(step)

    p = Project(name="Cantilever")
    bar = p.add(
        Geometry(
            name="Bar",
            source=step.name,
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(name="Root", entity="face", members=model.faces_on_plane("x", 0.0)),
                NamedSelection(name="Tip", entity="face", members=model.faces_on_plane("x", L)),
            ],
        )
    )
    steel = add_library_material(p, "general_carbon_steel")
    mesh = p.add(MeshRecipe(name="Tets 2.5 mm", global_size=Quantity(value=2.5, unit="mm")))
    comp = p.add(
        Component(
            name="Cantilever",
            parts=[PartInstance(name="Bar", geometry=bar.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Bar/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="fixed", selection="Bar/Root"),
                        Feature(
                            type="force",
                            selection="Bar/Tip",
                            values={"y": Quantity(value=-100, unit="N")},
                        ),
                    ],
                ),
                Physics(
                    type="heat_transfer",
                    features=[
                        Feature(
                            type="temperature",
                            selection="Bar/Root",
                            values={"T": Quantity(value=100, unit="degC")},
                        ),
                        Feature(
                            type="temperature",
                            selection="Bar/Tip",
                            values={"T": Quantity(value=20, unit="degC")},
                        ),
                    ],
                ),
            ],
        )
    )
    solid, heat = ["solid_mechanics"], ["heat_transfer"]
    p.add(
        Study(
            name="Tip load",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary", physics=solid)],
        )
    )
    p.add(
        Study(
            name="Modes",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="modal", physics=solid, settings={"modes": 6})],
        )
    )
    p.add(
        Study(
            name="Conduction",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary", physics=heat)],
        )
    )
    save_project(p, HERE)
    print(f"Wrote the cantilever project to {HERE}")


if __name__ == "__main__":
    main()
