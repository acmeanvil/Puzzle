"""The weld forms (ADR 0078, ADR 0072 section 10): what MCP `weld_add` and `weld_fatigue`
do, without the agent.

- **Add weld…** (Model → Bolts and fasteners, a component's Fasteners folder, the Welds
  panel): the edges it runs along (picked in the view with the Edges filter, or an edge
  selection), type, legs, pattern and name in one form; where it goes
  (`weld_generator.plan`: the part it joins, the faces the legs lie on) is shown as the
  values change, and OK submits `weld_generator.commands`' batch, the same one the tool
  returns.
- **Weld fatigue…** (Model → Bolts and fasteners, a weld's menu, a run's, the Welds
  panel): AISC 360 Appendix 3 (`weld_fields.fatigue`) or ASME VIII-2 5.5.5
  (`weld_fields.structural_stress`) on a study's latest run, the stations' table and
  their permissible cycles plotted. Read only: nothing changes.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.agent_prefill import add_ask_button
from puzzle.gui.live_form import LiveForm, quantity, shown_length
from puzzle.gui.quick_form import Ask
from puzzle.gui.theme import role

TYPES = [("Fillet", "fillet"), ("Groove", "groove")]
GROOVES = [("Complete joint penetration (CJP)", "cjp"),
           ("Partial joint penetration (PJP)", "pjp")]  # fmt: skip
PATTERNS = [("Continuous", "continuous"), ("Intermittent", "intermittent")]
METHODS = [("AISC 360-16 Appendix 3 (stress range)", "aisc"),
           ("ASME VIII-2 5.5.5 (structural stress)", "asme")]  # fmt: skip
SIDES = [("The attachment's toe (the path's part)", "attachment"),
         ("The plate's toe (the other part)", "plate")]  # fmt: skip
MATERIALS = [("Steel", "steel"), ("Aluminium", "aluminium")]


def _project(window):
    return window.session.project


# --- Add weld… -------------------------------------------------------------------------


def edge_sources(window, comp) -> list[tuple[str, str]]:
    """Where a weld's edges come from: the edges picked in the view (on this component's
    mesh), then each part's edge selections, as (shown name, value)."""
    from puzzle.model.schema import Geometry

    out = []
    picked = picked_edges(window, comp)
    if picked:
        n = len(picked)
        out.append((f"The {n} edge{'s' if n > 1 else ''} picked in the view", "picked"))
    project = _project(window)
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [(f"{s.name} ({part.name})", f"{part.name}/{s.name}")
                    for s in geo.selections if s.entity == "edge"]  # fmt: skip
    return out


def picked_edges(window, comp) -> list[tuple[str, str]]:
    """(part, edge name) of the edges picked in the view on `comp`'s mesh."""
    if str(getattr(window, "mesh_component", "") or "") != str(comp.id):
        return []
    names = getattr(window, "mesh_names", {}).get(str(comp.id), {})
    picks = [names[e] for e in window.viewport.picked if e[0] == 1 and e in names]
    return list(dict.fromkeys(picks))


def edges_of(window, comp, source: str) -> tuple[str, list[str]]:
    """A source's (part, edge names); ValueError when it spans parts or is empty."""
    from puzzle.model.schema import Geometry

    if source == "picked":
        picks = picked_edges(window, comp)
        parts = {p for p, _ in picks}
        if len(parts) != 1:
            raise ValueError("pick edges of one part (the weld's path runs on one part)")
        return parts.pop(), [name for _, name in picks]
    part, _, name = (source or "").partition("/")
    inst = next((p for p in comp.parts if p.name == part), None)
    geo = _project(window).objects.get(inst.geometry) if inst else None
    sel = next((s for s in geo.selections if s.name == name and s.entity == "edge"), None) if (
        isinstance(geo, Geometry)) else None  # fmt: skip
    if sel is None:
        raise ValueError("choose the edges the weld runs along")
    return part, list(sel.members)


