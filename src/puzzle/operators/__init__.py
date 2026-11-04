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
        # Data operators (phase 2): they compute before a solve.
        OperatorType(
            "number", "Number", "Data",
            "A value with its unit, typed in: a parameter by this operator's name.",
            (PortSpec("value", "Value", ("number",), typed="number"),),
            (PortSpec("out", "Value", ("number",)),),
            (),
        ),
        OperatorType(
            "slider", "Slider", "Data",
            "A value dragged between a low and a high, in steps: a parameter by this "
            "operator's name. Dragging previews the geometry it drives; it never solves.",
            (PortSpec("value", "Value", ("number",), required=False, typed="number"),),
            (PortSpec("out", "Value", ("number",)),),
            ("low", "high", "increment"),
        ),
        OperatorType(
            "series", "Series", "Data",
            "A list from a start, a step and a count: 2, 4, 6, 8.",
            (PortSpec("start", "Start", ("number",), typed="number"),
             PortSpec("step", "Step", ("number",), typed="number"),
             PortSpec("count", "Count", ("number",), typed="number")),
            (PortSpec("out", "List", ("list",)),),
            (),
        ),
        OperatorType(
            "range", "Range", "Data",
            "A list of evenly spaced values from a start to an end, both included.",
            (PortSpec("start", "Start", ("number",), typed="number"),
             PortSpec("end", "End", ("number",), typed="number"),
             PortSpec("count", "Count", ("number",), typed="number")),
            (PortSpec("out", "List", ("list",)),),
            (),
        ),
        OperatorType(
            "expression", "Expression", "Data",
            "A formula over x, y, z and the project's parameters, with units checked "
            "(2 * x + 5 mm). A list in gives a list out, item by item.",
            (PortSpec("x", "x", ("number", "list"), required=False, typed="number"),
             PortSpec("y", "y", ("number", "list"), required=False, typed="number"),
             PortSpec("z", "z", ("number", "list"), required=False, typed="number")),
            (PortSpec("out", "Value", ("number",)),),
            ("expression",),
        ),
        OperatorType(
            "table", "Table", "Data",
            "Named columns with units, typed in or read from a CSV (the table keeps the "
            "rows, so they are hashed and versioned with the project).",
            (),
            (PortSpec("out", "Table", ("table",)),),
            ("text",),
        ),
        OperatorType(
            "list", "List", "Data",
            "One list made from another: an item, a slice, sorted, reversed, its length, a "
            "table's column, the sum, the smallest or largest.",
            (PortSpec("list", "List", ("list", "table")),
             PortSpec("index", "Index", ("number",), required=False, typed="number"),
             PortSpec("end", "End", ("number",), required=False, typed="number")),
            (PortSpec("out", "List", ("list",)), PortSpec("first", "First", ("number",))),
            ("operation", "column"),
        ),
        OperatorType(
            "random", "Random", "Data",
            "Seeded samples about a mean (normal, log-normal or uniform): a load's or a "
            "modulus's scatter; a sweep over them is Monte Carlo with real solves.",
            (PortSpec("mean", "Mean", ("number",), typed="number"),
             PortSpec("spread", "Spread", ("number",), typed="number"),
             PortSpec("count", "Count", ("number",), typed="number")),
            (PortSpec("out", "List", ("list",)),),
            ("distribution", "seed"),
        ),
        # Tables (ADR 0064): polars frames with a quantity kind per column.
        OperatorType(
            "result_table", "Result table", "Tables",
            "A run's results as a table: node (or element), x, y, z, part, step and the "
            "results chosen, one row per item per step.",
            (PortSpec("result", "Result", ("result", "field")),),
            (PortSpec("out", "Table", ("table",)),),
            ("quantities", "step", "steps", "selection", "layout"),
        ),
        OperatorType(
            "points_table", "Points table", "Tables",
            "A sweep's or an optimisation's points as a table: the variables, outputs, "
            "feasible and the run.",
            (PortSpec("source", "Points", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            (),
        ),
        OperatorType(
            "file", "File", "Tables",
            "A table from a file in the project: CSV (headers 'name [unit]'), Parquet or "
            "Excel; a pattern (tests/*.csv) stacks several, with a file column.",
            (),
            (PortSpec("out", "Table", ("table",)),),
            ("file",),
        ),
        OperatorType(
            "columns", "Columns", "Tables",
            "Pick, order, rename or drop columns: x, y, von_mises as vm.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("pick", "drop"),
        ),
        OperatorType(
            "filter_rows", "Filter rows", "Tables",
            "The rows where a condition holds: von_mises > 200 MPa and part == "
            "\"Bracket\" (units checked), or a polars expression.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("condition", "syntax"),
        ),
        OperatorType(
            "sort", "Sort", "Tables",
            "Rows in order of one or more columns, ascending or descending.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("by", "descending"),
        ),
        OperatorType(
            "top_n", "Top N", "Tables",
            "The N rows with the largest (or smallest) value of a column, e.g. the 20 "
            "highest von Mises nodes; per group with By.",
            (PortSpec("table", "Table", ("table",)),
             PortSpec("count", "N", ("number",), required=False, typed="number")),
            (PortSpec("out", "Table", ("table",)),),
            ("column", "largest", "by"),
        ),
        OperatorType(
            "new_column", "New column", "Tables",
            "A column worked out from the others: margin = 250 MPa / von_mises - 1, units "
            "checked; or a polars expression.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("new_name", "expression", "syntax", "unit"),
        ),
        OperatorType(
            "convert_units", "Convert units", "Tables",
            "A column shown and exported in another unit of its kind: stress in MPa, "
            "reactions in kN.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("column", "unit"),
        ),
        OperatorType(
            "round", "Round", "Tables",
            "Numbers rounded in their shown units: decimal places or significant figures.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("pick", "decimals", "significant"),
        ),
        OperatorType(
            "group", "Group and aggregate", "Tables",
            "Rows grouped by columns (part, step, point), each group reduced: "
            "max(von_mises), count(), quantile(von_mises, 0.95).",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("by", "aggregations"),
        ),
        OperatorType(
            "to_number", "To number", "Tables",
            "One value of a column (max, min, mean, sum, median, first, last, count): "
            "for checks, panels and optimisations.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Value", ("number",)),),
            ("column", "reduction", "by"),
        ),
        OperatorType(
            "pairs", "Pairs", "Data",
            "Two lists into a table: item by item, or every combination.",
            (PortSpec("a", "A", ("list",)), PortSpec("b", "B", ("list",))),
            (PortSpec("out", "Table", ("table",)),),
            ("combination",),
        ),
    )
}  # fmt: skip
# Types that compute from data, before a solve: pre-solve when nothing they read is a
# result (ADR 0062 item 3), and then parameters by their name.
DATA = frozenset({"number", "slider", "series", "range", "expression", "table", "list",
                  "pairs", "random", "file", "columns", "filter_rows", "sort", "top_n",
                  "new_column", "convert_units", "round", "group", "to_number"})  # fmt: skip
