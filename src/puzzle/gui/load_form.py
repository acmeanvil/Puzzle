"""A load or support in one form, on picked faces or a named selection (ADR 0090 UX-2).

Adding a pressure used to ask only "which face selection?", refuse when none was named
yet, and leave the value to be found in a folded Values group in Properties. Here one
form asks what it is (when not already chosen), where it goes and its values with
their units:

- "Applied to" offers the faces (edges, bodies, points) picked in the view, as a new
  selection named after the load, then the component's selections of that kind;
- with nothing picked and nothing named, picking starts with the right filter and a
  note says what to do; Add again uses the picks;
- the values are typed as "500 N" or a bare number in the unit shown (ADR 0024).

OK makes the selection (if new) and the feature as one batch: one history entry, one
undo. The same function serves the tree's Add load or support menus, the Advisor's buttons,
the pick bar's Add load or support… and Model → Physics and loads.
"""

from __future__ import annotations

from puzzle.commands.model import Edit, EditOp
from puzzle.model.schema import Component, Geometry, Quantity

PICKED = "\x00picked"  # the "Applied to" choice that makes a selection of the picks


def _short(spec) -> str:
    return spec.label.split(" (")[0]


def value_label(spec, key: str) -> str:
    """'Force x', 'Pressure', 'Temperature': a value's row in the form."""
    if len(spec.values) == 1:
        return _short(spec)
    return f"{_short(spec)} {key}"


def _picked(window, comp: Component, entity: str):
    """(part, geometry, member names) of what is picked on `comp`, when it is of
    `entity` and on one part; else None."""
    viewport = window.viewport
    if viewport.pick_kind != entity or not viewport.picked:
        return None
    if window.mesh_component != str(comp.id):
        return None
    picks = window.picked_names()
    parts = {p for p, _ in picks}
    if len(parts) != 1:
        return None
    part = next(p for p in comp.parts if p.name == parts.pop())
    geo = window.session.project.objects.get(part.geometry)
    if not isinstance(geo, Geometry):
        return None
    members = []
    for _, name in picks:
        for n in name if isinstance(name, (list, tuple)) else [name]:
            if n not in members:
                members.append(n)
    return part, geo, members


def _new_name(geo: Geometry, spec) -> str:
    taken = {s.name for s in geo.selections}
    n = 1
    while f"{_short(spec)} {n}" in taken:
        n += 1
    return f"{_short(spec)} {n}"


def specs_for(comp: Component, index: int, entity: str | None = None) -> list:
    """The loads and supports a component's physics takes, optionally of one kind of
    entity."""
    from puzzle.model.catalog import CATALOG

    physics = comp.physics[index].type
    return [s for s in CATALOG.get(physics, []) if entity is None or s.entity == entity]


def add_load(window, comp: Component, index: int, spec=None, entity: str | None = None):
    """The one form for a load or support (`spec` given: that one; else chosen in the
    form among those of `entity`). Returns the Outcome, or None."""
    from puzzle.gui.menus import selections_of
    from puzzle.gui.quick_form import Ask
    from puzzle.model.units import parse_number, parse_quantity, pretty, shown
    from puzzle.settings import current

    project = window.session.project
    specs = [spec] if spec is not None else specs_for(comp, index, entity)
    if not specs:
        return None
    entities = {s.entity for s in specs}
    kind = entities.pop() if len(entities) == 1 else (entity or "face")
    picked = _picked(window, comp, kind)
    named = selections_of(project, comp, kind)
    if picked is None and not named:  # nothing to put it on: pick first
        window.toggle_picking(True)
        bar = window.pick_bar.filter
        if bar.findData(kind) >= 0:
            bar.setCurrentIndex(bar.findData(kind))
        words = {"face": "faces", "edge": "edges", "body": "bodies", "vertex": "points"}[kind]
        window.toasts.show(f"Pick the {words} in the view (double-click), then add "
                           f"{_short(specs[0]).lower()} again", state="info")  # fmt: skip
        return None
    units = current().display_units()
    where = []
    if picked is not None:
        from puzzle.gui.window import _count

        part, _geo, members = picked
        where.append((f"The {_count(len(members), kind)} picked on {part.name} "
                      "(a new selection)", PICKED))  # fmt: skip
    where += [(name, name) for name in named]
    by_type = {s.type: s for s in specs}
    asks = []
    if len(specs) > 1:
        asks.append(Ask("type", "What", choices=[(s.label, s.type) for s in specs]))
    asks.append(Ask("selection", "Applied to", choices=where))
    for s in specs:
        for key, (value, unit) in s.values.items():
            v, u = shown(value, unit, units) if unit != "dimensionless" else (value, "")
            asks.append(Ask(
                f"{s.type}:{key}", value_label(s, key), kind="text",
                default=f"{v:g} {pretty(u)}".strip(),
                shown=(None if len(specs) == 1
                       else (lambda vals, t=s.type: vals.get("type") == t)),
            ))  # fmt: skip

    def chosen(vals):
        return by_type[vals.get("type", specs[0].type)]

    def read(vals) -> dict[str, Quantity]:
        """The values typed, as quantities; ValueError naming the row that is wrong."""
        s = chosen(vals)
        out = {}
        for key, (_value, unit) in s.values.items():
            text = vals.get(f"{s.type}:{key}", "")
            try:
                if unit == "dimensionless":
                    out[key] = Quantity(value=parse_number(text), unit=unit)
                    continue
                number, typed = parse_quantity(text, shown(0.0, unit, units)[1])
            except ValueError as exc:
                raise ValueError(f"{value_label(s, key)}: {exc}") from None
            out[key] = Quantity(value=number, unit=typed)
        return out

    def check(vals) -> str | None:
        try:
            read(vals)
        except ValueError as exc:
            return str(exc)
        return None

    title = f"Add {_short(specs[0]).lower()}" if len(specs) == 1 else "Add load or support"
    got = window.ask_form(title, asks, check=check)
    if got is None:
        return None
    s = chosen(got)
    commands = []
    selection = got["selection"]
    if selection == PICKED:
        part, geo, members = picked
        name = _new_name(geo, s)
        value = {
            "name": name,
            "entity": kind,
            "members": members,
            "anchors": window._anchors_of(members),
        }
        commands.append(
            Edit(object=str(geo.id), ops=[EditOp(op="append", path="selections", value=value)])
        )
        selection = f"{part.name}/{name}"
    feature = s.feature(selection)
    feature.values = read(got)
    op = EditOp(op="append", path=f"physics.{index}.features",
                value=feature.model_dump(mode="json"))  # fmt: skip
    commands.append(Edit(object=str(comp.id), ops=[op]))
    out = window.submit_batch(commands)
    if out is not None and got["selection"] == PICKED:
        window.viewport.clear_picks()
        window.viewport.pick_history.clear()  # the load is what undo takes back
    return out


