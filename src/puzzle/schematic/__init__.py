"""The project schematic (ADR 0058): the whole project as nodes and wires, generated
from the model and never stored.

`build(project, runs, states)` is a pure function of the project, its stored runs and
their states (ADR 0033). The GUI draws it, `puzzle schematic` prints or exports it, the
MCP tool `schematic` returns it as data, and the outline's flow section reads it.

- Nodes sit in columns: inputs (files), resources (geometry, materials, mesh recipes,
  parameter sets, imported fields), models (components), studies (a study fed by
  another to its right), results (each study's runs as one node, derived results).
- Smaller references (coordinate systems, amplitudes, bolt types, construction
  geometry, result tools) are chips on the node that uses them, not nodes.
- Two kinds of wire, never mixed: data wires carry values (a transfer, an imported field
  into the feature it drives, runs into a derived result), labelled and coloured by what
  flows; use wires say "is built from" (file → geometry → component → study).
- Ports, as Blender's: a study's inputs are its component and each transfer into it; its
  outputs the fields its physics produce (from the catalog, so a study not yet run shows
  them). A data wire joins an output port to an input port; use wires join nodes.

Where a node sits never means anything: `layout` places them (`puzzle.schematic.layout`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

from puzzle.model.schema import (
    Component,
    Geometry,
    ImportedField,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    ResultDefinition,
    ResultProbe,
    Study,
    Transfer,
    TreeGroup,
)

COLUMNS = ["Inputs", "Resources", "Models", "Studies", "Results"]
INPUTS, RESOURCES, MODELS, STUDIES, RESULTS = range(5)

# How every kind of project object appears (ADR 0058 item 2): its own node, a chip on the
# nodes that use it, or not at all (with why). A new kind must say which:
# tests/test_schematic.py checks every kind against this table.
SHOWN = {
    "parameters": "node",
    "geometry": "node",
    "material": "node",
    "mesh": "node",
    "field": "node",
    "component": "node",
    "study": "node",
    "result": "node",
    "operator": "node",  # ADR 0062
    "optimisation": "node",  # ADR 0063
    "mesh_study": "node",  # ADR 0066
    "transfer": "wire",
    "axes": "chip",
    "amplitude": "chip",
    "bolt_type": "chip",
    "construction": "chip",
    "probe": "chip",
    "group": "frame",
    "preset": "none",  # copied into studies when applied; nothing refers to one
    "labels": "none",  # the tree's own names for runs
    "units": "none",  # what the GUI shows; never an input
    "cut": "none",  # a saved view of results
}
CHIP_WORDS = {
    "axes": "CS",
    "amplitude": "Amplitude",
    "bolt_type": "Bolt type",
    "construction": "Construction",
    "probe": "Result tool",
}
KIND_WORDS = {
    "file": "File",
    "parameters": "Parameter set",
    "geometry": "Geometry",
    "material": "Material",
    "mesh": "Mesh recipe",
    "field": "Imported field",
    "component": "Component",
    "study": "Study",
    "runs": "Results",
    "result": "Derived result",
    "operator": "Operator",
    "optimisation": "Optimisation",
    "mesh_study": "Mesh study",
    "group": "Group",
}
# What flows along a data wire, for its colour (as Blender colours sockets by type).
FLOWS = ("temperature", "displacement", "stress", "pressure", "velocity", "other")
# Their colours on a light ground (the SVG export; the GUI's come from its theme).
FLOW_COLOURS = {"temperature": "#d9480f", "displacement": "#1c7ed6", "stress": "#9c36b5",
                "pressure": "#0c8599", "velocity": "#2f9e44", "other": "#5c677d"}  # fmt: skip
STATE_COLOURS = {"complete": "#4caf50", "up_to_date": "#4caf50", "missing": "#e5534b",
                 "failed": "#e5534b", "out_of_date": "#d4a72c", "running": "#4f8fe6",
                 "not_run": "#8a94a3", "unused": "#8a94a3"}  # fmt: skip


@dataclass
class Port:
    key: str
    label: str
    side: str  # "in" or "out"
    carries: str = ""  # the field it takes or gives ("temperature")
    type: str = ""  # an operator port's type (ADR 0062: number, field, ...), "" otherwise


@dataclass
class Node:
    id: str
    kind: str  # a KIND_WORDS key
    column: int
    label: str
    subtitle: str = ""
    object: str = ""  # the project object's id, a run's id, or "" for a file
    state: str = "complete"
    reason: str = ""
    ports: list[Port] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    tips: dict[str, str] = field(default_factory=dict)  # chip -> who else uses it
    badge: str = ""  # "× 12 points", "CalculiX · Code_Aster"
    rows: list[str] = field(default_factory=list)  # a component's parts and materials
    used_by: int = 0  # use wires out of it (a resource used more than once says so)
    group: str = ""  # the tree group it sits in
    value: str = ""  # an operator's value as shown (ADR 0062): "212 MPa", "Pass: ..."
    verdict: str = ""  # a check's "pass" or "fail"
    slider: tuple[float, float, float] | None = None  # a slider's (low, high, value), SI

    def port(self, key: str) -> Port | None:
        return next((p for p in self.ports if p.key == key), None)

    def inputs(self) -> list[Port]:
        return [p for p in self.ports if p.side == "in"]

    def outputs(self) -> list[Port]:
        return [p for p in self.ports if p.side == "out"]


@dataclass
class Wire:
    source: str
    target: str
    kind: str  # "data", "use", or "drives" (study -> the optimisation running it, ADR 0063)
    source_port: str = ""  # "" joins the node itself (use wires)
    target_port: str = ""
    label: str = ""
    carries: str = ""
    object: str = ""  # a transfer's or field use's object, for selecting the wire
    type: str = ""  # what an operator's wire carries (ADR 0062): number, field, ...

    @property
    def flow(self) -> str:
        return flow_of(self.carries)


@dataclass
class Group:
    id: str
    label: str
    members: list[str]  # node ids
    folded: bool = False


@dataclass
class Schematic:
    nodes: dict[str, Node] = field(default_factory=dict)
    wires: list[Wire] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def node_of(self, object_id: str) -> Node | None:
        """The node standing for a project object (or a run), if it has one."""
        return next((n for n in self.nodes.values() if n.object == object_id), None)

    def upstream(self, node_id: str) -> set[str]:
        """Everything the node is built from or fed by, through both kinds of wire."""
        return self._walk(node_id, forward=False)

    def downstream(self, node_id: str) -> set[str]:
        """What would go out of date if the node changed."""
        return self._walk(node_id, forward=True)

    def _walk(self, start: str, forward: bool) -> set[str]:
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            here = frontier.pop()
            for w in self.wires:
                if w.kind == "drives":  # a mark, not a dependency: no loop through it
                    continue
                a, b = (w.source, w.target) if forward else (w.target, w.source)
                if a == here and b not in seen and b != start:
                    seen.add(b)
                    frontier.append(b)
        return seen

    def to_dict(self) -> dict:
        """The schematic as plain data (MCP, `puzzle schematic --json`)."""
        return {
            "columns": COLUMNS,
            "nodes": [asdict(n) for n in self.nodes.values()],
            "wires": [asdict(w) for w in self.wires],
            "groups": [asdict(g) for g in self.groups],
        }

    def flow_lines(self) -> list[str]:
        """The outline's flow section (ADR 0015): what feeds what, in words."""
        names = {n.id: n.label for n in self.nodes.values()}
        lines = []
        for w in self.wires:
            if w.kind == "drives":
                lines.append(f"{names[w.target]} runs {names[w.source]} once per point")
            if w.kind == "data":
                what = f" ({w.label})" if w.label else ""
                lines.append(f"{names[w.source]} -> {names[w.target]}{what}")
        for n in self.nodes.values():
            if n.used_by > 1:
                users = sorted(names[w.target] for w in self.wires
                               if w.kind == "use" and w.source == n.id)  # fmt: skip
                lines.append(f"{KIND_WORDS[n.kind].lower()} {n.label} used by {', '.join(users)}")
        return lines


