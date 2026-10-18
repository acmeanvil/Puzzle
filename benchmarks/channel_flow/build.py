"""Build the laminar channel-flow benchmark: developing flow between parallel plates.

Water enters a 10 mm high, 100 mm long channel at a uniform 1 mm/s (Reynolds
number 10 on the plate gap). The side faces are symmetry planes, so the flow is
two-dimensional. Within a few millimetres it becomes plane Poiseuille flow:

    u(y) = 6 U y (h - y) / h^2         centreline speed 1.5 U
    dp/dx = -12 mu U / h^2             (-0.1202 Pa/m here)

    python benchmarks/channel_flow/build.py
    puzzle run benchmarks/channel_flow --study "Channel flow"
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
H, LENGTH, WIDTH = 0.01, 0.1, 0.004  # m
U = 0.001  # m/s, mean (inlet) speed
MESH_SIZE_MM = 0.5


def build(
    directory: Path = HERE, mesh_size_mm: float = MESH_SIZE_MM, iterations: int = 500
) -> Project:
    step = directory / "channel.step"
    with gmsh_session("channel"):
        gmsh.model.occ.addBox(0, 0, 0, LENGTH, H, WIDTH)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    model = import_step(step)

    p = Project(name="Laminar channel flow")
    channel = p.add(
        Geometry(
            name="Channel",
            source=step.name,
            selections=[
                NamedSelection(name="Fluid", entity="body", members=["body-1"]),
                NamedSelection(name="Inlet", entity="face", members=model.faces_on_plane("x", 0.0)),
                NamedSelection(
                    name="Outlet", entity="face", members=model.faces_on_plane("x", LENGTH)
                ),
                NamedSelection(
                    name="Walls",
                    entity="face",
                    members=model.faces_on_plane("y", 0.0) + model.faces_on_plane("y", H),
                ),
                NamedSelection(
                    name="Sides",
                    entity="face",
                    members=model.faces_on_plane("z", 0.0) + model.faces_on_plane("z", WIDTH),
                ),
            ],
        )
    )
    water = add_library_material(p, "water")
    mesh = p.add(
        MeshRecipe(
            name="Hexahedra",
            global_size=Quantity(value=mesh_size_mm, unit="mm"),
            element_order=1,
            element_shape="hexahedra",
        )
    )
    comp = p.add(
        Component(
            name="Channel",
            parts=[PartInstance(name="Channel", geometry=channel.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=water.id, selection="Channel/Fluid")],
            physics=[
                Physics(
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
            ],
        )
    )
    p.add(
        Study(
            name="Channel flow",
            component=comp.id,
            solver="code_saturne",
            steps=[SolveStep(type="stationary", settings={"iterations": iterations})],
        )
    )
    save_project(p, directory)
    return p


if __name__ == "__main__":
    build()
    print(f"Wrote the channel-flow project to {HERE}")
