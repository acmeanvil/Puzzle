"""Result tools in the GUI (ADR 0042): reactions, results over a selection, paths.

Each tool is made from one form (`gui.quick_form`) and kept as a `ResultProbe` under
Results in the tree, read again for each run. The Results ribbon (shown with any result)
and the support features' menus lead here; `title` names a tool in the tree and `rows`
lists its values for the Properties panel, in display units.
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.gui.quick_form import Ask
from puzzle.model.schema import Component, ResultProbe, Study

REDUCTIONS = [
    ("Maximum", "max"),
    ("Minimum", "min"),
    ("Average (area or volume weighted)", "mean"),
    ("Integral (over the area or volume)", "integral"),
]
SUPPORTS = ("fixed", "displacement", "elastic_support", "compression_only_support")
KIND_NAMES = {0: "points", 1: "edges", 2: "faces", 3: "bodies"}


# --- what the tools read -----------------------------------------------------------------


def sources(window) -> list[tuple[str, str]]:
    """Studies with a completed run, as (name, id); the one shown first."""
    project = window.session.project
    done = {r.study_id for r in window._store().runs() if r.status == "completed"}
    out = [(s.name, str(s.id)) for s in project.of_type(Study) if str(s.id) in done]
    shown = current_source(window)
    out.sort(key=lambda p: p[1] != shown)
    return out


def study_of(window, comp) -> str | None:
    """A study of the component with a run (the one shown, else the newest), for a
    support's Reaction force…."""
    shown = current_source(window)
    ids = {str(s.id) for s in window.session.project.of_type(Study) if s.component == comp.id}
    if shown in ids:
        return shown
    for r in reversed(window._store().runs()):
        if r.study_id in ids and r.status == "completed":
            return r.study_id
    return None


def current_source(window) -> str | None:
    run_id = getattr(window, "_result_run", "") or ""
    if not run_id or run_id.startswith("derived:"):
        return None
    try:
        return window._store().get_run(run_id).study_id
    except Exception:  # noqa: BLE001 - a run gone from the store: no current source
        return None


def _run_of(window, source_id: str):
    """(mesh, results) of a study's newest run."""
    from puzzle.post import derived

    store = window._store()
    record = derived.run_for(window.session.project, store, _uuid(source_id))
    return store.read_mesh(record.mesh_id), store.read_results(record.run_id)


def selection_choices(window, source_id: str, supports_first: bool = False) -> list:
    """The run's selections as ("Bar/Root (faces)", "Bar/Root"); supports first and
    named by their feature when asked (reactions)."""
    if not source_id:
        return []
    mesh, _ = _run_of(window, source_id)
    study = window.session.project.objects.get(_uuid(source_id))
    comp = window.session.project.objects.get(study.component) if study else None
    supports = {}
    if isinstance(comp, Component):
        from puzzle.gui.tree import _feature_label

        for ph in comp.physics:
            for f in ph.features:
                if f.type in SUPPORTS:
                    supports.setdefault(f.selection, _feature_label(ph.type, f.type).lower())
    out = []
    for name, group in mesh.groups.items():
        text = f"{name} ({KIND_NAMES.get(group.dim, 'entities')})"
        if name in supports:
            text = f"{name}: {supports[name]}"
        out.append((text, name))
    if supports_first:
        out.sort(key=lambda p: p[1] not in supports)
    return out


def quantity_choices(window, source_id: str) -> list:
    if not source_id:
        return []
    from puzzle.post.derived import _items_of

    _, results = _run_of(window, source_id)
    return [(i.label, i.key) for i in _items_of(results) if i.field != "reaction_force"]


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(text)
    except (TypeError, ValueError):
        return None


def _point(text: str, per_metre: float):
    """ "x, y, z" in the display length unit -> metres, or None."""
    try:
        values = [float(v) / per_metre for v in text.replace(";", ",").split(",")]
    except ValueError:
        return None
    return tuple(values) if len(values) == 3 else None


def _shown(values, per_metre: float) -> str:
    return ", ".join(f"{v * per_metre:.6g}" for v in values)


# --- making them -------------------------------------------------------------------------