def port_colour(port: Port | None) -> str:
    """A socket's colour: an operator port's by its type (ADR 0062), a field's by what
    flows (a result port carrying a field keeps that field's colour)."""
    from puzzle.operators import TYPE_COLOURS

    if port is None:
        return FLOW_COLOURS["other"]
    if port.type and port.type != "result":
        return TYPE_COLOURS[port.type]
    if port.carries:
        return FLOW_COLOURS[flow_of(port.carries)]
    return TYPE_COLOURS["result"] if port.type else "#8a94a3"


def wire_colour(wire: Wire) -> str:
    """A data wire's colour, as its socket's."""
    from puzzle.operators import TYPE_COLOURS

    if wire.type and wire.type != "result":
        return TYPE_COLOURS[wire.type]
    if wire.type == "result" and not wire.carries:
        return TYPE_COLOURS["result"]
    return FLOW_COLOURS[wire.flow]


def socket_shape(port: Port | None) -> str:
    """circle, diamond, square or triangle (ADR 0062 item 2: a type's own shape)."""
    from puzzle.operators import SHAPES

    return SHAPES.get(port.type, "circle") if port is not None else "circle"


VERDICT_COLOURS = {"pass": "#2f9e44", "fail": "#e03131"}


def flow_of(field_name: str) -> str:
    """Which colour family a field's wire takes."""
    name = field_name or ""
    for family in FLOWS[:-1]:
        if name.startswith(family) or (family == "pressure" and name.endswith("pressure")):
            return family
    if name in ("strain", "plastic_strain", "plastic_strain_equivalent"):
        return "stress"
    if name == "heat_flux":
        return "temperature"
    return "other"


