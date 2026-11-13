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


def bolt_key(component, connector) -> str:
    """A bolt (fastener) as the view state names it: by component and its name."""
    return f"{component.id}|{connector.name}"


@dataclass
class Visibility:
    root: str
    shown: set[str] = field(default_factory=set)  # systems and construction drawn always
    hidden_features: set[str] = field(default_factory=set)
    hidden_bolts: set[str] = field(default_factory=set)  # bolts not drawn (`bolt_key`)

    @classmethod
    def load(cls, root: Path) -> Visibility:
        key = str(Path(root).resolve())
        try:
            data = json.loads(store_path().read_text(encoding="utf-8")).get(key, {})
        except (OSError, ValueError, AttributeError):
            data = {}
        return cls(key, set(data.get("shown", [])), set(data.get("hidden_features", [])),
                   set(data.get("hidden_bolts", [])))  # fmt: skip

    def save(self) -> None:
        path = store_path()
        try:
            every = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(every, dict):
                every = {}
        except (OSError, ValueError):
            every = {}
        every[self.root] = {"shown": sorted(self.shown),
                            "hidden_features": sorted(self.hidden_features),
                            "hidden_bolts": sorted(self.hidden_bolts)}  # fmt: skip
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

    def hide_bolts(self, keys, hidden: bool = True) -> None:
        keys = set(keys)
        self.hidden_bolts = self.hidden_bolts | keys if hidden else self.hidden_bolts - keys
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
    v.hidden_bolts = set()
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


def colour_bodies(window, geo, bodies, colour: str | None = "ask") -> object:
    """A colour for some bodies of one geometry (a body selection's, from the tree);
    None takes their own colour away, back to the geometry's."""
    if colour == "ask":
        colour = window.pick_colour(next((geo.body_colours[b] for b in bodies
                                          if b in geo.body_colours), geo.colour))  # fmt: skip
        if colour is None:
            return None
    own = dict(geo.body_colours)
    for b in bodies:
        if colour is None:
            own.pop(b, None)
        else:
            own[b] = colour
    return _geometry_edits(window, [(geo, "body_colours", own)])


# --- hiding from the tree: bodies, bolts and groups ---------------------------------------
# A Thing is what the view can leave out. Geometries and bodies are the model's (one
# command or batch, so one undo); symbols, bolts and the systems drawn always are view
# state. Groups hide what they hold, through the same Things.


@dataclass(frozen=True)
class Thing:
    kind: str  # "geometry", "body", "feature", "bolt" or "extra"
    ref: str  # a geometry's id (geometry, body), a feature's or bolt's key, an object's id
    body: str = ""  # a body's name


def bodies_of(window, geo) -> list[str]:
    """A geometry's bodies by name: its body selections' members, and the bodies of any
    mesh or preview made of it."""
    from puzzle.model.schema import Component

    names = {m for s in geo.selections if s.entity == "body" for m in s.members}
    names |= set(geo.hidden_bodies)
    comps = list(window.session.project.of_type(Component))
    comps += [c for c, _ in getattr(window, "_standalones", {}).values()]
    for c in comps:
        parts = {p.name for p in c.parts if p.geometry == geo.id}
        for e, (part, name) in window.mesh_names.get(str(c.id), {}).items() if parts else ():
            if e[0] == 3 and part in parts:
                names.add(name)
    return sorted(names)


def _object_things(window, obj) -> list[Thing]:
    from puzzle.model.schema import Component, Geometry

    project = window.session.project
    if isinstance(obj, Geometry):
        return [Thing("geometry", str(obj.id))]
    if isinstance(obj, Component):  # a component shows as its parts' geometry
        return [Thing("geometry", str(p.geometry)) for p in obj.parts
                if isinstance(project.objects.get(p.geometry), Geometry)]  # fmt: skip
    if isinstance(obj, _kinds()):
        return [Thing("extra", str(obj.id))]
    return []


