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

from puzzle.look import LIGHT as _LIGHT
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
    "axes": "node",  # ADR 0071: a node, so it can be wired
    "amplitude": "node",  # ADR 0071: a node, so it can be wired
    "bolt_type": "node",  # ADR 0071: a node, so it can be wired
    "construction": "node",  # ADR 0071: a node, so it can be wired
    "probe": "node",  # ADR 0071: a node, so it can be wired
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
    "feature": "Load or support",
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
    "axes": "Coordinate system",
    "amplitude": "Amplitude",
    "bolt_type": "Bolt type",
    "construction": "Construction",
    "probe": "Result tool",
}
# What flows along a data wire, for its colour (as Blender colours sockets by type).
FLOWS = ("temperature", "displacement", "stress", "pressure", "velocity", "other")
# Their colours on a light ground (the SVG export; the GUI's come from its theme).
# They are the theme's tokens (ADR 0045; `puzzle.look`, no Qt), its light set's.
FLOW_COLOURS = dict(_LIGHT.flows)
STATE_COLOURS = dict(_LIGHT.states)


@dataclass
class Port:
    key: str
    label: str
    side: str  # "in" or "out"
    carries: str = ""  # the field it takes or gives ("temperature")
    type: str = ""  # an operator port's type (ADR 0062: number, field, ...), "" otherwise
    help: str = ""  # what it takes or gives, in words (ADR 0071): its socket's tooltip


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
    help: str = ""  # an operator's help card as text (ADR 0071): purpose, example, ports
    path: str = ""  # a feature's place in its component ("physics.0.features.1")
    sketch: list = field(default_factory=list)  # a curve's small plot: lines of (x, y) in 0..1
    # A folded group's nodes (ADR 0071: a cluster's ports are theirs, keyed
    # "<member id>::<port>"), for the view to wire to the member a port belongs to.
    inside: list = field(default_factory=list)
    # How its value is drawn (ADR 0090 package 13): "" nothing to show, "current",
    # "stale" (worked out from a run no longer current, or reading what is out of
    # date: muted, never a verdict's colour), "error", "working" (being worked out).
    value_state: str = ""

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
    return TYPE_COLOURS["result"] if port.type else STATE_COLOURS["not_run"]


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


