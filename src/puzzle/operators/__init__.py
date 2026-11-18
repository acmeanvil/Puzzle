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
import re
from dataclasses import dataclass, replace
from uuid import UUID

from puzzle.errors import Problem, PuzzleError

# Port types (ADR 0062 item 2): what each holds, and how its socket is drawn.
PORT_TYPES = {
    "number": "a quantity with its unit (SI inside)",
    "list": "numbers, or texts",
    "table": "named columns with units",
    "points": "positions, in metres (a table of x, y, z too)",
    "profile": "a closed outline: a polyline, circle, rectangle or polygon",
    "geometry": "a geometry object",
    "field": "a result on the mesh, per node or element",
    "result": "a run, or a derived result",
    "verdict": "pass or fail, with the margin",
    "text": "words: a label or a line for a report",
    "series": "a curve (or one per step): x and y with their units, as plotted",
}
TYPE_WORDS = {
    "number": "Number",
    "list": "List",
    "table": "Table",
    "points": "Points",
    "profile": "Profile",
    "geometry": "Geometry",
    "field": "Field",
    "result": "Result",
    "verdict": "Verdict",
    "text": "Text",
    "series": "Curve",
}
# Socket shapes, as Grasshopper and Blender tell types apart at a glance.
SHAPES = {"number": "diamond", "list": "diamond", "table": "square", "points": "square",
          "profile": "square",
          "geometry": "square", "field": "circle", "result": "circle",
          "verdict": "triangle", "text": "diamond", "series": "square"}  # fmt: skip
# Their colours on a light ground (the SVG; the GUI draws the same).
TYPE_COLOURS = {"number": "#2b8a3e", "list": "#5c940d", "table": "#e67700",
                "points": "#c2255c", "profile": "#a61e4d", "geometry": "#495057",
                "field": "#7048e8",
                "result": "#1c7ed6", "verdict": "#d6336c", "text": "#868e96",
                "series": "#0c8599"}  # fmt: skip
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
    aliases: tuple[str, ...] = ()  # other tools' names that find it (ADR 0064 item 6)
    more: PortSpec | None = None
    """An input that repeats (`curve5`, `curve6`...): one more empty socket appears once
    the last is wired, so the operator takes any number of them (Plot's curves)."""

    def input(self, key: str) -> PortSpec | None:
        found = next((p for p in self.inputs if p.key == key), None)
        if found is None and self.more is not None:
            n = _more_index(self.more, key)
            if n is not None:
                return _more_port(self.more, n)
        return found

    def ports_for(self, op) -> tuple[PortSpec, ...]:
        """The inputs one operator shows: its type's, and for a repeating input those
        wired so far plus one empty."""
        if self.more is None:
            return self.inputs
        start = _more_start(self)
        wired = [n for key in getattr(op, "inputs", {}) or {}
                 if (n := _more_index(self.more, key)) is not None]  # fmt: skip
        last = max(wired, default=start - 1)
        return self.inputs + tuple(_more_port(self.more, n) for n in range(start, last + 2))

    def output(self, key: str) -> PortSpec | None:
        return next((p for p in self.outputs if p.key == key), None)


def _more_start(t: OperatorType) -> int:
    """The first repeating input's number: after the fixed ones (Plot: value, b, c, d)."""
    return len(t.inputs) + 1


def _more_index(more: PortSpec, key: str) -> int | None:
    m = re.fullmatch(re.escape(more.key) + r"(\d+)", key)
    return int(m.group(1)) if m else None


def _more_port(more: PortSpec, n: int) -> PortSpec:
    return PortSpec(f"{more.key}{n}", more.label.format(n=n), more.types, required=False,
                    typed=more.typed)  # fmt: skip


# ADR 0071 decision 3: the series operators (`operators.series`). A and B take a curve or
# a number; with two numbers the arithmetic gives a number (`first_type` with a project).
_CN = ("series", "number")
# ADR 0071 step 5: the arithmetic and Normalise take result fields too (a Filter's, or
# another field operator's), giving a field drawn in the view.
_CNF = ("series", "number", "field")


def _ab(a: str = "A", b: str = "B", b_typed: bool = True) -> tuple[PortSpec, ...]:
    return (PortSpec("a", a, _CNF, typed="number"),
            PortSpec("b", b, _CNF, typed="number" if b_typed else ""))  # fmt: skip


