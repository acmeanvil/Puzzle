"""Build the NAFEMS LE1 benchmark project: elliptic membrane under outward pressure.

NAFEMS Standard Benchmark LE1 (plane stress):
- Quarter of an elliptic annulus. Inner edge: (x/2.0)^2 + (y/1.0)^2 = 1.
  Outer edge: (x/3.25)^2 + (y/2.75)^2 = 1. Thickness 0.1 m.
- E = 210 GPa, nu = 0.3.
- Uniform outward pressure of 10 MPa on the outer edge.
- Symmetry: u_x = 0 on x = 0, u_y = 0 on y = 0.
- Target: sigma_yy = 92.7 MPa at point D = (2.0, 0.0).

Modelled here as a 3D solid of second-order tetrahedra; u_z = 0 on z = 0 makes that
face a symmetry plane, so the plate stays in plane stress.

    python benchmarks/nafems_le1/build.py
    puzzle run benchmarks/nafems_le1 --study LE1
"""

from pathlib import Path

import gmsh

from puzzle.geometry import gmsh_session, import_step
from puzzle.model import (
    Component,
    Feature,
    Geometry,
    Material,
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
THICKNESS = 0.1
POINT_D = (2.0, 0.0)
TARGET_SYY = 92.7e6  # Pa
MESH_SIZE_MM = 100.0
EDGE_SIZE_MM = 20.0  # along the inner edge, where the stress peaks


def write_geometry(path: Path) -> Path:
    with gmsh_session("le1"):
        occ = gmsh.model.occ
        ring, _ = occ.cut(
            [(2, occ.addDisk(0, 0, 0, 3.25, 2.75))], [(2, occ.addDisk(0, 0, 0, 2.0, 1.0))]
        )
        plate, _ = occ.intersect(ring, [(2, occ.addRectangle(0, 0, 0, 3.25, 2.75))])
        occ.extrude(plate, 0, 0, THICKNESS)
        occ.synchronize()
        gmsh.write(str(path))
    return path


def build(
    directory: Path = HERE, mesh_size_mm: float = MESH_SIZE_MM, edge_size_mm: float = EDGE_SIZE_MM
) -> Project:
    step = write_geometry(directory / "le1.step")
    model = import_step(step)
    curved = sorted((e for e in model.of_dim(2) if e.kind != "Plane"), key=lambda e: e.measure)
    inner, outer = curved[0].name, curved[-1].name

    p = Project(name="NAFEMS LE1")
    plate = p.add(
        Geometry(
            name="Plate",
            source=step.name,
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(
                    name="X symmetry", entity="face", members=model.faces_on_plane("x", 0.0)
                ),
                NamedSelection(
                    name="Y symmetry", entity="face", members=model.faces_on_plane("y", 0.0)
                ),
                NamedSelection(
                    name="Mid plane", entity="face", members=model.faces_on_plane("z", 0.0)
                ),
                NamedSelection(name="Outer edge", entity="face", members=[outer]),
                NamedSelection(name="Inner edge", entity="face", members=[inner]),
            ],
        )
    )
    steel = p.add(
        Material(
            name="LE1 material",
            description="NAFEMS LE1: E = 210 GPa, nu = 0.3",
            properties={
                "youngs_modulus": Quantity(value=210, unit="GPa"),
                "poissons_ratio": Quantity(value=0.3),
            },
        )
    )
    mesh = p.add(
        MeshRecipe(
            name="Tets",
            global_size=Quantity(value=mesh_size_mm, unit="mm"),
            local_sizes={"Plate/Inner edge": Quantity(value=edge_size_mm, unit="mm")},
        )
    )
    comp = p.add(
        Component(
            name="Membrane",
            parts=[PartInstance(name="Plate", geometry=plate.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Plate/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(
                            type="displacement",
                            selection="Plate/X symmetry",
                            values={"x": Quantity(value=0, unit="m")},
                        ),
                        Feature(
                            type="displacement",
                            selection="Plate/Y symmetry",
                            values={"y": Quantity(value=0, unit="m")},
                        ),
                        Feature(
                            type="displacement",
                            selection="Plate/Mid plane",
                            values={"z": Quantity(value=0, unit="m")},
                        ),
                        # Outward pressure: negative, since positive pressure pushes into a surface.
                        Feature(
                            type="pressure",
                            selection="Plate/Outer edge",
                            values={"p": Quantity(value=-10, unit="MPa")},
                        ),
                    ],
                )
            ],
        )
    )
    p.add(
        Study(
            name="LE1", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")]
        )
    )
    save_project(p, directory)
    return p


if __name__ == "__main__":
    build()
    print(f"Wrote the NAFEMS LE1 project to {HERE}")