def words(name: str) -> str:
    from puzzle.post.quantities import words as w

    return w(name)


def outputs(project: Project, study: Study) -> list[str]:
    """The fields a study's runs make, from its physics (the catalog), not from a run."""
    from puzzle.model.catalog import CONTACT_OUTPUTS, HARMONIC_OUTPUTS, OUTPUTS

    comp = project.objects.get(study.component)
    if not isinstance(comp, Component):
        return []
    chosen = {p for s in study.solve_steps() for p in s.physics}
    out: list[str] = []
    for ph in comp.physics:
        if chosen and ph.type not in chosen:
            continue
        if ph.type == "custom_pde" and ph.equation is not None:
            out += [u.name for u in ph.equation.unknowns]
            continue
        out += OUTPUTS.get(ph.type, [])
        if ph.type == "solid_mechanics":
            if any(s.type == "frequency_domain" for s in study.solve_steps()):
                out += HARMONIC_OUTPUTS
            if any(c.type != "bonded" for c in comp.contacts):
                out += CONTACT_OUTPUTS
    return list(dict.fromkeys(out))


def _files(project: Project) -> list[tuple[str, str]]:
    """(file, the id of the object reading it) for every outside file the project reads."""
    found = []
    for g in project.of_type(Geometry):
        if g.source:
            found.append((g.source, str(g.id)))
        for step in g.history:
            for key in ("file", "dxf"):
                value = step.args.get(key)
                if isinstance(value, str) and value and not step.suppressed:
                    found.append((value, str(g.id)))
    for f in project.of_type(ImportedField):
        found.append((f.file, str(f.id)))
        found += [(t.file, str(f.id)) for t in f.times if getattr(t, "file", "")]
    return found


def _sweep_badge(study: Study) -> str:
    points = len(study.sweep_points())
    parts = [f"× {points} points"] if study.sweeps() and points > 1 else []
    if study.shootout and study.shootout.solvers:
        from puzzle.adapters import SOLVER_NAMES

        parts.append(" · ".join(SOLVER_NAMES.get(s, s) for s in study.all_solvers()))
    return "  ".join(parts)