# UX-15: a physics' loads and supports in groups a person looks in, the common ones
# first, rather than 25 items in schema order. Types not listed land in "More".
GROUPS = {
    "solid_mechanics": (
        ("", ("fixed", "force", "pressure")),
        ("Supports", ("displacement", "elastic_support", "compression_only_support",
                      "remote_displacement", "symmetry", "antisymmetry", "node_spring")),
        ("Loads", ("remote_force", "remote_force_rigid", "traction", "line_load")),
        ("Body loads", ("gravity", "acceleration", "rotation", "body_temperature",
                        "body_force", "initial_stress")),
        ("Bolts", ("bolt_pretension", "bolt_adjustment")),
        ("Explicit dynamics", ("initial_velocity", "velocity", "rigid_wall")),
    ),
}  # fmt: skip
# What a feature needs of the component's studies to mean anything (greyed out else).
NEEDS_STEP = {"initial_velocity": "explicit", "velocity": "explicit", "rigid_wall": "explicit"}
MENU_TITLE = "Add load or support"


def fill_menu(window, menu, comp: Component, index: int, add=None) -> None:
    """A physics' loads and supports into `menu`, grouped (UX-15); `add(menu, text,
    slot, tip)` makes an item (the menu bar's `_add`), else a plain action."""
    from PySide6.QtWidgets import QMenu

    from puzzle.model.catalog import specs
    from puzzle.model.schema import Study

    physics = comp.physics[index].type
    by_type = {s.type: s for s in specs(physics)}
    steps = {st.type for s in window.session.project.of_type(Study)
             if s.component == comp.id for st in s.solve_steps()}  # fmt: skip

    def item(where, s) -> None:
        tip = ""
        need = NEEDS_STEP.get(s.type)
        if add is not None:
            action = add(where, s.label, lambda sp=s: add_load(window, comp, index, sp), tip)
        else:
            action = where.addAction(s.label)
            action.triggered.connect(lambda _=False, sp=s: add_load(window, comp, index, sp))
        if need and need not in steps:
            action.setEnabled(False)
            action.setToolTip(f"Only in a study with an {need} step")

    groups = GROUPS.get(physics)
    if groups is None:  # a short list: as it is
        for s in by_type.values():
            item(menu, s)
        return
    placed = set()
    for title, types in groups:
        chosen = [by_type[t] for t in types if t in by_type]
        placed |= {s.type for s in chosen}
        if not chosen:
            continue
        where = menu
        if title:
            where = QMenu(title, menu)
            where.setToolTipsVisible(True)
            menu.addMenu(where)
        for s in chosen:
            item(where, s)
        if not title:
            menu.addSeparator()
    rest = [s for t, s in by_type.items() if t not in placed]
    if rest:
        more = QMenu("More", menu)
        menu.addMenu(more)
        for s in rest:
            item(more, s)
