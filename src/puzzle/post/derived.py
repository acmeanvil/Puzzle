"""Derived results (ADR 0025): combinations of load cases, and custom equations.

A `ResultDefinition` is evaluated from stored runs whenever it is shown or queried;
nothing is stored. `evaluate` finds each case's run (the study's newest, or a sweep
point's), puts every field on the first case's mesh, and combines:

- linear: sum of coefficient × field, component-wise (so von Mises and the principal
  stresses are taken after combining, as Ansys does);
- srss: square root of the sum of the squares, component-wise;
- envelope: the largest (or smallest) of one listed result over the cases, and which
  case gave it;
- range: for two cases, case 1 − case 2 (signed, component-wise, as a stress range is
  for fatigue), else the maximum minus the minimum; and the mean.

An equation is an expression over the results of a study's run or of another derived
result, evaluated per node (or element) on numpy arrays. It is parsed with `ast` and
walked here, never passed to `eval`: names, numbers, + - * / ** %, comparisons, `and`,
`or`, `not`, and the functions in FUNCTIONS; nothing else.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.post.quantities import Item, catalog, compute
from puzzle.results.model import Field, Results, StepResult

COMBINATIONS = {
    "linear": "Linear combination",
    "srss": "Square root of the sum of squares (SRSS)",
    "envelope": "Envelope (maximum or minimum)",
    "range": "Range and mean",
}
METHODS = {**COMBINATIONS, "equation": "Equation"}


def _reduce_or_pairwise(fn_reduce, fn_pair):
    def f(*args):
        if len(args) == 1:
            return np.full_like(np.asarray(args[0], float), fn_reduce(args[0]))
        out = args[0]
        for a in args[1:]:
            out = fn_pair(out, a)
        return out

    return f


FUNCTIONS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    # One argument: the extreme over the model; several: the extreme at each node.
    "min": _reduce_or_pairwise(np.nanmin, np.minimum),
    "max": _reduce_or_pairwise(np.nanmax, np.maximum),
    "where": lambda c, a, b: np.where(c, a, b),
}
CONSTANTS = {"pi": np.pi, "e": np.e}
_BINARY = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.divide,
    ast.Pow: np.power,
    ast.Mod: np.mod,
}
_COMPARE = {
    ast.Lt: np.less,
    ast.LtE: np.less_equal,
    ast.Gt: np.greater,
    ast.GtE: np.greater_equal,
    ast.Eq: np.equal,
    ast.NotEq: np.not_equal,
}


@dataclass
class Derived:
    """An evaluated derived result: shown and queried like a run's results."""

    mesh: Mesh
    results: Results
    items: list[Item]
    run_ids: list[str]
    warnings: list[str] = field(default_factory=list)


# --- names in equations --------------------------------------------------------------

ALIASES = {
    "displacement:total": "deformation",
    "displacement:x": "deformation_x",
    "displacement:y": "deformation_y",
    "displacement:z": "deformation_z",
    "stress:von_mises": "von_mises",
    "stress:max_shear": "max_shear",
}


def name_of(item: Item) -> str:
    """How an equation names a listed result: `von_mises`, `deformation_y`,
    `temperature`, `stress_xx`, `stress_p1`, `stress_range_von_mises`."""
    if item.key in ALIASES:
        return ALIASES[item.key]
    if item.what in ("value", "total"):
        return item.field
    return f"{item.field}_{item.what}"


def names(items: list[Item]) -> dict[str, Item]:
    return {name_of(i): i for i in items}


