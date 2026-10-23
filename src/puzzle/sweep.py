"""Running a study's sweep (ADR 0018): one run per point, each an ordinary run.

A point sets parameter values (`ParameterSet` entries by name) and material choices
(the component's assignment at a location gets the chosen material). `apply` makes the
project copy a point runs on; `labels` says the point in words, as the run records it
and the results list it ("Bar/Body": "Copper", "t": "12 mm").
"""

from __future__ import annotations

import copy
from uuid import UUID

from puzzle.errors import PuzzleError
from puzzle.model.schema import Component, Material, ParameterSet, Project, Quantity, Study


def apply(project: Project, study: Study, point: dict) -> Project:
    """A copy of the project with the point's values, its study without sweep steps."""
    p = copy.deepcopy(project)
    for key, value in point.items():
        if key.startswith("material:"):
            location = key.removeprefix("material:")
            comp = p.get(study.component, Component)
            hit = [a for a in comp.materials if a.selection == location]
            if not hit:
                raise PuzzleError(
                    f"study '{study.name}': no material assignment at '{location}' in "
                    f"component '{comp.name}'",
                    code="inconsistent",
                )
            material = p.objects.get(UUID(value))
            if not isinstance(material, Material):
                raise PuzzleError(f"study '{study.name}': no material {value}", code="not_found")
            for a in hit:
                a.material = material.id
        else:
            sets = [ps for ps in p.of_type(ParameterSet) if key in ps.values]
            if not sets:
                raise PuzzleError(
                    f"study '{study.name}' sweeps unknown parameter '{key}'", code="inconsistent"
                )
            sets[0].values[key] = value
    s = p.get(study.id, Study)
    s.steps = s.solve_steps()
    return p


def labels(project: Project, point: dict) -> dict[str, str]:
    """The point in words: locations by material name, parameters as value and unit."""
    out = {}
    for key, value in point.items():
        if key.startswith("material:"):
            m = project.objects.get(UUID(value))
            out[key.removeprefix("material:")] = m.name if m else value
        else:
            q = value if isinstance(value, Quantity) else Quantity(**value)
            out[key] = f"{q.value:g}" if q.unit == "dimensionless" else f"{q.value:g} {q.unit}"
    return out


def title(point_labels: dict[str, str]) -> str:
    """A point as one short phrase: "Copper", or "Copper, t = 12 mm"."""
    parts = [v if "/" in k else f"{k} = {v}" for k, v in point_labels.items()]
    return ", ".join(parts)


def materials_only(study: Study) -> bool:
    """A sweep that changes only materials: every point can reuse one mesh."""
    return all(p.location is not None for s in study.sweeps() for p in s.parameters)
