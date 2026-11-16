"""Selection by rule… (ADR 0062 phase 3; ADR 0072 section 10): a selection whose
members are described, not listed, in one form: inside a box, near a point, facing a
direction, faces of an area range, or a union, intersection or difference of other
selections. The members it picks are counted and lit in the view as the values change
(`naming.resolve`, as the mesher resolves them), and OK makes it by one Edit appending
it to the geometry's selections, the command an agent's `submit` sends.

Points and directions start from the picks when something is picked on the part: the
first pick's centre, a picked flat face's normal. Lengths are in the units shown.
"""

from __future__ import annotations

from uuid import UUID

from puzzle.gui.quick_form import QuickForm

ENTITIES = [("Faces", "face"), ("Edges", "edge"), ("Points", "vertex"), ("Bodies", "body")]
RULES = [
    ("Inside a box", "box"),
    ("Near a point", "near"),
    ("Facing a direction (flat faces)", "normal"),
    ("Area between (faces)", "area"),
    ("Union of selections", "union"),
    ("Intersection of selections", "intersection"),
    ("Difference: the first less the others", "difference"),
]
SETS = ("union", "intersection", "difference")
NONE = ("Nothing", "")


def rule_of(values: dict) -> dict:
    """The rule (SI) a filled form describes; ValueError says what is wrong."""
    from puzzle.settings import area_unit, length_unit

    _, per_m = length_unit()
    _, per_m2 = area_unit()
    how = values.get("rule")

    def point(key: str, scale: float = 1.0) -> list[float]:
        parts = [p for p in str(values.get(key, "")).replace(";", ",").split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError("a point or direction is three numbers: x, y, z")
        return [float(p) / scale for p in parts]

    def number(key: str, scale: float = 1.0) -> float:
        return float(str(values.get(key, "")).strip()) / scale

    if how == "box":
        low, high = point("low", per_m), point("high", per_m)
        if any(a > b for a, b in zip(low, high, strict=True)):
            raise ValueError("the box's low corner is below its high corner on every axis")
        return {"box": {"low": low, "high": high}}
    if how == "near":
        d = number("distance", per_m)
        if d <= 0:
            raise ValueError("the distance is above zero")
        return {"near": {"point": point("point", per_m), "distance": d}}
    if how == "normal":
        direction = point("direction")
        if not any(direction):
            raise ValueError("the direction is not zero")
        return {"normal": {"direction": direction, "within": number("within")}}
    if how == "area":
        out = {}
        if str(values.get("min_area", "")).strip():
            out["min_area"] = number("min_area", per_m2)
        if str(values.get("max_area", "")).strip():
            out["max_area"] = number("max_area", per_m2)
        if not out:
            raise ValueError("give a smallest area, a largest, or both")
        return out
    if how in SETS:
        names = list(dict.fromkeys(n for n in (values.get("first"), values.get("second"),
                                               values.get("third")) if n))  # fmt: skip
        if len(names) < 2:
            raise ValueError("choose two selections or more")
        return {how: names}
    raise ValueError("choose a rule")


def command(geometry, name: str, entity: str, rule: dict):
    """The Edit adding the selection: what OK submits and what an agent would."""
    from puzzle.commands.model import Edit, EditOp

    value = {"name": name, "entity": entity, "members": [], "rule": rule}
    return Edit(object=str(geometry.id), ops=[EditOp(op="append", path="selections",
                                                     value=value)])  # fmt: skip


def members(project, geometry, named, name: str, entity: str, rule: dict) -> list[str]:
    """The entities the rule picks now, resolved with the geometry's own selections (a
    set reads them) and its data inputs (ADR 0062)."""
    from puzzle.geometry.naming import resolve, rule_inputs
    from puzzle.model.schema import NamedSelection

    trial = NamedSelection(name=name, entity=entity, rule=rule)
    selections = [*geometry.selections, trial]
    reports = resolve(rule_inputs(project, selections), named)
    return reports[-1].resolved


class RuleForm(QuickForm):
    """The one-dialog form with a line counting the members, lit in the view."""

    def __init__(self, title, asks, intro, check, window, *, preview=None) -> None:
        self.preview_with = preview
        self.matches = None
        super().__init__(title, asks, intro, check, window)
        from PySide6.QtWidgets import QLabel

        from puzzle.gui.theme import role

        self.matches = role(QLabel(""), "muted")
        self.matches.setWordWrap(True)
        self.layout().insertWidget(self.layout().count() - 1, self.matches)
        self._refresh()

    def _refresh(self) -> None:
        super()._refresh()
        if self.matches is not None and self.preview_with is not None:
            self.matches.setText(self.preview_with(self.values()))


def selection_by_rule(window, geometry=None):
    """Ask the rule and its values, show what it picks, make it (one Edit)."""
    from puzzle.gui.agent_prefill import run_form
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Geometry
    from puzzle.settings import area_unit, length_unit

    project = window.session.project
    geos = [(g.name, str(g.id)) for g in project.of_type(Geometry)]
    if not geos:
        window.statusBar().showMessage("Make a part first: Geometry → New part…", 8000)
        return None
    start = str(geometry.id) if geometry is not None else _picked_geometry(window) or geos[0][1]
    unit, _ = length_unit()
    area, _ = area_unit()
    named_cache: dict[str, object] = {}
    at, facing = _from_picks(window, start, named_cache)

    def geo_of(v):
        return project.objects.get(UUID(v.get("geometry") or start))

    def others(v) -> list:
        geo = geo_of(v)
        return [(s.name, s.name) for s in getattr(geo, "selections", [])
                if s.entity == v.get("entity")]  # fmt: skip

    def taken(v) -> set:
        return {s.name for s in getattr(geo_of(v), "selections", [])}

    def problem(v) -> str | None:
        if not v.get("name"):
            return "Give it a name"
        if v["name"] in taken(v):
            return f"{geo_of(v).name} already has a selection '{v['name']}'"
        try:
            rule_of(v)
        except ValueError as exc:
            return str(exc)
        return None

    def preview(v) -> str:
        """The count and the first names of what it picks; lit in the view."""
        try:
            rule = rule_of(v)
            geo = geo_of(v)
            named = _named(window, geo, named_cache)
            found = members(project, geo, named, v.get("name") or "(new)", v["entity"], rule)
        except Exception as exc:  # noqa: BLE001 - incomplete values, or a geometry that fails
            return f"Picks nothing yet: {getattr(exc, 'message', exc)}"
        highlight(window, geo, v["entity"], found)
        shown = ", ".join(found[:6]) + (", …" if len(found) > 6 else "")
        kind = next(t for t, k in ENTITIES if k == v["entity"]).lower()
        if len(found) == 1:
            kind = "body" if kind == "bodies" else kind[:-1]
        return f"Picks {len(found)} {kind}" + (f": {shown}" if found else "")

    def prompt(v) -> str:
        try:
            said = f"the rule {rule_of(v)} (SI units)"
        except ValueError:
            said = "a rule"
        geo = geo_of(v)
        return (f"Make a selection '{v.get('name')}' of {v.get('entity')}s on "
                f"{geo.name if geo else 'the part'} by {said}, and check what it picks "
                "(selections_check).")  # fmt: skip

    def rule_is(*kinds):
        return lambda v: v.get("rule") in kinds

    asks = [
        Ask("geometry", "Part", choices=geos, default=start),
        Ask("entity", "Of", choices=ENTITIES, default="face"),
        Ask("rule", "Rule", choices=RULES, default="box"),
        Ask("low", f"Box from x, y, z ({unit})", kind="text", default="0, 0, 0",
            shown=rule_is("box")),
        Ask("high", f"Box to x, y, z ({unit})", kind="text", default=at or "1, 1, 1",
            shown=rule_is("box")),
        Ask("point", f"Point x, y, z ({unit})", kind="text", default=at or "0, 0, 0",
            shown=rule_is("near"), help="From the first pick's centre when one is picked"),
        Ask("distance", f"Within ({unit})", kind="text", default="1", shown=rule_is("near"),
            help="An entity's centre this close to the point"),
        Ask("direction", "Direction x, y, z", kind="text", default=facing or "0, 0, 1",
            shown=rule_is("normal"), help="Flat faces whose outward normal is this way; "
            "a picked flat face's normal when one is picked"),
        Ask("within", "Within (degrees)", kind="text", default="1", shown=rule_is("normal")),
        Ask("min_area", f"Smallest area ({area})", kind="text", default="",
            shown=rule_is("area"), help="Leave empty for no lower bound"),
        Ask("max_area", f"Largest area ({area})", kind="text", default="",
            shown=rule_is("area"), help="Leave empty for no upper bound"),
        Ask("first", "Selection", choices=lambda v: others(v) or [NONE],
            shown=rule_is(*SETS)),
        Ask("second", "And", choices=lambda v: others(v) or [NONE], shown=rule_is(*SETS)),
        Ask("third", "And (optional)", choices=lambda v: [NONE, *others(v)], default="",
            shown=rule_is(*SETS)),
        Ask("name", "Name", kind="text", default="By rule"),
    ]  # fmt: skip
    got = run_form(window, "Selection by rule", asks, help="form:selection_rule",
                   prompt=prompt, check=problem, dialog_class=_with_preview(preview),
                   intro="Its members are found again from the rule every time the part "
                   "changes, so it never detaches.")  # fmt: skip
    if not got:
        return None
    geo = geo_of(got)
    return window.submit_batch([command(geo, got["name"], got["entity"], rule_of(got))])


def _with_preview(preview):
    def make(title, asks, intro, check, parent):
        return RuleForm(title, asks, intro, check, parent, preview=preview)

    return make


def _named(window, geo, cache: dict):
    """The part's named entities, regenerated once per form."""
    key = str(geo.id)
    if key not in cache:
        from puzzle.geometry.naming import named_geometry
        from puzzle.geometry.session import occ_lock

        with occ_lock:
            cache[key] = named_geometry(window.session.project, geo, window.session.root)
    return cache[key]


def highlight(window, geo, entity: str, found: list[str]) -> list:
    """The rule's members lit on the mesh or preview shown (the part on its own when no
    component shows it); the entities lit, for tests."""
    from puzzle.gui.viewport import KIND_DIM

    project = window.session.project
    comp = None
    try:
        comp = project.objects.get(UUID(window.mesh_component or ""))
    except ValueError:
        comp = None
    if comp is None or not any(p.geometry == geo.id for p in getattr(comp, "parts", [])):
        comp = window._standalone(geo)
    cid = str(comp.id)
    mesh = window.meshes.get(cid) or window._preview(comp)
    if mesh is None:
        return []
    parts = {p.name for p in comp.parts if p.geometry == geo.id}
    wanted, dim = set(found), KIND_DIM[entity]
    names = window.mesh_names.get(cid, {})
    hits = [e for e, (p, n) in names.items() if p in parts and e[0] == dim
            and any(x in wanted for x in (n if isinstance(n, list) else [n]))]  # fmt: skip
    if dim == 3:  # a body shows through the faces that bound it
        boundaries = window.mesh_boundaries.get(cid, {})
        hits = [f for e in hits for f in boundaries.get(e, [])]
    window.viewport.show_mesh(mesh, None, hits)
    return hits


def _picked_geometry(window) -> str | None:
    """The geometry of what is picked, if one geometry."""
    try:
        comp = window.session.project.objects.get(UUID(window.mesh_component or ""))
    except ValueError:
        comp = None
    comp = comp or next((c for c, _ in window._standalones.values()
                         if str(c.id) == window.mesh_component), None)  # fmt: skip
    picks = window.picked_names() if comp is not None else []
    parts = {p.name: str(p.geometry) for p in getattr(comp, "parts", [])}
    geos = {parts[p] for p, _ in picks if p in parts}
    return geos.pop() if len(geos) == 1 else None


def _from_picks(window, geo_id: str, cache: dict) -> tuple[str | None, str | None]:
    """The first pick's centre and a picked flat face's normal, as typed text in the
    units shown, when what is picked lies on this geometry."""
    if _picked_geometry(window) != geo_id:
        return None, None
    from puzzle.geometry.naming import _plane_normal, props
    from puzzle.settings import length_unit

    _, per_m = length_unit()
    geo = window.session.project.objects.get(UUID(geo_id))
    kind = window.viewport.pick_kind
    try:
        named = _named(window, geo, cache)
    except Exception:  # noqa: BLE001 - a geometry that fails: no defaults from it
        return None, None
    at = facing = None
    for _, n in window.picked_names():
        for name in n if isinstance(n, list) else [n]:
            shapes = named.of.get(kind, {}).get(name)
            if not shapes:
                continue
            if at is None:
                at = ", ".join(f"{c * per_m:.6g}" for c in props(kind, shapes[0])[2])
            normal = _plane_normal(kind, shapes[0])
            if facing is None and normal is not None:
                facing = ", ".join(f"{c:.6g}" for c in normal)
    return at, facing
