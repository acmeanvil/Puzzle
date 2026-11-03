"""Operators (ADR 0062): project objects that compute, wired together in the schematic.

An operator (`model.schema.Operator`) has typed input ports, each a wire from another
object's output or a value typed in, and typed output ports. This module says what
each type takes and gives (`TYPES`), which outputs other objects offer (`output_type`),
which wires fit (`accepts`), and refuses wiring that can't work (`refuse`): an unknown
port, a wrong type, a loop. `puzzle.operators.evaluate` computes them.

Phase 1 is the post-solve operators: they read results, are evaluated on demand and
never stored, as derived results are (ADR 0025), so nothing upstream of a study reads
one and they never stale a run.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from uuid import UUID

from puzzle.errors import Problem, PuzzleError

# Port types (ADR 0062 item 2): what each holds, and how its socket is drawn.
PORT_TYPES = {
    "number": "a quantity with its unit (SI inside)",
    "list": "numbers, or texts",
    "table": "named columns with units",
    "points": "positions, in metres",
    "geometry": "a geometry object",
    "field": "a result on the mesh, per node or element",
    "result": "a run, or a derived result",
    "verdict": "pass or fail, with the margin",
}
TYPE_WORDS = {
    "number": "Number",
    "list": "List",
    "table": "Table",
    "points": "Points",
    "geometry": "Geometry",
    "field": "Field",
    "result": "Result",
    "verdict": "Verdict",
}
# Socket shapes, as Grasshopper and Blender tell types apart at a glance.
SHAPES = {"number": "diamond", "list": "diamond", "table": "square", "points": "square",
          "geometry": "square", "field": "circle", "result": "circle",
          "verdict": "triangle"}  # fmt: skip
# Their colours on a light ground (the SVG; the GUI draws the same).
TYPE_COLOURS = {"number": "#2b8a3e", "list": "#5c940d", "table": "#e67700",
                "points": "#c2255c", "geometry": "#495057", "field": "#7048e8",
                "result": "#1c7ed6", "verdict": "#d6336c"}  # fmt: skip
ANY = tuple(PORT_TYPES)


@dataclass(frozen=True)
class PortSpec:
    key: str
    label: str
    types: tuple[str, ...]  # what it takes (inputs) or gives (outputs, the first)
    required: bool = True
    typed: str = ""  # what a value typed in is ("number", "text"), "" if none may be

    @property
    def type(self) -> str:
        return self.types[0]


@dataclass(frozen=True)
class OperatorType:
    key: str
    label: str
    group: str  # the palette's heading
    help: str
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]
    settings: tuple[str, ...]  # the Operator fields this type reads

    def input(self, key: str) -> PortSpec | None:
        return next((p for p in self.inputs if p.key == key), None)

    def output(self, key: str) -> PortSpec | None:
        return next((p for p in self.outputs if p.key == key), None)


TYPES: dict[str, OperatorType] = {
    t.key: t
    for t in (
        OperatorType(
            "reduce", "Reduce", "Results",
            "A result over the model or a selection as one number: its maximum, minimum, "
            "mean or integral (the result tools as a node).",
            (PortSpec("result", "Result", ("result", "field")),),
            (PortSpec("out", "Value", ("number",)),),
            ("quantity", "selection", "reduction", "step"),
        ),
        OperatorType(
            "filter", "Filter", "Results",
            "A result kept on a selection, and above or below a limit; the rest left out.",
            (PortSpec("result", "Result", ("result", "field")),
             PortSpec("limit", "Limit", ("number",), required=False, typed="number")),
            (PortSpec("out", "Field", ("field",)),),
            ("quantity", "selection", "keep", "step"),
        ),
        OperatorType(
            "check", "Check", "Results",
            "A number, or a field's extreme, against an allowable: pass or fail, with the "
            "margin (limit / value − 1 for at most).",
            (PortSpec("value", "Value", ("number", "field")),
             PortSpec("limit", "Limit", ("number",), typed="number")),
            (PortSpec("out", "Verdict", ("verdict",)),
             PortSpec("margin", "Margin", ("number",))),
            ("compare",),
        ),
        OperatorType(
            "panel", "Panel", "Display",
            "Shows any output's value on the canvas.",
            (PortSpec("value", "Value", ANY),),
            (),
            (),
        ),
        OperatorType(
            "plot", "Plot", "Results",
            "A number against the steps (time, frequency, increment) or a sweep's points, "
            "as a table, drawn in the Plots panel.",
            (PortSpec("value", "Value", ("number",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("against",),
        ),
        OperatorType(
            "export", "Export", "Display",
            "Any output to a file in the project: CSV, or a result in an export format.",
            (PortSpec("value", "Value", ANY),),
            (),
            ("file", "format"),
        ),
    )
}  # fmt: skip


def accepts(spec: PortSpec, given: str) -> bool:
    """Whether an input port takes an output of type `given` (a number where a list is
    wanted, as a one-item list)."""
    return given in spec.types or (given == "number" and "list" in spec.types)


def outputs_of(project, obj) -> list[tuple[str, str, str]]:
    """(port, label, type) of every output an object offers to an operator."""
    from puzzle.model.schema import (
        Operator,
        ParameterSet,
        ResultDefinition,
        ResultProbe,
        Study,
    )

    if isinstance(obj, Operator):
        return [(p.key, p.label, p.type) for p in TYPES[obj.type].outputs]
    if isinstance(obj, ParameterSet):
        return [(k, k, "number") for k in obj.values]
    if isinstance(obj, ResultProbe):
        return [("out", "Value", "number")]
    if isinstance(obj, ResultDefinition):
        return [("result", "Result", "result")]
    if isinstance(obj, Study):
        from puzzle.post.quantities import words
        from puzzle.schematic import outputs

        return [("result", "Result", "result")] + [
            (f, words(f), "result") for f in outputs(project, obj)
        ]
    return []


def output_type(project, source: UUID, port: str) -> str | None:
    """The type of `source`'s output `port`, None if it has no such output. A study's
    field ports are results limited to that field."""
    from puzzle.model.schema import Study

    obj = project.objects.get(source)
    if obj is None:
        return None
    if isinstance(obj, Study):  # any field: a run may hold more than the catalog lists
        return "result"
    found = next((t for k, _, t in outputs_of(project, obj) if k == port), None)
    return found


def sources(project, op, key: str) -> list[tuple[str, str, str]]:
    """What may be wired into `op`'s input `key`: (label, source id, port), for the
    Properties panel's dropdowns and the agent."""
    from puzzle.model.schema import Operator

    spec = TYPES[op.type].input(key)
    if spec is None:
        return []
    below = downstream(project, op.id)
    out = []
    for obj in project.objects.values():
        if obj.id == op.id or obj.id in below:
            continue
        for port, label, kind in outputs_of(project, obj):
            if accepts(spec, kind):
                what = obj.name if isinstance(obj, Operator) and port == "out" else (
                    f"{obj.name}: {label}")  # fmt: skip
                out.append((what, str(obj.id), port))
    return out


