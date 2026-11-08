"""Operators in the window (ADR 0062): made from the palette (Tab, or a double-click on
the schematic's empty canvas), from the tree's Operators heading or the Results menu;
wired by drawing a wire in the schematic or picking a source in Properties; their value
shown on the node and in Properties.

Every change is an ordinary command (`Create`, `Edit`), so it is undoable and logged.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from puzzle.commands.model import Create, Edit, EditOp
from puzzle.errors import PuzzleError
from puzzle.operators import TYPES, accepts, default_name, output_type, sources

ROLE = Qt.ItemDataRole.UserRole
NOT_WIRED = "Not wired"
TYPED = "A value typed in"


def new_operator(window, type_: str, source: str | None = None, port: str = "out",
                 at: tuple[float, float] | None = None) -> str | None:  # fmt: skip
    """Make an operator of `type_`, wired from `source`'s output `port` into the first
    input that takes it; placed at `at` in the schematic. Returns its id."""
    project = window.session.project
    inputs = {}
    if source:
        given = output_type(project, UUID(source), port)
        spec = next((p for p in TYPES[type_].inputs if given and accepts(p, given)), None)
        if spec is not None:
            inputs[spec.key] = {"source": source, "port": port}
    data = {"name": default_name(project, type_), "type": type_, "inputs": inputs}
    outcome = window.submit(Create(kind="operator", data=data))
    if outcome is None or not outcome.created:
        return None
    used = [type_] + [k for k in getattr(window, "recent_operators", []) if k != type_]
    window.recent_operators = used[:RECENT]  # offered first next time (this session)
    oid = outcome.created[0]["id"]
    if at is not None:  # where it was asked for, as if dragged there
        from puzzle.schematic import view_state

        panel = window.schematic
        panel.view.positions[oid] = (round(at[0], 1), round(at[1], 1))
        view_state.save(window.session.root, panel.view)
        panel.refresh()
    window.select_in_tree(("object", oid))
    return oid


def wire(window, op_id: str, key: str, source: str, port: str):
    """Wire `source`'s output `port` into the operator's input `key` (one Edit)."""
    value = {"source": source, "port": port}
    return window.submit(Edit(object=op_id, ops=[EditOp(op="set", path=f"inputs.{key}",
                                                        value=value)]))  # fmt: skip


def input_rows(window, op) -> list[tuple[str, str, list, object, str | None]]:
    """Each input as (key, label, [(name, value)], current value, typed text or None):
    what may be wired in (never the operator itself or what reads it); an input that
    takes a typed value has its text (empty when none) while it isn't wired."""
    project = window.session.project
    out = []
    for spec in TYPES[op.type].inputs:
        inp = op.inputs.get(spec.key)
        current, typed = None, None
        if inp is not None and inp.source is not None:
            current = {"source": str(inp.source), "port": inp.port}
        elif spec.typed:  # unwired: a value may be typed under it
            has = inp is not None and inp.value is not None
            current, typed = ("typed" if has else None), (_typed_text(inp.value) if has else "")
        options: list = [(NOT_WIRED, None)] + ([(TYPED, "typed")] if current == "typed" else [])
        options += [(text, {"source": sid, "port": port})
                    for text, sid, port in sources(project, op, spec.key)]  # fmt: skip
        out.append((spec.key, spec.label, options, current, typed))
    return out


def _typed_text(value) -> str:
    from puzzle.gui.properties import format_value
    from puzzle.model.schema import Quantity

    if isinstance(value, Quantity):
        return format_value(value.model_dump())
    return format_value(value)


def parse_typed(text: str):
    """ "200 MPa" -> a quantity, "2.5" -> a number; the unit checked as typed."""
    from puzzle.model.units import known, plain

    text = text.strip()
    if not text:
        return None
    number, _, unit = text.partition(" ")
    value = float(number)
    if not unit.strip():
        return value
    unit = plain(unit.strip())
    if not known(unit):
        raise ValueError(f"unknown unit '{unit}'")
    return {"value": value, "unit": unit}