VERDICT_COLOURS = dict(_LIGHT.verdicts)


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
    from puzzle.model.catalog import (
        CONTACT_OUTPUTS,
        EDDY_OUTPUTS,
        HARMONIC_OUTPUTS,
        OUTPUTS,
    )

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
        if ph.type == "magnetic_fields" and (
            any(s.type == "frequency_domain" for s in study.solve_steps())
            or any(f.type == "stranded_coil" for f in ph.features)
        ):
            out += EDDY_OUTPUTS
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
    keys: dict | None = None,
    evaluate: bool = True,
) -> Schematic:
    """The schematic of `project`. `runs` are the result store's records, `states` the
    objects' states (`project.status.status`; computed here when not given), `root` the
    project folder (files not found there are marked missing), `folded` the tree groups
    drawn as one node, `fields` whether imported fields are shown (the preference
    `connections.schematic`), and `store` the results store, given when the operators'
    values are to be shown (ADR 0062). `keys` are `graph.run_hashes`, when worked out
    already. With `evaluate` false nothing is worked out: operators' values are left to
    `values` and `fill` (the GUI does that off its thread, ADR 0090 package 13), each
    node that would show one marked "working"."""
    from puzzle.adapters import SOLVER_NAMES
    from puzzle.operators import resolved
    from puzzle.project.status import State, run_state, status

    runs = list(runs)
    listed = resolved(project)  # sweeps over an operator's list count its values
    if keys is None and (runs or (evaluate and store is not None) or states is None):
        keys = run_keys_of(project, root)
    if states is None:
        states = status(project, runs, keys=keys)
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
    from puzzle.model.schema import Amplitude, BoltType, ConstructionGeometry, CoordinateSystem

    for kind, cls in (("geometry", Geometry), ("material", Material), ("mesh", MeshRecipe),
                      ("parameters", ParameterSet), ("axes", CoordinateSystem),
                      ("amplitude", Amplitude), ("bolt_type", BoltType),
                      ("construction", ConstructionGeometry)):  # fmt: skip
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
                        subtitle=" · ".join(p.name for p in c.parts), rows=rows,
                        badge=_joints(c)))  # fmt: skip
        in_features = _feature_nodes(c, node, out, use)  # ADR 0071: loads and supports
        for ref in c.dependencies():
            if ref not in in_features:  # a load's own references go to its node
                use(str(ref), str(c.id))
        if fields:
            _field_ports(project, c, node, out)
    _feature_titles(out)

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
        st = run_state(latest, project, keys=keys or {})
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

    from puzzle.operators import TYPE_WORDS, outputs_of

    for p in project.of_type(ResultProbe):  # result tools: nodes beside what they read
        node = add(Node(str(p.id), "probe", RESULTS, p.name, object=str(p.id),
                        subtitle=words(p.type)))  # fmt: skip
        node.ports += [Port(k, label, "out", type=t,
                            help=f"{label}: gives a {TYPE_WORDS[t].lower()}")
                       for k, label, t in outputs_of(project, p)]  # fmt: skip
    _operators(project, out, states)
    _optimisations(project, out)
    _mesh_studies(project, out)
    # Every reference between two nodes is a use wire (ADR 0071 decision 8: each can be
    # drawn and removed), whatever made it.
    # their data wires are these; a component's are drawn with it (its loads' to theirs)
    drawn = ("operator", "result", "optimisation", "mesh_study", "component")
    for obj in project.objects.values():
        if str(obj.id) in out.nodes and obj.kind not in drawn:
            for ref in obj.dependencies():
                src = out.nodes.get(str(ref)) or out.nodes.get(f"runs:{ref}")
                if src is not None:
                    use(src.id, str(obj.id))
    _chips(project, out, names)
    from puzzle.schematic.links import source_kinds

    wireable = source_kinds()
    for n in out.nodes.values():  # a socket to draw a reference from (ADR 0071 decision 8)
        if n.kind in wireable and not n.outputs() and n.kind != "operator":  # SCH-UX-5
            n.ports.append(Port("use", "Use in…", "out", help="Drag from here onto what uses "
                                "it (a material onto a component, a coordinate system onto "
                                "a component's load, a study onto a result tool)"))  # fmt: skip
    for n in out.nodes.values():
        n.used_by = len({w.target for w in out.wires if w.kind == "use" and w.source == n.id})
    if evaluate:
        fill(out, values(project, store, keys, root))
    else:
        for nid in _shown_by(project, store) & set(out.nodes):
            out.nodes[nid].value_state = "working"
    _groups(project, out, folded)
    return out


def _operators(project: Project, out: Schematic, states: dict) -> None:
    """Operators (ADR 0062) as nodes after what they read, a typed port per input and
    output, joined by data wires; their values come from `values`."""
    from puzzle.model.schema import Operator
    from puzzle.operators import (
        FOLLOWS_INPUTS,
        TYPE_WORDS,
        TYPES,
        doc_text,
        first_type,
        is_pre,
        port_doc,
    )

    ops = project.of_type(Operator)
    pre = {op.id for op in ops if is_pre(project, op)}
    only_results = _limits(project, ops, pre)
    for op in ops:
        t = TYPES[op.type]
        # Beside what it feeds (item 8): a parameter in Resources; a limit or an allowable
        # that only checks read, beside them in Results (SCH-UX-4).
        column = RESOURCES if op.id in pre and op.id not in only_results else RESULTS
        node = Node(str(op.id), "operator", column, op.name, subtitle=_op_subtitle(op),
                    object=str(op.id), help=doc_text(op.type))  # fmt: skip
        st = states.get(str(op.id))
        if st is not None:
            node.state, node.reason = st.state, st.reason
        node.ports = []
        for i, p in enumerate(t.outputs):
            given = first_type(op, project) if i == 0 else p.type
            # SCH-9: where the type follows the inputs, so does the label ("Value", not
            # "Curve", on a Safety factor of two numbers).
            label = TYPE_WORDS[given] if i == 0 and op.type in FOLLOWS_INPUTS else p.label
            node.ports.append(Port(p.key, label, "out", type=given, help=port_doc(p, "out")))
        node.ports += [Port(p.key, p.label, "in", type=p.type, help=port_doc(p, "in"))
                       for p in _shown_inputs(op, t)]  # fmt: skip
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


