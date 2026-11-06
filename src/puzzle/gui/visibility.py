"""What the model view draws besides the selection: view state, never the model.

Coordinate systems and construction paths and planes are drawn while selected; Show
keeps one drawn over the model with the mesh. Supports and loads are drawn as symbols;
Hide leaves one out (the view bar's Supports and loads toggle still turns them all off).
Neither changes a solve, so both live per user and project in
`<user data dir>/visibility.json`, as the schematic's layout does, and never in the
project's files or history. Hidden geometry and bodies are the model's
(`Geometry.hidden`, `hidden_bodies`), set by commands, since a project's look travels
with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def store_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "visibility.json"


def feature_key(component, physics, feature) -> str:
    """A feature as the view state names it: features have no name or id of their own,
    so by component, physics, type and selection (stable when others are added)."""
    return f"{component.id}|{physics.type}|{feature.type}|{feature.selection}"


@dataclass
class Visibility:
    root: str
    shown: set[str] = field(default_factory=set)  # systems and construction drawn always
    hidden_features: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, root: Path) -> Visibility:
        key = str(Path(root).resolve())
        try:
            data = json.loads(store_path().read_text(encoding="utf-8")).get(key, {})
        except (OSError, ValueError, AttributeError):
            data = {}
        return cls(key, set(data.get("shown", [])), set(data.get("hidden_features", [])))

    def save(self) -> None:
        path = store_path()
        try:
            every = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(every, dict):
                every = {}
        except (OSError, ValueError):
            every = {}
        every[self.root] = {"shown": sorted(self.shown),
                            "hidden_features": sorted(self.hidden_features)}  # fmt: skip
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(every, indent=1), encoding="utf-8")
        tmp.replace(path)

    # --- what menus change -------------------------------------------------------------

    def show(self, ids, on: bool = True) -> None:
        """Draw these systems or construction objects always (or only when selected)."""
        ids = {str(i) for i in ids}
        self.shown = self.shown | ids if on else self.shown - ids
        self.save()

    def hide_features(self, keys, hidden: bool = True) -> None:
        keys = set(keys)
        self.hidden_features = self.hidden_features | keys if hidden else (
            self.hidden_features - keys)  # fmt: skip
        self.save()


# --- what menus do (the tree's right-click and Geometry → Show and suppress) --------------


def _redraw(window) -> None:
    window.refresh()  # the tree's marks
    window._selected()  # the view


def _kinds():
    from puzzle.model.schema import ConstructionGeometry, CoordinateSystem

    return CoordinateSystem, ConstructionGeometry


def _of_kind(window, kind) -> list:
    return list(window.session.project.of_type(kind))


def show_objects(window, objects, on: bool = True) -> None:
    """Draw coordinate systems or construction geometry always (on), or only while
    selected."""
    window.visibility.show([o.id for o in objects], on)
    _redraw(window)


def show_only(window, keep) -> None:
    """Of the systems and construction geometry, only `keep` is drawn always."""
    window.visibility.shown = {str(keep.id)}
    window.visibility.save()
    _redraw(window)


def _features(comp):
    return [(ph, f) for ph in comp.physics for f in ph.features]


def hide_features(window, comp, pairs, hidden: bool = True) -> None:
    window.visibility.hide_features([feature_key(comp, ph, f) for ph, f in pairs], hidden)
    _redraw(window)


def hide_other_features(window, comp, keep) -> None:
    """Every symbol of the component hidden but this feature's."""
    keys = [feature_key(comp, ph, f) for ph, f in _features(comp)]
    mine = feature_key(comp, *keep)
    window.visibility.hidden_features = (window.visibility.hidden_features | set(keys)) - {mine}
    window.visibility.save()
    _redraw(window)