def show(window, op) -> None:
    """Properties for an operator: its inputs as dropdowns, then its value."""
    panel = window.properties
    panel.add_inputs(input_rows(window, op))
    from puzzle.operators import evaluate as ev

    try:
        ctx = ev.Context(window.session.project, window._store(), root=window.session.root)
        text = ev.detail(ctx, op)
    except PuzzleError as exc:
        text = exc.message
    panel.add_rows("Output", [("Shows", text)])
    from puzzle.operators import operator_doc

    doc = operator_doc(op.type)  # ADR 0071: what it is for, always at hand
    panel.add_rows("What it does", [("Purpose", doc["purpose"])]
                   + ([("Example", doc["example"])] if doc["example"] else []))  # fmt: skip
    if op.type == "preview":
        preview(window, op)
    if op.type == "filter":  # ADR 0062: the field it keeps, on the mesh it came from
        preview_field(window, op)
    if op.type == "script":  # ADR 0062 phase 4: its code in a box of its own
        from puzzle.operators.scripts import imports, missing

        panel.add_button("Code", "Edit code…", lambda: edit_code(window, op))
        try:
            needs, lacking = imports(op.code), missing(op.code)
        except PuzzleError as exc:
            needs, lacking = [], [exc.message]
        rows = [("Imports", ", ".join(needs) or "the standard library only")]
        if lacking:
            rows.append(("Not installed", ", ".join(lacking) + " (puzzle scripts add …)"))
        if not window.session.project.trusted:
            rows.append(("Runs", "not until the project is trusted (Project → Trust…)"))
        panel.add_rows("Script", rows)
    viewer = getattr(window, "table_view", None)
    if viewer is not None:
        viewer.show_table(op)  # a table's rows in the Table tab (ADR 0064)
    if op.type == "slider2d":  # two sliders, x then y (ADR 0064A A2)
        for port, key, low, high in (("x", "value", op.low, op.high),
                                     ("y", "value2", op.low2, op.high2)):  # fmt: skip
            if low is None or high is None:
                continue
            try:
                now = ev.value(ev.Context(window.session.project, None), op, port).value
            except PuzzleError:
                continue
            held = {"v": now}  # dragged to; written once on release (one undo)
            panel.add_slider(low.to_si(), high.to_si(), now,
                             lambda v, h=held: h.update(v=v),
                             lambda k=key, h=held: _slide2d(window, op, k, h["v"]))  # fmt: skip
    if op.type == "slider":  # drag here as on its node: previews, never solves
        from puzzle.schematic import _slider_of

        found = _slider_of(ev.Context(window.session.project, None), op)
        if found is not None:
            low, high, value = found
            panel.add_slider(low, high, value, lambda v: window.slide(str(op.id), v),
                             lambda: window.slide_done(str(op.id)))  # fmt: skip


def _slide2d(window, op, key: str, si: float) -> None:
    """A 2D slider's x or y set where its slider was let go: one Edit."""
    from puzzle.model.units import DIMENSIONS, convert, kind_of

    q = op.low if key == "value" else op.low2
    unit = q.unit
    kind = kind_of(unit) if unit != "dimensionless" else None
    value = convert(si, DIMENSIONS[kind][1], unit) if kind else si
    window.properties.edited.emit(
        str(op.id), f"inputs.{key}", {"value": {"value": float(f"{value:.6g}"), "unit": unit}}
    )


def save_cluster(window, group_id: str) -> str | None:
    """A group of operators kept as a cluster in the user's library (ADR 0064 item 9),
    to put into any project: its inputs from outside become its ports."""
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Operator
    from puzzle.operators import clusters

    project = window.session.project
    group = project.objects.get(UUID(group_id))
    members = [UUID(m) for m in getattr(group, "members", []) if _is_uuid(m)]
    ops = [m for m in members if isinstance(project.objects.get(m), Operator)]
    if not ops:
        window.statusBar().showMessage("A cluster is made of a group's operators", 8000)
        return None
    got = window.ask_form("Save as cluster", [
        Ask("name", "Name", kind="text", default=group.name),
        Ask("help", "What it does", kind="text", default=""),
    ])  # fmt: skip
    if not got:
        return None
    path = clusters.save(project, ops, got["name"], got.get("help", ""))
    window.statusBar().showMessage(f"Saved the cluster {got['name']}: it is under Add "
                                   "operator → My clusters in every project", 10000)  # fmt: skip
    return str(path)