_CURVE_IN = (PortSpec("series", "Curve", ("series",)),)
_CURVE_OUT = (PortSpec("out", "Curve", ("series",)),)
SERIES_TYPES = (
    OperatorType(
        "divide_by",
        "Divide (A ÷ B)",
        "Curves",
        "A curve (or number) divided by another or by a number, point by point; units "
        "follow (stress ÷ stress is a pure number).",
        _ab(),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "subtract",
        "Subtract (A − B)",
        "Curves",
        "A curve (or number) less another or a number, point by point; both in the same "
        "kind of unit.",
        _ab(),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "multiply",
        "Multiply (A × B)",
        "Curves",
        "A curve (or number) times another or a number, point by point; units multiply "
        "(force × length is a moment).",
        _ab(),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "add",
        "Add (A + B)",
        "Curves",
        "A curve (or number) plus another or a number, point by point; both in the same "
        "kind of unit.",
        _ab(),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "utilisation",
        "Utilisation",
        "Checks",
        "How much of an allowable a value uses: value ÷ allowable, 1 at the limit (a "
        "stress along a path against the material's yield strength).",
        _ab("Value", "Allowable"),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "safety_factor",
        "Safety factor",
        "Checks",
        "How many times a value fits in its allowable: allowable ÷ value, above 1 is safe.",
        _ab("Value", "Allowable"),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "margin",
        "Margin of safety",
        "Checks",
        "How far inside its allowable a value is: allowable ÷ value − 1, above 0 is safe.",
        _ab("Value", "Allowable"),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "normalise",
        "Normalise",
        "Curves",
        "A curve divided by a number, by another curve, or (nothing wired) by its own "
        "largest size: a shape between −1 and 1 to compare with others.",
        (
            PortSpec("series", "Curve or field", ("series", "field")),
            PortSpec("by", "By", _CNF, required=False, typed="number"),
        ),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "curve_formula",
        "Curve formula",
        "Curves",
        "A new y worked out at every point from x, y and wired numbers a, b, c, units "
        "checked: y / 250 MPa, y * x, abs(y).",
        (*_CURVE_IN, *(PortSpec(k, k, ("number",), required=False, typed="number") for k in "abc")),
        _CURVE_OUT,
        ("expression",),
    ),
    OperatorType(
        "field_formula",
        "Field formula",
        "Results",
        "A new result worked out at every node from one field f (and another, g), wired "
        "numbers a, b, c and parameters, units checked: f / 250 MPa, f - g, abs(f), drawn in "
        "the view.",
        (
            PortSpec("f", "Field f", ("field",)),
            PortSpec("g", "Field g", ("field",), required=False),
            *(PortSpec(k, k, ("number",), required=False, typed="number") for k in "abc"),
        ),
        (PortSpec("out", "Field", ("field",)),),
        ("expression", "quantity", "step"),  # which result and step a wired study gives
    ),
    OperatorType(
        "slice",
        "Slice",
        "Results",
        "A field on a plane (a construction plane) as a table of the cut's points and "
        "values, as ParaView's Slice: what the stress is across a wall.",
        (PortSpec("field", "Field", ("field",)),),
        (PortSpec("out", "Points", ("table",)),),
        ("target", "quantity", "step"),
    ),
    OperatorType(
        "clip",
        "Clip",
        "Results",
        "A field kept on one side of a plane (above: the side its normal faces), as "
        "ParaView's Clip: the rest left out, still on the mesh.",
        (PortSpec("field", "Field", ("field",)),),
        (PortSpec("out", "Field", ("field",)),),
        ("target", "keep", "quantity", "step"),
    ),
    OperatorType(
        "stream",
        "Stream tracer",
        "Results",
        "Streamlines of a vector result (velocity) from seeds along a construction path, "
        "as a table of their points with the speed, as ParaView's Stream Tracer.",
        (
            PortSpec("result", "Result", ("result",)),
            PortSpec("seeds", "Seeds", ("number",), required=False, typed="number"),
        ),
        (PortSpec("out", "Points", ("table",)),),
        ("quantity", "target", "step"),
    ),
    OperatorType(
        "glyph",
        "Glyph",
        "Results",
        "A vector result's arrows (at most Arrows of the nodes, evenly spread) as a table "
        "of points, components and sizes, as ParaView's Glyph.",
        (
            PortSpec("result", "Result", ("result",)),
            PortSpec("seeds", "Arrows", ("number",), required=False, typed="number"),
        ),
        (PortSpec("out", "Points", ("table",)),),
        ("quantity", "step"),
    ),
    OperatorType(
        "contour",
        "Contour",
        "Results",
        "Where a field takes a value (an iso-surface), as a table of points, as ParaView's "
        "Contour: the 250 MPa surface, the 100 °C front.",
        (
            PortSpec("field", "Field", ("field",)),
            PortSpec("level", "Value", ("number",), typed="number"),
        ),
        (PortSpec("out", "Points", ("table",)),),
        ("quantity", "step"),
    ),
    OperatorType(
        "curve_from_table",
        "Curve from table",
        "Curves",
        "A table's column against another as a curve (the first two number columns if "
        "none is chosen): a model value against temperature, a test's readings.",
        (PortSpec("table", "Table", ("table",)),),
        _CURVE_OUT,
        ("by", "column"),
    ),
    OperatorType(
        "over_steps",
        "Over the steps",
        "Curves",
        "One value's history: a number (a result summary, a result tool) worked out at "
        "every step of the run it reads (time, frequency, increment), or at every sweep "
        "point, as a curve.",
        (PortSpec("value", "Value", ("number",)),),
        _CURVE_OUT,
        ("against",),
    ),
    OperatorType(
        "at_step",
        "At step",
        "Curves",
        "One step's curve from a curve per step (a path in a transient run): the step "
        "picked by its number, the last by default.",
        _CURVE_IN,
        _CURVE_OUT,
        ("step",),
    ),
    OperatorType(
        "compare_steps",
        "Compare with step",
        "Curves",
        "Every step's curve against a reference step's, as a difference or a ratio; for "
        "a history, every step's value against the reference step's.",
        _CURVE_IN,
        _CURVE_OUT,
        ("step", "comparison"),
    ),
    OperatorType(
        "envelope",
        "Envelope",
        "Curves",
        "The largest (or smallest) value at each x over every curve wired in: the worst "
        "over all steps, or over several paths.",
        (
            *_CURVE_IN,
            PortSpec("b", "Curve 2", ("series",), required=False),
            PortSpec("c", "Curve 3", ("series",), required=False),
        ),
        _CURVE_OUT,
        ("reduction",),
    ),
    OperatorType(
        "match_x",
        "Match x values",
        "Curves",
        "A curve read at another curve's x values (linear between its points), so the "
        "two can be divided or subtracted point by point.",
        (*_CURVE_IN, PortSpec("onto", "Onto", ("series",))),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "area_under",
        "Area under curve",
        "Curves",
        "The area under a curve (trapezoids), in y × x units: an impulse from a force "
        "against time, a force from a pressure along a line.",
        _CURVE_IN,
        (PortSpec("out", "Area", ("number",)),),
        (),
    ),
    OperatorType(
        "slope",
        "Slope",
        "Curves",
        "How fast y changes with x at each point (dy/dx), in y ÷ x units: a stress "
        "gradient through a wall, a velocity from a displacement history.",
        _CURVE_IN,
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "limit_line",
        "Limit line",
        "Outputs",
        "A number drawn across a plot as a dashed line: the allowable on a stress curve.",
        (PortSpec("value", "Value", ("number",), typed="number"),),
        _CURVE_OUT,
        (),
    ),
    OperatorType(
        "annotate",
        "Annotate",
        "Outputs",
        "A curve with its largest (or smallest) point marked and labelled on the plot, "
        "by a note or its value.",
        _CURVE_IN,
        _CURVE_OUT,
        ("reduction", "note"),
    ),
)
# The arithmetic: two numbers give a number, anything with a curve gives a curve.
CURVE_OR_NUMBER = frozenset({"divide_by", "subtract", "multiply", "add", "utilisation",
                             "safety_factor", "margin"})  # fmt: skip