def _shown_inputs(op, t) -> list:
    """The inputs a node draws (SCH-UX-5): every required one, every optional one wired
    or typed, and one empty optional socket more, so a Report shows the items it has
    and a socket for the next rather than Item A to Item H."""
    shown, spare = [], False
    for p in t.ports_for(op):
        inp = (op.inputs or {}).get(p.key)
        used = inp is not None and (inp.source is not None or inp.value is not None)
        if p.required or used:
            shown.append(p)
        elif not spare:
            shown.append(p)
            spare = True
    return shown


def _limits(project: Project, ops: list, pre: set) -> set:
    """The before-solving operators only after-solving ones read (a limit, an
    allowable): read by at least one operator, by no other kind of object, and not by
    name anywhere in the model (a history argument, a bound value, a sweep, a toggle),
    nor feeding one that is (SCH-UX-4: they were drawn in Resources, wired across three
    columns to their checks)."""
    from puzzle.project.graph import _strings

    named: set[str] = set()
    for obj in project.objects.values():
        if obj.kind != "operator":
            named |= _strings(obj.model_dump(mode="json"), set())
    readers: dict = {op.id: [] for op in ops}
    for op in ops:
        for dep in op.dependencies():
            if dep in readers:
                readers[dep].append(op.id)
    others = {d for o in project.objects.values() if o.kind != "operator"
              for d in o.dependencies()}  # fmt: skip
    out = {op.id for op in ops if op.id in pre and readers[op.id] and op.name not in named
           and op.id not in others}  # fmt: skip
    changed = True
    while changed:  # a limit feeding something the model reads is a parameter after all
        changed = False
        for oid in list(out):
            if any(r in pre and r not in out for r in readers[oid]):
                out.discard(oid)
                changed = True
    return out


def _op_subtitle(op) -> str:
    """An operator's type, and for the ones told apart by their settings what those say
    (OP-NEW-14: eight Result summaries differed only by the names given them):
    "Result summary · maximum equivalent (von Mises) stress on Bar/Root"."""
    from puzzle.operators import TYPES

    label = TYPES[op.type].label
    if op.type == "reduce":
        from puzzle.operators.evaluate import REDUCTIONS
        from puzzle.post.quantities import label_of

        wired = (op.inputs or {}).get("result")
        port = wired.port if wired is not None and wired.source is not None else ""
        if op.quantity:
            what = label_of(op.quantity)
        elif port and port not in ("result", "out"):  # a study's field port: that field
            what = words(port)
        else:
            what = "of its first result"
        what = what[:1].lower() + what[1:]
        on = f" on {op.selection}" if op.selection else ""
        return f"{label} · {REDUCTIONS[op.reduction].lower()} {what}{on}"
    if op.type == "check":
        return f"{label} · {'at most' if op.compare == 'at_most' else 'at least'}"
    return label


def _mesh_studies(project: Project, out: Schematic) -> None:
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


