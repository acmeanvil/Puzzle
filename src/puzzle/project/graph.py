"""Content hashing, staleness and the study graph behind the schematic view.

Every object's hash covers its own settings plus the hashes of everything it
reads from. A study also reads from the transfers into it, so a change upstream
of a transfer reaches the target study. Results record the hash they were
computed from; a study is stale when its current hash differs.
"""

from __future__ import annotations

import hashlib
import json
from graphlib import CycleError, TopologicalSorter
from uuid import UUID

from puzzle.model.schema import ParameterSet, Project, ProjectObject, Study, Transfer


def _inputs(project: Project, obj: ProjectObject) -> list[UUID]:
    deps = list(obj.dependencies())
    if isinstance(obj, Study):
        deps += [t.id for t in project.transfers_into(obj.id)]
        # Parameters are global: every study reads all parameter sets.
        deps += [p.id for p in project.of_type(ParameterSet)]
    return deps


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _own(obj: ProjectObject, *, drop: frozenset[str] = frozenset()) -> dict:
    # A shoot-out never changes the primary solver's result, so it never stales it.
    exclude = {"name", *drop} | ({"shootout"} if isinstance(obj, Study) else set())
    return obj.model_dump(mode="json", exclude=exclude)


def content_hashes(project: Project) -> dict[UUID, str]:
    """Hash every object in dependency order. Raises ValueError on a transfer cycle."""
    order = TopologicalSorter({oid: _inputs(project, obj) for oid, obj in project.objects.items()})
    try:
        ordered = list(order.static_order())
    except CycleError as exc:
        raise ValueError(f"transfers form a cycle: {exc.args[1]}") from exc

    hashes: dict[UUID, str] = {}
    for oid in ordered:
        obj = project.objects.get(oid)
        if obj is None:  # dangling reference; reported by Project.validate
            continue
        inputs = sorted(hashes.get(d, "missing") for d in _inputs(project, obj))
        hashes[oid] = _digest({"kind": obj.kind, "own": _own(obj), "inputs": inputs})
    return hashes


def run_hashes(project: Project) -> dict[UUID, dict[str, list[str]]]:
    """Result keys per study, per solver, one per sweep point.

    A key covers the study setup (minus the solver choice), the solver and the sweep
    point. So a point already computed is a cache hit when other points are added,
    and a shoot-out solver's runs survive a change of primary solver.
    """
    hashes = content_hashes(project)
    runs: dict[UUID, dict[str, list[str]]] = {}
    for study in project.of_type(Study):
        inputs = sorted(hashes.get(d, "missing") for d in _inputs(project, study))
        # Each solver's settings count for its own runs only (ADR 0023).
        own = _own(study, drop=frozenset({"solver", "solver_settings", "expert_settings"}))
        # The point's own values identify a run; the rest of the sweep definition doesn't.
        own["steps"] = [s for s in own["steps"] if s["type"] != "parametric_sweep"]
        setup = _digest({"own": own, "inputs": inputs})
        runs[study.id] = {
            solver: [
                _digest(
                    {
                        "setup": setup,
                        "solver": solver,
                        **(
                            {
                                "settings": study.solver_settings.get(solver, {}),
                                "expert": study.expert_settings.get(solver, {}),
                            }
                            if study.solver_settings.get(solver)
                            or study.expert_settings.get(solver)
                            else {}
                        ),
                        "point": {
                            k: v if isinstance(v, str) else v.model_dump(mode="json")
                            for k, v in point.items()
                        },
                    }
                )
                for point in study.sweep_points()
            ]
            for solver in study.all_solvers()
        }
    return runs


def stale_studies(project: Project, computed: dict[UUID, str]) -> set[UUID]:
    """Studies whose current hash differs from the hash their stored result was computed from."""
    current = content_hashes(project)
    return {s.id for s in project.of_type(Study) if computed.get(s.id) != current[s.id]}


class StudyGraph:
    """The schematic: studies as nodes, transfers as the only edges."""

    def __init__(self, project: Project) -> None:
        self.studies = {s.id: s for s in project.of_type(Study)}
        self.edges: list[Transfer] = project.of_type(Transfer)

    def downstream(self, study_id: UUID) -> set[UUID]:
        """Every study reachable from `study_id` through transfers."""
        seen: set[UUID] = set()
        frontier = [study_id]
        while frontier:
            current = frontier.pop()
            for t in self.edges:
                if t.source == current and t.target not in seen:
                    seen.add(t.target)
                    frontier.append(t.target)
        return seen

    def run_order(self) -> list[UUID]:
        sorter = TopologicalSorter({sid: [] for sid in self.studies})
        for t in self.edges:
            sorter.add(t.target, t.source)
        return [sid for sid in sorter.static_order() if sid in self.studies]