def build(
    project: Project,
    runs=(),
    states: dict | None = None,
    root: Path | None = None,
    folded: frozenset[str] = frozenset(),
    fields: bool = True,
    store=None,
) -> Schematic:
    """The schematic of `project`. `runs` are the result store's records, `states` the
    objects' states (`project.status.status`; computed here when not given), `root` the
    project folder (files not found there are marked missing), `folded` the tree groups
    drawn as one node, `fields` whether imported fields are shown (the preference
    `connections.schematic`), and `store` the results store, given when the operators'
    values are to be shown (ADR 0062)."""
    from puzzle.adapters import SOLVER_NAMES
    from puzzle.operators import resolved
    from puzzle.project.status import State, run_state, status

    runs = list(runs)
    listed = resolved(project)  # sweeps over an operator's list count its values
    if states is None:
        states = status(project, runs)
    names = {str(o.id): o.name for o in project.objects.values()}
    out = Schematic()

    def state_of(oid: str) -> State:
        return states.get(oid) or State("complete")

    def add(node: Node) -> Node:
        st = state_of(node.object) if node.object else None
        if st is not None:
            node.state, node.reason = st.state, st.reason
        out.nodes[node.id] = node
        return node

    def use(source: str, target: str, port: str = "") -> None:
        known = source in out.nodes and target in out.nodes and source != target
        if known and not any(w.source == source and w.target == target and w.kind == "use"
                             for w in out.wires):  # fmt: skip
            out.wires.append(Wire(source, target, "use", target_port=port))

    # Inputs: outside files, one node per file however many objects read it.
    for path, reader in _files(project):
        if not fields and isinstance(project.objects.get(UUID(reader)), ImportedField):
            continue
        key = f"file:{path}"
        if key not in out.nodes:
            node = Node(key, "file", INPUTS, Path(path).name, subtitle=_folder(path))
            if root is not None and not (Path(root) / path).exists():
                node.state, node.reason = "missing", f"{path} is not in the project folder"
            out.nodes[key] = node

    # Resources.
    for kind, cls in (("geometry", Geometry), ("material", Material), ("mesh", MeshRecipe),
                      ("parameters", ParameterSet)):  # fmt: skip
        for obj in project.of_type(cls):
            sub = ""
            if isinstance(obj, Geometry):
                sub = "history" if obj.history else ("file" if obj.source else "")
                if obj.suppressed:
                    sub = "suppressed"
            node = add(Node(str(obj.id), kind, RESOURCES, obj.name, subtitle=sub,
                            object=str(obj.id)))  # fmt: skip
            if isinstance(obj, ParameterSet):  # each value a number to wire (ADR 0062)
                node.ports = [Port(k, k, "out", type="number") for k in obj.values]
    if fields:
        for f in project.of_type(ImportedField):
            cols = [c.name for c in f.columns if c.role == "value"]
            node = add(Node(str(f.id), "field", RESOURCES, f.name, object=str(f.id),
                            subtitle=", ".join(cols)))  # fmt: skip
            node.ports = [Port(c, words(c), "out", c) for c in cols]
    for path, reader in _files(project):
        use(f"file:{path}", reader)
    for g in project.of_type(Geometry):  # a section of another geometry (ADR 0048)
        for dep in g.dependencies():
            use(str(dep), str(g.id))

    # Models.
    for c in project.of_type(Component):
        mats = {a.selection: names.get(str(a.material), "?") for a in c.materials}
        rows = []
        for p in c.parts:
            on = [m for sel, m in mats.items() if sel == p.name or sel.startswith(p.name + "/")]
            rows.append(f"{p.name}: {', '.join(dict.fromkeys(on)) or 'no material'}")
        node = add(Node(str(c.id), "component", MODELS, c.name, object=str(c.id),
                        subtitle=" · ".join(p.name for p in c.parts), rows=rows))  # fmt: skip
        for ref in c.dependencies():
            use(str(ref), str(c.id))
        if fields:
            _field_ports(project, c, node, out)

    # Studies, and transfers between them.
    for s in project.of_type(Study):
        physics = sorted({p for st in s.solve_steps() for p in st.physics})
        what = ", ".join(words(p) for p in physics) if physics else ""
        steps = ", ".join(words(st.type) for st in s.solve_steps())
        sub = " · ".join(x for x in (SOLVER_NAMES.get(s.solver, s.solver), steps, what) if x)
        node = add(Node(str(s.id), "study", STUDIES, s.name, subtitle=sub, object=str(s.id),
                        badge=_sweep_badge(listed.objects.get(s.id, s))))  # fmt: skip
        node.ports = [Port("component", "Component", "in")]
        node.ports += [Port(f, words(f), "out", f) for f in outputs(project, s)]
        use(str(s.component), str(s.id), "component")
        swept = s.swept_parameters()
        for p in project.of_type(ParameterSet):
            if swept & set(p.values):
                use(str(p.id), str(s.id))
    for t in project.of_type(Transfer):
        src, tgt = out.nodes.get(str(t.source)), out.nodes.get(str(t.target))
        if src is None or tgt is None:
            continue
        if src.port(t.field) is None:  # a field the catalog doesn't list: still a port
            src.ports.append(Port(t.field, words(t.field), "out", t.field))
        key = f"transfer:{t.id}"
        tgt.ports.append(Port(key, f"{words(t.field)} ({words(t.role).lower()})", "in", t.field))
        step = "last step" if t.source_step == -1 else f"step {t.source_step + 1}"
        label = f"{words(t.field).lower()}, {step}, {t.mapping}"
        out.wires.append(Wire(src.id, tgt.id, "data", t.field, key, label, t.field, str(t.id)))

    # Results: each study's runs as one node (a sweep's or shoot-out's too).
    by_study: dict[str, list] = {}
    for r in runs:
        by_study.setdefault(r.study_id, []).append(r)
    for s in project.of_type(Study):
        mine = sorted(by_study.get(str(s.id), []), key=lambda r: r.recorded_at)
        if not mine:
            continue
        latest = mine[-1]
        label = f"{s.name}: results"
        node = Node(f"runs:{s.id}", "runs", RESULTS, label, object=latest.run_id,
                    subtitle=f"{len(mine)} run{'s' if len(mine) != 1 else ''}",
                    badge=_sweep_badge(listed.objects.get(s.id, s)))  # fmt: skip
        st = run_state(latest, project)
        node.state, node.reason = st.state, st.reason
        node.ports = [Port("result", "Result", "out", type="result")]
        node.ports += [Port(f, words(f), "out", f) for f in outputs(project, s)]
        out.nodes[node.id] = node
        use(str(s.id), node.id)
    for d in project.of_type(ResultDefinition):
        method = "Fatigue" if d.method == "fatigue" else words(d.method)
        node = add(Node(str(d.id), "result", RESULTS, d.name, subtitle=method, object=str(d.id)))
        node.ports = [Port("result", "Result", "out", type="result"), Port("in", "Cases", "in")]
        for ref in dict.fromkeys(d.dependencies()):
            src = out.nodes.get(f"runs:{ref}") or out.nodes.get(str(ref))
            if src is not None:
                out.wires.append(Wire(src.id, node.id, "data", target_port="in",
                                      label=words(d.quantity or "") if getattr(d, "quantity", "")
                                      else "", carries=d.quantity or ""))  # fmt: skip

    _operators(project, out, store, states)
    _optimisations(project, out, store)
    _mesh_studies(project, out, store)
    _chips(project, out, names)
    for n in out.nodes.values():
        n.used_by = len({w.target for w in out.wires if w.kind == "use" and w.source == n.id})
    _groups(project, out, folded)
    return out