def downstream(project, oid: UUID) -> set[UUID]:
    """Operators reading `oid`, directly or through others."""
    from puzzle.model.schema import Operator

    ops = project.of_type(Operator)
    seen: set[UUID] = set()
    frontier = [oid]
    while frontier:
        here = frontier.pop()
        for o in ops:
            if here in o.dependencies() and o.id not in seen:
                seen.add(o.id)
                frontier.append(o.id)
    return seen


def refuse(project, op) -> None:
    """Refuse wiring that can't work: a port the source lacks, a type the input doesn't
    take, a value typed where none may be. Empty inputs are allowed (a node is wired up
    a step at a time) and reported as problems instead."""
    spec_of = TYPES[op.type]
    for key, inp in op.inputs.items():
        spec = spec_of.input(key)
        if inp.source is not None:
            src = project.objects.get(inp.source)
            if src is None:
                continue  # a missing reference: Project.problems says so
            given = output_type(project, inp.source, inp.port)
            if given is None:
                ports = ", ".join(k for k, _, _ in outputs_of(project, src)) or "none"
                raise PuzzleError(
                    f"'{src.name}' has no output '{inp.port}' (its outputs: {ports})",
                    code="invalid", path=op.name)  # fmt: skip
            if not accepts(spec, given):
                raise PuzzleError(
                    f"{spec_of.label} '{op.name}': its {spec.label.lower()} input takes "
                    f"{_types(spec.types)}, not a {TYPE_WORDS[given].lower()}",
                    code="invalid", path=op.name)  # fmt: skip
        elif inp.value is not None and not spec.typed:
            raise PuzzleError(
                f"{spec_of.label} '{op.name}': its {spec.label.lower()} input must be wired",
                code="invalid", path=op.name)  # fmt: skip


