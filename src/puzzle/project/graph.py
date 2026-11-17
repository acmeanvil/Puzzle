"""Content hashing, staleness and the study graph behind the schematic view.

Every object's hash covers its own settings plus the hashes of everything it
reads from. A study also reads from the transfers into it, so a change upstream
of a transfer reaches the target study. Results record the hash they were
computed from; a study is stale when its current hash differs.

There is one definition of what a run depends on (ADR 0090 package 1): the project as
stored, through `keyed`, hashed here. `run_study` takes its keys from the project it is
given, before it puts parameter values in, drops suppressed geometry and toggled
features and resolves derived axes; so the tree, the outline, MCP and the schematic,
which hash the stored project, agree with it. What those steps read is in the stored
hash already: a bound quantity's parameter by name and that parameter's value (a
study's `parameters`), the `suppressed` flag, a feature's `when`, a system's geometry.
The files objects read are hashed by content (`puzzle.project.files`).
"""

from __future__ import annotations

import hashlib
import json
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import UUID

from puzzle.model.schema import Component, ParameterSet, Project, ProjectObject, Study, Transfer

_WORD = re.compile(r"[A-Za-z_]\w*")


def _strings(data, out: set[str]) -> set[str]:
    """Every string value in a dump, whole and as identifier words (a history argument
    "2 * L", a profile "Hex[0].x", a feature's `when`, a quantity's `parameter`)."""
    if isinstance(data, str):
        out.add(data)
        out.update(_WORD.findall(data))
    elif isinstance(data, dict):
        for v in data.values():
            _strings(v, out)
    elif isinstance(data, list | tuple):
        for v in data:
            _strings(v, out)
    return out


def _parameters(project: Project) -> tuple[dict[str, list[UUID]], dict[str, list]]:
    """The parameters a model can read by name (`operators.parameter_values`): each
    pre-solve operator by its name (a 2D slider's as `<name>_x` and `<name>_y`), and
    each parameter set's values."""
    from puzzle.operators import pre_solve

    ops: dict[str, list[UUID]] = {}
    for o in pre_solve(project):
        for name in (f"{o.name}_x", f"{o.name}_y") if o.type == "slider2d" else (o.name,):
            ops.setdefault(name, []).append(o.id)
    values: dict[str, list] = {}
    for ps in project.of_type(ParameterSet):
        for name, q in ps.values.items():
            values.setdefault(name, []).append(q.model_dump(mode="json"))
    return ops, values