def _operators(project: Project, out: Schematic, store, states: dict) -> None:
    """Operators (ADR 0062) as nodes after what they read, a typed port per input and
    output, joined by data wires; with a `store`, each shows its value."""
    from puzzle.model.schema import Operator
    from puzzle.operators import TYPES, is_pre

    ops = project.of_type(Operator)
    pre = {op.id for op in ops if is_pre(project, op)}
    for op in ops:
        t = TYPES[op.type]
        column = RESOURCES if op.id in pre else RESULTS  # beside what it feeds (item 8)
        node = Node(str(op.id), "operator", column, op.name, subtitle=t.label,
                    object=str(op.id))  # fmt: skip
        st = states.get(str(op.id))
        if st is not None:
            node.state, node.reason = st.state, st.reason
        node.ports = [Port(p.key, p.label, "out", type=p.type) for p in t.outputs]
        node.ports += [Port(p.key, p.label, "in", type=p.type) for p in t.inputs]
        out.nodes[node.id] = node
    for op in ops:
        node = out.nodes[str(op.id)]
        for key, inp in op.inputs.items():
            if inp.source is None:
                if inp.value is not None:  # a typed value, shown on its port
                    port = node.port(key)
                    if port is not None:
                        port.label = f"{port.label}: {_typed_text(inp.value)}"
                continue
            src, port, label = _wire_end(project, out, inp.source, inp.port)
            if src is None:
                continue
            spec = TYPES[op.type].input(key)
            given = _given(project, inp.source, inp.port)
            carries = inp.port if given == "result" and inp.port not in ("result", "out") else ""
            out.wires.append(Wire(src.id, node.id, "data", port, key, label, carries,
                                  str(op.id), given or (spec.type if spec else "")))  # fmt: skip
    _model_wires(project, out, pre)
    if ops:  # pre-solve ones need no results, so they always show their values
        from puzzle.operators import evaluate as ev

        ctx = ev.Context(project, store)
        for op in ops:
            if store is None and op.id not in pre:
                continue
            node = out.nodes[str(op.id)]
            node.value, node.verdict = ev.shown(ctx, op)
            if op.type == "slider":
                node.slider = _slider_of(ctx, op)


