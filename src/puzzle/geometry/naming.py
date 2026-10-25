"""Robust naming (G3, ADR 0014): every selection member resolved, re-found or flagged.

A regenerated shape names its faces and bodies (`history.NamedShape`). This module
adds the rest of the naming:

- **Edges and points are named by the faces that meet there**: the edge between
  "Plate/+x" and "Plate/+z" is "Plate/+x|Plate/+z"; a corner is "A|B|C". Such names
  follow the faces through every edit the face names survive (a hole resized, a
  fillet added elsewhere), unlike the file-order "edge-N". Where two edges join the
  same faces (a seam), "#2", "#3" ... follow in midpoint order.
- **Anchors** record where each member was when it was selected (kind, measure,
  centre; `NamedSelection.anchors`). They re-find a member whose name is gone, and
  check names that only count positions ("face-7" of an imported file): if the file
  was replaced, "face-7" may now be another face, and the anchor says so.
- **Rules** (`NamedSelection.rule`) describe the members instead of listing them:
  {"plane": {"axis": "x", "at": 0}} or {"radius": 0.004}; they are evaluated on every
  regeneration, so a selection with a rule cannot detach.

`resolve` reports each member as kept, re-found (a confident signature match) or
detached (with ranked candidates, never guessed). `suggest_rule` offers the rule that
picks exactly a reattached set, so the next regeneration resolves it unaided.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX

from puzzle.geometry.history import NamedShape, _bounds, _size, _static, _subshapes

ENTITIES = ("body", "face", "edge", "vertex")
# Names that only count positions in a file: they may point elsewhere once it changes.
POSITIONAL = re.compile(r"(^|[/|])(face|edge|vertex|body)-\d+($|[|#])")
SURFACES = {
    GeomAbs_Plane: "Plane",
    GeomAbs_Cylinder: "Cylinder",
    GeomAbs_Cone: "Cone",
    GeomAbs_Sphere: "Sphere",
    GeomAbs_Torus: "Torus",
}
CURVES = {GeomAbs_Line: "Line", GeomAbs_Circle: "Circle"}
# Gmsh's entity types, in the same coarse kinds (anchors made from a preview mesh).
GMSH_KINDS = {
    "Plane": "Plane",
    "Cylinder": "Cylinder",
    "Cone": "Cone",
    "Sphere": "Sphere",
    "Torus": "Torus",
    "Line": "Line",
    "Circle": "Circle",
    "Point": "Point",
    "Volume": "Solid",
}


def coarse_kind(dim: int, gmsh_type: str) -> str:
    """A Gmsh entity type as an anchor kind."""
    if dim in (0, 3):
        return {0: "Point", 3: "Solid"}[dim]
    return GMSH_KINDS.get(gmsh_type, "Other")


class _Index:
    """Shapes as keys, the same sub-shape in any orientation being one key."""

    def __init__(self) -> None:
        self._buckets: dict[int, list[tuple[object, list]]] = {}

    def get(self, shape) -> list:
        for s, values in self._buckets.setdefault(hash(shape), []):
            if s.IsSame(shape):
                return values
        values: list = []
        self._buckets[hash(shape)].append((shape, values))
        return values


@dataclass
class Named:
    """Every named entity of a shape, by entity type: name -> sub-shapes."""

    shape: object
    of: dict[str, dict[str, list]]
    size: float

    def signature(self, entity: str, name: str) -> list:
        return anchor(entity, self.of[entity][name])


def name_all(named: NamedShape) -> Named:
    """Faces and bodies as the history named them; edges and points by their faces."""
    label = _Index()
    for name, faces in sorted(named.names.items()):
        for f in faces:
            label.get(f).append(name)
    of = {"body": dict(named.bodies), "face": dict(named.names)}
    if not _subshapes(named.shape, TopAbs_FACE):  # a line body (beams): named by position
        for entity, kind in (("edge", TopAbs_EDGE), ("vertex", TopAbs_VERTEX)):
            of[entity] = {
                f"{entity}-{i}": [s] for i, s in enumerate(_subshapes(named.shape, kind), start=1)
            }
        return Named(named.shape, of, max(_size(named.shape), 1e-12))
    for entity, kind in (("edge", TopAbs_EDGE), ("vertex", TopAbs_VERTEX)):
        meets = _Index()
        order = []
        for f in _subshapes(named.shape, TopAbs_FACE):
            names = label.get(f)
            first = min(names) if names else "?"
            for sub in _subshapes(f, kind):
                around = meets.get(sub)
                if not around:
                    order.append(sub)
                if first not in around:
                    around.append(first)
        groups: dict[str, list] = {}
        for sub in order:
            groups.setdefault("|".join(sorted(meets.get(sub))), []).append(sub)
        out: dict[str, list] = {}
        for base, subs in groups.items():
            subs = sorted(subs, key=lambda s: tuple(round(c, 9) for c in _centre(s)))
            for i, sub in enumerate(subs, start=1):
                out[base if i == 1 else f"{base}#{i}"] = [sub]
        of[entity] = out
    return Named(named.shape, of, max(_size(named.shape), 1e-12))


# --- anchors: where a member was ---------------------------------------------------------


def anchor(entity: str, shapes: list) -> list:
    """[kind, measure, x, y, z] of one member (several pieces: summed, centre weighted)."""
    kinds, total, moment = set(), 0.0, [0.0, 0.0, 0.0]
    for s in shapes:
        kind, measure, centre = props(entity, s)
        kinds.add(kind)
        weight = measure if measure > 0 else 1.0
        total += measure
        moment = [m + weight * c for m, c in zip(moment, centre, strict=True)]
    weights = total if total > 0 else len(shapes)
    kind = kinds.pop() if len(kinds) == 1 else "Mixed"
    return [kind, total, *(m / max(weights, 1e-300) for m in moment)]


def props(entity: str, s) -> tuple[str, float, tuple[float, float, float]]:
    props = GProp_GProps()
    if entity == "vertex":
        from OCP.BRep import BRep_Tool

        p = _static(BRep_Tool, "Pnt")(s)
        return "Point", 0.0, (p.X(), p.Y(), p.Z())
    if entity == "edge":
        _static(BRepGProp, "LinearProperties")(s, props)
        kind = CURVES.get(BRepAdaptor_Curve(s).GetType(), "Other")
    elif entity == "face":
        _static(BRepGProp, "SurfaceProperties")(s, props)
        kind = SURFACES.get(BRepAdaptor_Surface(s).GetType(), "Other")
    else:
        _static(BRepGProp, "VolumeProperties")(s, props)
        kind = "Solid"
    c = props.CentreOfMass()
    return kind, props.Mass(), (c.X(), c.Y(), c.Z())


def _centre(s) -> tuple[float, float, float]:
    entity = {TopAbs_EDGE: "edge", TopAbs_VERTEX: "vertex", TopAbs_FACE: "face"}.get(
        s.ShapeType(), "body"
    )
    return props(entity, s)[2]


def distance(a: list, b: list, size: float) -> float:
    """How far apart two anchors are: relative measure change plus centre shift over
    the model size; infinite across kinds."""
    if a[0] != b[0]:
        return math.inf
    dm = abs(a[1] - b[1]) / max(abs(a[1]), abs(b[1]), 1e-300) if (a[1] or b[1]) else 0.0
    return dm + math.dist(a[2:5], b[2:5]) / size


# What counts as "the same entity" without a name: measure within 0.1%, centre within
# 1e-5 of the model size (re-exports differ in the last digits, not more).
SAME = 1e-3 + 1e-5


@dataclass
class Member:
    name: str
    status: str  # kept | refound | detached | rule
    now: str | None = None  # the name it resolves to
    candidates: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class SelectionReport:
    selection: str
    entity: str
    members: list[Member]
    rule: dict | None = None

    @property
    def resolved(self) -> list[str]:
        return [m.now for m in self.members if m.now]

    @property
    def detached(self) -> list[Member]:
        return [m for m in self.members if m.status == "detached"]

    def to_dict(self) -> dict:
        return {
            "selection": self.selection,
            "entity": self.entity,
            "rule": self.rule,
            "members": [
                {
                    "name": m.name,
                    "status": m.status,
                    "now": m.now,
                    "candidates": [{"name": n, "distance": round(d, 6)} for n, d in m.candidates],
                }
                for m in self.members
            ],
        }


def resolve(selections, named: Named, aliases: dict[str, str] | None = None):
    """Each selection's members on the regenerated shape (see the module docstring).

    `aliases`: older names still accepted for a member (the file's "edge-9" for what is
    now "A|B"); the report keeps the stored name and resolves to the current one."""
    return [resolve_one(sel, named, aliases or {}) for sel in selections]


def resolve_one(sel, named: Named, aliases: dict[str, str] | None = None) -> SelectionReport:
    names = named.of.get(sel.entity, {})
    aliases = aliases or {}
    if sel.rule:
        hits = by_rule(named, sel.entity, sel.rule)
        return SelectionReport(
            sel.name, sel.entity, [Member(n, "rule", n) for n in hits], rule=dict(sel.rule)
        )
    signatures: dict[str, list] = {}

    def sig(n: str) -> list:
        if n not in signatures:
            signatures[n] = anchor(sel.entity, names[n])
        return signatures[n]

    members, taken = [], set()
    for m in sel.members:
        mark = sel.anchors.get(m)
        current = m if m in names else aliases.get(m)
        if current in names and not (
            mark and POSITIONAL.search(m) and distance(mark, sig(current), named.size) > SAME
        ):
            members.append(Member(m, "kept", current))
            taken.add(current)
            continue
        if not mark:
            members.append(Member(m, "detached"))
            continue
        ranked = sorted(
            ((n, distance(mark, sig(n), named.size)) for n in names),
            key=lambda t: t[1],
        )
        ranked = [(n, d) for n, d in ranked if math.isfinite(d)][:5]
        best = ranked[0] if ranked else None
        unique = best and (len(ranked) < 2 or ranked[1][1] > 10 * max(best[1], 1e-12))
        if best and best[1] <= SAME and unique and best[0] not in taken:
            members.append(Member(m, "refound", best[0], ranked))
            taken.add(best[0])
        else:
            members.append(Member(m, "detached", None, ranked))
    return SelectionReport(sel.name, sel.entity, members)


# --- rules ---------------------------------------------------------------------------------


def by_rule(named: Named, entity: str, rule: dict) -> list[str]:
    """Names of the entities a rule picks, in name order. Conditions combine:

    {"plane": {"axis": "x", "at": 0.0}}   lying in the plane x = 0 (faces, edges, points)
    {"radius": 0.004}                     cylindrical faces or circular edges of radius r
    {"kind": "Cylinder"}                  of one surface or curve kind
    """
    tol = max(1e-6 * named.size, 1e-6)  # OCC bounding boxes are padded (CLAUDE.md)
    out = []
    for name, shapes in named.of.get(entity, {}).items():
        if all(_obeys(entity, s, rule, tol) for s in shapes):
            out.append(name)
    if not out:
        return []
    return sorted(out)


def _obeys(entity: str, s, rule: dict, tol: float) -> bool:
    if "plane" in rule:
        i = "xyz".index(rule["plane"]["axis"])
        b = _bounds(s)
        at = rule["plane"]["at"]
        if abs(b[i] - at) > tol or abs(b[i + 3] - at) > tol:
            return False
    if "kind" in rule and props(entity, s)[0] != rule["kind"]:
        return False
    if "radius" in rule:
        r = _radius(entity, s)
        if r is None or abs(r - rule["radius"]) > 1e-6 * max(rule["radius"], 1e-9):
            return False
    return True


def _radius(entity: str, s) -> float | None:
    if entity == "face":
        surface = BRepAdaptor_Surface(s)
        return surface.Cylinder().Radius() if surface.GetType() == GeomAbs_Cylinder else None
    if entity == "edge":
        curve = BRepAdaptor_Curve(s)
        return curve.Circle().Radius() if curve.GetType() == GeomAbs_Circle else None
    return None


def suggest_rule(named: Named, entity: str, members: list[str]) -> dict | None:
    """A rule that picks exactly these members, if a simple one does: a common plane,
    or a common radius (cylinders, circles). None when no rule fits."""
    shapes = [s for m in members for s in named.of.get(entity, {}).get(m, [])]
    if not shapes or entity == "body":
        return None
    want = sorted(set(members))
    tol = max(1e-6 * named.size, 1e-6)
    tries = []
    boxes = [_bounds(s) for s in shapes]
    for i, axis in enumerate("xyz"):
        lo, hi = min(b[i] for b in boxes), max(b[i + 3] for b in boxes)
        if hi - lo <= 2 * tol:
            tries.append({"plane": {"axis": axis, "at": round((lo + hi) / 2, 12)}})
    radii = {_radius(entity, s) for s in shapes}
    if len(radii) == 1 and None not in radii:
        r = radii.pop()
        tries.append({"radius": round(r, 12)})
        tries += [{"radius": round(r, 12), **t} for t in tries if "plane" in t]
    for rule in tries:
        if by_rule(named, entity, rule) == want:
            return rule
    return None


def describe_rule(rule: dict) -> str:
    """A rule in words, for the GUI: "on the plane x = 0 mm, radius 4 mm"."""
    parts = []
    if "plane" in rule:
        p = rule["plane"]
        parts.append(f"on the plane {p['axis']} = {p['at'] * 1e3:.4g} mm")
    if "radius" in rule:
        parts.append(f"radius {rule['radius'] * 1e3:.4g} mm")
    if "kind" in rule:
        parts.append(rule["kind"].lower())
    return ", ".join(parts)


# --- a project geometry --------------------------------------------------------------------


def named_geometry(project, geometry, root) -> Named:
    """A project geometry's named entities: its history regenerated, or its file read as
    a one-step import (names "face-N", as the mesher names them)."""
    from pathlib import Path

    from puzzle.geometry.history import Op, ops_of, regenerate
    from puzzle.model.schema import ParameterSet

    params = {k: q.to_si() for ps in project.of_type(ParameterSet) for k, q in ps.values.items()}
    if geometry.history:
        return name_all(regenerate(ops_of(geometry), params, root))
    shape = regenerate([Op("import", "F", {"file": str(Path(root) / geometry.source)})])
    plain = NamedShape(
        shape.shape,
        {n.removeprefix("F/"): fs for n, fs in shape.names.items()},
        {"body-1": shape.solids()}
        if len(shape.solids()) == 1
        else {f"body-{i}": [s] for i, s in enumerate(shape.solids(), start=1)},
    )
    return name_all(plain)


@dataclass
class Resolved:
    """A project geometry with its selections resolved, as the mesher uses it."""

    named: Named
    path: object  # the file the mesher reads (a BREP of a history, or the source file)
    files: dict[str, dict[str, list[str]]]  # entity -> persistent name -> file names
    back: dict[str, str]  # file name -> persistent name
    reports: list[SelectionReport]


def resolve_geometry(project, geometry, root, work_dir) -> Resolved:
    """Regenerate (a history, to a BREP in `work_dir`) or read (a file) the geometry,
    match its persistent names to the file's entities by measure and centre (exact:
    both describe the same shape), and resolve every selection. Members stored by
    file name before G3 (edge-9) resolve through those names still."""

    from puzzle.geometry.session import occ_lock

    with occ_lock:
        return _resolve_geometry(project, geometry, root, work_dir)


def _resolve_geometry(project, geometry, root, work_dir) -> Resolved:
    from pathlib import Path

    from puzzle.geometry.history import write_brep
    from puzzle.geometry.model import import_step

    named = named_geometry(project, geometry, root)
    if geometry.history:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "-" for c in geometry.name)
        path = Path(work_dir) / f"{safe}.brep"
        write_brep(named.shape, path)
    else:
        path = Path(root) / geometry.source
    files = _file_names(named, import_step(path))
    back = {f: name for entity in files.values() for name, fs in entity.items() for f in fs}
    return Resolved(named, path, files, back, resolve(geometry.selections, named, back))


def check_selections(project, geometry, root) -> Resolved:
    """A geometry's selections resolved in a scratch folder (the GUI, the agent, the
    CLI): what the reattach list shows."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="puzzle-naming-") as work:
        return resolve_geometry(project, geometry, root, work)


