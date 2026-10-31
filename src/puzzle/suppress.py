"""Suppressed geometry (Ansys's suppressed bodies): left out of meshing and solving.

A geometry marked `suppressed` takes every part made of it out of each component, with
what hangs on those parts: material assignments, contacts, bolts, beam and shell
sections and physics features on their selections. `applied(project)` gives the
project as studies then see it (`run_study` and the mesh preview use it); a component
left with no parts can't be solved (`Project.problems`: `suppressed`). Hidden geometry
is only not drawn: it is solved as ever.
"""

from __future__ import annotations

import dataclasses


def _on(selection: str | None, parts: set[str]) -> bool:
    return bool(selection) and selection.split("/", 1)[0] in parts


def suppressed_parts(project, comp) -> set[str]:
    from puzzle.model.schema import Geometry

    return {
        p.name
        for p in comp.parts
        if isinstance(g := project.objects.get(p.geometry), Geometry) and g.suppressed
    }


def without(project, comp):
    """The component with its suppressed parts and what hangs on them taken out."""
    gone = suppressed_parts(project, comp)
    if not gone:
        return comp
    physics = [
        p.model_copy(update={"features": [f for f in p.features if not _on(f.selection, gone)]})
        for p in comp.physics
    ]
    return comp.model_copy(
        update={
            "parts": [p for p in comp.parts if p.name not in gone],
            "materials": [m for m in comp.materials if not _on(m.selection, gone)],
            "contacts": [c for c in comp.contacts
                         if not (_on(c.source, gone) or _on(c.target, gone))],
            "connectors": [c for c in comp.connectors
                           if not (_on(c.source, gone) or _on(c.target, gone))],
            "beam_sections": [b for b in comp.beam_sections if not _on(b.selection, gone)],
            "shell_sections": [s for s in comp.shell_sections if not _on(s.selection, gone)],
            "physics": physics,
        }
    )  # fmt: skip


def applied(project):
    """The project with every component's suppressed parts taken out."""
    from puzzle.model.schema import Component

    changed = {}
    for comp in project.of_type(Component):
        trimmed = without(project, comp)
        if trimmed is not comp and trimmed.parts:
            changed[comp.id] = trimmed
    if not changed:
        return project
    return dataclasses.replace(project, objects={**project.objects, **changed})