def show_everything(window) -> None:
    """Every geometry, body and symbol drawn, and every system and construction object
    drawn always: one batch for the model's part (so one undo), the view state after."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Geometry

    edits = []
    for g in _of_kind(window, Geometry):
        ops = []
        if g.hidden:
            ops.append(EditOp(op="set", path="hidden", value=False))
        if g.hidden_bodies:
            ops.append(EditOp(op="set", path="hidden_bodies", value=[]))
        if ops:
            edits.append(Edit(object=str(g.id), ops=ops))
    if edits:
        window.submit(edits[0]) if len(edits) == 1 else window.submit_batch(edits)
    v = window.visibility
    v.hidden_features = set()
    v.shown = {str(o.id) for kind in _kinds() for o in _of_kind(window, kind)}
    v.save()
    _redraw(window)


# --- bodies and colours (the model: commands) --------------------------------------------


def _geometry_edits(window, changes) -> object:
    """[(geometry, path, value)] as one command (or one batch), so it undoes at once."""
    from puzzle.commands.model import Edit, EditOp

    edits = [Edit(object=str(g.id), ops=[EditOp(op="set", path=path, value=value)])
             for g, path, value in changes if getattr(g, path) != value]  # fmt: skip
    if not edits:
        return None
    out = window.submit(edits[0]) if len(edits) == 1 else window.submit_batch(edits)
    window._selected()
    return out


def picked_bodies(window) -> list[tuple]:
    """The bodies picked in the view (the Bodies filter), as (geometry, [body names])."""
    from puzzle.model.schema import Geometry

    comp, names = window._shown_component(window.viewport.mesh)
    geos = window._part_geometries(comp)
    out: dict = {}  # by geometry id: models aren't hashable
    for e in window.viewport.picked:
        part, name = names.get(tuple(e), (None, None))
        if e[0] == 3 and part in geos and isinstance(geos[part], Geometry):
            out.setdefault(str(geos[part].id), (geos[part], []))[1].append(name)
    return list(out.values())


def hide_picked_bodies(window) -> object:
    picked = picked_bodies(window)
    if not picked:
        window.statusBar().showMessage("Pick bodies first (Pick, Bodies, double-click)", 6000)
        return None
    window.viewport.clear_picks()
    return _geometry_edits(window, [(g, "hidden_bodies", sorted(set(g.hidden_bodies) | set(b)))
                                    for g, b in picked])  # fmt: skip


def show_all_bodies(window) -> object:
    from puzzle.model.schema import Geometry

    return _geometry_edits(window, [(g, "hidden_bodies", []) for g in
                                    _of_kind(window, Geometry) if g.hidden_bodies])  # fmt: skip


def colour_geometry(window, geometries, colour: str | None = "ask") -> object:
    """A colour for these geometries' parts (asked for by default); None resets it to the
    theme's. Their own body colours are cleared, so the whole part takes it."""
    if colour == "ask":
        colour = window.pick_colour(geometries[0].colour if geometries else None)
        if colour is None:
            return None
    changes = [(g, "colour", colour) for g in geometries]
    changes += [(g, "body_colours", {}) for g in geometries if g.body_colours]
    return _geometry_edits(window, changes)


def colour_picked_bodies(window, colour: str | None = "ask") -> object:
    picked = picked_bodies(window)
    if not picked:
        window.statusBar().showMessage("Pick bodies first (Pick, Bodies, double-click)", 6000)
        return None
    if colour == "ask":
        colour = window.pick_colour()
        if colour is None:
            return None
    changes = []
    for g, bodies in picked:
        own = dict(g.body_colours)
        for b in bodies:
            if colour is None:
                own.pop(b, None)
            else:
                own[b] = colour
        changes.append((g, "body_colours", own))
    return _geometry_edits(window, changes)


def toggle_selected(window) -> None:
    """Show or hide what the tree has selected (the menu bar's way to the right-click)."""
    from puzzle.gui import menus
    from puzzle.model.schema import Component, Geometry

    node = window.tree.current()
    if node is None or window.session is None:
        return
    project = window.session.project
    obj = project.objects.get(menus._uuid(node.object)) if node.object else None
    if isinstance(obj, _kinds()) and node.kind == "object":
        show_objects(window, [obj], str(obj.id) not in window.visibility.shown)
    elif isinstance(obj, Geometry):
        menus.show_geometry(window, [obj], obj.hidden)
    elif isinstance(obj, Component) and node.kind == "part":
        geo = project.objects.get(obj.parts[int(node.path.split(".")[1])].geometry)
        if isinstance(geo, Geometry):
            menus.show_geometry(window, [geo], geo.hidden)
    elif isinstance(obj, Component) and node.kind == "feature":
        i, j = int(node.path.split(".")[1]), int(node.path.split(".")[3])
        pair = (obj.physics[i], obj.physics[i].features[j])
        hidden = feature_key(obj, *pair) in window.visibility.hidden_features
        hide_features(window, obj, [pair], not hidden)
    else:
        window.statusBar().showMessage(
            "Select a coordinate system, construction object, load, support or part", 6000
        )