def key_things(window, scope: str, key: str, seen=()) -> list[Thing]:
    """What a group's member `key` in `scope` (see `tree.container_key`) can hide."""
    from puzzle.gui.menus import _uuid
    from puzzle.model.schema import Component, Geometry, TreeGroup

    project = window.session.project
    obj = project.objects.get(_uuid(key))
    if isinstance(obj, TreeGroup):
        if key in seen:
            return []
        return [t for m in obj.members for t in key_things(window, obj.section, m, (*seen, key))]
    if obj is not None:
        return _object_things(window, obj)
    kind, _, name = key.partition(":")
    oid, _, physics = scope.partition("/")
    owner = project.objects.get(_uuid(oid))
    if isinstance(owner, Geometry) and kind == "selection":
        sel = next((s for s in owner.selections if s.name == name), None)
        if sel is not None and sel.entity == "body":
            return [Thing("body", str(owner.id), b) for b in sel.members]
    elif isinstance(owner, Component) and kind == "part":
        part = next((p for p in owner.parts if p.name == name), None)
        if part is not None and isinstance(project.objects.get(part.geometry), Geometry):
            return [Thing("geometry", str(part.geometry))]
    elif isinstance(owner, Component) and kind == "connector":
        found = next((c for c in owner.connectors if c.name == name), None)
        return [Thing("bolt", bolt_key(owner, found))] if found is not None else []
    elif isinstance(owner, Component) and kind == "feature" and physics:
        ftype, _, selection = name.partition(":")
        return [Thing("feature", feature_key(owner, ph, f)) for ph in owner.physics
                if ph.type == physics for f in ph.features
                if f.type == ftype and f.selection == selection]  # fmt: skip
    return []


def scope_keys(window, scope: str) -> list[str]:
    """Every member key a scope holds (what "others" means for a group)."""
    from puzzle.gui.menus import _uuid
    from puzzle.gui.tree import SECTIONS
    from puzzle.model.schema import Component, Geometry

    project = window.session.project
    cls = dict(SECTIONS).get(scope)
    if cls is not None:
        return [str(o.id) for o in project.of_type(cls)]
    oid, _, physics = scope.partition("/")
    owner = project.objects.get(_uuid(oid))
    if isinstance(owner, Geometry):
        return [f"selection:{s.name}" for s in owner.selections]
    if isinstance(owner, Component) and physics:
        return [f"feature:{f.type}:{f.selection}" for ph in owner.physics if ph.type == physics
                for f in ph.features]  # fmt: skip
    if isinstance(owner, Component):
        return [f"part:{p.name}" for p in owner.parts] + [
            f"connector:{c.name}" for c in owner.connectors]  # fmt: skip
    return []


def is_hidden(window, thing: Thing) -> bool:
    from puzzle.gui.menus import _uuid

    v = window.visibility
    if thing.kind in ("geometry", "body"):
        geo = window.session.project.objects.get(_uuid(thing.ref))
        if geo is None:
            return False
        return geo.hidden or (thing.kind == "body" and thing.body in geo.hidden_bodies)
    if thing.kind == "feature":
        return thing.ref in v.hidden_features
    if thing.kind == "bolt":
        return thing.ref in v.hidden_bolts
    return thing.ref not in v.shown  # a system or construction object drawn only selected