class _Graph:
    """Each object's inputs, with a study's parameters found by what its model reads
    (OP-3): a study reads a parameter only when its own setup, its component, the
    component's geometry, recipe, materials, fields and systems, or an operator one of
    those names, mention it by name. Reads are found from the stored text, so nothing
    is missed that the model could look up by name: history arguments, bound quantities,
    sweep parameters, `when` toggles, morph handles, selection rules, equations (a
    custom PDE's weak form is text too). A word that only happens to equal a parameter's
    name makes the study read it: a needless "out of date", never a missed one."""

    def __init__(self, project: Project, root: Path | None) -> None:
        self.project, self.root = project, root
        self.own: dict[UUID, dict] = {}
        self.deps = {oid: list(obj.dependencies()) for oid, obj in project.objects.items()}
        self.reads: dict[UUID, dict[str, list]] = {}
        ops, values = None, None
        for study in project.of_type(Study):
            extra = [t.id for t in project.transfers_into(study.id)]
            if ops is None:
                ops, values = _parameters(project)
            words = self._words(study, ops) if (ops or values) else set()
            read_ops = [i for n in sorted(words & ops.keys()) for i in ops[n]]
            self.deps[study.id] = list(dict.fromkeys(self.deps[study.id] + extra + read_ops))
            self.reads[study.id] = {n: values[n] for n in sorted(words & values.keys())}

    def own_of(self, obj: ProjectObject) -> dict:
        got = self.own.get(obj.id)
        if got is None:
            got = self.own[obj.id] = _own(obj)
            from puzzle.project.files import digests

            found = digests(obj, self.root)
            if found:  # BE-1: what the files hold, not only their names
                got["files"] = found
        return got

    def _words(self, study: Study, ops: dict[str, list[UUID]]) -> set[str]:
        """The words of everything the study's hash covers, through its component and
        the operators named, but not into other studies (their hashes come in whole)."""
        objects = self.project.objects
        words: set[str] = set()
        seen: set[UUID] = set()
        frontier = [study.id, *(t.id for t in self.project.transfers_into(study.id))]
        while frontier:
            oid = frontier.pop()
            obj = objects.get(oid)
            if oid in seen or obj is None:
                continue
            seen.add(oid)
            if isinstance(obj, Study) and oid != study.id:
                continue
            found = _strings(self.own_of(obj), set())
            words |= found
            frontier += [d for d in obj.dependencies() if d not in seen]
            frontier += [i for n in found & ops.keys() for i in ops[n] if i not in seen]
        return words


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _unbound(data):
    """A dump with each parameter-bound quantity's stored `value` left out (OP-2): the
    run puts the parameter's value in its place, and that value is in the study's
    `parameters` (or its operator's hash); the stored one only shows the last."""
    if isinstance(data, dict):
        if data.get("parameter") and "value" in data and "unit" in data:
            return {k: v for k, v in data.items() if k != "value"}
        return {k: _unbound(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_unbound(v) for v in data]
    return data


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
        if data.get("preload_step") == "auto":  # the default: older hashes kept
            data.pop("preload_step")
        if not data.get("combinations"):  # ADR 0079 step 2: none, as before
            data.pop("combinations", None)
        if data.get("seismic") is None:  # ADR 0079 step 4: none, as before
            data.pop("seismic", None)
        for key, default in (
            ("weak_springs", "auto"),
            ("weak_spring_factor", 1e-6),
            ("weak_spring_limit", 1e-3),
        ):  # ADR 0080: older hashes kept
            if data.get(key) == default:
                data.pop(key)
    if obj.kind == "result" and data.get("error_target") == 0.02:  # ADR 0066's default
        data.pop("error_target")
    for key in ("hidden", "hidden_bodies", "colour", "body_colours"):
        data.pop(key, None)  # what the view shows, and how, never changes a solve
    data.pop("tags", None)  # nor do the words it is found by (ADR 0064A J6)
    data.pop("made_from", None)  # a construction's words for what it was placed on
    if data.get("share_faces") is True:  # the default: older projects keep their hashes
        data.pop("share_faces")
    if obj.kind == "mesh":  # ADR 0077's defaults: older recipes keep their hashes
        for key, default in (
            ("revolved_bolts", True),
            ("bolt_sectors", 24),
            ("hexahedron_nodes", 20),
        ):
            if data.get(key) == default:
                data.pop(key)
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
        data.pop("member_design", None)  # ADR 0079: how members are checked, after the solve
        if not data.get("welds"):  # ADR 0078: no welds, as before them
            data.pop("welds", None)
        for key in ("member_releases", "member_offsets"):  # ADR 0079: none, as before
            if not data.get(key):
                data.pop(key, None)
        if not data.get("elements"):  # ADR 0082: no element choice, as before
            data.pop("elements", None)
        for key in ("cyclic", "periodic"):  # ADR 0081: none, as before
            if data.get(key) is None:
                data.pop(key, None)
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
                if feature.get("case") is None:  # ADR 0079 step 2: no load case
                    feature.pop("case", None)
                for use in (feature.get("fields") or {}).values():  # stage 2: no weight
                    if use.get("weight") is None:
                        use.pop("weight", None)
        for bolt in data.get("connectors", []):
            bolt.pop("group", None)
            bolt.pop("rule", None)
            bolt.pop("through", None)
    return _unbound(data)


def keyed(project: Project) -> Project:
    """The project as run keys see it: bolts given their types' values (ADR 0050) and
    sweeps over a list given its values (ADR 0062), so a point already computed is
    found. Everything else is keyed as stored (see the module's docstring); `run_study`
    keys from the project it is given, so the two agree."""
    from puzzle.bolts import resolved as bolts_resolved
    from puzzle.operators import resolved

    return resolved(bolts_resolved(project))


def content_hashes(project: Project, root: Path | None = None) -> dict[UUID, str]:
    """Hash every object in dependency order. Raises ValueError on a transfer cycle.
    Files resolve against `root` (default: the folder the project was loaded from)."""
    return _hashes(_Graph(project, root if root is not None else project.root))


def _hashes(graph: _Graph) -> dict[UUID, str]:
    project = graph.project
    order = TopologicalSorter({oid: graph.deps[oid] for oid in project.objects})
    try:
        ordered = list(order.static_order())
    except CycleError as exc:
        raise ValueError(f"transfers form a cycle: {exc.args[1]}") from exc

    hashes: dict[UUID, str] = {}
    for oid in ordered:
        obj = project.objects.get(oid)
        if obj is None:  # dangling reference; reported by Project.validate
            continue
        inputs = sorted(hashes.get(d, "missing") for d in graph.deps[oid])
        own = graph.own_of(obj)
        if graph.reads.get(oid):  # the parameter sets' values its model reads (OP-3)
            own = own | {"parameters": graph.reads[oid]}
        hashes[oid] = _digest({"kind": obj.kind, "own": own, "inputs": inputs})
    return hashes


def run_hashes(project: Project, root: Path | None = None) -> dict[UUID, dict[str, list[str]]]:
    """Result keys per study, per solver, one per sweep point.

    A key covers the study setup (minus the solver choice), the solver and the sweep
    point. So a point already computed is a cache hit when other points are added,
    and a shoot-out solver's runs survive a change of primary solver. Files resolve
    against `root` (default: the folder the project was loaded from).
    """
    if root is None:
        root = project.root
    project = keyed(project)
    graph = _Graph(project, root)
    hashes = _hashes(graph)
    runs: dict[UUID, dict[str, list[str]]] = {}
    for study in project.of_type(Study):
        inputs = sorted(hashes.get(d, "missing") for d in graph.deps[study.id])
        # Each solver's settings count for its own runs only (ADR 0023).
        own = _own(study, drop=frozenset({"solver", "solver_settings", "expert_settings"}))
        # The point's own values identify a run; the rest of the sweep definition doesn't.
        own["steps"] = [s for s in own["steps"] if s["type"] != "parametric_sweep"]
        if graph.reads.get(study.id):
            own["parameters"] = graph.reads[study.id]
        setup = _digest({"own": own, "inputs": inputs})
        from puzzle.preload import needed

        # A preload step put in front (puzzle.preload) changes the solve on that solver.
        preloads = {s: needed(project, study, s) for s in study.all_solvers()}
        runs[study.id] = {
            solver: [
                _digest(
                    {
                        "setup": setup,
                        "solver": solver,
                        **({"preload": preloads[solver]} if preloads[solver] else {}),
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


def variant(key: str, *how) -> str:
    """The key of a run of the study's own model on a mesh made another way (ADR 0066:
    a convergence level, an adaptive pass): the study's key, then what was changed. So
    the run is current exactly while the study's key is (`base_key`)."""
    return f"{key}+{_digest([*how])[:24]}"


def base_key(key: str) -> str:
    """The study's key a run's key was made from (itself, for a plain run)."""
    return key.split("+", 1)[0]


def mesh_hash(project: Project, component_id: UUID, root: Path | None = None) -> str:
    """What a component's mesh depends on (GUI-22): its geometries (with their files'
    contents), its recipe, its parts, selections, contacts and other settings but its
    loads and materials, and the parameters those read. A mesh kept under one hash is
    stale once the hash changes."""
    graph = _Graph(project, root if root is not None else project.root)
    hashes = _hashes(graph)
    comp = project.objects.get(component_id)
    if not isinstance(comp, Component):
        return ""
    own = {k: v for k, v in _own(comp).items() if k not in ("physics", "materials")}
    reads = [d for d in (*(p.geometry for p in comp.parts), comp.mesh)]
    ops, values = _parameters(project)
    words: set[str] = set()
    for oid in reads:
        obj = project.objects.get(oid)
        if obj is not None:
            words |= _strings(_own(obj), set())
    words |= _strings(own, set())
    named = sorted(i for n in words & ops.keys() for i in ops[n])
    return _digest(
        {
            "own": own,
            "inputs": [hashes.get(d, "missing") for d in (*reads, *named)],
            "parameters": {n: values[n] for n in sorted(words & values.keys())},
        }
    )


def stale_studies(
    project: Project, computed: dict[UUID, str], root: Path | None = None
) -> set[UUID]:
    """Studies whose current hash differs from the hash their stored result was computed from."""
    current = content_hashes(project, root)
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