def parse(expression: str) -> ast.Expression:
    """The expression's tree, refusing anything outside the small language."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise PuzzleError(f"the equation doesn't read: {exc.msg}", code="invalid") from exc
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Call,
        ast.Name,
        ast.Constant,
        ast.Load,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        *_BINARY,
        *_COMPARE,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise PuzzleError(
                f"equations can't use {type(node).__name__.lower()}: use names, numbers, "
                "+ - * / ** %, comparisons and the listed functions",
                code="invalid",
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise PuzzleError("equations take numbers only, not text", code="invalid")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                what = node.func.id if isinstance(node.func, ast.Name) else "that"
                raise PuzzleError(
                    f"no function '{what}'; the functions are {', '.join(FUNCTIONS)}",
                    code="invalid",
                )
            if node.keywords:
                raise PuzzleError("functions take plain arguments, not name=value", code="invalid")
    return tree


def used_names(expression: str) -> set[str]:
    tree = parse(expression)
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in calls}


def check(expression: str, known: list[str]) -> None:
    """Refuse unknown names, suggesting the nearest ("did you mean von_mises?")."""
    for name in sorted(used_names(expression)):
        if name in known or name in CONSTANTS:
            continue
        near = difflib.get_close_matches(name, known, n=1)
        hint = f"; did you mean {near[0]}?" if near else ""
        raise PuzzleError(f"no result or parameter '{name}'{hint}", code="not_found")


def evaluate_expression(expression: str, values: dict[str, np.ndarray | float]) -> np.ndarray:
    tree = parse(expression)
    check(expression, list(values))
    scope = {**CONSTANTS, **values}

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return scope[node.id]
        if isinstance(node, ast.BinOp):
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp):
            v = walk(node.operand)
            return (
                np.logical_not(v)
                if isinstance(node.op, ast.Not)
                else (-v if isinstance(node.op, ast.USub) else v)
            )
        if isinstance(node, ast.BoolOp):
            fn = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            out = walk(node.values[0])
            for v in node.values[1:]:
                out = fn(out, walk(v))
            return out
        if isinstance(node, ast.Compare):
            left, out = walk(node.left), True
            for op, right in zip(node.ops, node.comparators, strict=True):
                r = walk(right)
                out = np.logical_and(out, _COMPARE[type(op)](left, r))
                left = r
            return out
        if isinstance(node, ast.Call):
            return FUNCTIONS[node.func.id](*(walk(a) for a in node.args))
        raise PuzzleError(f"can't evaluate {type(node).__name__}", code="invalid")

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return np.asarray(walk(tree), float)


# --- combining ----------------------------------------------------------------------


def _items_of(results: Results) -> list[Item]:
    fields: dict = {}
    for s in results.steps:
        for f in s.fields.values():
            fields.setdefault(f.name, (f.name, f.location, list(f.components)))
    return catalog(list(fields.values()))


def _step(results: Results, index: int) -> StepResult:
    if not results.steps:
        raise PuzzleError("the run has no result steps", code="not_found")
    try:
        return results.steps[index]
    except IndexError:
        raise PuzzleError(
            f"no result step {index + 1}: the run has {len(results.steps)}", code="not_found"
        ) from None


def _aligned(cases, warnings: list[str]) -> tuple[Mesh, dict[str, list[tuple[Field, np.ndarray]]]]:
    """Every field all the cases have, on the first case's mesh: name -> per case
    (the field as a template, its values in the first case's order)."""
    from puzzle.transfer import map_field

    mesh0, step0 = cases[0][0], cases[0][1]
    out: dict[str, list] = {}
    for name, f0 in step0.fields.items():
        if f0.location == "global":
            continue
        if any(name not in step.fields for _, step, _ in cases[1:]):
            warnings.append(f"'{name}' is not in every case; left out")
            continue
        rows = []
        for mesh, step, _ in cases:
            f = step.fields[name]
            if f.location == "node":
                rows.append((f0, map_field(f, mesh, mesh0).values))
            elif len(f.ids) == len(f0.ids) and np.array_equal(np.sort(f.ids), np.sort(f0.ids)):
                rows.append((f0, f.at(f0.ids)))
            else:
                rows = None
                warnings.append(f"'{name}' is per element on different meshes; left out")
                break
        if rows:
            out[name] = rows
    return mesh0, out


def _ids(f0: Field, mesh0: Mesh) -> np.ndarray:
    return mesh0.node_ids.copy() if f0.location == "node" else f0.ids.copy()


def combine(method: str, cases, quantity: str | None = None, extreme: str = "max"):
    """cases: (mesh, step, coefficient) per case → (mesh, Results, items, warnings)."""
    warnings: list[str] = []
    mesh0, fields = _aligned(cases, warnings)
    coefs = [c for _, _, c in cases]
    out: dict[str, Field] = {}
    items: list[Item] | None = None
    if method in ("linear", "srss"):
        for name, rows in fields.items():
            f0 = rows[0][0]
            scaled = [c * np.asarray(v, float) for (_, v), c in zip(rows, coefs, strict=True)]
            total = sum(scaled) if method == "linear" else np.sqrt(sum(s**2 for s in scaled))
            out[name] = Field(name, f0.location, f0.components, _ids(f0, mesh0), total)
    elif method == "range":
        for name, rows in fields.items():
            f0 = rows[0][0]
            scaled = np.stack(
                [c * np.asarray(v, float) for (_, v), c in zip(rows, coefs, strict=True)]
            )
            if len(scaled) == 2:
                rng, mean = scaled[0] - scaled[1], 0.5 * (scaled[0] + scaled[1])
            else:
                hi, lo = scaled.max(axis=0), scaled.min(axis=0)
                rng, mean = hi - lo, 0.5 * (hi + lo)
            for word, v in (("range", rng), ("mean", mean)):
                out[f"{name}_{word}"] = Field(
                    f"{name}_{word}", f0.location, f0.components, _ids(f0, mesh0), v
                )
    elif method == "envelope":
        listed = {
            i.key: i
            for i in catalog([(n, r[0][0].location, r[0][0].components) for n, r in fields.items()])
        }
        item = listed.get(quantity or "")
        if item is None:
            raise PuzzleError(
                f"the cases have no result '{quantity}' in common",
                code="not_found",
                details={"results": sorted(listed)},
            )
        rows = fields[item.field]
        f0 = rows[0][0]
        per = np.stack(
            [
                compute(item, c * np.asarray(v, float), f0.components)
                for (_, v), c in zip(rows, coefs, strict=True)
            ]
        )
        pick = np.nanargmax(per, axis=0) if extreme == "max" else np.nanargmin(per, axis=0)
        value = per[pick, np.arange(per.shape[1])]
        ids = _ids(f0, mesh0)
        out["envelope"] = Field("envelope", f0.location, [""], ids, value[:, None])
        out["envelope_case"] = Field("envelope_case", f0.location, [""], ids, (pick + 1.0)[:, None])
        word = "Maximum" if extreme == "max" else "Minimum"
        items = [
            Item(
                "envelope:value",
                f"{word} {item.label[:1].lower()}{item.label[1:]} over the cases",
                "envelope",
                "value",
                "Envelope",
                item.unit,
            ),
            Item(
                "envelope_case:value", "Case giving it", "envelope_case", "value", "Envelope", None
            ),
        ]
    else:
        raise PuzzleError(f"no combination '{method}'", code="invalid")
    results = Results("derived", [StepResult(1, "combination", 0.0, fields=out)])
    return mesh0, results, items if items is not None else _items_of(results), warnings


# --- evaluating a definition from the store ----------------------------------------------

_CACHE: dict[str, Derived] = {}
_CACHE_SIZE = 16


def run_for(project, store, study_id: UUID, point: str | None = None):
    """The run a case reads: the study's newest (with that sweep point, if given)."""
    from puzzle.sweep import title

    study = project.objects.get(study_id)
    name = study.name if study is not None else str(study_id)
    for r in reversed(store.runs()):  # newest first
        if r.study_id != str(study_id) or r.status != "completed":
            continue
        labels = r.point_labels()
        if point is None or (labels and title(labels) == point):
            return r
    what = f" for {point}" if point else ""
    raise PuzzleError(f"study '{name}' has no run{what} yet: run it first", code="not_found")


def _nonlinear(results: Results) -> bool:
    return any("plastic_strain_equivalent" in s.fields for s in results.steps)


def evaluate(project, store, definition, _seen: tuple = ()) -> Derived:
    """The definition's result, from the stored runs it reads (cached by their ids)."""
    if definition.id in _seen:
        raise PuzzleError(
            f"derived result '{definition.name}' depends on itself", code="inconsistent"
        )
    seen = (*_seen, definition.id)
    if definition.method == "equation":
        return _equation(project, store, definition, seen)
    records = [run_for(project, store, c.study, c.point) for c in definition.cases]
    key = _key(definition, [r.run_id for r in records])
    if key in _CACHE:
        return _CACHE[key]
    cases, warnings = [], []
    for case, record in zip(definition.cases, records, strict=True):
        results = store.read_results(record.run_id)
        if definition.method == "linear" and _nonlinear(results):
            warnings.append(
                f"{record.study_name} is a nonlinear (plastic) result: a linear combination "
                "of it is only an estimate"
            )
        cases.append((store.read_mesh(record.mesh_id), _step(results, case.step), case.coefficient))
    mesh, results, items, more = combine(
        definition.method, cases, definition.quantity, definition.extreme
    )
    return _remember(
        key, Derived(mesh, results, items, [r.run_id for r in records], warnings + more)
    )


def source_of(project, store, definition, seen: tuple = ()):
    """What an equation reads: (mesh, results, items, run ids)."""
    from puzzle.model.schema import ResultDefinition

    src = project.objects.get(definition.source)
    if isinstance(src, ResultDefinition):
        d = evaluate(project, store, src, seen)
        return d.mesh, d.results, d.items, d.run_ids
    if src is None:
        raise PuzzleError(
            f"derived result '{definition.name}': its source is gone", code="not_found"
        )
    record = run_for(project, store, src.id, definition.source_point)
    results = store.read_results(record.run_id)
    return store.read_mesh(record.mesh_id), results, _items_of(results), [record.run_id]


def parameters(project) -> dict[str, float]:
    from puzzle.model.schema import ParameterSet

    out = {}
    for ps in project.of_type(ParameterSet):
        for name, q in ps.values.items():
            out.setdefault(name, q.to_si())
    return out


def _equation(project, store, definition, seen) -> Derived:
    mesh, results, items, run_ids = source_of(project, store, definition, seen)
    key = _key(definition, run_ids)
    if key in _CACHE:
        return _CACHE[key]
    step = _step(results, definition.source_step)
    by_name = names(items)
    params = parameters(project)
    check(definition.expression, [*by_name, *params])
    used = used_names(definition.expression)
    wanted = [by_name[n] for n in sorted(used) if n in by_name]
    locations = {step.fields[i.field].location for i in wanted if i.field in step.fields}
    if len(locations) > 1:
        raise PuzzleError(
            "the equation mixes results per node and per element; use one kind",
            code="invalid",
        )
    location = locations.pop() if locations else "node"
    if location == "node":
        ids = mesh.node_ids.copy()
    else:
        ids = next(step.fields[i.field].ids for i in wanted)
    values: dict[str, np.ndarray | float] = dict(params)
    for i in wanted:
        f = step.fields.get(i.field)
        if f is None:
            raise PuzzleError(f"'{name_of(i)}' is not in that result step", code="not_found")
        values[name_of(i)] = compute(i, f.at(ids), f.components)
    v = evaluate_expression(definition.expression, values)
    v = np.broadcast_to(v, (len(ids),)).astype(float)
    out = Field("equation", location, [""], ids, v[:, None])
    res = Results(
        "derived", [StepResult(1, step.kind, step.value, mode=step.mode, fields={"equation": out})]
    )
    item = Item(
        "equation:value", definition.name, "equation", "value", definition.name, definition.unit
    )
    return _remember(key, Derived(mesh, res, [item], run_ids))


def _key(definition, run_ids: list[str]) -> str:
    text = json.dumps([definition.model_dump(mode="json"), run_ids], sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _remember(key: str, d: Derived) -> Derived:
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = d
    return d
