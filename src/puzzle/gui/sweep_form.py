"""Add sweep step… (ADR 0018, ADR 0072 section 10): a study's parametric sweep in one form.

Each of the project's parameters (the parameter sets' values and the pre-solve number
operators, by name) is not swept, or takes a list of values, a range (start, end, how
many) or the list a list operator gives (`SweepParameter.source`); every combination or
in pairs. The point count shows as the values change. OK submits one Edit of the study's
steps (`sweep.with_sweep_step`), so it undoes in one step; a sweep step's Edit sweep…
reopens the form on it. Material choices at a location are the study's Materials…, and
an edited step keeps them.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import QLabel

from puzzle.gui.agent_prefill import add_ask_button
from puzzle.gui.live_form import LiveForm, quantity
from puzzle.gui.quick_form import Ask
from puzzle.gui.theme import role

COMBINATIONS = [("Every combination", "grid"), ("In pairs (first with first, …)", "paired")]


def parameter_units(project) -> dict[str, str]:
    """Each parameter's name -> the unit it is given in ("dimensionless" for operators)."""
    from puzzle.model.schema import ParameterSet

    out = {n: "dimensionless" for n in project.parameter_names()}
    for ps in project.of_type(ParameterSet):
        out.update({k: q.unit for k, q in ps.values.items()})
    return out


def list_operators(project) -> list[tuple[str, str]]:
    """Pre-solve operators whose output is a list: a swept parameter's values from one."""
    from puzzle import operators

    out = []
    for op in operators.pre_solve(project):
        try:
            if operators.first_type(op, project) == "list":
                out.append((op.name, str(op.id)))
        except Exception:  # noqa: BLE001 - an operator not wired up yet: not offered
            continue
    return sorted(out)


def _values(text: str, unit: str) -> list[dict]:
    """ "2, 4, 6 mm" or "2 mm, 4 mm": the last unit given covers the bare numbers."""
    from puzzle.gui.properties import parse_list
    from puzzle.model.units import parse_quantity

    items = parse_list(text or "")
    if not items:
        raise ValueError("list the values, comma separated")
    kind = "any" if unit == "dimensionless" else None  # an operator's: any unit it reads
    given = [parse_quantity(i, unit, kind)[1] for i in items]
    typed = [g for i, g in zip(items, given, strict=True) if not i.strip()[-1:].isdigit()]
    last = typed[-1] if typed else unit
    return [quantity(i, last, kind) for i in items]


def _range(start: str, end: str, count: str, unit: str) -> list[dict]:
    from puzzle.model.units import convert

    kind = "any" if unit == "dimensionless" else None
    a, b = quantity(start, unit, kind), quantity(end, unit, kind)
    try:
        n = int(count)
    except ValueError:
        raise ValueError("how many values: a whole number") from None
    if n < 2:
        raise ValueError("a range needs at least two values")
    stop = convert(b["value"], b["unit"], a["unit"])
    return [{"value": a["value"] + (stop - a["value"]) * k / (n - 1), "unit": a["unit"]}
            for k in range(n)]  # fmt: skip


def parameters(values: dict, units: dict[str, str]) -> list[dict]:
    """The form's values as the sweep step's parameters (SweepParameter dicts)."""
    out = []
    for name in sorted(units):
        how = values.get(f"how:{name}", "none")
        if how == "list":
            out.append({"name": name, "values": _values(values.get(f"values:{name}"),
                                                        units[name])})  # fmt: skip
        elif how == "range":
            out.append({"name": name, "values": _range(
                values.get(f"start:{name}", ""), values.get(f"end:{name}", ""),
                values.get(f"count:{name}", ""), units[name])})  # fmt: skip
        elif how == "operator":
            out.append({"name": name, "source": values[f"source:{name}"], "port": "out"})
    return out


def _count(project, params: list[dict], combination: str) -> int | None:
    """How many points the step makes (None: an operator's list can't be read now)."""
    from uuid import UUID

    from puzzle.operators import evaluate as ev

    lengths = []
    for p in params:
        if "source" in p:
            try:
                got = ev.source(ev.Context(project, None), UUID(p["source"]), "out")
                lengths.append(len(ev.as_list(got).values))
            except Exception:  # noqa: BLE001 - unreadable yet: the count is unknown
                return None
        else:
            lengths.append(len(p["values"]))
    if combination == "paired":
        return lengths[0] if len(set(lengths)) == 1 else None
    return math.prod(lengths)


def _number(value: float) -> str:
    """Every digit a value has, shortest (repr): read back, it is the same float, so a
    sweep reopened and accepted keeps its points' run keys (PANEL-9)."""
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def _text(values: list) -> str:
    """A parameter's values as typed back: "2, 4, 6 mm"."""
    units = {v.unit for v in values}
    if len(units) == 1:
        unit = next(iter(units))
        tail = "" if unit == "dimensionless" else f" {unit}"
        return ", ".join(_number(v.value) for v in values) + tail
    return ", ".join(f"{_number(v.value)} {v.unit}" for v in values)