def set_hidden(window, hide=(), show=()) -> object:
    """Hide some Things and show others: geometries and bodies as one command (or one
    batch), the view state after; then the tree and the view again."""
    from puzzle.gui.menus import _uuid

    project = window.session.project
    flags: dict = {}  # geometry id -> [hidden, hidden bodies]
    v = window.visibility

    def state(ref):
        if ref not in flags:
            geo = project.objects.get(_uuid(ref))
            if geo is None:
                return None
            flags[ref] = [geo.hidden, set(geo.hidden_bodies)]
        return flags[ref]

    for on, things in ((True, hide), (False, show)):
        for t in things:
            if t.kind == "geometry" and (s := state(t.ref)) is not None:
                s[0] = on
                if not on:
                    s[1].clear()  # shown: all of it
            elif t.kind == "body" and (s := state(t.ref)) is not None:
                if on:
                    s[1].add(t.body)
                else:
                    s[1].discard(t.body)
                    if s[0]:  # a body of a hidden geometry: it alone is shown
                        geo = project.objects.get(_uuid(t.ref))
                        others = set(bodies_of(window, geo)) - {
                            x.body for x in show if x.kind == "body" and x.ref == t.ref
                        }
                        s[0], s[1] = False, s[1] | others
            elif t.kind == "feature":
                v.hidden_features = v.hidden_features | {t.ref} if on else (
                    v.hidden_features - {t.ref})  # fmt: skip
            elif t.kind == "bolt":
                v.hidden_bolts = v.hidden_bolts | {t.ref} if on else v.hidden_bolts - {t.ref}
            elif t.kind == "extra":
                v.shown = v.shown - {t.ref} if on else v.shown | {t.ref}
    v.save()
    changes = []
    for ref, (hidden, bodies) in flags.items():
        geo = project.objects[_uuid(ref)]
        changes += [(geo, "hidden", hidden), (geo, "hidden_bodies", sorted(bodies))]
    out = _geometry_edits_batch(window, changes)
    _redraw(window)
    return out


def _geometry_edits_batch(window, changes) -> object:
    """As `_geometry_edits`, with a geometry's several fields in one Edit."""
    from puzzle.commands.model import Edit, EditOp

    by: dict = {}
    for g, path, value in changes:
        if getattr(g, path) != value:
            by.setdefault(str(g.id), []).append(EditOp(op="set", path=path, value=value))
    edits = [Edit(object=gid, ops=ops) for gid, ops in by.items()]
    if not edits:
        return None
    return window.submit(edits[0]) if len(edits) == 1 else window.submit_batch(edits)


def all_geometry_things(window) -> list[Thing]:
    """Every geometry and every body: "everything drawn", as Hide all others sees it."""
    from puzzle.model.schema import Geometry

    out = []
    for g in _of_kind(window, Geometry):
        out.append(Thing("geometry", str(g.id)))
        out += [Thing("body", str(g.id), b) for b in bodies_of(window, g)]
    return out


def node_things(window, node) -> list[Thing]:
    """What a tree item can hide: a geometry, a body selection's bodies, a part, a bolt,
    a load's symbol, a system, or everything a group holds."""
    from puzzle.gui.menus import _uuid
    from puzzle.gui.tree import member_key

    project = window.session.project
    obj = project.objects.get(_uuid(node.object)) if node.object else None
    if node.kind == "group":
        return key_things(window, "", node.object)
    if node.kind == "object":
        return _object_things(window, obj) if obj is not None else []
    item = window.tree.item_for(node)
    scope = window.tree.scope_of(item) if item is not None else None
    key = member_key(node)
    return key_things(window, scope, key) if scope and key else []


def hide_others(window, node) -> object:
    """Everything like this item (bolts for a bolt, symbols for a symbol) hidden but it;
    for a body or geometry, every other geometry and body."""
    mine = node_things(window, node)
    if not mine:
        return None
    kinds = {t.kind for t in mine}
    holding = {t.ref for t in mine if t.kind == "body"}  # geometries a shown body is in
    allowed = kinds | ({"geometry"} if holding else set())
    keep = set(mine)

    def other(t) -> bool:
        if t in keep or t.kind not in allowed:
            return False
        if t.kind == "geometry":
            return t.ref not in holding
        return t.kind != "body" or t.ref in holding  # other geometries hide whole

    return set_hidden(window, [t for t in _pool(window, node, kinds) if other(t)], mine)


def _pool(window, node, kinds) -> list[Thing]:
    """The things "others" and "all" range over: the item's scope, and every geometry
    and body when it holds geometry."""
    pool = _scope_pool(window, node)
    if kinds & {"geometry", "body"}:
        pool += all_geometry_things(window)
    return list(dict.fromkeys(pool))