def _mesh_study_value(project: Project, ms, store) -> Shown | None:
    """A mesh study's meshes run and its verdict, from the store."""
    from puzzle import convergence as conv

    study = project.objects.get(ms.study)
    if study is None:
        return None
    try:
        levels = conv.run(project, study, Path("."), Path("."), store=store,
                          count=ms.levels, ratio=ms.ratio, outputs=ms.quantities,
                          read_only=True)  # fmt: skip
    except Exception:  # noqa: BLE001 - a broken check still draws
        return None
    got = Shown(badge=f"{sum(1 for lv in levels if lv.run)} of {len(levels)} meshes")
    vs = conv.verdicts(levels, ms.target)
    if any(v.state != "too few levels" for v in vs):
        got.value = "; ".join(v.words for v in vs)
    return got


def _optimisations(project: Project, out: Schematic) -> None:
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


def _optimisation_value(project: Project, opt, store) -> Shown | None:
    """An optimisation's points run and its best so far, from the store."""
    from puzzle import optimise

    try:
        pts = optimise.points(project, store, opt)
    except Exception:  # noqa: BLE001 - a broken optimisation still draws
        return None
    done = sum(p.run for p in pts)
    got = Shown(badge=(f"{done} points" if optimise.adaptive(opt)
                       else f"{done} of {len(pts)} points"))  # fmt: skip
    top = optimise.best(opt, pts)
    if top is not None:
        from puzzle.operators.evaluate import quantity_text

        best = ("best " + quantity_text(top.objective, top.kinds.get("objective"))
                if top.objective is not None else "a feasible point")  # fmt: skip
        got.value = f"{best} at {top.title}"
    return got


@dataclass
class Shown:
    """What one node shows that has to be worked out: an operator's value (and verdict,
    curve, knob), an optimisation's or mesh study's progress. `stale` names the run no
    longer current it was worked out from; `error` says the value is an error's words."""

    value: str = ""
    verdict: str = ""
    sketch: list = field(default_factory=list)
    slider: tuple[float, float, float] | None = None
    stale: str = ""
    error: bool = False
    badge: str = ""


def run_keys_of(project: Project, root=None) -> dict:
    """`graph.run_hashes`, worked out once a refresh and shared by status, the tree, the
    schematic and its values (SCH-1: they had been worked out seven times)."""
    from puzzle.project.graph import run_hashes

    try:
        return run_hashes(project, root)
    except ValueError:  # a transfer loop (refused by the model): every run is stale
        return {}


def _shown_by(project: Project, store) -> set[str]:
    """The nodes whose values `values` works out: every operator that needs no results,
    and with a store every operator, optimisation and mesh study."""
    from puzzle.operators import is_pre

    with_store = ("operator", "optimisation", "mesh_study") if store is not None else ()
    return {str(o.id) for o in project.objects.values() if o.kind in with_store
            or (o.kind == "operator" and is_pre(project, o))}  # fmt: skip


def values(project: Project, store=None, keys: dict | None = None, root=None) -> dict:
    """Node id -> `Shown`, for every node `_shown_by` names: the half of `build` that
    evaluates, kept apart so the GUI runs it off its thread (ADR 0090 package 13,
    SCH-1). Never raises for one operator: its error is its value. `project` is only
    read, so a session's project (which a change replaces, never edits) is safe to hand
    a worker."""
    from puzzle.model.schema import MeshStudy, Operator, Optimisation
    from puzzle.operators import evaluate as ev

    out: dict[str, Shown] = {}
    wanted = _shown_by(project, store)
    ops = [op for op in project.of_type(Operator) if str(op.id) in wanted]
    if ops:
        ctx = ev.Context(project, store, root=root)
        if keys is not None:
            ctx.shared["keys"] = keys
        for op in ops:
            text, verdict, ok = ev.appearance(ctx, op)
            got = Shown(text, verdict, error=not ok, stale=ev.stale_note(ctx, op.id))
            got.sketch = _sketch(ctx, op)
            if op.type == "slider":
                got.slider = _slider_of(ctx, op)
            out[str(op.id)] = got
    for opt in project.of_type(Optimisation):
        if str(opt.id) in wanted:
            found = _optimisation_value(project, opt, store)
            if found is not None:
                out[str(opt.id)] = found
    for ms in project.of_type(MeshStudy):
        if str(ms.id) in wanted:
            found = _mesh_study_value(project, ms, store)
            if found is not None:
                out[str(ms.id)] = found
    return out