# Whose output's type follows what is wired in (a field in gives a field out).
FOLLOWS_INPUTS = CURVE_OR_NUMBER | {"normalise"}


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
            "margin (limit / value − 1 for at most). Fed a stress linearization, ASME "
            "VIII-2's membrane, membrane + bending and primary + secondary checks against "
            "the allowable S.",
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
            "a table's column against another, or any number of curves (a path, a "
            "history, a limit line) together: a new socket appears as each is wired. Drawn "
            "in the Plots panel as lines, bars or points; axes, units and the legend come "
            "from what is wired in.",
            (PortSpec("value", "Value", ("number", "table", "series")),
             PortSpec("b", "Curve 2", ("series",), required=False),
             PortSpec("c", "Curve 3", ("series",), required=False),
             PortSpec("d", "Curve 4", ("series",), required=False)),
            (PortSpec("out", "Table", ("table",)), PortSpec("curves", "Curves", ("series",))),
            ("against", "chart", "by", "column"),
            more=PortSpec("curve", "Curve {n}", ("series",), required=False),
        ),
        OperatorType(
            "export", "Export", "Display",
            "Any output to a file in the project: CSV, Parquet or Excel for tables, a "
            "result in an export format, or a result's view as a PNG (format png: a "
            "snapshot for a report).",
            (PortSpec("value", "Value", ANY),),
            (),
            ("file", "format", "quantity", "step"),
        ),
        OperatorType(
            "report", "Report", "Display",
            "What the schematic worked out, gathered into one HTML file to send: checks "
            "(pass or fail), numbers, curves drawn, tables, fields' ranges, each headed by "
            "the node it comes from. Written with Export now.",
            tuple(PortSpec(k, f"Item {k.upper()}", ANY, required=k == "a") for k in "abcdefgh"),
            (),
            ("file",),
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
        # ADR 0062 phase 4: Python in its own environment, in a trusted project only
        OperatorType(
            "script", "Python script", "Logic",
            "Python run apart (numpy, scipy, polars and what you install): its inputs a, "
            "b, c, d by name, it sets out. Runs only in a trusted project.",
            tuple(PortSpec(k, k, ("number", "list", "table", "points", "text"),
                           required=False, typed="number") for k in "abcd"),
            (PortSpec("out", "Out", ("number", "list", "table", "points", "text")),),
            ("returns", "timeout_s"),
        ),
        # ADR 0062 phase 3: geometry from data, before a solve (operators.geometry_data)
        OperatorType(
            "points", "Points", "Geometry",
            "Points from x, y and z (numbers or lists, a number repeated) or a table's x, "
            "y, z columns, in a coordinate system if given. A table of x, y, z too.",
            (PortSpec("x", "x", ("number", "list"), required=False, typed="number"),
             PortSpec("y", "y", ("number", "list"), required=False, typed="number"),
             PortSpec("z", "z", ("number", "list"), required=False, typed="number"),
             PortSpec("table", "Table", ("table",), required=False)),
            (PortSpec("out", "Points", ("points",)),),
            ("axes",),
        ),
        OperatorType(
            "grid", "Grid", "Geometry",
            "A grid of points on a plane (an axis plane at an origin, or a construction "
            "plane; counts and sizes along it) or in a box (its corners and three counts).",
            (PortSpec("nu", "Count along u", ("number",), required=False, typed="number"),
             PortSpec("nv", "Count along v", ("number",), required=False, typed="number"),
             PortSpec("nw", "Count along z (box)", ("number",), required=False,
                      typed="number"),
             PortSpec("size_u", "Size along u", ("number",), required=False, typed="number"),
             PortSpec("size_v", "Size along v", ("number",), required=False, typed="number"),
             *(PortSpec(f"{c}0", f"Origin {c} (box: low {c})", ("number",), required=False,
                        typed="number") for c in "xyz"),
             *(PortSpec(f"{c}1", f"Box: high {c}", ("number",), required=False,
                        typed="number") for c in "xyz")),
            (PortSpec("out", "Points", ("points",)),),
            ("region", "plane", "target"),
        ),
        OperatorType(
            "profile", "Profile", "Geometry",
            "A closed outline from data: a polyline through points, a circle, a rectangle "
            "or a regular polygon, for a face, an extrusion or a revolve to name.",
            (PortSpec("points", "Points", ("points",), required=False),
             PortSpec("x", "Centre (corner) x", ("number",), required=False, typed="number"),
             PortSpec("y", "Centre (corner) y", ("number",), required=False, typed="number"),
             PortSpec("radius", "Radius", ("number",), required=False, typed="number"),
             PortSpec("width", "Width", ("number",), required=False, typed="number"),
             PortSpec("height", "Height", ("number",), required=False, typed="number"),
             PortSpec("sides", "Sides", ("number",), required=False, typed="number"),
             PortSpec("angle", "Turned by (deg)", ("number",), required=False,
                      typed="number")),
            (PortSpec("out", "Profile", ("profile",)),),
            ("shape", "centred"),
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
            (PortSpec("out", "Value", ("number",)),
             PortSpec("passes", "Passes", ("verdict",))),
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
            "table's column; a statistic (sum, smallest, largest, mean, median, standard "
            "deviation, percentile, product, weighted mean); index of, shift, chunk, insert, "
            "replace, repeat, every nth, at indexes, unique, union, intersection, "
            "difference, remap, bin, differences, cumulative sum, moving average.",
            (PortSpec("list", "List", ("list", "table")),
             PortSpec("index", "Index", ("number",), required=False, typed="number"),
             PortSpec("end", "End", ("number",), required=False, typed="number"),
             PortSpec("weights", "Weights", ("list",), required=False),
             PortSpec("other", "Other list", ("list",), required=False)),
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
            "Numbers rounded to decimal places or significant figures, in the unit it "
            "names for columns of that kind, else in the unit Convert units gave a column, "
            "else SI; never in the display units.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("pick", "decimals", "significant", "unit"),
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
            "lookup", "Lookup", "Data",
            "A value read off a table at a number (or a list): y at x, linear between rows, "
            "stepped or nearest. Grasshopper's Graph Mapper, Excel's VLOOKUP.",
            (PortSpec("table", "Table", ("table",)),
             PortSpec("at", "At", ("number", "list"), typed="number")),
            (PortSpec("out", "Value", ("number",)),),
            ("by", "column", "interpolation"),
        ),
        OperatorType(
            "model_value", "Model value", "Inputs",
            "A value the model holds, read as it is now: a material's yield or ultimate "
            "strength, Young's modulus or density (or its table against temperature), a "
            "load's or support's value, a section's thickness, a mesh size, a parameter. "
            "Change the model and it follows.",
            (),
            (PortSpec("out", "Value", ("number", "table")),),
            ("target", "property"),
        ),
        OperatorType(
            "measure", "Measure", "Geometry",
            "A part's or selection's length, area, volume, mass (with its material), "
            "centroid, size or radius, from the exact geometry: no solve needed.",
            (),
            (PortSpec("out", "Value", ("number",)),),
            ("target", "selection", "measurement"),
        ),
        OperatorType(
            "join", "Join", "Tables",
            "Two tables side by side, matched on key columns (node, point): two solvers' "
            "results by node, then a New column for their difference.",
            (PortSpec("table", "Table", ("table",)), PortSpec("other", "Second table", ("table",))),
            (PortSpec("out", "Table", ("table",)),),
            ("by", "how", "suffix"),
        ),
        OperatorType(
            "stack", "Stack", "Tables",
            "Tables' rows one after another, with a column naming each row's table.",
            (PortSpec("table", "Table", ("table",)), PortSpec("other", "Second", ("table",)),
             PortSpec("third", "Third", ("table",), required=False)),
            (PortSpec("out", "Table", ("table",)),),
            ("new_name",),
        ),
        OperatorType(
            "pivot", "Pivot", "Tables",
            "Long to wide: a row per key, a column per value of another column (a column "
            "per step, per solver; at most 200).",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("by", "column", "pick", "reduction"),
        ),
        OperatorType(
            "unpivot", "Unpivot", "Tables",
            "Wide to long: columns folded into a name column and a value column.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("by", "pick", "new_name"),
        ),
        OperatorType(
            "distinct", "Distinct", "Tables",
            "Rows kept once: whole rows alike, or alike on some columns.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("by",),
        ),
        OperatorType(
            "fill_missing", "Fill missing", "Tables",
            "Missing values filled from the row before or after, the mean, zero or a "
            "value; or those rows dropped.",
            (PortSpec("table", "Table", ("table",)),
             PortSpec("value", "Value", ("number",), required=False, typed="number")),
            (PortSpec("out", "Table", ("table",)),),
            ("pick", "fill"),
        ),
        OperatorType(
            "rolling", "Running and rolling", "Tables",
            "A running or rolling sum, mean, maximum or minimum along an ordered column "
            "(time), per group: a history's peak so far.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("column", "reduction", "window", "by", "pick", "new_name"),
        ),
        OperatorType(
            "mesh_table", "Mesh table", "Results",
            "A run's mesh, one row per element: type, part, centre, shape quality and "
            "aspect: the worst elements and where.",
            (PortSpec("result", "Result", ("result",)),),
            (PortSpec("out", "Table", ("table",)),),
            (),
        ),
        OperatorType(
            "project_table", "Project table", "Inputs",
            "The project's objects as rows: parts with material, volume and mass; bolts; "
            "materials; selections; studies; features; contacts.",
            (),
            (PortSpec("out", "Table", ("table",)),),
            ("objects",),
        ),
        OperatorType(
            "run_history", "Run history", "Results",
            "A study's runs, one row each: when, solver, status, seconds, the point.",
            (PortSpec("source", "Study", ("result",)),),
            (PortSpec("out", "Table", ("table",)),),
            (),
        ),
        OperatorType(
            "history_table", "Probe history", "Results",
            "A result tool's value at every step, as a table.",
            (PortSpec("probe", "Result tool", ("number",)),),
            (PortSpec("out", "Table", ("table",)),),
            (),
        ),
        OperatorType(
            "mode_table", "Mode table", "Results",
            "A modal or buckling run's modes: frequency or load factor.",
            (PortSpec("result", "Result", ("result",)),),
            (PortSpec("out", "Table", ("table",)),),
            (),
        ),
        OperatorType(
            "contact_table", "Contact table", "Results",
            "Each contact's forces, closed area, pressure, penetration, sliding and gap.",
            (PortSpec("result", "Result", ("result",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("step",),
        ),
        OperatorType(
            "bolt_table", "Bolt table", "Results",
            "Each bolt's axial, shear, torsion, bending, stress and use of proof and yield.",
            (PortSpec("result", "Result", ("result",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("step",),
        ),
        OperatorType(
            "nearest", "Nearest", "Results",
            "Each of a table's points (x, y, z) at its nearest node: its distance and the "
            "results there (test gauges matched to the model).",
            (PortSpec("result", "Result", ("result",)), PortSpec("points", "Points", ("table",))),
            (PortSpec("out", "Table", ("table",)),),
            ("quantities", "step"),
        ),
        OperatorType(
            "section", "Section", "Results",
            "A result over a construction plane's cut as a table, and the force and moment "
            "across it.",
            (PortSpec("result", "Result", ("result",)),),
            (PortSpec("out", "Table", ("table",)), PortSpec("force", "Normal force", ("number",)),
             PortSpec("moment", "Moment", ("number",))),
            ("target", "quantities", "step"),
        ),
        OperatorType(
            "divide", "Divide", "Geometry",
            "Points evenly along a construction path or a named edge (a weld line): x, y, "
            "z and the distance along.",
            (PortSpec("count", "Points", ("number",), required=False, typed="number"),),
            (PortSpec("out", "Table", ("table",)),),
            ("target", "selection"),
        ),
        OperatorType(
            "clearance", "Clearance", "Geometry",
            "The least distance between two parts (or selections) where they are placed: "
            "a constraint with no solve.",
            (),
            (PortSpec("out", "Value", ("number",)),),
            ("target", "selection", "other"),
        ),
        OperatorType(
            "text", "Text", "Logic",
            "A line from values: \"σ max = {x} at {y}\", each in its display unit; for "
            "panels, exports and reports.",
            (PortSpec("x", "x", ANY, required=False), PortSpec("y", "y", ANY, required=False),
             PortSpec("z", "z", ANY, required=False)),
            (PortSpec("out", "Text", ("text",)),),
            ("template",),
        ),
        OperatorType(
            "preview", "Preview", "Outputs",
            "A table's rows drawn in the 3D view as labelled points (x, y, z and a column "
            "for the label): the worst nodes, marked.",
            (PortSpec("table", "Table", ("table",)),),
            (),
            ("column",),
        ),
        OperatorType(
            "toggle", "Toggle", "Inputs",
            "A switch, 1 (on) or 0 (off), a parameter by its name: a feature whose When "
            "names it applies only while it is on.",
            (PortSpec("value", "On", ("number",), required=False, typed="number"),),
            (PortSpec("out", "Value", ("number",)),),
            (),
        ),
        OperatorType(
            "choice", "Choice", "Inputs",
            "One of a list of presets (M8, M10, M12), a parameter by its name; a sweep "
            "or optimisation can run over them.",
            (PortSpec("index", "Pick", ("number",), required=False, typed="number"),),
            (PortSpec("out", "Value", ("number",)), PortSpec("options", "Options", ("list",))),
            ("options", "labels"),
        ),
        OperatorType(
            "slider2d", "2D slider", "Inputs",
            "Two values dragged together (width and thickness), parameters <name>_x and "
            "<name>_y.",
            (PortSpec("value", "x", ("number",), required=False, typed="number"),
             PortSpec("value2", "y", ("number",), required=False, typed="number")),
            (PortSpec("x", "x", ("number",)), PortSpec("y", "y", ("number",))),
            ("low", "high", "low2", "high2"),
        ),
        OperatorType(
            "merge_points", "Merge points", "Tables",
            "Rows whose points (x, y, z) are closer than a spacing made one, their values "
            "averaged: duplicates from a scan or a test rig.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("spacing",),
        ),
        OperatorType(
            "thin_points", "Thin points", "Tables",
            "One row kept per cell of a spacing: a dense imported cloud thinned before "
            "mapping.",
            (PortSpec("table", "Table", ("table",)),),
            (PortSpec("out", "Table", ("table",)),),
            ("spacing",),
        ),
        OperatorType(
            "intersection", "Intersection", "Geometry",
            "Where two parts or selections meet, or a construction plane cuts one: curves "
            "as points along them (a weld line, a section's outline), or crossing points.",
            (PortSpec("count", "Points per curve", ("number",), required=False,
                      typed="number"),),
            (PortSpec("out", "Table", ("table",)),),
            ("target", "selection", "other"),
        ),
        # ADR 0071 decision 3: curves (series) and the operators on them
        *SERIES_TYPES,
        OperatorType(
            "pairs", "Pairs", "Data",
            "Two lists into a table: item by item, or every combination.",
            (PortSpec("a", "A", ("list",)), PortSpec("b", "B", ("list",))),
            (PortSpec("out", "Table", ("table",)),),
            ("combination",),
        ),
    )
}  # fmt: skip

# The seven groups, in the order work flows (ADR 0064 item 6): the toolbar's Add
# operator menu, the palette, the outline and the skills all use them.
GROUPS = ("Inputs", "Geometry", "Logic", "Results", "Curves", "Tables", "Checks", "Outputs")
GROUP_OF = {
    "script": "Logic",
    "points": "Geometry",
    "grid": "Geometry",
    "profile": "Geometry",
    "number": "Inputs",
    "slider": "Inputs",
    "series": "Inputs",
    "range": "Inputs",
    "random": "Inputs",
    "table": "Inputs",
    "file": "Inputs",
    "measure": "Geometry",
    "model_value": "Inputs",
    "expression": "Logic",
    "list": "Logic",
    "pairs": "Logic",
    "lookup": "Logic",
    "result_table": "Results",
    "points_table": "Results",
    "reduce": "Results",
    "filter": "Results",
    "columns": "Tables",
    "filter_rows": "Tables",
    "sort": "Tables",
    "top_n": "Tables",
    "new_column": "Tables",
    "convert_units": "Tables",
    "round": "Tables",
    "group": "Tables",
    "to_number": "Tables",
    "join": "Tables",
    "stack": "Tables",
    "pivot": "Tables",
    "unpivot": "Tables",
    "distinct": "Tables",
    "fill_missing": "Tables",
    "rolling": "Tables",
    "mesh_table": "Results",
    "run_history": "Results",
    "history_table": "Results",
    "mode_table": "Results",
    "contact_table": "Results",
    "bolt_table": "Results",
    "nearest": "Results",
    "section": "Results",
    "project_table": "Inputs",
    "divide": "Geometry",
    "clearance": "Geometry",
    "text": "Logic",
    "preview": "Outputs",
    "toggle": "Inputs",
    "choice": "Inputs",
    "slider2d": "Inputs",
    "merge_points": "Tables",
    "thin_points": "Tables",
    "intersection": "Geometry",
    "check": "Checks",
    "panel": "Outputs",
    "plot": "Outputs",
    "export": "Outputs",
    "report": "Outputs",
}
# Grasshopper's, Excel's and SQL's names for each (ADR 0064A), so a search finds it.
ALIASES = {
    "script": ("python", "code", "ghpython", "custom"),
    "points": ("construct point", "point from xyz", "deconstruct point", "coordinates"),
    "grid": ("square grid", "populate", "point grid", "array of points"),
    "profile": ("polyline", "circle", "rectangle", "polygon", "outline", "curve"),
    "number": ("constant", "value", "parameter"),
    "slider": ("number slider", "digit scroller", "control knob"),
    "series": ("sequence", "arithmetic series"),
    "range": ("linspace", "divide domain", "domain"),
    "random": ("jitter", "monte carlo", "noise", "scatter"),
    "table": ("csv", "typed table", "data"),
    "file": ("read file", "import coordinates", "data input", "query directory", "excel",
             "parquet", "csv file"),
    "model_value": ("material property", "yield strength", "ultimate strength",
                    "young's modulus", "density", "load value", "setup value", "read model",
                    "property"),
    "measure": ("area", "volume", "length", "mass", "centroid", "bounding box",
                "area moments", "volume moments", "dimensions"),
    "expression": ("evaluate", "formula", "smaller than", "larger than", "equality",
                   "gate and", "gate or", "compare", "math", "if", "switch", "absolute",
                   "abs"),
    "list": ("list item", "sub list", "list length", "sort list", "reverse list",
             "mass addition", "average", "statistics", "median", "percentile",
             "weighted average"),
    "pairs": ("longest list", "cross reference", "zip", "weave"),
    "lookup": ("graph mapper", "vlookup", "interpolate", "lookup table"),
    "result_table": ("deconstruct mesh", "results to table", "export results"),
    "points_table": ("sweep points", "design points", "gene pool"),
    "reduce": ("maximum", "minimum", "extremes", "bounds", "integral", "mean",
               "integrate variables", "integrate", "von mises", "vm", "average", "peak",
               "hot spot", "probe"),  # the last six: OP-NEW-13, searches that found nothing
    "filter": ("threshold", "mask", "cull"),
    "columns": ("select columns", "rename", "drop columns", "pick columns"),
    "filter_rows": ("cull pattern", "cull index", "dispatch", "where", "filter content",
                    "select rows"),
    "sort": ("order by", "order content", "sort list"),
    "top_n": ("worst", "largest", "head", "limit", "highest"),
    "new_column": ("calculated column", "add column", "formula column"),
    "convert_units": ("unit system", "units"),
    "round": ("truncate", "significant figures"),
    "group": ("group by", "group content", "aggregate", "pivot table", "sum by"),
    "to_number": ("cherry picker", "extract", "single value"),
    "check": ("allowable", "pass fail", "verdict", "margin"),
    "panel": ("param viewer", "note"),
    "plot": ("quick graph", "bar graph", "chart", "value tracker"),
    "export": ("data output", "save", "write csv", "write excel", "bake", "snapshot",
               "screenshot", "png", "model view"),
    "join": ("merge", "vlookup", "match", "combine tables"),
    "stack": ("concatenate", "append", "entwine", "union all"),
    "pivot": ("wide", "crosstab", "flip matrix"),
    "unpivot": ("melt", "long", "explode"),
    "distinct": ("unique", "cull duplicates", "delete consecutive", "create set"),
    "fill_missing": ("replace nulls", "clean tree", "null item", "fill"),
    "rolling": ("running total", "cumulative", "moving average", "smooth numbers",
                "blur numbers"),
    "mesh_table": ("deconstruct mesh", "element quality", "mesh quality"),
    "project_table": ("bill of materials", "query model objects", "content details"),
    "run_history": ("data recorder", "value tracker", "runs"),
    "history_table": ("probe history", "over time"),
    "mode_table": ("frequencies", "eigenvalues", "modes"),
    "contact_table": ("contact forces", "contact pressure"),
    "bolt_table": ("fastener forces", "bolt forces"),
    "nearest": ("closest point", "pull point", "proximity", "mesh closest point", "gauges"),
    "section": ("brep plane", "mesh plane", "contour curves", "cut", "section force"),
    "divide": ("divide curve", "divide length", "curve frames", "points along"),
    "clearance": ("collision", "clash", "minimum distance", "interference"),
    "text": ("format", "concatenate", "text join", "label"),
    "preview": ("custom preview", "text tag", "point list", "dot display"),
    "toggle": ("boolean toggle", "button", "switch", "on off"),
    "choice": ("value list", "dropdown", "presets", "catalogue"),
    "slider2d": ("md slider", "pad", "two sliders"),
    "merge_points": ("cull duplicates", "point groups", "weld"),
    "thin_points": ("reduce point cloud", "downsample", "decimate"),
    "intersection": ("brep brep", "curve curve", "section", "weld line", "meet",
                     "brep plane"),
    # ADR 0071 decision 3: the curve operators
    "divide_by": ("ratio", "quotient", "division", "a / b"),
    "subtract": ("difference", "minus", "delta", "a - b"),
    "multiply": ("product", "times", "scale", "a * b"),
    "add": ("sum", "plus", "offset", "a + b"),
    "utilisation": ("utilization", "unity check", "usage", "stress ratio", "interaction ratio"),
    "safety_factor": ("factor of safety", "fos", "reserve factor", "sf"),
    "margin": ("margin", "ms", "reserve"),
    "normalise": ("normalize", "non-dimensional", "scale to maximum", "unit maximum"),
    "curve_formula": ("series expression", "map y", "transform curve"),
    "slice": ("cut plane", "section plane", "paraview slice"),
    "clip": ("half model", "cut away", "paraview clip"),
    "report": ("summary", "document", "write up", "calculation report", "html"),
    "stream": ("streamlines", "stream tracer", "pathlines", "flow lines", "paraview stream"),
    "glyph": ("arrows", "vector plot", "quiver", "paraview glyph"),
    "contour": ("iso-surface", "isosurface", "isoline", "level set", "paraview contour"),
    "field_formula": ("field expression", "result calculator", "calculator", "field math",
                      "derived field", "user defined result"),
    "curve_from_table": ("table to curve", "xy data", "columns to curve"),
    "over_steps": ("history", "time history", "against time", "against frequency",
                   "over time"),
    "at_step": ("pick step", "time slice", "one step"),
    "compare_steps": ("baseline", "relative to step", "against a step", "step difference"),
    "envelope": ("worst case", "max over steps", "upper bound", "lower bound", "combine"),
    "match_x": ("resample", "interpolate onto", "align curves", "common x"),
    "area_under": ("integrate", "trapezoid", "impulse", "integral of curve"),
    "slope": ("derivative", "gradient", "rate of change", "dy/dx"),
    "limit_line": ("allowable line", "threshold line", "horizontal line", "reference line"),
    "annotate": ("label maximum", "callout", "mark peak", "note"),
}  # fmt: skip
# Names a user looks for first (ADR 0071's naming rules): the result or the action in an
# engineer's words, not a programmer's or a data scientist's; no word that means another
# operator; the old name kept as an alias, so search still finds it. Keys never change:
# projects store the key, so renaming is the label alone.
LABELS = {
    "divide": "Points along",  # it isn't arithmetic: points along a path or edge
    "series": "Stepped list",  # "series" is a plot's data (ADR 0071)
    "section": "Plane cut values",  # beside Section cut and beam sections
    "filter": "Keep result where",
    "filter_rows": "Keep rows where",
    "reduce": "Result summary",
    "nearest": "Values at points",
    "expression": "Formula",
    "pivot": "Rows to columns",
    "unpivot": "Columns to rows",
    "stack": "Append tables",
    "join": "Match tables",
    "distinct": "Remove duplicates",
    "top_n": "Largest N rows",
    "group": "Summarise by group",
    "rolling": "Running totals and averages",
    "to_number": "Pick one value",
    "new_column": "Calculated column",
    "columns": "Choose columns",
    "lookup": "Read off a table",
    "pairs": "Pair up lists",
    "list": "List item or statistic",
    "panel": "Show value",
    "preview": "Mark points in view",
    "project_table": "Model contents",
    "file": "Read file",
    "random": "Random samples",
    "toggle": "On/off switch",
    "profile": "Outline",
    "check": "Check against limit",
}
TYPES = {
    k: replace(
        t,
        label=LABELS.get(k, t.label),
        group=GROUP_OF.get(k, t.group),
        aliases=ALIASES.get(k, ()) + ((t.label.lower(),) if k in LABELS else ()),
    )
    for k, t in TYPES.items()
}

# A worked example for the help card (ADR 0071 decision 1): what to wire, what comes out.
EXAMPLES = {
    "reduce": "A run's von Mises on the Bracket → its maximum: 212 MPa.",
    "check": "212 MPa against 250 MPa, at most → Pass, margin 0.18.",
    "filter": "Von Mises kept above 200 MPa: where the part is highly stressed.",
    "expression": "2 * x + 5 mm with x = 10 mm → 25 mm; a list in gives a list out.",
    "lookup": "Yield against temperature, read at 150 °C → its value there.",
    "measure": "The Bracket's volume and mass, from its geometry and material.",
    "model_value": "Steel's yield strength → 250 MPa; wire it into Check against limit or "
    "Divide, and change the material to see every check follow.",
    "plot": "A probe's displacement against the steps, or a table's column against another.",
    "number": "250 MPa, named Allowable: wire it into Check, or use it as a parameter.",
    "slider": "Thickness from 2 to 10 mm in 0.5 mm steps, driving a geometry.",
    "nearest": "Strain gauge positions from a CSV → the model's strain at each.",
    "result_table": "A run's displacement and von Mises, one row per node per step.",
    "to_number": "A table's von Mises column → its maximum, for a Check.",
    "new_column": "margin = 250 MPa / von_mises - 1, worked out for every row.",
    "group": "Rows by part → each part's largest von Mises.",
    "join": "CalculiX and Code_Aster results matched by node, then their difference.",
    "section": "A construction plane across a bolt → the force and moment it carries.",
    "divide": "20 points along a weld edge, with their distance along it.",
    "panel": "Wire any output in to see its value on the canvas.",
    "export": "A table to CSV in the project's exports, or the view as a PNG.",
    "divide_by": "Von Mises along a path ÷ 250 MPa → the same curve as a pure number.",
    "subtract": "CalculiX's path minus Code_Aster's (Match x values first if their points differ).",
    "utilisation": "Von Mises along a path with Model value's yield strength as the "
    "allowable → 0.85 at the worst point.",
    "safety_factor": "A peak stress of 200 MPa against 250 MPa → 1.25.",
    "margin": "A peak stress of 200 MPa against 250 MPa → 0.25.",
    "normalise": "A deflected shape along a beam ÷ its largest deflection → 1 at the tip.",
    "curve_formula": "y / 250 MPa, or y * a with a wired factor.",
    "report": "Three checks, a utilisation plot and a reactions table → exports/Report.html, "
    "'2 of 3 checks pass' at the top.",
    "slice": "Von Mises on a construction plane across a nozzle's wall → its points' values.",
    "clip": "Temperature kept above a plane at the weld → its largest value there (Reduce).",
    "contour": "Von Mises at 250 MPa → the points where the plate yields.",
    "stream": "Velocity in a channel from ten seeds across its inlet → the lines the flow takes.",
    "glyph": "Velocity at 200 nodes → arrows to plot, or pin in the view.",
    "field_formula": "Von Mises at the last step minus the first (f - g), or f / 250 MPa: "
    "the change, or the utilisation, shown on the model.",
    "over_steps": "A result tool's displacement in a transient run → displacement against time.",
    "at_step": "A path in a 20-step transient → the curve at step 20.",
    "compare_steps": "Each step's path against step 1, as a ratio: how the load spreads.",
    "envelope": "A path at every step → the largest stress at each point over all steps.",
    "match_x": "A test's strain readings read at the model path's points.",
    "area_under": "A pressure along a line → the force per unit width it carries.",
    "slope": "Temperature through a wall → its gradient, for the heat flux.",
    "limit_line": "The yield strength drawn across a stress plot.",
    "annotate": 'A path\'s largest von Mises marked "peak 212 MPa".',
}


def port_doc(spec: PortSpec, side: str = "in") -> str:
    """One line on a port: its name, what it takes (or gives) and whether it's needed."""
    if spec.types == ANY:
        kinds = "anything"
    else:
        kinds = "a " + " or ".join(TYPE_WORDS.get(t, t).lower() for t in spec.types)
    if side == "out":
        return f"{spec.label}: gives {kinds}"
    need = "needed" if spec.required else "optional"
    typed = ", or type one in" if spec.typed else ""
    return f"{spec.label}: takes {kinds}{typed} ({need})"


def operator_doc(key: str) -> dict:
    """What an operator kind does, for people and agents alike (the help card, tooltips,
    Properties, MCP's operator_help): its purpose, an example, its inputs and outputs."""
    t = TYPES[key]
    return {
        "key": key,
        "name": t.label,
        "group": t.group,
        "purpose": t.help,
        "example": EXAMPLES.get(key, ""),
        "inputs": [{"name": p.label, "port": p.key, "takes": list(p.types),
                    "required": p.required, "typed": p.typed or None,
                    "about": port_doc(p, "in")} for p in t.inputs],
        "outputs": [{"name": p.label, "port": p.key, "gives": p.types[0],
                     "about": port_doc(p, "out")} for p in t.outputs],
        "also_called": list(t.aliases),
    }  # fmt: skip


def doc_text(key: str) -> str:
    """The help card as plain text: purpose, example, inputs, outputs."""
    d = operator_doc(key)
    lines = [f"{d['name']}: {d['purpose']}"]
    if d["example"]:
        lines.append(f"Example: {d['example']}")
    lines += [f"In  · {i['about']}" for i in d["inputs"]]
    lines += [f"Out · {o['about']}" for o in d["outputs"]]
    return "\n".join(lines)


# Types that compute from data, before a solve: pre-solve when nothing they read is a
# result (ADR 0062 item 3), and then parameters by their name.
DATA = frozenset({"number", "slider", "series", "range", "expression", "table", "list",
                  "pairs", "random", "file", "columns", "filter_rows", "sort", "top_n",
                  "new_column", "convert_units", "round", "group", "to_number", "lookup",
                  "measure", "model_value", "join", "stack", "pivot", "unpivot", "distinct",
                  "fill_missing",
                  "rolling", "project_table", "divide", "clearance", "text", "toggle",
                  "choice", "slider2d", "merge_points", "thin_points",
                  "intersection", "points", "grid", "profile", "script",
                  "divide_by", "subtract", "multiply", "add", "utilisation",
                  "safety_factor", "margin", "normalise", "curve_formula",
                  "curve_from_table", "match_x", "area_under", "slope", "limit_line",
                  "annotate", "envelope"})  # fmt: skip
# Table operators (ADR 0064): data operators that read tables, so pre-solve when their
# table is (a file, a typed table) and post-solve when it comes from a run.


def first_type(op, project=None) -> str:
    """An operator's first output's type: a script's from its `returns`; with the
    project, the arithmetic's from what is wired in (two numbers make a number)."""
    if op.type == "script":
        return op.returns
    if op.type in FOLLOWS_INPUTS and project is not None:
        return arithmetic_type(project, op)
    if op.type == "model_value" and op.property.startswith("tables."):
        return "table"  # a property against temperature
    outputs = TYPES[op.type].outputs
    return outputs[0].type if outputs else ""


def arithmetic_type(project, op, seen=()) -> str:
    """What an arithmetic operator (or Normalise) gives, from what is wired in: a field
    if any input is a field, else a number if every input is a number, else a curve."""
    types = set()
    for inp in op.inputs.values():
        if inp.source is None or inp.source in seen:
            continue
        types.add(output_type(project, inp.source, inp.port, (*seen, op.id)))
    if "field" in types:
        return "field"
    if op.type == "normalise":
        return "series"
    return "number" if types <= {"number"} else "series"


def _numbers_only(project, op, seen=()) -> bool:
    """Whether every input of an arithmetic operator is a number (typed, or wired from a
    number output)."""
    for inp in op.inputs.values():
        if inp.source is None or inp.source in seen:
            continue
        if output_type(project, inp.source, inp.port, (*seen, op.id)) != "number":
            return False
    return True


def accepts(spec: PortSpec, given: str) -> bool:
    """Whether an input port takes an output of type `given` (a number where a list is
    wanted, as a one-item list)."""
    return (
        given in spec.types
        or (given == "number" and "list" in spec.types)
        or (given == "points" and "table" in spec.types)  # points are a table too
        or (given == "table" and "series" in spec.types)  # its first two number columns
        or (given == "series" and "table" in spec.types)  # its x and y columns (OP-NEW-3)
        # A study's result where a field is wanted: read as its quantity at its step
        # (evaluate._input), so Field formula, Slice and the rest take a study directly.
        or (given == "result" and "field" in spec.types)
    )


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
        return [(p.key, p.label, first_type(obj, project) if i == 0 else p.type)
                for i, p in enumerate(TYPES[obj.type].outputs)]  # fmt: skip
    if isinstance(obj, ParameterSet):
        return [(k, k, "number") for k in obj.values]
    if isinstance(obj, ResultProbe):  # its value, and curves (ADR 0071 decision 3)
        along = (
            [("along", "Along the path", "series")] if obj.type in ("path", "linearized") else []
        )
        return [("out", "Value", "number"), *along, ("history", "Over the steps", "series")]
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


def output_type(project, source: UUID, port: str, seen=()) -> str | None:
    """The type of `source`'s output `port`, None if it has no such output. A study's
    field ports are results limited to that field."""
    from puzzle.model.schema import Study

    obj = project.objects.get(source)
    if obj is None:
        return None
    if isinstance(obj, Study):  # a field a run may hold, if not one the catalog lists
        if port == "points":
            return "table"
        return "result" if port in ("result", "out") or port in study_fields(project, obj) \
            else None  # fmt: skip
    from puzzle.model.schema import Operator

    if isinstance(obj, Operator) and obj.type in FOLLOWS_INPUTS and port == "out":
        return arithmetic_type(project, obj, seen)
    found = next((t for k, _, t in outputs_of(project, obj) if k == port), None)
    return found


def study_fields(project, study) -> set[str]:
    """The field ports a study offers (OP-8): what its physics make (the catalog), and
    any field Puzzle knows a run can hold (a reaction force, a plastic strain)."""
    from puzzle.post.quantities import SCALARS, TENSORS, VECTORS
    from puzzle.schematic import outputs

    return set(outputs(project, study)) | set(VECTORS) | set(TENSORS) | set(SCALARS)


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


def studies_read(project, obj, seen: frozenset = frozenset()) -> list:
    """The studies an operator (or result tool, derived result) reads, through every
    operator and tool upstream of it, in no particular order: what "Evaluate up to
    here" runs first when they aren't up to date (ADR 0071 decision 4)."""
    from puzzle.model.schema import Study

    if obj is None or obj.id in seen:
        return []
    if isinstance(obj, Study):
        return [obj]
    seen = seen | {obj.id}
    out, ids = [], set()
    refs = list(getattr(obj, "dependencies", lambda: [])())
    source = getattr(obj, "source", None)  # a result tool reads its study or result
    if source is not None and not isinstance(source, str):
        refs.append(source)
    for ref in refs:
        for s in studies_read(project, project.objects.get(ref), seen):
            if s.id not in ids:
                ids.add(s.id)
                out.append(s)
    return out


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
        if inp.chosen():
            _refuse_choice(project, op, spec, inp)
    _refuse_settings(project, op)


def _refuse_choice(project, op, spec, inp) -> None:
    """A run chosen (ADR 0090 decision 4) only on a wire from a study or a result tool
    of one, and only a solver the study runs."""
    from puzzle.model.schema import ResultProbe, Study

    src = project.objects.get(inp.source) if inp.source else None
    if isinstance(src, ResultProbe):
        src = project.objects.get(src.source)
    if not isinstance(src, Study):
        raise PuzzleError(
            f"'{op.name}': its {spec.label.lower()} input reads no study, so no run is "
            "chosen there (choose it where the study is wired in)", code="invalid",
            path=op.name)  # fmt: skip
    if inp.solver and inp.solver not in src.all_solvers():
        raise PuzzleError(
            f"'{op.name}': study '{src.name}' doesn't run {inp.solver} (it runs "
            f"{', '.join(src.all_solvers())})", code="invalid", path=op.name)  # fmt: skip


def _refuse_settings(project, op) -> None:
    """Settings that are accepted but mean nothing (OP-13)."""
    if op.type == "slider" and op.low is not None and op.high is not None:
        from puzzle.model.units import kind_of

        def kind(q):
            return kind_of(q.unit) if q.unit and q.unit != "dimensionless" else None

        if kind(op.low) == kind(op.high) and op.low.to_si() > op.high.to_si():
            raise PuzzleError(f"slider '{op.name}': its lowest value is above its highest",
                              code="invalid", path=op.name)  # fmt: skip
        if op.increment is not None and kind(op.increment) != kind(op.low):
            raise PuzzleError(f"slider '{op.name}': it steps in {op.increment.unit}, which "
                              f"isn't the kind of {op.low.unit}", code="invalid",
                              path=op.name)  # fmt: skip


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
    # OP-1: a before-solving operator that can't be worked out is a problem of its own,
    # so the tree, the Advisor and the outline show it; a study reading it can't run.
    # ADR 0071: a value following a parameter that isn't one (renamed, or deleted).
    bound = [(obj, path, q) for obj in project.objects.values()
             for path, q in bound_quantities(obj)]  # fmt: skip
    failed: dict[str, PuzzleError] = {}
    # Scripts are run only when a value follows a parameter, as before: not on every
    # refresh for their problems alone.
    values = parameter_values(project, failed=failed, scripts=bool(bound))
    if failed:
        from puzzle.operators.evaluate import bare

        by_name = {o.name: o for o in project.of_type(Operator)}
    for name, exc in failed.items():
        op = by_name[name]
        out.append(Problem("operator_failed", name, f"{TYPES[op.type].label.lower()} "
                           f"'{name}' can't be worked out: {bare(op, exc.message)}",
                           {"code": exc.code} | exc.details))  # fmt: skip
    if bound:
        names = set(values)
        for obj, path, q in bound:
            if q.parameter in failed:
                out.append(Problem(
                    "operator_failed", obj.name,
                    f"{obj.kind} '{obj.name}': {path} follows parameter '{q.parameter}', "
                    "which can't be worked out", {"path": path,
                                                  "parameter": q.parameter}))  # fmt: skip
            elif q.parameter not in names:
                out.append(Problem(
                    "operator_input", obj.name,
                    f"{obj.kind} '{obj.name}': {path} follows parameter '{q.parameter}', "
                    "which doesn't exist", {"path": path}))  # fmt: skip
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
    elif op.type == "join":
        bits.append(f"{op.how} on {', '.join(op.by) or 'the first shared column'}")
    elif op.type in ("pivot", "unpivot", "distinct"):
        bits.append(", ".join(op.by) or "")
    elif op.type == "fill_missing":
        bits.append(op.fill)
    elif op.type == "rolling":
        bits.append(f"{'running' if op.window == 0 else f'{op.window}-row'} {op.reduction} "
                    f"of {op.column}")  # fmt: skip
    elif op.type == "project_table":
        bits.append(op.objects)
    elif op.type == "clearance":
        bits.append(f"{op.selection} to {op.other}")
    elif op.type == "text":
        bits.append(repr(op.template))
    elif op.type == "lookup":
        bits.append(f"{op.column} at {', '.join(op.by)} ({op.interpolation})")
    elif op.type == "measure":
        bits.append(f"{op.measurement} of {op.selection or 'the whole part'}")
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
            which = (f" at {inp.point}" if inp.point else "") + (
                f" on {inp.solver}" if inp.solver else "") + (
                " (one stored run)" if inp.run else "")  # fmt: skip
            wired.append(f"{spec.key} <- {name}{port}{which}")
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
        if inp.get("run"):  # ADR 0090 decision 4: a stored run by the tree's name
            inp["run"] = run_by_name(project, inp["source"], str(inp["run"]))
        value = inp.get("value")
        if isinstance(value, str):
            number, _, unit = value.strip().partition(" ")
            with contextlib.suppress(ValueError):  # else a text
                inp["value"] = {"value": float(number), "unit": unit.strip() or "dimensionless"}
        out[key] = inp
    return out


def run_choices(project, store, source) -> list[tuple[str, dict]]:
    """Which runs a wire from `source` (a study, or a result tool of one) may read, as
    (name, choice) for Properties and the agent (ADR 0090 decision 4): the current run,
    each sweep point, mesh level and adaptive pass by its title, each shoot-out solver,
    then each stored run by the tree's name. Empty for anything else."""
    from puzzle.adapters import SOLVER_NAMES
    from puzzle.model.schema import ResultProbe, Study
    from puzzle.sweep import title

    obj = project.objects.get(source)
    if isinstance(obj, ResultProbe):
        obj = project.objects.get(obj.source)
    if not isinstance(obj, Study):
        return []
    out: list[tuple[str, dict]] = [("Its run for the model as it is now", {})]
    runs = []
    if store is not None:
        try:
            runs = [r for r in reversed(store.runs()) if r.study_id == str(obj.id)
                    and r.status == "completed"]  # fmt: skip
        except Exception:  # noqa: BLE001 - no store yet: nothing has run
            runs = []
    points = list(dict.fromkeys(title(r.point_labels()) for r in runs if r.point_labels()))
    out += [(f"Point: {p}", {"point": p}) for p in points]
    out += [(f"Solver: {SOLVER_NAMES.get(s, s)}", {"solver": s})
            for s in obj.all_solvers()[1:]]  # fmt: skip
    from puzzle.operators.evaluate import run_name

    out += [(f"Run: {run_name(project, r)}", {"run": r.run_id}) for r in runs]
    return out


def run_by_name(project, source: str, name: str) -> str:
    """A stored run's id from its name as the tree shows it (or its id)."""
    from pathlib import Path

    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    root = getattr(project, "root", None)
    if root is None or not (Path(root) / RESULTS_DIR).exists():
        return name
    for label, choice in run_choices(project, ResultStore(Path(root) / RESULTS_DIR),
                                     UUID(str(source))):  # fmt: skip
        if "run" in choice and (choice["run"] == name or label.removeprefix("Run: ") == name):
            return choice["run"]
    raise PuzzleError(f"no stored run '{name}' of that study", code="not_found")


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


GEOMETRY_READERS = frozenset({"measure", "clearance", "divide", "project_table",
                              "intersection"})  # fmt: skip


def reads_geometry(project, op, seen: frozenset = frozenset()) -> bool:
    """Whether an operator reads the geometry (a Measure, or something wired from one):
    then it can't be a parameter the geometry reads, or regenerating would loop."""
    from puzzle.model.schema import Operator

    if op.type in GEOMETRY_READERS:
        return True
    for inp in op.inputs.values():
        src = project.objects.get(inp.source) if inp.source else None
        if (isinstance(src, Operator) and src.id not in seen
                and reads_geometry(project, src, seen | {op.id})):  # fmt: skip
            return True
    return False


def parameter_values(project, overrides: dict | None = None, failed: dict | None = None,
                     scripts: bool = True) -> dict[str, float]:  # fmt: skip
    """Every parameter by name, SI: the parameter sets' values, then each pre-solve
    number operator's (Number, Slider, a number Expression) by the operator's name.
    `overrides` (operator id -> SI value) are a slider being dragged (the preview's).
    Never raises (OP-1): an operator that can't be worked out is left out, and with
    `failed` given, put there (operator name -> its PuzzleError; an untrusted project's
    held scripts are its own problem). `scripts` False leaves script operators out."""
    from puzzle.model.schema import ParameterSet

    out = {k: q.to_si() for ps in project.of_type(ParameterSet) for k, q in ps.values.items()}
    numbers = [o for o in pre_solve(project) if TYPES[o.type].outputs
               and first_type(o, project) == "number"
               and (scripts or o.type != "script")
               and not reads_geometry(project, o)]  # fmt: skip
    if not numbers:
        return out
    from puzzle.operators import evaluate as ev

    ctx = ev.Context(project, None, overrides=dict(overrides or {}))
    for op in numbers:
        try:
            if op.type == "slider2d":  # two parameters: <name>_x and <name>_y
                x, y = ev.value(ctx, op, "x").value, ev.value(ctx, op, "y").value
                out.setdefault(f"{op.name}_x", x)
                out.setdefault(f"{op.name}_y", y)
                continue
            got = ev.value(ctx, op)
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001 - an operator in error: its problem says why
            if failed is not None and getattr(exc, "code", "") != "untrusted":
                failed[op.name] = exc if isinstance(exc, PuzzleError) else ev.failure(op, exc)
            continue
        if isinstance(got, ev.Number):
            out.setdefault(op.name, got.value)
    return out


def read_by(project, study) -> list:
    """The operators a study's model reads: those it names (as its run key finds them,
    ADR 0090 package 1) and every operator those read."""
    from puzzle.model.schema import Operator
    from puzzle.project.graph import _Graph

    if not project.of_type(Operator):
        return []
    graph = _Graph(project, getattr(project, "root", None))
    seen: set = set()
    frontier = [d for d in graph.deps.get(study.id, []) if d in project.objects]
    read = []
    while frontier:
        oid = frontier.pop()
        obj = project.objects.get(oid)
        if oid in seen or not isinstance(obj, Operator):
            continue
        seen.add(oid)
        read.append(obj)
        frontier += [d for d in obj.dependencies() if d not in seen]
    return read


def failures_read(project, study) -> list[PuzzleError]:
    """The before-solving operators a study's model reads that can't be worked out: it
    can't run without them (OP-1)."""
    from puzzle.operators import evaluate as ev

    read = read_by(project, study)
    if not read:
        return []
    ctx = ev.Context(project, None)
    out = []
    for op in read:
        if op.type not in DATA or not is_pre(project, op) or reads_geometry(project, op):
            continue
        try:
            ev.outputs(ctx, op)
        except PuzzleError as exc:
            out.append(exc)
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


def bound_quantities(obj, path: str = ""):
    """(dotted path, Quantity) for every quantity in `obj` that names a parameter."""
    from pydantic import BaseModel

    from puzzle.model.schema import Quantity

    if isinstance(obj, Quantity):
        if obj.parameter:
            yield path, obj
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from bound_quantities(getattr(obj, name), f"{path}.{name}" if path else name)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from bound_quantities(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            yield from bound_quantities(v, f"{path}.{i}" if path else str(i))


def _with_values(obj, values: dict, owner: str, failed: dict | None = None):
    """`obj` with every parameter-bound quantity's value put in (SI, in its own unit)."""
    from pydantic import BaseModel

    from puzzle.model.schema import Quantity
    from puzzle.model.units import to_si

    if isinstance(obj, Quantity):
        if not obj.parameter:
            return obj
        if failed and obj.parameter in failed:  # OP-1: said as what it is
            raise PuzzleError(f"{owner}: a value follows parameter '{obj.parameter}', which "
                              f"can't be worked out: {failed[obj.parameter].message}",
                              code="operator_failed")  # fmt: skip
        if obj.parameter not in values:
            raise PuzzleError(f"{owner}: a value follows parameter '{obj.parameter}', which "
                              "doesn't exist (a parameter set's value, or a Number, Slider "
                              "or Expression by its name)", code="inconsistent")  # fmt: skip
        per_unit = to_si(1.0, obj.unit) if obj.unit != "dimensionless" else 1.0
        return obj.model_copy(update={"value": values[obj.parameter] / per_unit})
    if isinstance(obj, BaseModel):
        changed = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _with_values(old, values, owner, failed)
            if new is not old:
                changed[name] = new
        return obj.model_copy(update=changed) if changed else obj
    if isinstance(obj, dict):
        out = {k: _with_values(v, values, owner, failed) for k, v in obj.items()}
        return out if any(out[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        out = [_with_values(v, values, owner, failed) for v in obj]
        return out if any(a is not b for a, b in zip(out, obj, strict=True)) else obj
    return obj


def with_parameters(project):
    """The project with every quantity that names a parameter given that parameter's
    value now (ADR 0071: numbers wired into loads, supports, mesh sizes, settings); the
    project passed in is never changed. A name that isn't a parameter is refused."""
    bound = [(oid, obj) for oid, obj in project.objects.items()
             if next(bound_quantities(obj), None) is not None]  # fmt: skip
    if not bound:
        return project
    import copy

    failed: dict[str, PuzzleError] = {}
    values = parameter_values(project, failed=failed)
    out = copy.copy(project)
    out.objects = dict(project.objects)
    for oid, obj in bound:
        out.objects[oid] = _with_values(obj, values, f"{obj.kind} '{obj.name}'", failed)
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


def rename_references(project, old: str, new: str) -> list[str]:
    """A parameter renamed (a Number, Slider or other pre-solve operator, ADR 0062): every
    place naming it by its text follows. History arguments equal to it (and a sketch's
    dimensions), sweeps' and optimisations' variables, and the name as a word in
    expressions and derived-result equations. Edits `project` in place; returns the
    objects changed, by name."""
    import re

    from puzzle.geometry.history import WORDS
    from puzzle.model.schema import Geometry, Operator, Optimisation, ResultDefinition, Study

    word = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w(])") if old.isidentifier() else None
    changed = []

    def walk(v, key=""):
        if key in WORDS and key not in ("profile", "sketch", "positions"):  # other things
            return v
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return new if v == old else v

    def text(s):
        return word.sub(new, s) if word and isinstance(s, str) else s

    for obj in list(project.objects.values()):
        data, before = obj.model_dump(mode="json"), None
        if isinstance(obj, Geometry):
            before = data["history"]
            data["history"] = [s | {"args": walk(s["args"])} for s in before]
        elif isinstance(obj, Study):
            before = data["steps"]
            data["steps"] = [s | ({"parameters": [p | {"name": new if p["name"] == old else
                                                      p["name"]} for p in s["parameters"]]}
                                  if s.get("parameters") else {}) for s in before]  # fmt: skip
        elif isinstance(obj, Optimisation):
            before = data["variables"]
            data["variables"] = [v | {"name": new if v["name"] == old else v["name"]}
                                 for v in before]  # fmt: skip
        elif isinstance(obj, (Operator, ResultDefinition)) and obj.expression:
            before = data["expression"]
            data["expression"] = text(before)
        else:
            continue
        key = {Geometry: "history", Study: "steps", Optimisation: "variables"}.get(
            type(obj), "expression"
        )
        if data[key] != before:
            project.objects[obj.id] = type(obj).model_validate(data)
            changed.append(obj.name)
    return changed