def _file_names(named: Named, model) -> dict[str, dict[str, list[str]]]:
    """Persistent names -> the file's entity names (face-N ...), per entity type, by
    measure and centre (vectorised: a large import has thousands of faces)."""
    import numpy as np

    from puzzle.geometry.model import ENTITY_DIM

    tol = 1e-6 * max(model.size, 1e-12)
    out: dict[str, dict[str, list[str]]] = {}
    for entity in ENTITIES:
        pool = model.of_dim(ENTITY_DIM[entity])
        names: dict[str, list[str]] = {}
        out[entity] = names
        if not pool:
            continue
        centres = np.array([e.center for e in pool])
        measures = np.array([e.measure for e in pool])
        for name, shapes in named.of.get(entity, {}).items():
            for s in shapes:
                _, measure, centre = props(entity, s)
                d = np.linalg.norm(centres - np.asarray(centre), axis=1)
                ok = (np.abs(measures - measure) <= 1e-6 * max(abs(measure), 1e-30)) & (d <= tol)
                if ok.any():
                    hit = pool[int(np.argmin(np.where(ok, d, np.inf)))].name
                    if hit not in names.setdefault(name, []):
                        names[name].append(hit)
    return out


def reattach_ops(geometry, choices: dict[str, dict], named: Named, reports=None) -> list:
    """Edit ops that apply reattachment choices to a geometry's selections.

    `choices`: selection name -> {"replace": {old: new or None}, "rule": dict or None}.
    New members get fresh anchors; a rule replaces the member list. With `reports`,
    members kept under an older name (edge-9) are stored under their current one too.
    """
    from puzzle.commands.model import EditOp

    renamed = {
        r.selection: {m.name: m.now for m in r.members if m.status == "kept" and m.now != m.name}
        for r in reports or []
    }
    ops = []
    for i, sel in enumerate(geometry.selections):
        choice = dict(choices.get(sel.name) or {})
        if renamed.get(sel.name):
            choice["replace"] = {**renamed[sel.name], **choice.get("replace", {})}
        if not choice:
            continue
        if choice.get("rule"):
            ops.append(EditOp(op="set", path=f"selections.{i}.rule", value=choice["rule"]))
            ops.append(EditOp(op="set", path=f"selections.{i}.anchors", value={}))
            ops.append(
                EditOp(
                    op="set",
                    path=f"selections.{i}.members",
                    value=by_rule(named, sel.entity, choice["rule"]),
                )
            )
            continue
        replace = choice.get("replace", {})
        members = []
        for m in sel.members:
            new = replace.get(m, m)
            if new and new not in members:
                members.append(new)
        anchors = {
            m: (
                sel.anchors[m]
                if m in sel.anchors and m not in replace
                else named.signature(sel.entity, m)
            )
            for m in members
            if m in named.of.get(sel.entity, {})
        }
        ops.append(EditOp(op="set", path=f"selections.{i}.members", value=members))
        ops.append(EditOp(op="set", path=f"selections.{i}.anchors", value=anchors))
    return ops