# ADR 0054: a probe can be followed while its study solves (the Solution panel).
TRACK = [("After the solve", False), ("While solving too (Solution panel)", True)]


def _trackable(quantity: str | None) -> bool:
    return (quantity or "").split(":")[0] in ("displacement", "temperature", "reaction_force")


def _create(window, data: dict):
    from puzzle.commands.model import Create

    return window.submit(Create(kind="probe", data=data))


def _unique(window, base: str) -> str:
    taken = {p.name for p in window.session.project.of_type(ResultProbe)}
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _no_runs(window) -> bool:
    if sources(window):
        return False
    window.statusBar().showMessage("Run a study first: result tools read its results", 8000)
    return True


def add_reaction(window, selection: str | None = None, source: str | None = None):
    """Force and moment reaction on a support or any selection (Ansys's Force and
    Moment Reaction)."""
    from puzzle.settings import length_unit

    if _no_runs(window):
        return None
    unit, per_metre = length_unit()
    asks = [
        Ask("source", "Results of", choices=sources(window), default=source),
        Ask(
            "selection",
            "Reaction on",
            choices=lambda v: selection_choices(window, v.get("source"), supports_first=True),
            default=selection,
            help="A support's selection, or any selection: the sum of the reaction forces "
            "of its nodes",
        ),
        Ask(
            "about",
            "Moment about",
            choices=[("The selection's centroid", "centroid"), ("A point", "point")],
        ),
        Ask(
            "point",
            f"Point (x, y, z in {unit})",
            kind="text",
            default="0, 0, 0",
            shown=lambda v: v.get("about") == "point",
        ),
        Ask(
            "track",
            "Follow",
            choices=TRACK,
            help="Also plot it in the Solution panel as the study solves (ADR 0054)",
        ),
        Ask("name", "Name", kind="text", follow=lambda v: f"Reaction on {v.get('selection')}"),
    ]

    def check(v) -> str | None:
        if not v.get("selection"):
            return "The results have no selections"
        if v.get("about") == "point" and _point(v.get("point", ""), per_metre) is None:
            return "Type the point as three numbers: x, y, z"
        return None if v.get("name") else "Give it a name"

    got = window.ask_form(
        "Reaction",
        asks,
        intro="The force and moment a support takes: the solver's reaction forces summed "
        "over the selection's nodes.",
        check=check,
    )
    if got is None:
        return None
    data = {
        "name": _unique(window, got["name"]),
        "source": got["source"],
        "type": "reaction",
        "selection": got["selection"],
        "track": bool(got.get("track")),
    }
    if got.get("about") == "point":
        data["about"] = list(_point(got["point"], per_metre))
    return _create(window, data)


def add_selection_result(window):
    """A result over a selection: maximum, minimum, average or integral (Ansys's scoped
    results, PrePoMax's Query)."""
    if _no_runs(window):
        return None
    shown_key = window.results_bar.field.currentData() if window.result else None
    asks = [
        Ask("source", "Results of", choices=sources(window)),
        Ask(
            "quantity",
            "Result",
            choices=lambda v: quantity_choices(window, v.get("source")),
            default=shown_key,
        ),
        Ask("selection", "Over", choices=lambda v: selection_choices(window, v.get("source"))),
        Ask("reduction", "Value", choices=REDUCTIONS),
        Ask(
            "track",
            "Follow",
            choices=TRACK,
            shown=lambda v: _trackable(v.get("quantity")) and v.get("reduction") != "integral",
            help="Also plot it in the Solution panel as the study solves (ADR 0054)",
        ),
        Ask("name", "Name", kind="text", follow=lambda v: _selection_title(window, v)),
    ]
    got = window.ask_form(
        "Result on a selection",
        asks,
        check=lambda v: (
            None
            if v.get("selection") and v.get("quantity") and v.get("name")
            else "Choose a result and a selection, and give it a name"
        ),
    )
    if got is None:
        return None
    return _create(
        window,
        {
            "name": _unique(window, got["name"]),
            "source": got["source"],
            "type": "selection",
            "quantity": got["quantity"],
            "selection": got["selection"],
            "reduction": got["reduction"],
            "track": bool(got.get("track"))
            and _trackable(got["quantity"])
            and got["reduction"] != "integral",
        },
    )