def sweep_step(window, study, index: int | None = None):
    """The Add sweep step… form (or, with `index`, the edit of that sweep step)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Study, SweepStep

    project = window.session.project
    study = project.get(study.id, Study)
    units = parameter_units(project)
    if not units:
        window.statusBar().showMessage(
            "A sweep varies parameters: add a parameter set (Parameters → New parameter set) "
            "or a Number operator first; materials are swept from the study's Materials…",
            10000)  # fmt: skip
        return None
    old = study.steps[index] if index is not None else None
    if old is not None and not isinstance(old, SweepStep):
        return None
    given = {p.name: p for p in (old.parameters if old else []) if p.location is None}
    ops = list_operators(project)
    hows = [("Not swept", "none"), ("A list of values", "list"), ("A range", "range")]
    if ops:
        hows.append(("From a list operator", "operator"))
    asks = []
    missing: dict[str, str] = {}  # a stored source no longer a list operator (PANEL-20)
    for name in sorted(units):
        p = given.get(name)
        how = "none" if p is None else ("operator" if p.source else "list")
        offered = list(ops)
        if p is not None and p.source and str(p.source) not in {v for _, v in ops}:
            missing[name] = str(p.source)
            offered.append(("An operator no longer offered (deleted, or not a list)",
                            str(p.source)))  # fmt: skip
        name_hows = hows if not offered or ops else [*hows, ("From a list operator", "operator")]
        shown_unit = "" if units[name] == "dimensionless" else f" {units[name]}"
        asks += [
            Ask(f"how:{name}", name, choices=name_hows, default=how),
            Ask(f"values:{name}", f"{name}: values", kind="text",
                default=_text(p.values) if p is not None and not p.source else "",
                shown=lambda v, n=name: v.get(f"how:{n}") == "list",
                help=f"Comma separated, with a unit: 2, 4, 6{shown_unit or ' mm'}"),
            Ask(f"start:{name}", f"{name}: from", kind="text", default="",
                shown=lambda v, n=name: v.get(f"how:{n}") == "range"),
            Ask(f"end:{name}", f"{name}: to", kind="text", default="",
                shown=lambda v, n=name: v.get(f"how:{n}") == "range"),
            Ask(f"count:{name}", f"{name}: how many values", kind="text", default="5",
                shown=lambda v, n=name: v.get(f"how:{n}") == "range"),
        ]  # fmt: skip
        if offered:
            asks.append(Ask(f"source:{name}", f"{name}: operator", choices=offered,
                            default=str(p.source) if p is not None and p.source else None,
                            shown=lambda v, n=name: v.get(f"how:{n}") == "operator"))  # fmt: skip
    asks.append(Ask("combination", "Combine", choices=COMBINATIONS,
                    default=old.combination if old else "grid"))  # fmt: skip
    kept = [p for p in (old.parameters if old else []) if p.location is not None]

    def steps_for(v) -> list[dict]:
        return sweep_steps(study, parameters(v, units), v.get("combination", "grid"), index)

    def check(v):
        for name, source in missing.items():
            if v.get(f"how:{name}") == "operator" and v.get(f"source:{name}") == source:
                return (f"{name}'s list operator is gone or no longer gives a list: choose "
                        "another, or another way to sweep it")  # fmt: skip
        try:
            params = parameters(v, units)
        except (ValueError, KeyError) as exc:
            return str(exc).strip("'\"")
        if not params and not kept:
            return "sweep at least one parameter"
        try:
            steps_for(v)
        except ValueError as exc:
            return str(exc).splitlines()[-1] if "\n" in str(exc) else str(exc)
        return None

    count = role(QLabel(""), "muted")

    def show(v):
        if check(v):
            count.setText("")
            return
        params = parameters(v, units)
        n = _count(project, params, v.get("combination", "grid")) if params else 1
        other = math.prod(len(s.points()) for i, s in enumerate(study.steps)
                          if isinstance(s, SweepStep) and i != index)  # fmt: skip
        if kept:
            n = None if n is None else n * len(SweepStep(parameters=kept).points())
        count.setText("Points: as many as the operator gives" if n is None else
                      f"This step: {n} point{'s' if n != 1 else ''}; the study runs "
                      f"{n * other} time{'s' if n * other != 1 else ''}.")  # fmt: skip

    title = f"Edit sweep of {study.name}" if old else f"Add sweep step to {study.name}"
    dialog = LiveForm(window, title, asks, check=check, changed=show, below=count,
                      intro="Each solve step runs once per point: every combination of the "
                      "swept parameters' values, or the first values together, then the "
                      "second, ...", help_key="form:sweep_step")  # fmt: skip

    def prompt(v):
        described = [f"{p['name']} over {_described(p)}" for p in _safe(v, units)]
        return (f"Add a parametric sweep step to study {study.name}: "
                f"{'; '.join(described) or 'which parameters?'}, "
                f"{'in pairs' if v.get('combination') == 'paired' else 'every combination'}. "
                "Edit the study's steps (sweep steps come first).")  # fmt: skip

    add_ask_button(dialog, window, prompt)
    if not window.run_dialog(dialog) or dialog.problem():
        return None
    steps = steps_for(dialog.values())
    return window.submit(Edit(object=str(study.id),
                              ops=[EditOp(op="set", path="steps", value=steps)]))  # fmt: skip


def sweep_steps(study, params: list[dict], combination: str, index: int | None) -> list[dict]:
    """`sweep.with_sweep_step`, its pydantic refusal as a ValueError's last line."""
    from pydantic import ValidationError

    from puzzle import sweep

    try:
        return sweep.with_sweep_step(study, params, combination, index)
    except ValidationError as exc:
        raise ValueError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from None


def _safe(v, units) -> list[dict]:
    try:
        return parameters(v, units)
    except (ValueError, KeyError):
        return []


def _described(p: dict) -> str:
    if "source" in p:
        return "an operator's list"
    return ", ".join(f"{q['value']:g} {q['unit']}".replace(" dimensionless", "")
                     for q in p["values"])  # fmt: skip