def _types(types: tuple[str, ...]) -> str:
    if types == ANY:
        return "anything"
    words = [TYPE_WORDS[t].lower() for t in types]
    return " or ".join(f"a {w}" for w in words)


def problems(project) -> list[Problem]:
    """Operators with a required input left empty, or wired wrongly (a source edited
    since): missing input for the tree and the schematic."""
    from puzzle.model.schema import Operator

    out = []
    for op in project.of_type(Operator):
        spec_of = TYPES[op.type]
        for spec in spec_of.inputs:
            inp = op.inputs.get(spec.key)
            empty = inp is None or (inp.source is None and inp.value is None)
            if empty and spec.required:
                out.append(Problem(
                    "operator_input", op.name,
                    f"{spec_of.label.lower()} '{op.name}': its {spec.label.lower()} input "
                    "is not wired", {"input": spec.key}))  # fmt: skip
        try:
            refuse(project, op)
        except PuzzleError as exc:
            out.append(Problem("operator_input", op.name, exc.message, {}))
    return out


def refuse_cycles(project) -> None:
    """Refuse a change that makes a loop of references (operators reading each other,
    transfers feeding back): the graph must have none (ADR 0062 item 1)."""
    from graphlib import CycleError, TopologicalSorter

    graph = {}
    for oid, obj in project.objects.items():
        deps = [d for d in obj.dependencies() if d in project.objects]
        if obj.kind == "study":
            deps += [t.id for t in project.transfers_into(oid)]
        graph[oid] = deps
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        loop = exc.args[1]
        names = [project.objects[i].name for i in loop if i in project.objects]
        raise PuzzleError(
            "that would make a loop: " + " → ".join(names), code="inconsistent",
            details={"loop": names}) from None  # fmt: skip


def default_name(project, type_: str) -> str:
    """ "Reduce 1", "Reduce 2", ...: the first free one."""
    taken = {o.name for o in project.objects.values()}
    base = TYPES[type_].label
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def describe(project, op) -> str:
    """An operator in words, for the outline: its type, settings and what is wired in
    ("reduce max of stress:von_mises on Bar/Root; result <- Tip load")."""
    t = TYPES[op.type]
    bits = [t.label.lower()]
    if op.type in ("reduce", "filter"):
        what = op.quantity or "the first listed result"
        bits.append(f"{op.reduction} of {what}" if op.type == "reduce" else what)
        if op.selection:
            bits.append(f"on {op.selection}")
        if op.type == "filter" and op.keep != "all":
            bits.append(f"kept {op.keep} its limit")
    elif op.type == "check":
        bits.append("at most" if op.compare == "at_most" else "at least")
    elif op.type == "plot":
        bits.append(f"against the {'steps' if op.against == 'step' else 'sweep points'}")
    elif op.type == "export":
        from puzzle.operators.evaluate import export_path

        bits.append(f"to {export_path(op)} ({op.format})")
    wired = []
    for spec in t.inputs:
        inp = op.inputs.get(spec.key)
        if inp is None or (inp.source is None and inp.value is None):
            wired.append(f"{spec.key} not wired")
        elif inp.source is not None:
            src = project.objects.get(inp.source)
            name = src.name if src is not None else "?"
            port = "" if inp.port in ("out", "result") else f".{inp.port}"
            wired.append(f"{spec.key} <- {name}{port}")
        else:
            from puzzle.model.schema import Quantity

            v = inp.value
            text = f"{v.value:g} {v.unit}" if isinstance(v, Quantity) else str(v)
            wired.append(f"{spec.key} = {text.removesuffix(' dimensionless')}")
    return " ".join(bits) + ("; " + ", ".join(wired) if wired else "")


def inputs_from(project, given: dict) -> dict:
    """Inputs as an agent or a script writes them, as stored: a source by name or id
    ({"source": "Tip load", "port": "result"}), a value as text ("200 MPa") or a
    number."""
    from puzzle.commands.session import resolve

    out = {}
    for key, inp in (given or {}).items():
        if not isinstance(inp, dict):  # a bare value
            inp = {"value": inp}
        inp = dict(inp)
        if inp.get("source"):
            inp["source"] = str(resolve(project, str(inp["source"])).id)
        value = inp.get("value")
        if isinstance(value, str):
            number, _, unit = value.strip().partition(" ")
            with contextlib.suppress(ValueError):  # else a text
                inp["value"] = {"value": float(number), "unit": unit.strip() or "dimensionless"}
        out[key] = inp
    return out