def _weld_args(values: dict) -> dict:
    """The form's values as `weld_generator.commands`' keywords (quantities as dicts,
    as MCP `weld_add` takes them)."""
    unit = shown_length(1.0).split(" ", 1)[1]
    fillet = values.get("type", "fillet") == "fillet"
    intermittent = values.get("pattern") == "intermittent"
    out = {
        "name": (values.get("name") or "").strip(),
        "leg": quantity(values.get("leg", ""), unit),
        "leg2": quantity(values["leg2"], unit) if fillet and values.get("leg2") else None,
        "type": values.get("type", "fillet"),
        "groove": values.get("groove") if not fillet else None,
        "pattern": values.get("pattern", "continuous"),
        "segment": quantity(values.get("segment", ""), unit) if intermittent else None,
        "pitch": quantity(values.get("pitch", ""), unit) if intermittent else None,
    }
    if not out["name"]:
        raise ValueError("give the weld a name")
    return out


def add_weld(window, comp=None):
    """The Add weld… form; OK submits the batch MCP `weld_add` would return."""
    from puzzle import weld_generator
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component, Quantity

    if comp is None:
        from puzzle.gui.main_menu import current_component

        comp = current_component(window)
        if comp is None:
            return None
    comp = _project(window).get(comp.id, Component)
    sources = edge_sources(window, comp)
    if not sources:
        window.statusBar().showMessage(
            "A weld runs along edges: pick them in the view (Pick, the Edges filter) on the "
            "component's mesh, or make an edge selection first", 10000)  # fmt: skip
        return None
    root = window.session.root
    plans: dict = {}  # (part, edges) -> WeldPlan or the reason there is none

    def planned(values):
        part, edges = edges_of(window, comp, values.get("source"))
        key = (part, tuple(edges))
        if key not in plans:
            try:
                plans[key] = weld_generator.plan(_project(window), comp, root, part, edges)
            except PuzzleError as exc:
                plans[key] = exc.message
        if isinstance(plans[key], str):
            raise ValueError(plans[key])
        return plans[key]

    def check(values):
        try:
            planned(values)
            args = _weld_args(values)
        except ValueError as exc:
            return str(exc)
        if any(w.name == args["name"] for w in comp.welds):
            return f"{comp.name} has a weld '{args['name']}' already"
        return None

    where = role(QLabel(""), "muted")
    where.setWordWrap(True)

    def show(values):
        try:
            p = planned(values)
        except ValueError:
            where.setText("")
            return
        where.setText(
            f"Joins {p.part} to {p.other}. The first leg lies on {p.part}'s "
            f"{', '.join(p.leg_faces)}, the second on {p.other}'s {', '.join(p.base_faces)}; "
            f"{len(p.edges)} edge{'s' if len(p.edges) > 1 else ''} along the path. A light "
            "weld: springs across the joint, line forces at its stations (CalculiX, "
            "Code_Aster, FEniCSx).")  # fmt: skip

    taken = {w.name for w in comp.welds}
    k = 1
    while f"Weld {k}" in taken:
        k += 1
    asks = [
        Ask("source", "Along", choices=sources,
            help="Edges picked in the view (Pick, Edges filter, double-click) or an edge "
            "selection; one part's edges, lying on the other part"),
        Ask("type", "Type", choices=TYPES),
        Ask("groove", "Groove", choices=GROOVES, shown=lambda v: v.get("type") == "groove"),
        Ask("leg", "Leg (a groove's depth)", kind="text", default=shown_length(0.006),
            help="With its unit; a bare number is in the unit shown"),
        Ask("leg2", "Second leg", kind="text", default="",
            shown=lambda v: v.get("type") == "fillet",
            help="An unequal fillet's leg on the other part; empty: equal legs"),
        Ask("pattern", "Pattern", choices=PATTERNS),
        Ask("segment", "Segment length", kind="text", default=shown_length(0.05),
            shown=lambda v: v.get("pattern") == "intermittent"),
        Ask("pitch", "Pitch", kind="text", default=shown_length(0.15),
            shown=lambda v: v.get("pattern") == "intermittent",
            help="From the start of one segment to the next"),
        Ask("name", "Name", kind="text", default=f"Weld {k}"),
    ]  # fmt: skip
    dialog = LiveForm(window, f"Add weld to {comp.name}", asks,
                      intro="Pick the edge at the joint's corner: of its two faces, the one "
                      "on the other part is the seat, the other carries the first leg.",
                      check=check, changed=show, below=where,
                      help_key="form:weld_add")  # fmt: skip

    def prompt(v):
        try:
            part, edges = edges_of(window, comp, v.get("source"))
        except ValueError:
            part, edges = "?", []
        return (f"Add a {v.get('type', 'fillet')} weld named '{v.get('name')}' to "
                f"{comp.name}, on part {part} along the edges {edges}, leg {v.get('leg')}"
                + (f", second leg {v['leg2']}" if v.get("leg2") else "")
                + (f", intermittent {v.get('segment')} every {v.get('pitch')}"
                   if v.get("pattern") == "intermittent" else "")
                + " (MCP weld_add).")  # fmt: skip

    add_ask_button(dialog, window, prompt)
    if not window.run_dialog(dialog) or dialog.problem():
        return None
    values = dialog.values()
    args = _weld_args(values)
    q = lambda v: Quantity(**v) if v is not None else None  # noqa: E731
    batch = weld_generator.commands(
        _project(window), comp, planned(values), name=args["name"], leg=q(args["leg"]),
        leg2=q(args["leg2"]), type=args["type"], groove=args["groove"],
        pattern=args["pattern"], segment=q(args["segment"]), pitch=q(args["pitch"]),
    )  # fmt: skip
    return window.submit_batch(batch)