def _is_uuid(text: str) -> bool:
    try:
        UUID(text)
    except ValueError:
        return False
    return True


def insert_template(window, key: str, source: str | None = None, port: str = "out"):
    """A recipe or cluster put in (ADR 0064 item 6), its first port wired from the
    selected node when that fits: one batch, one undo."""
    from puzzle.operators import clusters, output_type

    found = clusters.templates()[key]
    bindings = {}
    ports = found.get("ports", [])
    if source and ports:
        given = output_type(window.session.project, UUID(source), port)
        first = ports[0]
        if given is not None and (given == first["type"] or {given, first["type"]} <= {
                "result", "field"}):  # fmt: skip
            bindings[first["name"]] = (source, port)
    return window.submit_batch(clusters.commands(window.session.project, key, bindings))


def use_as_field(window, op):
    """An imported field reading a table operator's rows (ADR 0064 phase 3): x, y, z the
    points, every other number a value. Made in one step, then its uses are set as any
    field's (a feature's value from it)."""
    from puzzle import fields
    from puzzle.commands.model import Create

    try:
        data = fields.from_table(window.session.project, op.id)
    except PuzzleError as exc:
        window.statusBar().showMessage(exc.message, 8000)
        return None
    outcome = window.submit(Create(kind="field", data=data))
    if outcome is not None and outcome.created:
        oid = outcome.created[0]["id"]
        window.select_in_tree(("object", oid))
        return oid
    return None


PREVIEWED = 200  # points a Preview pins at most: labels beyond that are clutter