# A node in one of these states draws its value as stale: what it reads is not current.
STALE_STATES = ("out_of_date", "failed", "not_run", "running")


def fill(s: Schematic, shown: dict) -> None:
    """Put worked-out values (`values`) on their nodes, each with how it is drawn: a
    value from a run no longer current puts its node out of date (ADR 0090 decision 4),
    and a node out of date draws its value as stale, never as current (SCH-3)."""
    from puzzle.project.status import worst

    inside = {m.id: m for g in s.nodes.values() if g.kind == "group" for m in g.inside}
    for nid, got in shown.items():
        node = s.nodes.get(nid) or inside.get(nid)  # folded: kept for the group's sum
        if node is None:
            continue
        node.value, node.verdict, node.sketch = got.value, got.verdict, got.sketch
        if node.kind == "operator":
            node.slider = got.slider
        if got.badge:
            node.badge = got.badge
        if got.stale and node.state not in ("missing", "failed"):
            node.state = "out_of_date"
            node.reason = f"worked out from the run of {got.stale}, not current"
        if not node.value:
            node.value_state = ""
        elif got.error:
            node.value_state = "error"
        elif got.stale or node.state in STALE_STATES:
            node.value_state = "stale"
        else:
            node.value_state = "current"
    for g in s.nodes.values():  # a folded group: its worst state, its checks summed
        if g.kind == "group" and g.inside:
            g.state = worst(m.state for m in g.inside)
            _fold_verdicts(g)


def _fold_verdicts(group: Node) -> None:
    """A folded group of checks says how many fail (SCH-UX-10), drawn as stale when any
    of them is."""
    checks = [m for m in group.inside if m.verdict in ("pass", "fail")]
    if not checks:
        return
    failing = sum(1 for m in checks if m.verdict == "fail")
    group.value = (f"{len(checks)} checks: {failing} fail" if failing
                   else f"{len(checks)} checks: all pass")  # fmt: skip
    group.verdict = "fail" if failing else "pass"
    group.value_state = ("current" if all(m.value_state == "current" for m in checks)
                         else "stale")  # fmt: skip


def value_look(node: Node) -> tuple[str, str]:
    """(how a node's value is coloured, the text drawn): "pass" or "fail", the verdict's
    colour, only for a current value; else "stale" (muted, said to be out of date),
    "not_run" (waiting for a study's run), "error", "working" or "text". The GUI and
    the SVG draw by this, so no out-of-date verdict is drawn as current (ADR 0090
    package 13)."""
    text = node.value
    if node.value_state == "working":
        return "working", text or "working…"
    if node.value_state == "error" and node.state == "not_run":
        return "not_run", text  # waiting for a run, not wrong: muted, as the tree says
    if node.value_state == "error":
        return "error", text
    if node.value_state == "stale" or (node.value and node.state in STALE_STATES):
        if "stale" not in text and "out of date" not in text:
            text = f"{text} (out of date)"
        return "stale", text
    if node.verdict in ("pass", "fail"):
        return node.verdict, text
    return "text", text