def _mesh_studies(project: Project, out: Schematic, store) -> None:
    """Mesh studies (ADR 0066) right of the study they drive, as optimisations are: a
    "drives" mark from the study, the meshes run and the verdict."""
    from puzzle.model.schema import MeshStudy

    for ms in project.of_type(MeshStudy):
        node = Node(str(ms.id), "mesh_study", RESULTS, ms.name,
                    subtitle=f"{ms.levels} meshes, ratio {ms.ratio:g}",
                    object=str(ms.id))  # fmt: skip
        out.nodes[node.id] = node
        if str(ms.study) in out.nodes:
            out.wires.append(Wire(str(ms.study), node.id, "drives", label="drives",
                                  object=str(ms.id)))  # fmt: skip
        study = project.objects.get(ms.study)
        if store is None or study is None:
            continue
        from puzzle import convergence as conv

        try:
            levels = conv.run(project, study, Path("."), Path("."), store=store,
                              count=ms.levels, ratio=ms.ratio, outputs=ms.quantities,
                              read_only=True)  # fmt: skip
        except Exception:  # noqa: BLE001 - a broken check still draws
            continue
        node.badge = f"{sum(1 for lv in levels if lv.run)} of {len(levels)} meshes"
        vs = conv.verdicts(levels, ms.target)
        if any(v.state != "too few levels" for v in vs):
            node.value = "; ".join(v.words for v in vs)


def _optimisations(project: Project, out: Schematic, store) -> None:
    """Optimisations (ADR 0063 item 9) right of the study they drive: inputs wired from
    the design variables and the objective and constraint outputs, a dashed "drives"
    mark to the study (not a data wire: nothing flows back, so the graph has no
    cycle), the best value so far and the point count."""
    from puzzle.model.schema import Optimisation

    by_name: dict[str, tuple[str, str]] = {}
    for ps in project.of_type(ParameterSet):
        for k in ps.values:
            by_name.setdefault(k, (str(ps.id), k))
    for o in project.objects.values():
        if o.kind == "operator" and o.type in ("number", "slider"):
            by_name.setdefault(o.name, (str(o.id), "out"))
    for opt in project.of_type(Optimisation):
        from puzzle.optimise import METHODS

        node = Node(str(opt.id), "optimisation", RESULTS, opt.name,
                    subtitle=METHODS[opt.method], object=str(opt.id))  # fmt: skip
        node.ports = [Port(f"var:{v.name}", f"Variable: {v.name}", "in", type="number")
                      for v in opt.variables]  # fmt: skip
        if opt.objective is not None:
            node.ports.append(Port("objective", "Objective", "in", type="number"))
        node.ports += [Port(f"constraint:{i}", f"Constraint {i + 1}", "in", type="number")
                       for i in range(len(opt.constraints))]  # fmt: skip
        out.nodes[node.id] = node
        for v in opt.variables:
            src = by_name.get(v.name)
            if src and src[0] in out.nodes:
                out.wires.append(Wire(src[0], node.id, "data", src[1], f"var:{v.name}", "",
                                      "", str(opt.id), "number"))  # fmt: skip
        ends = ([("objective", opt.objective)] if opt.objective else []) + [
            (f"constraint:{i}", c) for i, c in enumerate(opt.constraints)]  # fmt: skip
        for key, o in ends:
            src, port, label = _wire_end(project, out, o.source, o.port)
            if src is not None:
                out.wires.append(Wire(src.id, node.id, "data", port, key, label, "",
                                      str(opt.id), "number"))  # fmt: skip
        if str(opt.study) in out.nodes:  # drawn study to optimisation, left to right
            out.wires.append(Wire(str(opt.study), node.id, "drives", label="drives",
                                  object=str(opt.id)))  # fmt: skip
        if store is not None:
            from puzzle import optimise

            try:
                pts = optimise.points(project, store, opt)
            except Exception:  # noqa: BLE001 - a broken optimisation still draws
                continue
            done = sum(p.run for p in pts)
            node.badge = (f"{done} points" if optimise.adaptive(opt)
                          else f"{done} of {len(pts)} points")  # fmt: skip
            top = optimise.best(opt, pts)
            if top is not None:
                from puzzle.operators.evaluate import quantity_text

                best = ("best " + quantity_text(top.objective, top.kinds.get("objective"))
                        if top.objective is not None else "a feasible point")  # fmt: skip
                node.value = f"{best} at {top.title}"