# Table operators (ADR 0064): data operators that read tables, so pre-solve when their
# table is (a file, a typed table) and post-solve when it comes from a run.


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

        points = [("points", "Sweep points", "table")] if obj.sweeps() else []
        return (
            [("result", "Result", "result")]
            + [(f, words(f), "result") for f in outputs(project, obj)]
            + points
        )
    from puzzle.model.schema import Optimisation

    if isinstance(obj, Optimisation):
        return [("points", "Points", "table")]
    return []


def output_type(project, source: UUID, port: str) -> str | None:
    """The type of `source`'s output `port`, None if it has no such output. A study's
    field ports are results limited to that field."""
    from puzzle.model.schema import Study

    obj = project.objects.get(source)
    if obj is None:
        return None
    if isinstance(obj, Study):  # any field: a run may hold more than the catalog lists
        return "table" if port == "points" else "result"
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
            if op.type in DATA and not is_pre(project, op):
                uses = [u for o in (op, *(project.objects[d] for d in downstream(project, op.id)))
                        for u in model_uses(project, o)]  # fmt: skip
                if uses:
                    raise PuzzleError(
                        f"'{op.name}' feeds the model ({uses[0]}), so it can't read results: "
                        "a loop from results back into the model is an optimisation "
                        "(ADR 0063)", code="inconsistent", path=op.name)  # fmt: skip
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
    elif op.type == "slider":

        def q(v):
            return f"{v.value:g} {v.unit}".removesuffix(" dimensionless") if v else "?"

        bits.append(f"from {q(op.low)} to {q(op.high)}"
                    + (f" in steps of {q(op.increment)}" if op.increment else ""))  # fmt: skip
    elif op.type == "expression":
        bits.append(repr(op.expression))
    elif op.type == "table":
        rows = [r for r in op.text.strip().splitlines() if r.strip()]
        bits.append(f"{max(len(rows) - 1, 0)} rows: {rows[0] if rows else 'empty'}")
    elif op.type == "list":
        bits.append(op.operation + (f" of column {op.column}" if op.column else ""))
    elif op.type == "pairs":
        bits.append(op.combination)
    elif op.type == "random":
        bits.append(f"{op.distribution}, seed {op.seed}")
    elif op.type == "result_table":
        what = ", ".join(op.quantities) or "its first result"
        bits.append(f"{what}, {'every step' if op.steps == 'all' else 'one step'}")
    elif op.type == "file":
        bits.append(op.file or "no file")
    elif op.type == "columns":
        bits.append(", ".join(op.pick) + (f" without {', '.join(op.drop)}" if op.drop else ""))
    elif op.type == "filter_rows":
        bits.append(repr(op.condition) + (" (polars)" if op.syntax == "polars" else ""))
    elif op.type == "sort":
        bits.append(", ".join(op.by) + (" descending" if op.descending else ""))
    elif op.type == "top_n":
        bits.append(f"{'largest' if op.largest else 'smallest'} {op.column}"
                    + (f" per {', '.join(op.by)}" if op.by else ""))  # fmt: skip
    elif op.type == "new_column":
        bits.append(f"{op.new_name} = {op.expression}")
    elif op.type == "convert_units":
        bits.append(f"{op.column} in {op.unit}")
    elif op.type == "round":
        bits.append(f"{op.decimals} {'figures' if op.significant else 'places'}")
    elif op.type == "group":
        bits.append(", ".join(op.aggregations) + (f" per {', '.join(op.by)}" if op.by else ""))
    elif op.type == "to_number":
        bits.append(f"{op.reduction} of {op.column}")
    if op.type in DATA:
        bits.append("(before solving)" if is_pre(project, op) else "(after solving)")
        uses = model_uses(project, op)
        if uses:
            bits.append("drives " + ", ".join(uses))
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


