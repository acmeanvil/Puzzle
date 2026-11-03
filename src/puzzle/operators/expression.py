"""The Expression operator's formulas (ADR 0062 item 4): the derived results' small
language (ADR 0025: parsed with `ast` and walked, never `eval`), over quantities, so
units are checked as it is worked out. "2 * x + 5 mm" adds a length to a length; adding
a pressure to a length is refused.

A unit is written after a number ("5 mm", "200 MPa") or as a name ("x / mm"). Names are
x, y, z (the inputs), the project's parameters, pi and e, and any unit pint knows.
"""

from __future__ import annotations

import ast
import re

import numpy as np

from puzzle.errors import PuzzleError

# "5 mm" → "5*mm": a number followed by a unit name.
_UNIT_AFTER = re.compile(r"(?<![\w.])(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\s+([A-Za-zµ°Ω][\w]*)")


def _prepared(text: str) -> str:
    return _UNIT_AFTER.sub(r"(\1*\2)", text.replace("^", "**"))


def evaluate(expression: str, values: dict[str, tuple]) -> tuple[np.ndarray | float, str | None]:
    """The formula's value (SI) and its kind of unit (model.units.DIMENSIONS, None if
    dimensionless). `values` are name -> (SI value or array, kind)."""
    from pint import DimensionalityError, UndefinedUnitError

    from puzzle.model.units import DIMENSIONS, kind_of, registry
    from puzzle.post.derived import _BINARY, _COMPARE, CONSTANTS, FUNCTIONS, parse

    if not expression.strip():
        raise PuzzleError("the expression is empty", code="inconsistent")
    ureg = registry()
    tree = parse(_prepared(expression))

    def q(value, kind):
        unit = DIMENSIONS[kind][1] if kind in DIMENSIONS else "dimensionless"
        return ureg.Quantity(np.asarray(value, float) if np.ndim(value) else float(value), unit)

    scope = {k: q(*v) for k, v in values.items()}
    scope |= {k: ureg.Quantity(v, "dimensionless") for k, v in CONSTANTS.items()}

    def name(n: str):
        if n in scope:
            return scope[n]
        try:
            return ureg.Quantity(1.0, n)
        except (UndefinedUnitError, AttributeError, ValueError, TypeError):
            import difflib

            near = difflib.get_close_matches(n, list(values), n=1)
            hint = f"; did you mean {near[0]}?" if near else ""
            raise PuzzleError(f"no input, parameter or unit '{n}'{hint}",
                              code="not_found") from None  # fmt: skip

    def call(fn: str, args):
        if fn in ("min", "max"):
            pick = np.nanmin if fn == "min" else np.nanmax
            if len(args) == 1:
                return ureg.Quantity(pick(np.asarray(args[0].magnitude)), args[0].units)
            out = args[0]
            for a in args[1:]:
                b = a.to(out.units).magnitude
                out = ureg.Quantity((np.minimum if fn == "min" else np.maximum)(
                    out.magnitude, b), out.units)  # fmt: skip
            return out
        if fn == "where":
            c, a, b = args
            return ureg.Quantity(np.where(np.asarray(c), a.magnitude, b.to(a.units).magnitude),
                                 a.units)  # fmt: skip
        if fn in ("exp", "log", "log10", "sin", "cos", "tan"):
            x = args[0].to("dimensionless").magnitude  # refuses a unit
            return ureg.Quantity(FUNCTIONS[fn](x), "dimensionless")
        return FUNCTIONS[fn](*args)

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return ureg.Quantity(float(node.value), "dimensionless")
        if isinstance(node, ast.Name):
            return name(node.id)
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow):
                return left ** right.to("dimensionless").magnitude
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            v = walk(node.operand)
            if isinstance(node.op, ast.Not):
                return np.logical_not(v)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Compare):
            left, out = walk(node.left), True
            for op, right in zip(node.ops, node.comparators, strict=True):
                r = walk(right)
                out = np.logical_and(out, _COMPARE[type(op)](left, r))
                left = r
            return out
        if isinstance(node, ast.BoolOp):
            fn = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            out = walk(node.values[0])
            for v in node.values[1:]:
                out = fn(out, walk(v))
            return out
        if isinstance(node, ast.Call):
            return call(node.func.id, [walk(a) for a in node.args])
        raise PuzzleError(f"can't work out {type(node).__name__}", code="invalid")

    try:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            got = walk(tree)
    except DimensionalityError as exc:
        raise PuzzleError(f"the units don't agree: {_words(exc)}", code="invalid") from None
    if not hasattr(got, "magnitude"):  # a comparison: true or false
        return np.asarray(got, float) if np.ndim(got) else float(bool(got)), None
    base = got.to_base_units()
    unit = f"{base.units}"
    kind = None if base.dimensionless else kind_of(unit)
    if not base.dimensionless and kind is None:
        raise PuzzleError(f"the result's unit ({base.units:~P}) is not one Puzzle shows; "
                          "divide by a unit to make it one", code="invalid")  # fmt: skip
    magnitude = base.to(DIMENSIONS[kind][1]).magnitude if kind else base.magnitude
    return (np.asarray(magnitude, float) if np.ndim(magnitude) else float(magnitude)), kind


def _words(exc) -> str:
    a = getattr(exc, "dim1", "") or ""
    b = getattr(exc, "dim2", "") or ""
    if a and b:
        return f"{_dim(a)} and {_dim(b)} can't be added, compared or converted"
    return str(exc)


def _dim(d) -> str:
    text = str(d).strip("[]")
    return "a number" if not text or text == "dimensionless" else text.replace("[", "").replace(
        "]", "")  # fmt: skip
