"""Model value (ADR 0071 step 3): a value the model holds, read as an operator's output.

Any object's quantities can be read: a material's properties (yield and ultimate
strength, Young's modulus, density ...) and its temperature tables, a load's or support's
values, a section's thickness, a mesh recipe's sizes, a step's settings, a parameter.
`readable(project, obj)` lists them, each by a dotted path and a label in the tree's
words; `read(project, obj, path)` gives a number (SI, with its quantity kind) or, for a
temperature table, a two-column table. The list is found by walking the object, so a
quantity added to the schema later is readable without a change here.

It reads no results: a pre-solve operator, so it can feed checks, formulas and plots,
and drive the model as a parameter does.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from puzzle.errors import PuzzleError

SKIP = {"id", "tags", "history", "selections", "sources", "info", "fatigue", "solver_keywords"}


@dataclass(frozen=True)
class Readable:
    path: str  # "properties.yield_strength", "physics.0.features.1.values.p"
    label: str  # "Yield strength", "Pressure on Bracket/Inner: p"
    kind: str | None  # the quantity kind (model.units.DIMENSIONS), None for a table
    table: bool = False  # a temperature-dependent property: a table, not a number


def _words(text: str) -> str:
    return str(text).replace("_", " ").capitalize()


def _item(name: str, item) -> str:
    """A list item's name in labels: a load by its type and where, else its name."""
    if name == "features":
        return f"{_words(getattr(item, 'type', ''))} on {getattr(item, 'selection', '')}"
    for key in ("name", "selection", "type", "kind"):
        value = getattr(item, key, None)
        if value:
            return str(value) if key in ("name", "selection") else _words(value)
    return ""


def _walk(value, path: str, label: str, out: list[Readable], material: bool = False):
    from puzzle.model.schema import PropertyTable, Quantity
    from puzzle.model.units import kind_of

    if isinstance(value, Quantity):
        out.append(Readable(path, label, kind_of(value.unit)))
    elif isinstance(value, PropertyTable):
        out.append(Readable(path, f"{label} against temperature", None, table=True))
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            if name in SKIP:
                continue
            child = getattr(value, name)
            # a container's name is no part of a label (its items name themselves):
            # "Pressure on Bracket/Inner: p", not "Physics: Features: ...: Values: p"
            words = "" if isinstance(child, list | dict) else _words(name)
            _walk(child, f"{path}.{name}" if path else name,
                  ": ".join(w for w in (label, words) if w), out, material)  # fmt: skip
    elif isinstance(value, dict):
        from puzzle.materials.registry import label as property_label

        for key, child in value.items():
            words = property_label(key) if material else str(key)
            _walk(child, f"{path}.{key}", ": ".join(w for w in (label, words) if w), out,
                  material)  # fmt: skip
    elif isinstance(value, list):
        name = path.rsplit(".", 1)[-1]
        for i, child in enumerate(value):
            words = _item(name, child) or f"{_words(name)} {i + 1}"
            here = label if name == "physics" else ": ".join(w for w in (label, words) if w)
            if name == "physics":  # one physics is no part of a load's name
                here = label
            _walk(child, f"{path}.{i}", here, out, material)


def readable(project, obj) -> list[Readable]:
    """Every value `obj` holds that a Model value can read, in the tree's words."""
    from puzzle.model.schema import Material

    found: list[Readable] = []
    _walk(obj, "", "", found, material=isinstance(obj, Material))
    return [r for r in found if r.label]


def _at(obj, path: str):
    value = obj
    for part in path.split("."):
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            value = getattr(value, part)
    return value


def read(project, obj, path: str, label: str = ""):
    """The value at `path`: a Number (SI) or, for a temperature table, a Table of
    temperature and the property."""
    from puzzle.model.schema import PropertyTable, Quantity
    from puzzle.model.units import kind_of, to_si
    from puzzle.operators.evaluate import Number, Table

    try:
        value = _at(obj, path)
    except (AttributeError, KeyError, IndexError, ValueError):
        known = [r.label for r in readable(project, obj)][:12]
        raise PuzzleError(f"{obj.name} has no value '{path}'", code="not_found",
                          details={"readable": known}) from None  # fmt: skip
    name = label or next((r.label for r in readable(project, obj) if r.path == path), path)
    if isinstance(value, Quantity):
        return Number(float(value.to_si()), kind_of(value.unit), name)
    if isinstance(value, PropertyTable):
        rows = [(t, to_si(v, value.unit)) for t, v in value.curve()]
        return Table(["temperature", name], rows, ["temperature", kind_of(value.unit)])
    raise PuzzleError(f"{obj.name}'s '{path}' isn't a value with a unit", code="invalid")
