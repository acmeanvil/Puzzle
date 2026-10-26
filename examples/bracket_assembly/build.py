"""Build the bracket assembly example: a steel angle bracket bolted to a base plate.

    python examples/bracket_assembly/build.py      # writes the project next to this file
    puzzle studies examples/bracket_assembly
    puzzle run examples/bracket_assembly --study "Bolted"

An assembly to explore contacts, fasteners and parts placed in one component:
- Base: a 120 x 80 x 10 mm plate with two 8 mm holes, fixed underneath.
- Bracket: an angle (a 120 x 80 x 8 mm flange and a 60 mm upright at its back edge),
  placed on the base with its holes over the base's.
- A 1 kN push on the top of the upright, towards the front: the flange's back edge
  tries to lift off the base.

Studies (two components, the same parts):
- Bonded: the flange glued to the base (conformal mesh, shared nodes).
- Bolted: the flange sits on the base in frictional contact (μ = 0.2), held by two
  M8-size beam fasteners with 5 kN pretension each. Their ends are distributing
  (RBE3): the base's hole walls meet its fixed bottom, where a rigid end would clash.

Both geometries are histories (ADR 0014), so sizes can be changed in the Geometry tab.
"""

from pathlib import Path

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
from puzzle.model.schema import BeamFastener, Contact, HistoryStep
from puzzle.project import save_project

HERE = Path(__file__).parent
MM = 1e-3
HOLES = ((0.030, 0.040), (0.090, 0.040))  # x, y of the two bolt holes
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _holes() -> list[HistoryStep]:
    return [
        HistoryStep(kind="hole", name=f"Hole{i}", args={"r": 4 * MM, "x": x, "y": y})
        for i, (x, y) in enumerate(HOLES, start=1)
    ]


def _faces(*pairs) -> list[NamedSelection]:
    return [NamedSelection(name=n, entity="face", members=[m]) for n, m in pairs]


def main() -> None:
    p = Project(name="Bracket assembly")
    base = p.add(
        Geometry(
            name="Base",
            history=[
                HistoryStep(
                    kind="box", name="Plate", args={"dx": 120 * MM, "dy": 80 * MM, "dz": 10 * MM}
                ),  # fmt: skip
                *_holes(),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                *_faces(
                    ("Bottom", "Plate/-z"),
                    ("Top", "Plate/+z"),
                    ("Hole 1", "Hole1/wall"),
                    ("Hole 2", "Hole2/wall"),
                ),  # fmt: skip
            ],
        )
    )
    bracket = p.add(
        Geometry(
            name="Bracket",
            history=[
                HistoryStep(
                    kind="box", name="Flange", args={"dx": 120 * MM, "dy": 80 * MM, "dz": 8 * MM}
                ),  # fmt: skip
                HistoryStep(
                    kind="box",
                    name="Upright",
                    args={
                        "dx": 120 * MM,
                        "dy": 8 * MM,
                        "dz": 60 * MM,
                        "y0": 72 * MM,
                        "z0": 8 * MM,
                        "mode": "add",
                    },
                ),  # fmt: skip
                *_holes(),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                *_faces(
                    ("Seat", "Flange/-z"),
                    ("Push", "Upright/+z"),
                    ("Hole 1", "Hole1/wall"),
                    ("Hole 2", "Hole2/wall"),
                ),  # fmt: skip
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    fine = p.add(MeshRecipe(name="Tets 4 mm", global_size=q(4, "mm"), element_order=2))
    # Frictional contact iterates (ten load increments, a few iterations each): a coarser
    # mesh keeps the bolted study to minutes.
    coarse = p.add(MeshRecipe(name="Tets 8 mm", global_size=q(8, "mm"), element_order=2))

    def component(name: str, contact: Contact, bolts: bool, mesh) -> Component:
        physics = Physics(
            type="solid_mechanics",
            features=[
                Feature(type="fixed", selection="Base/Bottom"),
                Feature(type="force", selection="Bracket/Push", values={"y": q(-1, "kN")}),
            ],
            settings={"temperature_dependent_materials": False},
        )
        fasteners = [
            BeamFastener(name=f"Bolt {i}", source=f"Bracket/Hole {i}", target=f"Base/Hole {i}",
                         diameter=q(8, "mm"), material=steel.id, pretension=q(5, "kN"),
                         ends="distributing")  # fmt: skip
            for i in (1, 2)
        ] if bolts else []  # fmt: skip
        return p.add(
            Component(
                name=name,
                parts=[
                    PartInstance(name="Base", geometry=base.id),
                    PartInstance(
                        name="Bracket", geometry=bracket.id, translation=(0.0, 0.0, 10 * MM)
                    ),  # fmt: skip
                ],
                mesh=mesh.id,
                materials=[
                    MaterialAssignment(material=steel.id, selection=f"{part}/Body")
                    for part in ("Base", "Bracket")
                ],  # fmt: skip
                contacts=[contact],
                connectors=fasteners,
                physics=[physics],
            )
        )

    glued = component(
        "Bonded bracket",
        Contact(name="Seat", type="bonded", source="Bracket/Seat", target="Base/Top"),
        bolts=False,
        mesh=fine,
    )
    bolted = component(
        "Bolted bracket",
        Contact(
            name="Seat", type="frictional", friction=0.2, source="Bracket/Seat", target="Base/Top"
        ),  # fmt: skip
        bolts=True,
        mesh=coarse,
    )
    static = [SolveStep(type="stationary")]
    p.add(Study(name="Bonded", component=glued.id, solver="calculix", steps=static))
    p.add(Study(name="Bolted", component=bolted.id, solver="calculix", steps=static))
    save_project(p, HERE)
    print(f"wrote {HERE}")


if __name__ == "__main__":
    main()