def is_pre(project, op, seen=()) -> bool:
    """A data operator that reads no results, directly or through others: it computes
    before a solve, and its value goes into the studies' content hashes."""
    from puzzle.model.schema import Operator, ParameterSet

    if op.type not in DATA or op.id in seen:
        return op.type in DATA
    for inp in op.inputs.values():
        if inp.source is None:
            continue
        src = project.objects.get(inp.source)
        if isinstance(src, ParameterSet):
            continue
        if not isinstance(src, Operator) or not is_pre(project, src, (*seen, op.id)):
            return False
    return True


def pre_solve(project) -> list:
    """The operators that compute before a solve."""
    from puzzle.model.schema import Operator

    return [o for o in project.of_type(Operator) if is_pre(project, o)]


def parameter_values(project, overrides: dict | None = None) -> dict[str, float]:
    """Every parameter by name, SI: the parameter sets' values, then each pre-solve
    number operator's (Number, Slider, a number Expression) by the operator's name.
    `overrides` (operator id -> SI value) are a slider being dragged (the preview's)."""
    from puzzle.model.schema import ParameterSet

    out = {k: q.to_si() for ps in project.of_type(ParameterSet) for k, q in ps.values.items()}
    numbers = [o for o in pre_solve(project) if TYPES[o.type].outputs
               and TYPES[o.type].outputs[0].type == "number"]  # fmt: skip
    if not numbers:
        return out
    from puzzle.operators import evaluate as ev

    ctx = ev.Context(project, None, overrides=dict(overrides or {}))
    for op in numbers:
        try:
            got = ev.value(ctx, op)
        except PuzzleError:
            continue  # an operator in error: its problem says why
        if isinstance(got, ev.Number):
            out.setdefault(op.name, got.value)
    return out


def resolved(project):
    """The project with every sweep parameter that takes its values from an operator
    given them (as SI quantities), for counting and running the points. The project
    passed in is never changed."""
    from puzzle.model.schema import Quantity, Study

    wired = [(s, i, j) for s in project.of_type(Study) for i, step in enumerate(s.sweeps())
             for j, p in enumerate(step.parameters) if p.source is not None]  # fmt: skip
    if not wired:
        return project
    import copy

    from puzzle.model.units import DIMENSIONS
    from puzzle.operators import evaluate as ev

    out = copy.deepcopy(project)
    ctx = ev.Context(out, None)
    for study, i, j in wired:
        param = out.objects[study.id].sweeps()[i].parameters[j]
        try:
            got = ev.source(ctx, param.source, param.port)
        except PuzzleError:
            continue  # no points: the problem says why
        values = ev.as_list(got)
        unit = DIMENSIONS[values.kind][1] if values.kind in DIMENSIONS else "dimensionless"
        param.values = [Quantity(value=float(v), unit=unit) for v in values.values]
    return out


def model_uses(project, op) -> list[str]:
    """Where the model reads an operator, in words: history arguments naming it, sweeps
    over it or taking their values from it."""
    from puzzle.model.schema import Geometry, Study

    found = []
    for g in project.of_type(Geometry):
        for step in g.history:
            for key, value in step.args.items():
                if value == op.name:
                    found.append(f"{g.name}: {step.name} {key}")
    for s in project.of_type(Study):
        for step in s.sweeps():
            for p in step.parameters:
                if p.source == op.id or (p.source is None and p.name == op.name):
                    found.append(f"study {s.name}'s sweep")
    return found


def with_values(project, overrides: dict):
    """A copy of the project with Numbers' and Sliders' values set (operator id -> SI),
    for previewing a slider while it is dragged; the project passed in is unchanged."""
    import copy

    from puzzle.model.schema import OperatorInput, Quantity
    from puzzle.model.units import DIMENSIONS

    out = copy.deepcopy(project)
    for oid, value in overrides.items():
        op = out.objects.get(oid)
        if op is None or op.type not in ("number", "slider"):
            continue
        from puzzle.operators import evaluate as ev

        try:
            kind = ev.value(ev.Context(project, None), project.objects[oid]).kind
        except PuzzleError:
            kind = None
        unit = DIMENSIONS[kind][1] if kind in DIMENSIONS else "dimensionless"
        op.inputs["value"] = OperatorInput(value=Quantity(value=float(value), unit=unit))
    return out