# --- Weld fatigue… ---------------------------------------------------------------------


def _stress(pa: float) -> str:
    from puzzle.model.units import pretty, shown
    from puzzle.settings import current

    v, u = shown(pa, "Pa", current().display_units())
    return f"{v:.4g} {pretty(u)}"


def _steps(text: str) -> tuple[int, ...]:
    from puzzle.gui.properties import parse_steps

    out = tuple(parse_steps(text or ""))
    if len(out) > 2:
        raise ValueError("steps: one (from zero) or two")
    return out


def _cycles(text: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    value = float(text)
    if value <= 0:
        raise ValueError("cycles must be positive")
    return value


def evaluate(project, store, run_id: str, values: dict, cache: dict | None = None) -> dict:
    """The fatigue check the form shows: the same calls MCP `weld_fatigue` makes, on
    `run_id`'s results."""
    from puzzle.model.schema import Component, Study
    from puzzle.post.weld_fields import fatigue, structural_stress

    record = next(r for r in store.runs() if r.run_id == run_id)
    study = next(s for s in project.of_type(Study) if str(s.id) == record.study_id)
    comp = project.get(study.component, Component)
    cache = {} if cache is None else cache
    if run_id not in cache:
        cache[run_id] = (store.read_mesh(record.mesh_id), store.read_results(run_id))
    mesh, res = cache[run_id]
    steps = _steps(values.get("steps", ""))
    cycles = _cycles(values.get("cycles", ""))
    if values.get("method") == "asme":
        text = (values.get("thickness") or "").strip()
        unit = shown_length(1.0).split(" ", 1)[1]
        thickness = None
        if text:
            from puzzle.model.schema import Quantity

            thickness = Quantity(**quantity(text, unit)).to_si()
        try:
            return structural_stress(project, comp, mesh, res, values["weld"], steps, thickness,
                                     cycles, side=values.get("side", "attachment"),
                                     basis=values.get("basis", "-3sigma"),
                                     material=values.get("material", "steel"))  # fmt: skip
        except ValueError as exc:
            from puzzle.errors import PuzzleError

            raise PuzzleError(str(exc), code="invalid") from None
    if cycles is None:
        raise ValueError("give the number of cycles")
    return fatigue(project, comp, mesh, res, values["weld"], values.get("category", "F"),
                   cycles, steps)  # fmt: skip


def latest_run(window, study_id: str) -> str | None:
    """A study's latest run (as MCP's tools read it), None before its first."""
    try:
        runs = [r for r in window._store().runs() if r.study_id == study_id]
    except Exception:  # noqa: BLE001 - no results store yet
        return None
    return runs[-1].run_id if runs else None


class FatigueView(QWidget):
    """Below the fatigue form: the verdict, the stations' table and their cycles."""

    def __init__(self) -> None:
        super().__init__()
        from puzzle.gui.solution_panel import Plot

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plot = Plot("Station", "Permissible cycles", log=True)
        self.plot.setMinimumHeight(150)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.plot)
        self.result: dict | None = None

    def show_result(self, out: dict | None, why: str = "") -> None:
        from PySide6.QtGui import QColor

        from puzzle.gui.theme import current as tokens

        self.result = out
        self.table.clear()
        if out is None:
            self.summary.setText(why)
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.plot.set_data([], empty=why or "Nothing to plot")
            return
        length = lambda m: shown_length(m)  # noqa: E731
        if "stations" in out:  # ASME VIII-2
            worst = out["worst"]
            text = (f"{out['clause']}, {out['where']}, thickness {length(out['thickness_m'])}: "
                    f"the worst station allows {worst['cycles']:.4g} cycles "
                    f"(equivalent range {worst['equivalent_range']:.4g}).")  # fmt: skip
            if "damage" in out:
                verdict = "passes" if out["damage"] <= 1 else "fails"
                text += f" Damage at {out['cycles']:.4g} cycles: {out['damage']:.3f} ({verdict})."
            self.summary.setText(text)
            headers = ["Station", "At", "Membrane range", "Bending range",
                       "Structural range", "Cycles"]  # fmt: skip
            rows = [[str(i + 1), ", ".join(length(c) for c in s["at_m"]),
                     _stress(s["membrane_range_Pa"]), _stress(s["bending_range_Pa"]),
                     _stress(s["structural_stress_range_Pa"]), f"{s['cycles']:.4g}"]
                    for i, s in enumerate(out["stations"])]  # fmt: skip
            points = [(i + 1.0, s["cycles"]) for i, s in enumerate(out["stations"])
                      if math.isfinite(s["cycles"])]  # fmt: skip
            self.plot.set_data([("Cycles", points, QColor(tokens().accent))],
                               empty="Every station's range is zero")  # fmt: skip
        else:  # AISC 360 Appendix 3: the governing station
            verdict = "passes" if out["ratio"] <= 1 else "fails"
            self.summary.setText(f"{out['clause']}, category {out['category']}: ratio "
                                 f"{out['ratio']:.3f} ({verdict}).")  # fmt: skip
            headers = ["Quantity", "Value"]
            rows = [["Stress range", _stress(out["stress_range_Pa"])],
                    ["Allowable range F_SR", _stress(out["allowable_range_Pa"])],
                    ["Ratio", f"{out['ratio']:.3f}"],
                    ["Cycles", f"{out['cycles']:.4g}"],
                    ["Steps", ", ".join(str(k) for k in out["steps"])],
                    ["Where", out["where"]],
                    ["At", ", ".join(length(c) for c in out["at_m"])]]  # fmt: skip
            self.plot.set_data([], empty="AISC's check is one governing range: no stations "
                               "to plot")  # fmt: skip
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()