def _selection_title(window, v) -> str:
    from puzzle.post.quantities import label_of

    how = dict((b, a) for a, b in REDUCTIONS).get(v.get("reduction"), "").split(" (")[0]
    what = label_of(v["quantity"]) if v.get("quantity") else ""
    return f"{how} {what.lower()} on {v.get('selection')}"


def default_path(window, source_id: str) -> tuple[tuple, tuple]:
    """Through the model's centre along its longest side, end to end."""
    mesh, _ = _run_of(window, source_id)
    lo, hi = mesh.coords.min(axis=0), mesh.coords.max(axis=0)
    axis = int(np.argmax(hi - lo))
    start, end = (lo + hi) / 2, (lo + hi) / 2
    start[axis], end[axis] = lo[axis], hi[axis]
    return tuple(start), tuple(end)


def add_path(window):
    """A result along a straight line, plotted against the distance (Ansys's Path)."""
    from puzzle.settings import length_unit

    if _no_runs(window):
        return None
    unit, per_metre = length_unit()
    shown_key = window.results_bar.field.currentData() if window.result else None
    choices = sources(window)
    start, end = default_path(window, choices[0][1])

    def check(v) -> str | None:
        if not v.get("along"):
            a, b = _point(v.get("start", ""), per_metre), _point(v.get("end", ""), per_metre)
            if a is None or b is None:
                return "Type each end as three numbers: x, y, z"
            if np.allclose(a, b):
                return "The two ends are the same point"
        if not v.get("samples", "").isdigit() or not 2 <= int(v["samples"]) <= 10000:
            return "Points along it: a whole number from 2 to 10000"
        return None if v.get("name") else "Give it a name"

    typed = lambda v: not v.get("along")  # noqa: E731
    asks = [
        Ask("source", "Results of", choices=choices),
        Ask(
            "quantity",
            "Result",
            choices=lambda v: quantity_choices(window, v.get("source")),
            default=shown_key,
        ),
        Ask("along", "Along", choices=_along(window, "path")),
        Ask(
            "start",
            f"From (x, y, z in {unit})",
            kind="text",
            default=_shown(start, per_metre),
            shown=typed,
        ),  # fmt: skip
        Ask(
            "end",
            f"To (x, y, z in {unit})",
            kind="text",
            default=_shown(end, per_metre),
            shown=typed,
        ),  # fmt: skip
        Ask("samples", "Points along it", kind="text", default="50"),
        Ask("name", "Name", kind="text", follow=lambda v: f"Path: {_label(v.get('quantity'))}"),
    ]
    got = window.ask_form(
        "Path",
        asks,
        intro="The result at points along a straight line, plotted against the distance "
        "in the Plots tab. The default runs through the middle of the model, end to end.",
        check=check,
    )
    if got is None:
        return None
    return _create(
        window,
        {
            "name": _unique(window, got["name"]),
            "source": got["source"],
            "type": "path",
            "quantity": got["quantity"],
            **_line(got, per_metre),
            "samples": int(got["samples"]),
        },
    )


EQUIVALENTS = [
    ("von Mises (ASME VIII-2)", "von_mises"),
    ("Stress intensity, Tresca (older rules)", "tresca"),
]


def default_wall(window, source_id: str) -> tuple[tuple, tuple]:
    """Through the model's centre across its thinnest side, surface to surface."""
    mesh, _ = _run_of(window, source_id)
    lo, hi = mesh.coords.min(axis=0), mesh.coords.max(axis=0)
    axis = int(np.argmin(hi - lo))
    start, end = (lo + hi) / 2, (lo + hi) / 2
    start[axis], end[axis] = lo[axis], hi[axis]
    return tuple(start), tuple(end)