def _scope_pool(window, node) -> list[Thing]:
    from puzzle.gui.menus import _uuid
    from puzzle.model.schema import TreeGroup

    item = window.tree.item_for(node)
    if node.kind == "group":
        group = window.session.project.objects.get(_uuid(node.object))
        scope = group.section if isinstance(group, TreeGroup) else None
    else:
        scope = window.tree.scope_of(item) if item is not None else None
    if scope is None:
        return []
    return [t for k in scope_keys(window, scope) for t in key_things(window, scope, k)]


def show_all_like(window, node) -> object:
    """Everything like this item shown: every geometry and body for a body or geometry,
    else everything of its kinds in its scope."""
    mine = node_things(window, node)
    kinds = {t.kind for t in mine}
    allowed = kinds | ({"geometry"} if "body" in kinds else set())
    pool = _pool(window, node, kinds) + mine
    return set_hidden(window, (), list(dict.fromkeys(t for t in pool if t.kind in allowed)))


def hide_bolts(window, comp, connectors, hidden: bool = True) -> None:
    window.visibility.hide_bolts([bolt_key(comp, c) for c in connectors], hidden)
    _redraw(window)


def toggle_selected(window) -> None:
    """Show or hide what the tree has selected (the menu bar's way to the right-click)."""
    from puzzle.gui import menus
    from puzzle.model.schema import Component, Geometry

    node = window.tree.current()
    if node is None or window.session is None:
        return
    project = window.session.project
    obj = project.objects.get(menus._uuid(node.object)) if node.object else None
    if node.kind in ("selection", "connector", "group"):
        things = node_things(window, node)
        if things:
            hidden = all(is_hidden(window, t) for t in things)
            set_hidden(window, () if hidden else things, things if hidden else ())
            return
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
    elif isinstance(obj, Geometry) and node.kind == "selection":
        sel = obj.selections[int(node.path.split(".")[1])]
        if sel.entity == "body" and sel.members:  # a body of an assembly, from the tree
            things = node_things(window, node)
            hidden = all(is_hidden(window, t) for t in things)
            entries += [
                ("Show" if hidden else "Hide",
                 lambda: set_hidden(window, () if hidden else things, things if hidden else ())),
                ("Hide all others", lambda: hide_others(window, node)),
                ("Show all", lambda: show_all_like(window, node)),
                ("Colour…", lambda: colour_bodies(window, obj, sel.members)),
            ]  # fmt: skip
            if any(b in obj.body_colours for b in sel.members):
                entries.append(("Reset colour",
                                lambda: colour_bodies(window, obj, sel.members, None)))  # fmt: skip
    elif isinstance(obj, Component) and node.kind == "connector":
        c = obj.connectors[int(node.path.split(".")[1])]
        hidden = bolt_key(obj, c) in window.visibility.hidden_bolts
        entries += [("Show bolt" if hidden else "Hide bolt",
                     lambda: hide_bolts(window, obj, [c], not hidden)),
                    ("Hide all other bolts", lambda: hide_others(window, node)),
                    ("Show all bolts",
                     lambda: hide_bolts(window, obj, obj.connectors, False))]  # fmt: skip
        if c.group:
            same = [b for b in obj.connectors if b.group == c.group]
            entries += [(f"Hide bolt group {c.group}", lambda: hide_bolts(window, obj, same)),
                        (f"Show bolt group {c.group}",
                         lambda: hide_bolts(window, obj, same, False))]  # fmt: skip
    elif isinstance(obj, Component) and node.kind == "object" and obj.connectors:
        entries += [("Hide all bolts", lambda: hide_bolts(window, obj, obj.connectors)),
                    ("Show all bolts",
                     lambda: hide_bolts(window, obj, obj.connectors, False))]  # fmt: skip
    elif node.kind == "group":
        things = node_things(window, node)
        if things:
            hidden = all(is_hidden(window, t) for t in things)
            entries += [
                ("Show what it holds" if hidden else "Hide what it holds",
                 lambda: set_hidden(window, () if hidden else things, things if hidden else ())),
                ("Hide all others", lambda: hide_others(window, node)),
                ("Show all", lambda: show_all_like(window, node)),
            ]  # fmt: skip
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