def rules_for(geometry, choices: dict[str, dict], named: Named) -> dict[str, dict]:
    """For each selection the choices change, the rule (if any) that picks exactly its
    members afterwards: offered so the next regeneration resolves it unaided."""
    out = {}
    for sel in geometry.selections:
        choice = choices.get(sel.name)
        if not choice or sel.rule:
            continue
        replace = choice.get("replace", {})
        members = [replace.get(m, m) for m in sel.members]
        members = [m for m in dict.fromkeys(members) if m and m in named.of.get(sel.entity, {})]
        rule = suggest_rule(named, sel.entity, members) if members else None
        if rule:
            out[sel.name] = rule
    return out


def accept_all(reports: list[SelectionReport]) -> dict[str, dict]:
    """Choices taking every re-found member's new name and each detached member's best
    candidate (an empty choice for selections with nothing to change)."""
    choices = {}
    for r in reports:
        replace = {}
        for m in r.members:
            if m.status == "refound" or (m.status == "detached" and m.candidates):
                replace[m.name] = m.now or m.candidates[0][0]
        if replace:
            choices[r.selection] = {"replace": replace}
    return choices


__all__ = [
    "ENTITIES",
    "Named",
    "SelectionReport",
    "accept_all",
    "anchor",
    "by_rule",
    "check_selections",
    "coarse_kind",
    "describe_rule",
    "name_all",
    "props",
    "named_geometry",
    "reattach_ops",
    "resolve",
    "resolve_geometry",
    "rules_for",
    "suggest_rule",
]