def add_linearized(window):
    """Stress linearization (ASME VIII-2 Annex 5-A): a line through a wall, its stress
    split into membrane, bending and peak (Ansys's Linearized Stress)."""
    from puzzle.settings import length_unit

    if _no_runs(window):
        return None
    unit, per_metre = length_unit()
    choices = sources(window)
    start, end = default_wall(window, choices[0][1])

    def check(v) -> str | None:
        if not v.get("along"):
            a, b = _point(v.get("start", ""), per_metre), _point(v.get("end", ""), per_metre)
            if a is None or b is None:
                return "Type each end as three numbers: x, y, z"
            if np.allclose(a, b):
                return "The two ends are the same point"
        if not v.get("samples", "").isdigit() or not 3 <= int(v["samples"]) <= 10000:
            return "Points through the wall: a whole number from 3 to 10000"
        return None if v.get("name") else "Give it a name"

    asks = [
        Ask("source", "Results of", choices=choices),
        Ask("along", "Along", choices=_along(window, "path")),
        Ask(
            "start",
            f"From (x, y, z in {unit})",
            kind="text",
            default=_shown(start, per_metre),
            shown=lambda v: not v.get("along"),
        ),  # fmt: skip
        Ask(
            "end",
            f"To (x, y, z in {unit})",
            kind="text",
            default=_shown(end, per_metre),
            shown=lambda v: not v.get("along"),
        ),  # fmt: skip
        Ask("equivalent", "Equivalent stress", choices=EQUIVALENTS),
        Ask("samples", "Points through the wall", kind="text", default="47"),
        Ask("name", "Name", kind="text", default="Linearized stress"),
    ]
    got = window.ask_form(
        "Linearized stress",
        asks,
        intro="The stress along a straight line through a wall, from one surface to the "
        "other, split into membrane (the mean), bending (the linear part) and peak (the "
        "rest), as ASME VIII-2 Annex 5-A classifies it. Put the ends on the two surfaces; "
        "the default crosses the model's thinnest side at its centre.",
        check=check,
    )
    if got is None:
        return None
    return _create(
        window,
        {
            "name": _unique(window, got["name"]),
            "source": got["source"],
            "type": "linearized",
            **_line(got, per_metre),
            "samples": int(got["samples"]),
            "equivalent": got["equivalent"],
        },
    )


def _along(window, kind: str) -> list[tuple[str, str]]:
    """A path tool's line: typed in the form, or a construction path (ADR 0046)."""
    from puzzle.gui.construction import construction_choices

    return [("A line typed here", "")] + [
        (f"Construction path: {name}", value) for name, value in construction_choices(window, kind)
    ]


def _line(got: dict, per_metre: float) -> dict:
    if got.get("along"):
        return {"construction": got["along"]}
    return {"point": list(_point(got["start"], per_metre)),
            "end": list(_point(got["end"], per_metre))}  # fmt: skip


def _planes(window) -> list[tuple[str, str]] | None:
    """The construction planes, or None after telling the user how to make one."""
    from puzzle.gui.construction import construction_choices

    if _no_runs(window):
        return None
    planes = construction_choices(window, "plane")
    if not planes:
        window.statusBar().showMessage(
            "Make a plane first: Construction geometry → New plane… (Project menu, or the "
            "project's right-click menu)",
            10000,
        )
    return planes or None


def add_plane_result(window):
    """A result over a construction plane's cut: maximum, minimum, average or integral
    (Ansys's results scoped to a Surface)."""
    planes = _planes(window)
    if planes is None:
        return None
    shown_key = window.results_bar.field.currentData() if window.result else None
    names = dict((v, n) for n, v in planes)
    asks = [
        Ask("source", "Results of", choices=sources(window)),
        Ask(
            "quantity",
            "Result",
            choices=lambda v: quantity_choices(window, v.get("source")),
            default=shown_key,
        ),  # fmt: skip
        Ask("plane", "On the plane", choices=planes),
        Ask("reduction", "Value", choices=REDUCTIONS),
        Ask(
            "name",
            "Name",
            kind="text",
            follow=lambda v: (
                f"{_label(v.get('quantity')).capitalize()} on {names.get(v.get('plane'), '')}"
            ),
        ),  # fmt: skip
    ]
    got = window.ask_form(
        "Result on a plane",
        asks,
        intro="A result over the cut the plane makes through the model. Shown, the model "
        "is cut at the plane so you see the values on it.",
        check=lambda v: (
            None if v.get("quantity") and v.get("name") else "Choose a result, and give it a name"
        ),  # fmt: skip
    )
    if got is None:
        return None
    return _create(window, {
        "name": _unique(window, got["name"]), "source": got["source"], "type": "plane",
        "quantity": got["quantity"], "construction": got["plane"],
        "reduction": got["reduction"],
    })  # fmt: skip