def edit_code(window, op):
    """A script's code in one form (multi-line), written as one Edit."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.quick_form import Ask

    got = window.ask_form(f"{op.name}: code", [
        Ask("code", "Python (inputs a, b, c, d; set out)", kind="multiline", default=op.code),
    ])  # fmt: skip
    if got is None or got["code"] == op.code:
        return None
    return window.submit(Edit(object=str(op.id), ops=[EditOp(op="set", path="code",
                                                             value=got["code"])]))  # fmt: skip


def preview_field(window, op) -> bool:
    """A Filter's field drawn in the 3D view: its kept values on its selection (or the
    whole model), the values it leaves out in the muted colour. Returns whether drawn."""
    import numpy as np

    from puzzle.gui import theme
    from puzzle.operators import evaluate as ev

    try:
        ctx = ev.Context(window.session.project, window._store(), root=window.session.root)
        fv = ev.value(ctx, op)
    except PuzzleError as exc:
        window.statusBar().showMessage(exc.message, 8000)
        return False
    if not isinstance(fv, ev.FieldValue):
        return False
    values = np.asarray(fv.values, float)
    values = values[:, 0] if values.ndim > 1 else values
    if fv.location == "element":  # drawn per node: each element's value on its nodes
        from puzzle.post.estimates import nodal
        from puzzle.results.model import Field

        field = nodal(fv.mesh, Field("filtered", "element", [""], fv.ids, values[:, None]))
        ids, values = field.ids, field.values[:, 0]
    else:
        ids = fv.ids
    left_out = ~np.isfinite(values)
    from puzzle.model.units import DIMENSIONS

    kind = fv.item.unit  # a kind ("pressure"); the view wants its SI unit
    unit = DIMENSIONS[kind][1] if kind in DIMENSIONS else ""
    window.viewport.show_field_values(
        fv.mesh, fv.selection, ids, np.where(left_out, np.nan, values), left_out,
        title=f"{op.name}: {fv.label or fv.item.label}", unit=unit,
        beyond_colour=theme.current().muted,
    )  # fmt: skip
    return True


def preview(window, op) -> int:
    """A Preview operator's table drawn in the 3D view (ADR 0064A row I1): each row's
    x, y, z pinned with its `column`'s value as the label (in display units), at most
    PREVIEWED. Returns how many were pinned."""
    from puzzle.operators import evaluate as ev

    try:
        ctx = ev.Context(window.session.project, window._store(), root=window.session.root)
        got = ev._input(ctx, op, "table")
    except PuzzleError as exc:
        window.statusBar().showMessage(exc.message, 8000)
        return 0
    if not isinstance(got, ev.Table) or not {"x", "y", "z"} <= set(got.columns):
        window.statusBar().showMessage(f"{op.name}: its table needs x, y and z", 8000)
        return 0
    label = op.column if op.column in got.columns else got.columns[-1]
    kind = dict(zip(got.columns, got.kinds, strict=False)).get(label)
    rows = got.frame.select(["x", "y", "z", label]).head(PREVIEWED).rows()
    marks = [((x, y, z), ev.quantity_text(v, kind) if isinstance(v, float) else str(v))
             for x, y, z, v in rows if None not in (x, y, z)]  # fmt: skip
    window.viewport.pin_points(marks)
    return len(marks)


def choices(window, op, path: str):
    """The dropdowns of an operator's own settings (the quantity from the results it
    reads, the selection from their mesh), or None for the usual ones."""
    from puzzle.gui import result_tools as tools

    project = window.session.project
    if op.type == "measure":  # ADR 0064: what to measure, by name
        return _measure_choices(project, op, path)
    if op.type == "model_value":  # ADR 0071: an object, then one of its values, by name
        return model_value_choices(project, op, path)
    if path == "axes" and op.type == "points":  # ADR 0062 phase 3: by name, never an id
        from puzzle.model.schema import CoordinateSystem

        return [("Global", None)] + [(c.name, str(c.id)) for c in project.of_type(CoordinateSystem)]
    if path == "target" and op.type == "grid":
        from puzzle.model.schema import ConstructionGeometry

        planes = [c for c in project.of_type(ConstructionGeometry) if c.type == "plane"]
        return [("An axis plane at the origin", None)] + [(c.name, str(c.id)) for c in planes]
    if path == "column" and op.type != "list":  # a table's columns, as it is now
        columns = table_columns(window, op)
        if columns:
            return [(c, c) for c in columns]
        return None
    if path not in ("quantity", "selection"):
        return None
    study = upstream_study(window.session.project, op)
    if study is None:
        return None
    try:
        if path == "quantity":
            return [("The first listed", None)] + tools.quantity_choices(window, study)
        return [("The whole model", None)] + tools.selection_choices(window, study)
    except PuzzleError:  # no run yet: typed in
        return None


def table_columns(window, op) -> list[str]:
    """The columns of the table wired into an operator, from its value now (empty when
    it can't be worked out yet)."""
    from puzzle.operators import evaluate as ev

    inp = op.inputs.get("table")
    if inp is None or inp.source is None:
        return []
    try:
        root = window.session.root
        got = ev.source(ev.Context(window.session.project, window._store(), root=root),
                        inp.source, inp.port)  # fmt: skip
    except PuzzleError:
        return []
    return list(got.columns) if isinstance(got, ev.Table) else []


def _measure_choices(project, op, path):
    """Measure's target (a component or geometry) and its parts or selections."""
    from puzzle.model.schema import Component, Geometry

    if path == "target":
        return [(f"{c.name} (component)", str(c.id)) for c in project.of_type(Component)] + [
            (f"{g.name} (geometry)", str(g.id)) for g in project.of_type(Geometry)]  # fmt: skip
    if path != "selection":
        return None
    obj = project.objects.get(op.target) if op.target else None
    if isinstance(obj, Component):
        out = []
        for part in obj.parts:
            geo = project.objects.get(part.geometry)
            out.append((f"{part.name} (the whole part)", part.name))
            for sel in getattr(geo, "selections", []):
                out.append((f"{part.name}/{sel.name}", f"{part.name}/{sel.name}"))
        return out
    if isinstance(obj, Geometry):
        return [("The whole body", None)] + [(s.name, s.name) for s in obj.selections]
    return None


def upstream_study(project, op, seen=()) -> str | None:
    """The study whose results an operator reads, nearest first (through operators,
    result tools and derived results)."""
    from puzzle.model.schema import Operator, ResultDefinition, ResultProbe, Study

    for inp in op.inputs.values():
        obj = project.objects.get(inp.source) if inp.source else None
        if isinstance(obj, ResultProbe):
            obj = project.objects.get(obj.source)
        if isinstance(obj, ResultDefinition):
            deps = obj.dependencies()
            obj = project.objects.get(deps[-1]) if deps else None
        if isinstance(obj, Study):
            return str(obj.id)
        if isinstance(obj, Operator) and obj.id not in seen:
            found = upstream_study(project, obj, (*seen, op.id))
            if found:
                return found
    return None


def export_now(window, op) -> None:
    """Write an export operator's file now."""
    from puzzle.operators import evaluate as ev

    try:
        written = ev.export(ev.Context(window.session.project, window._store()), op,
                            window.session.root)  # fmt: skip
    except PuzzleError as exc:
        window.report(exc)
        return
    names = ", ".join(str(p.relative_to(window.session.root)) for p in written)
    window.statusBar().showMessage(f"Wrote {names}", 8000)


def show_plot(window, op) -> None:
    """A plot operator's table in the Plots panel, ticked alone."""
    window.plots.show_operator(op)


RECENT = 5  # recently made operator types the palette and menu offer first


def fitting(window, source: str | None, port: str = "out") -> list[str]:
    """The types whose first input takes `source`'s output: what fits after it."""
    from puzzle.operators import accepts, output_type

    if not source:
        return []
    given = output_type(window.session.project, UUID(source), port)
    if given is None:
        return []
    return [t.key for t in TYPES.values() if t.inputs and any(accepts(p, given)
                                                               for p in t.inputs)]  # fmt: skip


def recent(window) -> list[str]:
    return list(getattr(window, "recent_operators", []))


def ordered() -> list:
    """Every type by group, groups in the order work flows (ADR 0064 item 6), each
    group's types alphabetical by their label (the maintainer, 2026-11-08)."""
    from puzzle.operators import GROUPS

    return sorted(TYPES.values(), key=lambda t: (GROUPS.index(t.group), t.label.casefold()))


class Palette(QDialog):
    """Find an operator by name (Tab, or a double-click on the empty canvas, as
    Grasshopper's and Blender's search): Enter or a double-click makes it. What fits
    the selected node's output comes first, then the recently used, then every type by
    group; a search also reads other tools' names ("cull pattern" finds Filter rows)."""

    def __init__(self, window, at=None, source=None, port="out") -> None:
        super().__init__(window)
        self.window, self.at, self.source, self.port = window, at, source, port
        self.setWindowTitle("Add an operator")
        self.made: str | None = None
        column = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Operator name: reduce, check, sort, cull…")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._make)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _item: self._make())
        fits = fitting(window, source, port)
        used = [k for k in recent(window) if k in TYPES]
        sections = []
        if fits:
            sections.append(("Fits here", [TYPES[k] for k in fits]))
        if used:
            sections.append(("Recently used", [TYPES[k] for k in used]))
        sections.append(("Every operator", ordered()))
        for title, types in sections:
            head = QListWidgetItem(title)
            head.setFlags(Qt.ItemFlag.NoItemFlags)  # a heading: not chosen
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            self.list.addItem(head)
            for t in types:
                item = QListWidgetItem(f"{t.label}  ·  {t.group}")
                item.setData(ROLE, t.key)
                item.setToolTip(t.help)
                self.list.addItem(item)
        self.help = QLabel("")
        self.help.setWordWrap(True)
        from puzzle.gui import theme

        theme.role(self.help, "muted")
        self.list.currentItemChanged.connect(self._described)
        column.addWidget(self.search)
        column.addWidget(self.list, 1)
        column.addWidget(self.help)
        self._filter("")
        self.resize(360, 380)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        first = named = None
        shown: set[str] = set()
        for i in range(self.list.count()):
            item = self.list.item(i)
            key = item.data(ROLE)
            if key is None:  # a heading: shown unless searching
                item.setHidden(bool(words))
                continue
            t = TYPES[key]
            alias = next((a for a in t.aliases if words and all(w in a for w in words)), None)
            hay = f"{t.label} {t.group} {t.help} {' '.join(t.aliases)}".lower()
            hide = not all(w in hay for w in words) or (bool(words) and key in shown)
            item.setHidden(hide)
            item.setText(f"{t.label}  ·  {t.group}" + (f"   (as {alias})" if alias and
                                                        not hide else ""))  # fmt: skip
            if hide:
                continue
            shown.add(key)
            if first is None:
                first = item
            if named is None and all(w in t.label.lower() for w in words):
                named = item  # a name that matches comes before a description that does
        first = named or first
        if first is not None:
            self.list.setCurrentItem(first)

    def _described(self, item, _old=None) -> None:
        key = item.data(ROLE) if item is not None else None
        self.help.setText(TYPES[key].help if key else "")

    def choose(self, key: str) -> str | None:
        """Make the operator of type `key` (the list's choice; tests call it)."""
        self.made = new_operator(self.window, key, self.source, self.port, self.at)
        self.accept()
        return self.made

    def _make(self) -> None:
        item = self.list.currentItem()
        if item is not None and not item.isHidden() and item.data(ROLE):
            self.choose(item.data(ROLE))


def open_palette(window, at=None, source=None, port="out") -> Palette:
    """The palette, wired from the selected node's first output that fits when there is
    one (a run selected, then Tab, Reduce: the Reduce reads that run)."""
    palette = Palette(window, at, source, port)
    window.last_palette = palette
    palette.open()
    palette.search.setFocus()
    return palette


# --- phase 2: what data operators drive ---------------------------------------------


def _driver(window, source: str, port: str) -> str | None:
    """The parameter name a source gives a history argument: a pre-solve number
    operator's own name, or a parameter set's parameter."""
    from puzzle.model.schema import Operator, ParameterSet
    from puzzle.operators import is_pre

    obj = window.session.project.objects.get(UUID(source))
    if isinstance(obj, ParameterSet):
        return port if port in obj.values else None
    outs = TYPES[obj.type].outputs if isinstance(obj, Operator) else ()
    if outs and outs[0].type == "number" and is_pre(window.session.project, obj):
        return obj.name
    return None


def drive_argument(window, source_node, port: str, geometry_id: str):
    """Wire a number into one of a geometry's history arguments (ADR 0062 item 5): the
    argument then names it, as arguments name parameters. One form: which argument."""
    from puzzle.gui.properties import format_value
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Geometry

    project = window.session.project
    source = source_node.object
    name = _driver(window, source, port if source_node.kind == "parameters" else "out")
    geo = project.objects.get(UUID(geometry_id))
    if name is None or not isinstance(geo, Geometry):
        window.statusBar().showMessage(
            "A number that reads no results (a Number, Slider or Expression, or a "
            "parameter) drives a history argument", 8000)  # fmt: skip
        return None
    options = []
    for i, step in enumerate(geo.history):
        for key, value in step.args.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) or (
                    isinstance(value, str) and value in _numbers(project)):  # fmt: skip
                options.append((f"{step.name}: {key} (now {format_value(value)})",
                                f"{i}:{key}"))  # fmt: skip
    if not options:
        window.statusBar().showMessage(f"{geo.name} has no history argument to drive", 8000)
        return None
    got = window.ask_form(f"Drive {geo.name} by {name}", [
        Ask("argument", "History argument", choices=options,
            help="It takes the value from now on; its own value is replaced")])  # fmt: skip
    if not got:
        return None
    index, key = got["argument"].split(":", 1)
    return window.submit(Edit(object=geometry_id, ops=[EditOp(
        op="set", path=f"history.{index}.args.{key}", value=name)]))  # fmt: skip