# --- the tree's right-click ----------------------------------------------------------------


def add_to_menu(window, menu, node, obj) -> None:
    """Show, hide and colour items for what the tree item is (D2, D4)."""
    from puzzle.model.schema import Component, Geometry

    cs_kind, built_kind = _kinds()
    entries = []
    if node.kind == "project":
        entries.append(("Show everything", lambda: show_everything(window)))
    elif node.kind == "section" and node.label in ("Coordinate systems", "Construction geometry"):
        kind = cs_kind if node.label == "Coordinate systems" else built_kind
        what = node.label.lower()  # the same words as Geometry → Show and suppress
        entries += [
            (f"Show all {what}", lambda: show_objects(window, _of_kind(window, kind), True)),
            (f"Hide all {what}", lambda: show_objects(window, _of_kind(window, kind), False)),
        ]
    elif isinstance(obj, cs_kind | built_kind) and node.kind == "object":
        on = str(obj.id) in window.visibility.shown
        entries += [("Hide" if on else "Show", lambda: show_objects(window, [obj], not on)),
                    ("Hide all others", lambda: show_only(window, obj))]  # fmt: skip
    elif isinstance(obj, Geometry) and node.kind == "object":
        entries.append(("Colour…", lambda: colour_geometry(window, [obj])))
        if obj.colour or obj.body_colours:
            entries.append(("Reset colour", lambda: colour_geometry(window, [obj], None)))
        if obj.hidden_bodies:
            entries.append(("Show hidden bodies", lambda: _geometry_edits(
                window, [(obj, "hidden_bodies", [])])))  # fmt: skip
    elif isinstance(obj, Component) and node.kind == "part":
        from puzzle.gui import menus

        part = obj.parts[int(node.path.split(".")[1])]
        geo = window.session.project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            entries += [("Show" if geo.hidden else "Hide",
                         lambda: menus.show_geometry(window, [geo], geo.hidden)),
                        ("Hide all others", lambda: menus.hide_others(window, geo)),
                        ("Colour…", lambda: colour_geometry(window, [geo]))]  # fmt: skip
            if geo.colour or geo.body_colours:
                entries.append(("Reset colour", lambda: colour_geometry(window, [geo], None)))
    elif isinstance(obj, Component) and node.kind == "physics":
        ph = obj.physics[int(node.path.split(".")[1])]
        pairs = [(ph, f) for f in ph.features]
        entries += [
            ("Show its symbols", lambda: hide_features(window, obj, pairs, False)),
            ("Hide its symbols", lambda: hide_features(window, obj, pairs, True)),
        ]
    elif isinstance(obj, Component) and node.kind == "feature":
        i, j = int(node.path.split(".")[1]), int(node.path.split(".")[3])
        pair = (obj.physics[i], obj.physics[i].features[j])
        hidden = feature_key(obj, *pair) in window.visibility.hidden_features
        entries += [("Show symbol" if hidden else "Hide symbol",
                     lambda: hide_features(window, obj, [pair], not hidden)),
                    ("Hide all other symbols", lambda: hide_other_features(window, obj, pair)),
                    ("Show all symbols",
                     lambda: hide_features(window, obj, _features(obj), False))]  # fmt: skip
    if not entries:
        return
    from puzzle.gui.icons import icon

    first = menu.actions()[0] if menu.actions() else None
    for text, slot in entries:
        action = menu.addAction(text)
        action.triggered.connect(lambda _=False, f=slot: f())
        word = text.split()[0]
        if word in ("Show", "Hide", "Colour…"):
            action.setIcon(icon({"Show": "show", "Hide": "hide", "Colour…": "colour"}[word]))
    if first is not None:
        menu.insertSeparator(menu.actions()[-len(entries)])


def mark(item, hidden: bool, words: str) -> None:
    """A tree item drawn as hidden (muted, the eye-off icon), or shown always (the eye)."""
    from PySide6.QtGui import QColor

    from puzzle.gui import theme
    from puzzle.gui.icons import icon

    if hidden:
        item.setForeground(0, QColor(theme.current().muted))
        item.setIcon(0, icon("hide"))
    else:
        item.setIcon(0, icon("show"))
    tip = item.toolTip(0)
    item.setToolTip(0, f"{tip}; {words}" if tip else words)