def add_section_force(window):
    """The force and moment carried across a construction plane (Ansys's Force and
    Moment Reaction on a Surface): the stress's traction summed over the cut."""
    planes = _planes(window)
    if planes is None:
        return None
    names = dict((v, n) for n, v in planes)
    got = window.ask_form(
        "Section force",
        [
            Ask("source", "Results of", choices=sources(window)),
            Ask("plane", "Across the plane", choices=planes),
            Ask(
                "name",
                "Name",
                kind="text",
                follow=lambda v: f"Section force at {names.get(v.get('plane'), '')}",
            ),  # fmt: skip
        ],
        intro="The force and moment that the side the plane faces passes on to the rest, "
        "from the stress over the cut (moment about the cut's centroid). Across a beam, "
        "they are its shear force, axial force and bending moment there.",
        check=lambda v: None if v.get("name") else "Give it a name",
    )
    if got is None:
        return None
    return _create(window, {
        "name": _unique(window, got["name"]), "source": got["source"], "type": "section",
        "construction": got["plane"],
    })  # fmt: skip


def _label(key) -> str:
    from puzzle.post.quantities import label_of

    return label_of(key).lower() if key else ""


# --- showing them ------------------------------------------------------------------------


def title(probe: ResultProbe) -> str:
    """The tree's text: the tool's name, and what it is when the name doesn't say."""
    from puzzle.post.quantities import label_of

    if probe.type == "point":
        return f"{probe.name} · {label_of(probe.quantity)}"
    word, hint = {
        "selection": ("on a selection", " on "),
        "reaction": ("reaction", "reaction"),
        "path": ("path", "path"),
        "linearized": ("linearized stress", "lineariz"),
        "plane": ("on a plane", " on "),
        "section": ("section force", "section"),
    }[probe.type]
    return probe.name if hint in probe.name.lower() else f"{probe.name} · {word}"


def _value(si: float, kind) -> str:
    from puzzle.gui.viewport import _display

    value, unit = _display(np.array([si]), kind)
    return f"{value[0]:.5g}" + (f" {unit}" if unit else "")


def _moment(si: float) -> str:
    """N m in the display force and length units ("20 N·m", "177 lbf·in")."""
    from puzzle.gui.viewport import _display
    from puzzle.settings import length_unit

    force, f_unit = _display(np.array([si]), "force")
    unit, per_metre = length_unit()
    return f"{force[0] * per_metre:.5g} {f_unit}·{unit}"