def _numbers(project) -> set[str]:
    from puzzle.operators import parameter_values

    return set(parameter_values(project))


def sweep_over(window, source: str, port: str, study_id: str):
    """A study swept over an operator's list (or a Number or Slider by name, its
    values listed): one form for the parameter it sets (ADR 0062 item 4)."""
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Operator, Study
    from puzzle.operators import output_type

    project = window.session.project
    op = project.objects.get(UUID(source))
    study = project.objects.get(UUID(study_id))
    given = output_type(project, UUID(source), port) if op is not None else None
    if not isinstance(op, Operator) or not isinstance(study, Study) or given != "list":
        window.statusBar().showMessage(
            "A list (a Series, Range or List) dropped on a study sweeps it", 8000)  # fmt: skip
        return None
    names = sorted(project.parameter_names())
    if not names:
        window.statusBar().showMessage(
            "A sweep sets a parameter: make a parameter set, a Number or a Slider first",
            8000)  # fmt: skip
        return None
    why = "One run per item of the list, each with this parameter at that value"
    got = window.ask_form(
        f"Sweep {study.name} over {op.name}",
        [Ask("parameter", "Parameter it sets", choices=[(n, n) for n in names], help=why)],
    )
    if not got:
        return None
    param = {"name": got["parameter"], "source": source, "port": port}
    sweeps = [i for i, s in enumerate(study.steps) if s.type == "parametric_sweep"]
    if sweeps:
        op_ = EditOp(op="append", path=f"steps.{sweeps[0]}.parameters", value=param)
    else:
        steps = [s.model_dump(mode="json") for s in study.steps]
        sweep = {"type": "parametric_sweep", "parameters": [param]}
        op_ = EditOp(op="set", path="steps", value=[sweep, *steps])
    return window.submit(Edit(object=study_id, ops=[op_]))


