"""Shared helpers for tests that build geometry with Gmsh."""

from __future__ import annotations

from pathlib import Path

import gmsh

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
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

BAR = (0.2, 0.01, 0.01)  # L, b, h in metres


def write_bar_step(path: Path, size=BAR) -> Path:
    with gmsh_session("bar"):
        gmsh.model.occ.addBox(0, 0, 0, *size)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


def bar_project(
    step: Path,
    physics: list[Physics],
    mesh_size_mm: float = 2.5,
    **recipe,
) -> tuple[Project, Component]:
    """A steel bar along x with selections Root (x = 0), Tip (x = L) and Body."""
    model = import_step(step)
    project = Project(name="bar")
    geo = project.add(
        Geometry(
            name="Bar",
            source=step.name,
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(name="Root", entity="face", members=model.faces_on_plane("x", 0.0)),
                NamedSelection(
                    name="Tip", entity="face", members=model.faces_on_plane("x", BAR[0])
                ),
            ],
        )
    )
    steel = add_library_material(project, "structural_steel")
    mesh = project.add(
        MeshRecipe(name="Mesh", global_size=Quantity(value=mesh_size_mm, unit="mm"), **recipe)
    )
    comp = project.add(
        Component(
            name="Bar",
            parts=[PartInstance(name="Bar", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Bar/Body")],
            physics=physics,
        )
    )
    return project, comp


HALF = 0.1  # the split bar's half length; its section is BAR's


def _half(path):
    with gmsh_session("half"):
        gmsh.model.occ.addBox(0, 0, 0, HALF, BAR[1], BAR[2])
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Start", entity="face", members=model.faces_on_plane("x", 0.0)),
        NamedSelection(name="End", entity="face", members=model.faces_on_plane("x", HALF)),
        NamedSelection(name="Side", entity="face", members=model.faces_on_plane("y", 0.0)),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
    ]


def split_bar_project(
    tmp_path, solver, features, contact, physics="solid_mechanics", settings=None
):
    """Two 100 mm halves of the steel bar end to end, meshed 2.5 and 1.6 mm, joined by
    `contact` (source Right/Start, target Left/End)."""
    selections = _half(tmp_path / "half.step")
    p = Project(name="split bar")
    geo = p.add(Geometry(name="Half", source="half.step", selections=selections))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(
        MeshRecipe(
            name="Unmatched",
            global_size=Quantity(value=2.5, unit="mm"),
            local_sizes={"Right/Body": Quantity(value=1.6, unit="mm")},
        )
    )
    comp = p.add(
        Component(
            name="Split bar",
            parts=[
                PartInstance(name="Left", geometry=geo.id),
                PartInstance(name="Right", geometry=geo.id, translation=(HALF, 0.0, 0.0)),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=steel.id, selection="Left/Body"),
                MaterialAssignment(material=steel.id, selection="Right/Body"),
            ],
            contacts=[contact],
            physics=[Physics(type=physics, features=features, settings=settings or {})],
        )
    )
    study = p.add(
        Study(name="Load", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study
