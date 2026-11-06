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

from puzzle.model.schema import Component, ParameterSet, Project, ProjectObject, Study, Transfer


def _inputs(project: Project, obj: ProjectObject) -> list[UUID]:
    deps = list(obj.dependencies())
    if isinstance(obj, Study):
        deps += [t.id for t in project.transfers_into(obj.id)]
        # Parameters are global: every study reads all parameter sets, and every operator
        # computing before a solve (ADR 0062: a Number, a Slider, an Expression).
        deps += [p.id for p in project.of_type(ParameterSet)]
        from puzzle.operators import pre_solve

        deps += [o.id for o in pre_solve(project) if o.id not in deps]
    return deps


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _own(obj: ProjectObject, *, drop: frozenset[str] = frozenset()) -> dict:
    # A shoot-out never changes the primary solver's result, so it never stales it.
    exclude = {"name", *drop} | ({"shootout"} if isinstance(obj, Study) else set())
    data = obj.model_dump(mode="json", exclude=exclude)
    if isinstance(obj, Study) and data.get("morph") is None:  # ADR 0065: none, as before
        data.pop("morph", None)
    if isinstance(obj, Study):  # ADR 0066: a check of the study never changes its solve
        data.pop("convergence", None)
        data.pop("adapt", None)  # each pass's own recipe is in its run key
        data.pop("remote", None)  # ADR 0021: the same result wherever it is computed
    if obj.kind == "result" and data.get("error_target") == 0.02:  # ADR 0066's default
        data.pop("error_target")
    data.pop("hidden", None)  # what the view shows never changes a solve
    data.pop("tags", None)  # nor do the words it is found by (ADR 0064A J6)
    data.pop("made_from", None)  # a construction's words for what it was placed on
    if data.get("share_faces") is True:  # the default: older projects keep their hashes
        data.pop("share_faces")
    if obj.kind == "mesh":  # a box in global axes, as before boxes took a system
        for control in data.get("controls", []):
            if control.get("kind") == "box" and control.get("axes") is None:
                control.pop("axes", None)
    if obj.kind == "axes":  # ADR 0049 step 3's fields at their defaults: older hashes kept
        for key, default in (("relative_to", None), ("component", None), ("part", None),
                             ("geometry", None), ("entity", None), ("entity_name", None),
                             ("along", 0.5)):  # fmt: skip
            if key in data and data[key] == default:
                data.pop(key)
    if obj.kind == "operator":  # settings at their defaults: adding one stales nothing
        from puzzle.model.schema import Operator

        for key, f in Operator.model_fields.items():
            if key in data and not f.is_required() and data[key] == f.get_default(
                    call_default_factory=True):  # fmt: skip
                data.pop(key)
    if obj.kind == "field":  # ADR 0044's region, step, times, axis unset: older hashes kept
        for key, default in (("region", ""), ("index", ""), ("times", []), ("axis", "time"),
                             ("source", None), ("port", "out")):  # fmt: skip
            if key in data and data[key] == default:
                data.pop(key)
    if isinstance(obj, Component) and data.get("planar_model") == "plane_stress":
        data.pop("planar_model")  # the default: older projects keep their hashes
    if isinstance(obj, Component):  # how bolts are listed and made never changes a solve
        data.pop("bolt_rules", None)
        for assignment in data.get("materials", []):  # ADR 0049: no axes, as before them
            if assignment.get("axes") is None:
                assignment.pop("axes", None)
            if not assignment.get("fields"):  # ADR 0044 stage 3: no field use, as before
                assignment.pop("fields", None)
        for section in data.get("shell_sections", []):
            if not section.get("fields"):
                section.pop("fields", None)
        for physics in data.get("physics", []):  # ADR 0044: no field use, as before it
            for feature in physics.get("features", []):
                if not feature.get("fields"):
                    feature.pop("fields", None)
                if feature.get("when") is None:  # ADR 0064A A3: no toggle, as before
                    feature.pop("when", None)
                for use in (feature.get("fields") or {}).values():  # stage 2: no weight
                    if use.get("weight") is None:
                        use.pop("weight", None)
        for bolt in data.get("connectors", []):
            bolt.pop("group", None)
            bolt.pop("rule", None)
            bolt.pop("through", None)
    return data


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
    from puzzle.operators import resolved

    project = resolved(project)  # sweeps over a list: its values are the points
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