def rows(window, probe: ResultProbe, found: dict) -> dict[str, str]:
    """Properties of a result tool: what it reads and its values, in display units."""
    from puzzle.gui.window import _position

    source = window.session.project.objects.get(probe.source)
    out = {"Tool": probe.name, "Of": source.name if source is not None else "?"}
    if probe.type == "reaction":
        out["On"] = probe.selection
        for axis in "xyz":
            out[f"Force {axis.upper()}"] = _value(found["components"][axis], "force")
        out["Force (total)"] = _value(found["value"], "force")
        out["Moment about"] = _position(found["about"]) + ("" if probe.about else " (centroid)")
        for axis in "xyz":
            out[f"Moment {axis.upper()}"] = _moment(found["moment"][axis])
        out["Moment (total)"] = _moment(found["moment_total"])
        out["Nodes"] = str(found["count"])
        out["Note"] = (
            "The sum over the selection is the support's reaction; a single node's "
            "value in the reaction plot is only its share."
        )
        return out
    if probe.type == "linearized":
        return out | linearized_rows(probe, found)
    if probe.type == "section":
        built = window.session.project.objects.get(probe.construction)
        out["Across"] = built.name if built is not None else "?"
        for axis in "xyz":
            out[f"Force {axis.upper()}"] = _value(found["components"][axis], "force")
        out["Force (total)"] = _value(found["value"], "force")
        out["Normal force"] = _value(found["normal_force"], "force")
        out["Shear force"] = _value(found["shear_force"], "force")
        out["Moment about"] = _position(found["about"]) + " (the cut's centroid)"
        for axis in "xyz":
            out[f"Moment {axis.upper()}"] = _moment(found["moment"][axis])
        out["Moment (total)"] = _moment(found["moment_total"])
        out["Cut area"] = f"{found['area'] * _per_metre() ** 2:.5g} {_unit()}²"
        out["Note"] = (
            "What the side the plane faces passes on to the rest; the cut runs through "
            "the whole model, so place it where it crosses only the part you want."
        )
        return out
    out["Reads"] = found["label"]
    if probe.type == "plane":
        built = window.session.project.objects.get(probe.construction)
        out["On"] = built.name if built is not None else "?"
        how = dict((b, a) for a, b in REDUCTIONS)[probe.reduction]
        if probe.reduction == "integral":
            out[how] = f"{found['value']:.5g} (SI, × m²)"
        else:
            out[how] = _value(found["value"], found["unit"])
        if "point" in found:
            out["At"] = _position(found["point"])
        out["Cut area"] = f"{found['area'] * _per_metre() ** 2:.5g} {_unit()}²"
        out["Shown"] = "The model is cut at the plane while this tool is selected"
        return out
    if probe.type == "selection":
        out["Over"] = probe.selection
        how = dict((b, a) for a, b in REDUCTIONS)[probe.reduction]
        if probe.reduction == "integral":
            per = "volume" if found.get("measure") and _is_body(window, probe) else "area"
            out[how] = f"{found['value']:.5g} (SI, × m{'³' if per == 'volume' else '²'})"
        else:
            out[how] = _value(found["value"], found["unit"])
        if "point" in found:
            out["At"] = _position(found["point"])
        out["Values read"] = str(found["count"])
    elif probe.type == "path":
        out["From"] = _position(probe.point)
        out["To"] = _position(probe.end)
        out["Largest"] = _value(found["value"], found["unit"]) + " at " + _position(found["point"])
        out["Points"] = str(probe.samples)
        if found.get("outside"):
            out["Note"] = f"{found['outside']} points lie outside the model (left out)"
        out["Plot"] = "In the Plots tab, against the distance along the path"
    else:
        out["At"] = _position(probe.point)
        out["Value"] = _value(found["value"], found["unit"])
    return out


def _is_body(window, probe) -> bool:
    try:
        mesh, _ = _run_of(window, str(probe.source))
    except PuzzleError:
        return False
    group = mesh.groups.get(probe.selection)
    return group is not None and group.dim == 3


def linearized_rows(probe: ResultProbe, found: dict) -> dict[str, str]:
    """A linearization's Properties: each part's equivalent at both surfaces, and the
    components of membrane and bending."""
    from puzzle.gui.window import _position
    from puzzle.post.linearize import EQUIVALENTS as NAMES
    from puzzle.post.linearize import LABELS, ORDER

    unit = found.get("unit") or "pressure"
    out = {
        "From": _position(probe.point),
        "To": _position(probe.end),
        "Thickness": _value(found["thickness"], "length"),
        "Equivalent": NAMES[probe.equivalent],
    }
    start, end = found["parts"]["start"], found["parts"]["end"]
    for part in ("membrane", "bending", "membrane_bending", "peak", "total"):
        a, b = start[part]["equivalent"], end[part]["equivalent"]
        if part == "membrane":
            out[LABELS[part]] = _value(a, unit)
        else:
            out[LABELS[part]] = f"{_value(a, unit)} (start), {_value(b, unit)} (end)"
    for part in ("membrane", "bending"):
        for c in ORDER:
            out[f"{LABELS[part]} {c.upper()}"] = _value(start[part]["components"][c], unit)
    out["Note"] = (
        "Bending components are at the start; at the end they change sign. Components "
        "are in the global axes; the equivalents don't depend on the axes."
    )
    out["Plot"] = "In the Plots tab: total, membrane and membrane + bending along the line"
    return out


def _per_metre() -> float:
    from puzzle.settings import length_unit

    return length_unit()[1]


def _unit() -> str:
    from puzzle.settings import length_unit

    return length_unit()[0]