def _sketch(ctx, op) -> list:
    """A curve's small plot for its node (ADR 0071: a series is shown, never its rows)."""
    from puzzle.errors import PuzzleError
    from puzzle.operators import evaluate as ev

    try:
        got = ev.outputs(ctx, op)
    except PuzzleError:
        return []
    curve = got.get("curves") if op.type == "plot" else got.get("out")
    return curve.sketch() if getattr(curve, "type", "") == "series" else []


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
    import re

    from puzzle.operators import TYPES, first_type

    kinds = {op.name: first_type(op) for op in project.of_type(Operator)
             if op.id in pre and TYPES[op.type].outputs}  # fmt: skip
    entry = re.compile(r"^\s*(.+?)\s*\[\d+\](?:\.[xyz])?\s*$")  # "P[2].x": one of P's
    for g in project.of_type(Geometry):
        node = out.nodes.get(str(g.id))
        if node is None:
            continue
        for i, step in enumerate(g.history):
            for key, value in step.args.items():
                if not isinstance(value, str):
                    continue
                m = entry.match(value)
                name = m.group(1) if m and m.group(1) in by_name else value
                if name not in by_name:
                    continue
                src, port = by_name[name]
                if src not in out.nodes:
                    continue
                kind = "number" if m else kinds.get(name, "number")  # a points' or profile's
                pkey = f"arg:{i}:{key}"
                node.ports.append(Port(pkey, f"{step.name}: {key}", "in", type=kind))
                out.wires.append(Wire(src, node.id, "data", port, pkey, value, "",
                                      str(g.id), kind))  # fmt: skip
    # ADR 0071: a load's (or support's) value bound to a parameter, a port on its node.
    from puzzle.operators import bound_quantities

    for comp in project.of_type(Component):
        for i, ph in enumerate(comp.physics):
            for j, f in enumerate(ph.features):
                node = out.nodes.get(_feature_id(comp, f"physics.{i}.features.{j}"))
                if node is None:
                    continue
                for path, q in bound_quantities(f.values):
                    if q.parameter not in by_name:
                        continue
                    src, port = by_name[q.parameter]
                    if src not in out.nodes:
                        continue
                    pkey = f"value:{path}"
                    node.ports.append(Port(pkey, f"Value {path}", "in", type="number"))
                    out.wires.append(Wire(src, node.id, "data", port, pkey, q.parameter, "",
                                          str(comp.id), "number"))  # fmt: skip
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
    if isinstance(obj, ResultProbe):  # its own node (ADR 0071), by the port wired
        node = out.nodes.get(str(obj.id))
        if node is not None:
            return node, port or "out", ""
        node = out.nodes.get(f"runs:{obj.source}") or out.nodes.get(str(obj.source))
        return node, "result", f"{CHIP_WORDS['probe'].lower()} {obj.name}"
    return out.nodes.get(str(obj.id)), port, ""


def _folder(path: str) -> str:
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


def _folded_ports(inside, wires, held: set) -> list:
    """A folded group's sockets (ADR 0071: a cluster as one block): every member input
    wired from outside or left open, every member output wired outside or used by
    nothing, named "<member>: <port>" (the port's own name when one member). Inputs and
    outputs wired only within the group are hidden."""
    into = {(w.target, w.target_port): w.source in held for w in wires if w.kind == "data"
            and w.target in held}  # fmt: skip
    outside_in = {(w.target, w.target_port) for w in wires if w.kind == "data"
                  and w.target in held and w.source not in held}  # fmt: skip
    used = {(w.source, w.source_port) for w in wires if w.kind == "data" and w.source in held}
    used_out = {(w.source, w.source_port) for w in wires if w.kind == "data"
                and w.source in held and w.target not in held}  # fmt: skip
    one = len(inside) == 1
    ports = []
    for n in inside:
        for port in n.ports:
            key = (n.id, port.key)
            if port.side == "in":
                show = key in outside_in or key not in into
            else:
                show = key in used_out or key not in used
            if show:
                label = port.label if one else f"{n.label}: {port.label}"
                ports.append(Port(f"{n.id}::{port.key}", label, port.side, port.carries,
                                  port.type, port.help))  # fmt: skip
    if not any(p.side == "in" for p in ports):
        ports.insert(0, Port("in", "In", "in"))  # still something to draw a use wire to
    if not any(p.side == "out" for p in ports):
        ports.append(Port("out", "Out", "out"))
    return ports


def _joints(comp: Component) -> str:
    """What joins a component's parts, counted (SCH-10: contacts and bolts were on no
    node): "3 contacts · 2 bolts · 1 weld"."""
    counts = [(len(comp.contacts), "contact"), (len(comp.connectors), "bolt"),
              (len(comp.welds), "weld")]  # fmt: skip
    return " · ".join(f"{n} {word}{'s' if n != 1 else ''}" for n, word in counts if n)