def weld_fatigue(window, study_id: str | None = None, weld: str | None = None,
                 run_id: str | None = None):  # fmt: skip
    """The Weld fatigue… form: the check shown as its values change. Returns the last
    result shown (None if there was none)."""
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component, Study
    from puzzle.weld_strength import ASME_CURVES

    project = _project(window)
    studies = [s for s in project.of_type(Study)
               if (c := project.objects.get(s.component)) is not None
               and isinstance(c, Component) and c.welds]  # fmt: skip
    if not studies:
        window.statusBar().showMessage(
            "No study's component has a weld yet: Model → Bolts and fasteners → Add weld…",
            10000)  # fmt: skip
        return None
    if run_id is not None:
        study_id = window._store().get_run(run_id).study_id
    if study_id is None:
        from puzzle.gui.main_menu import current_study

        chosen = current_study(window)
        study_id = str(chosen.id) if chosen is not None else None
    if study_id not in {str(s.id) for s in studies}:
        study_id = str(studies[0].id)

    def welds(v):
        study = next((s for s in studies if str(s.id) == v.get("study")), studies[0])
        comp = project.get(study.component, Component)
        return [(w.name, w.name) for w in comp.welds]

    store = window._store()
    cache: dict = {}
    view = FatigueView()

    def run_of(v) -> str | None:
        if run_id is not None and window._store().get_run(run_id).study_id == v.get("study"):
            return run_id
        return latest_run(window, v.get("study"))

    def show(v):
        rid = run_of(v)
        if rid is None:
            view.show_result(None, "This study has no runs yet: run it, then check its welds.")
            return
        try:
            view.show_result(evaluate(project, store, rid, v, cache))
        except (ValueError, PuzzleError) as exc:
            view.show_result(None, getattr(exc, "message", None) or str(exc))

    categories = [("F: shear on the fillet's throat", "F")] + [
        (f"{c}: the base metal at the toe", c) for c in ("A", "B", "B'", "C", "D", "E", "E'")
    ]
    asme = lambda v: v.get("method") == "asme"  # noqa: E731
    asks = [
        Ask("study", "Study", choices=[(s.name, str(s.id)) for s in studies], default=study_id),
        Ask("weld", "Weld", choices=welds, default=weld),
        Ask("method", "Method", choices=METHODS),
        Ask("category", "Detail category", choices=categories,
            shown=lambda v: not asme(v), help="AISC 360-16 Table A-3.1"),
        Ask("cycles", "Cycles", kind="text", default="2e6",
            help="Stress range fluctuations in the design life; for ASME, empty: the "
            "permissible cycles only, no damage"),
        Ask("thickness", "Thickness at the toe", kind="text", default="", shown=asme,
            help="The attachment's (or the plate's) thickness; empty: a shell's own, or "
            "the plate's measured"),
        Ask("basis", "Design curve", shown=asme,
            choices=[(k, k) for k in ASME_CURVES["steel"]], default="-3sigma",
            help="Table 3-F.2M's curve: the mean, or so many standard deviations from it"),
        Ask("material", "Material", choices=MATERIALS, shown=asme),
        Ask("side", "Toe", choices=SIDES, shown=asme),
        Ask("steps", "Steps", kind="text", default="",
            help="Empty: the last step from zero; one step: from zero; two: the range "
            "between them (1-based, among the steps with weld results)"),
    ]  # fmt: skip
    dialog = LiveForm(window, "Weld fatigue", asks, changed=show, below=view,
                      intro="From the study's latest run. Nothing in the project changes.",
                      help_key="form:weld_fatigue")  # fmt: skip
    dialog.view = view
    dialog.buttons.button(dialog.buttons.StandardButton.Cancel).hide()

    def prompt(v):
        how = (f"ASME VIII-2 5.5.5 ({v.get('side')} toe, curve {v.get('basis')})"
               if asme(v) else f"AISC 360 Appendix 3 category {v.get('category')}")  # fmt: skip
        name = next((s.name for s in studies if str(s.id) == v.get("study")), "")
        return (f"Check the fatigue of weld '{v.get('weld')}' in study {name} by {how}, "
                f"{v.get('cycles') or 'no'} cycles, steps {v.get('steps') or 'the last'} "
                "(MCP weld_fatigue), and explain the result.")  # fmt: skip

    add_ask_button(dialog, window, prompt)
    window.run_dialog(dialog)
    return view.result