def _slider_of(ctx, op) -> tuple[float, float, float] | None:
    """(low, high, value) in SI, for the knob drawn on a slider's node."""
    from puzzle.errors import PuzzleError
    from puzzle.operators import evaluate as ev

    try:
        v = ev.value(ctx, op).value
    except PuzzleError:
        return None
    low = op.low.to_si() if op.low is not None else min(0.0, v)
    high = op.high.to_si() if op.high is not None else max(v * 2, low + 1.0)
    return (low, high, v)


def _model_wires(project: Project, out: Schematic, pre: set) -> None:
    """What drives the model (ADR 0062 items 4, 5): a history argument naming a
    parameter or a pre-solve operator becomes an input port on its geometry (the
    history stays one node), and a sweep over an operator's list, or over a Number or
    Slider by name, one on its study; each joined by a data wire from its source."""
    from puzzle.model.schema import Operator

    by_name: dict[str, tuple[str, str]] = {}  # parameter -> (node id, port)
    for ps in project.of_type(ParameterSet):
        for k in ps.values:
            by_name.setdefault(k, (str(ps.id), k))
    for op in project.of_type(Operator):
        if op.id in pre and str(op.id) in out.nodes:
            by_name.setdefault(op.name, (str(op.id), "out"))
    for g in project.of_type(Geometry):
        node = out.nodes.get(str(g.id))
        if node is None:
            continue
        for i, step in enumerate(g.history):
            for key, value in step.args.items():
                if not isinstance(value, str) or value not in by_name:
                    continue
                src, port = by_name[value]
                if src not in out.nodes:
                    continue
                pkey = f"arg:{i}:{key}"
                node.ports.append(Port(pkey, f"{step.name}: {key}", "in", type="number"))
                out.wires.append(Wire(src, node.id, "data", port, pkey, value, "",
                                      str(g.id), "number"))  # fmt: skip
    for st in project.of_type(Study):
        node = out.nodes.get(str(st.id))
        if node is None:
            continue
        for n, step in enumerate(st.sweeps()):
            for j, prm in enumerate(step.parameters):
                if prm.source is not None:
                    src, port, kind = str(prm.source), prm.port, "list"
                elif prm.location is None and prm.name in by_name:
                    (src, port), kind = by_name[prm.name], "number"
                    if project.objects.get(UUID(src)) is None or \
                            project.objects[UUID(src)].kind != "operator":  # fmt: skip
                        continue  # a parameter set's sweep: as before, a use wire
                else:
                    continue
                if src not in out.nodes:
                    continue
                pkey = f"sweep:{n}:{j}"
                node.ports.append(Port(pkey, f"Sweep: {prm.name}", "in", type=kind))
                out.wires.append(Wire(src, node.id, "data", port, pkey, "swept", "",
                                      str(st.id), kind))  # fmt: skip


def _given(project: Project, source, port: str) -> str | None:
    from puzzle.operators import output_type

    return output_type(project, source, port)


def _typed_text(value) -> str:
    from puzzle.model.schema import Quantity
    from puzzle.model.units import pretty

    if isinstance(value, Quantity):
        return f"{value.value:g} {pretty(value.unit)}".strip().removesuffix(" dimensionless")
    return f"{value:g}" if isinstance(value, float) else str(value)


def _wire_end(project: Project, out: Schematic, source, port: str):
    """(node, its port, the wire's label) a wire into an operator starts from: a study's
    results node (the study itself before it has run), a derived result, the node a
    result tool reads, a parameter set's value, another operator's output."""
    from puzzle.model.schema import ResultProbe, Study

    obj = project.objects.get(source)
    if obj is None:
        return None, "", ""
    if isinstance(obj, Study):
        node = out.nodes.get(f"runs:{obj.id}") or out.nodes.get(str(obj.id))
        if node is not None and node.port(port) is None and port in ("result", "out"):
            return node, "", ""
        label = "" if port in ("result", "out") else words(port).lower()
        return node, port, label
    if isinstance(obj, ResultProbe):
        node = out.nodes.get(f"runs:{obj.source}") or out.nodes.get(str(obj.source))
        return node, "result", f"{CHIP_WORDS['probe'].lower()} {obj.name}"
    return out.nodes.get(str(obj.id)), port, ""


def _folder(path: str) -> str:
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