def read_csv(window, op, path: str | None = None):
    """A table's rows read from a CSV (ADR 0062): kept in the table, so the project
    holds them; read again to update."""
    from pathlib import Path

    if path is None:
        path = window.ask_open_path("Read a CSV into the table", "CSV (*.csv *.txt)") \
            if hasattr(window, "ask_open_path") else None  # fmt: skip
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8")
    from puzzle.operators.evaluate import parse_table

    try:
        parse_table(text)
    except PuzzleError as exc:
        window.report(exc)
        return None
    return window.submit(Edit(object=str(op.id), ops=[EditOp(op="set", path="text",
                                                             value=text)]))  # fmt: skip


def driven_components(project, op) -> list:
    """The components whose geometry an operator drives (its name, or a pre-solve
    operator reading it, named by a history argument)."""
    from puzzle.model.schema import Component, Geometry
    from puzzle.operators import downstream

    names = {op.name} | {project.objects[d].name for d in downstream(project, op.id)}
    geos = {g.id for g in project.of_type(Geometry)
            if any(v in names for s in g.history for v in s.args.values()
                   if isinstance(v, str))}  # fmt: skip
    return [c for c in project.of_type(Component) if any(p.geometry in geos for p in c.parts)]


def model_value_choices(project, op, path):
    """Model value's object (anything holding a value: materials first) and the values it
    holds, by their names (ADR 0071 step 3)."""
    from puzzle.operators.model_values import readable

    if path == "target":
        order = {"material": 0, "parameters": 1, "component": 2, "mesh": 3, "study": 4}
        found = [o for o in project.objects.values() if readable(project, o)]
        found.sort(key=lambda o: (order.get(o.kind, 9), o.name))
        from puzzle.schematic import KIND_WORDS

        return [(f"{o.name} ({KIND_WORDS.get(o.kind, o.kind).lower()})", str(o.id))
                for o in found]  # fmt: skip
    if path == "property":
        obj = project.objects.get(op.target) if op.target else None
        return [(r.label, r.path) for r in readable(project, obj)] if obj is not None else []
    return None
