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
)

BAR = (0.2, 0.01, 0.01)  # L, b, h in metres


def write_bar_step(path: Path, size=BAR) -> Path:
    with gmsh_session("bar"):
        gmsh.model.occ.addBox(0, 0, 0, *size)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


def bar_project(
    step: Path, physics: list[Physics], mesh_size_mm: float = 2.5
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
    steel = add_library_material(project, "general_carbon_steel")
    mesh = project.add(MeshRecipe(name="Tets", global_size=Quantity(value=mesh_size_mm, unit="mm")))
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