def _field_ports(project: Project, comp: Component, node: Node, out: Schematic) -> None:
    """Imported fields into the features, materials and sections they drive: an input
    port each, joined by a data wire from the field's column (ADR 0044, phase 3)."""
    uses = []
    for i, ph in enumerate(comp.physics):
        for j, f in enumerate(ph.features):
            for value, u in f.fields.items():
                uses.append((f"physics.{i}.features.{j}.fields.{value}",
                             f"{words(f.type)} on {f.selection}: {value}", u))  # fmt: skip
    for i, a in enumerate(comp.materials):
        for value, u in a.fields.items():
            uses.append((f"materials.{i}.fields.{value}", f"Material on {a.selection}: {value}",
                         u))  # fmt: skip
    for i, s in enumerate(comp.shell_sections):
        for value, u in s.fields.items():
            uses.append((f"shell_sections.{i}.fields.{value}",
                         f"Thickness on {s.selection}", u))  # fmt: skip
    for path, text, u in uses:
        src = out.nodes.get(str(u.field))
        if src is None:
            continue
        key = f"field:{path}"
        node.ports.append(Port(key, text, "in", u.column))
        label = u.column + (f" ×{u.scale:g}" if u.scale != 1 else "")
        if src.port(u.column) is None:
            src.ports.append(Port(u.column, words(u.column), "out", u.column))
        out.wires.append(Wire(src.id, node.id, "data", u.column, key, label, u.column,
                              str(u.field)))  # fmt: skip


def _chips(project: Project, out: Schematic, names: dict[str, str]) -> None:
    """Small references as chips on the nodes that use them; each chip's tip names the
    other users of the same object."""
    users: dict[str, list[str]] = {}  # chip object id -> node ids using it
    for obj in project.objects.values():
        node = out.nodes.get(str(obj.id))
        if node is None:
            continue
        for ref in obj.dependencies():
            target = project.objects.get(ref)
            if target is not None and SHOWN.get(target.kind) == "chip":
                users.setdefault(str(ref), []).append(node.id)
    for p in project.of_type(ResultProbe):  # a result tool sits on what it reads
        node = out.nodes.get(f"runs:{p.source}") or out.nodes.get(str(p.source))
        if node is not None:
            users.setdefault(str(p.id), []).append(node.id)
            if p.construction is not None:
                users.setdefault(str(p.construction), []).append(node.id)
    for ref, nodes in users.items():
        obj = project.objects.get(UUID(ref))
        if obj is None:
            continue
        chip = f"{CHIP_WORDS[obj.kind]}: {obj.name}"
        for nid in dict.fromkeys(nodes):
            node = out.nodes[nid]
            if chip not in node.chips:
                node.chips.append(chip)
            others = sorted({out.nodes[o].label for o in nodes if o != nid})
            node.tips[chip] = ("Also used by " + ", ".join(others)) if others else "Used here only"


def _groups(project: Project, out: Schematic, folded: frozenset[str]) -> None:
    """Tree groups as frames around their nodes; a folded one becomes one node, its
    wires rerouted to it."""
    groups = project.of_type(TreeGroup)

    def members(g: TreeGroup, seen=()) -> list[str]:
        found = []
        for m in g.members:
            inner = next((x for x in groups if str(x.id) == m), None)
            if inner is not None and m not in seen:
                found += members(inner, (*seen, m))
                continue
            key = m.removeprefix("sweep:")
            node = out.nodes.get(key) or out.node_of(m.removeprefix("run:"))
            if node is not None:
                found.append(node.id)
        return list(dict.fromkeys(found))

    for g in groups:
        held = [m for m in members(g) if m in out.nodes]
        if not held:
            continue
        gid = str(g.id)
        group = Group(gid, g.name, held, gid in folded)
        out.groups.append(group)
        for m in held:
            out.nodes[m].group = out.nodes[m].group or gid
        if not group.folded:
            continue
        from puzzle.project.status import worst

        inside = [out.nodes[m] for m in held]
        node = Node(f"group:{gid}", "group", min(n.column for n in inside), g.name,
                    subtitle=f"{len(inside)} items", object=gid,
                    state=worst(n.state for n in inside))  # fmt: skip
        node.ports = [Port("in", "In", "in"), Port("out", "Out", "out")]
        for w in out.wires:
            if w.source in held:
                w.source, w.source_port = node.id, "out" if w.kind == "data" else ""
            if w.target in held:
                w.target, w.target_port = node.id, "in" if w.kind == "data" else ""
        out.wires = [w for w in out.wires if w.source != w.target]
        for m in held:
            del out.nodes[m]
        out.nodes[node.id] = node