def _feature_titles(out: Schematic) -> None:
    """Loads on two components with one title ("Fixed on Base/Bottom" twice) say which
    component each is on (SCH-10)."""
    by_title: dict[str, list[Node]] = {}
    for n in out.nodes.values():
        if n.kind == "feature":
            by_title.setdefault(n.label, []).append(n)
    for nodes in by_title.values():
        if len({n.object for n in nodes}) > 1:
            for n in nodes:
                comp = out.nodes.get(n.object)
                if comp is not None:
                    n.subtitle = f"{n.subtitle} · {comp.label}" if n.subtitle else comp.label


def _feature_id(comp: Component, path: str) -> str:
    return f"feature:{comp.id}:{path}"


def _feature_nodes(comp: Component, node: Node, out: Schematic, use) -> set:
    """Each load, support and other feature as a node of its own beside its component
    (ADR 0071 decision 8), wired into it; its coordinate system and amplitude wire into
    it rather than the component. Returns the references only features hold."""
    from uuid import UUID

    held: set = set()
    elsewhere = comp.model_dump(mode="json", exclude={"physics"})
    others = {v for v in _uuids(elsewhere)}
    for i, ph in enumerate(comp.physics):
        for j, f in enumerate(ph.features):
            path = f"physics.{i}.features.{j}"
            feat = Node(_feature_id(comp, path), "feature", MODELS,
                        f"{words(f.type)} on {f.selection}", subtitle=words(ph.type),
                        object=str(comp.id), path=path)  # fmt: skip
            feat.state, feat.reason = node.state, ""
            out.nodes[feat.id] = feat
            out.wires.append(Wire(feat.id, node.id, "use"))
            for ref in (f.axes, f.amplitude):
                if ref is not None:
                    use(str(ref), feat.id)
                    held.add(ref)
    return {UUID(r) if not isinstance(r, UUID) else r for r in held} - {UUID(o) for o in others}


def _uuids(data) -> list[str]:
    """Every id-looking string in an object's JSON form."""
    import re

    found = []
    if isinstance(data, dict):
        for v in data.values():
            found += _uuids(v)
    elif isinstance(data, list):
        for v in data:
            found += _uuids(v)
    elif isinstance(data, str) and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                                r"[0-9a-f]{4}-[0-9a-f]{12}", data):  # fmt: skip
        found.append(data)
    return found


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
        if path.startswith("physics."):  # a load's field goes into the load's own node
            feature = out.nodes.get(_feature_id(comp, path.rsplit(".fields.", 1)[0]))
            if feature is not None:
                feature.ports.append(Port(key, text.split(": ", 1)[-1], "in", u.column))
                label = u.column + (f" ×{u.scale:g}" if u.scale != 1 else "")
                if src.port(u.column) is None:
                    src.ports.append(Port(u.column, words(u.column), "out", u.column))
                out.wires.append(Wire(src.id, feature.id, "data", u.column, key, label,
                                      u.column, str(u.field)))  # fmt: skip
                continue
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
        node.ports = _folded_ports(inside, out.wires, set(held))
        node.inside = inside
        _fold_verdicts(node)  # SCH-UX-10: a folded group of checks says how many fail
        for w in out.wires:
            if w.source in held and w.target in held:
                continue  # inside the fold: dropped below
            if w.source in held:
                w.source, w.source_port = node.id, (f"{w.source}::{w.source_port}"
                                                    if w.kind == "data" else "")  # fmt: skip
            if w.target in held:
                w.target, w.target_port = node.id, (f"{w.target}::{w.target_port}"
                                                    if w.kind == "data" else "")  # fmt: skip
        out.wires = [w for w in out.wires if not (w.source in held or w.target in held)
                     and w.source != w.target]  # fmt: skip
        for m in held:
            del out.nodes[m]
        out.nodes[node.id] = node
