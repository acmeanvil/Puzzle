"""Welds from a picked edge (ADR 0078 decision 7), as the bolt generator makes bolts.

`plan(project, comp, root, part, edges, leg, ...)`: the edges are on one part (by their
names, "B/-x|B/-z": the two faces they join). Of each edge's two faces, the one lying
on another part (the seat: a lapped plate's underside, a bracket's foot) tells which
part the weld joins and on which of its faces the other leg lies; the edge's other face
carries the first leg. Every edge must reach the same other part. `commands(...)` is
one batch (one undo): the selections (the path, each leg's faces) and the weld.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puzzle.errors import PuzzleError


@dataclass
class WeldPlan:
    part: str
    other: str
    edges: list[str]
    leg_faces: list[str]  # on `part`
    base_faces: list[str]  # on `other`
    notes: list[str] = field(default_factory=list)


def plan(project, comp, root, part: str, edges: list[str]) -> WeldPlan:
    """Where a weld along `edges` of `part` goes (module docstring)."""
    from puzzle.geometry.contacts import find_contacts

    if part not in {p.name for p in comp.parts}:
        raise PuzzleError(f"no part '{part}' in {comp.name}", code="not_found")
    if not edges:
        raise PuzzleError("pick the edges the weld runs along", code="missing_input")
    touching = find_contacts(project, comp, root)
    others, legs, bases = set(), [], []
    for edge in edges:
        faces = edge.split("|")
        if len(faces) != 2:
            raise PuzzleError(f"'{edge}' is not an edge between two faces (A|B)",
                              code="invalid")  # fmt: skip
        seated = []
        for pair in touching:
            for mine, my_face, theirs, their_face in (
                (pair.source_part, pair.source_face, pair.target_part, pair.target_face),
                (pair.target_part, pair.target_face, pair.source_part, pair.source_face),
            ):
                if mine == part and my_face in faces and theirs != part:
                    seated.append((my_face, theirs, their_face))
        if not seated:
            raise PuzzleError(
                f"edge '{edge}' of {part} doesn't sit on another part: neither of its faces "
                "touches one (a fillet's edge lies on the part it joins)", code="invalid",
            )  # fmt: skip
        seat = seated[0][0]
        if len({s[0] for s in seated}) > 1:
            raise PuzzleError(f"both faces of edge '{edge}' touch other parts: pick the "
                              "edge at the joint's corner", code="invalid")  # fmt: skip
        legs.append(next(f for f in faces if f != seat))
        for _f, theirs, their_face in seated:
            others.add(theirs)
            bases.append(their_face)
    if len(others) != 1:
        raise PuzzleError(f"the edges reach {sorted(others)}: one weld joins two parts",
                          code="invalid")  # fmt: skip
    return WeldPlan(part, others.pop(), list(edges), list(dict.fromkeys(legs)),
                    list(dict.fromkeys(bases)))  # fmt: skip


def _selection(geometry, entity: str, members: list[str], name: str):
    """An existing selection holding just `members`, else a new one -> (name, new?)."""
    for s in geometry.selections:
        if s.entity == entity and sorted(s.members) == sorted(members) and not s.rule:
            return s.name, False
    taken = {s.name for s in geometry.selections}
    out, k = name, 2
    while out in taken:
        out, k = f"{name} {k}", k + 1
    return out, True


def commands(project, comp, planned: WeldPlan, *, name: str, leg, leg2=None,
             type: str = "fillet", groove=None, pattern: str = "continuous",
             segment=None, pitch=None, filler=None) -> list:  # fmt: skip
    """The batch making the selections and the weld (a `Weld`'s fields as given)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Geometry, NamedSelection, Weld

    if any(w.name == name for w in comp.welds):
        raise PuzzleError(f"{comp.name} has a weld '{name}' already", code="exists")
    by_part = {p.name: project.get(p.geometry, Geometry) for p in comp.parts}
    out, chosen = [], {}
    wanted = [
        (planned.part, "edge", planned.edges, f"{name} path"),
        (planned.part, "face", planned.leg_faces, f"{name} leg"),
        (planned.other, "face", planned.base_faces, f"{name} base"),
    ]
    adds: dict = {}
    for part, entity, members, label in wanted:
        geo = by_part[part]
        made, new = _selection(geo, entity, members, label)
        chosen[(part, entity, tuple(members))] = f"{part}/{made}"
        if new:
            adds.setdefault(geo.id, []).append(
                NamedSelection(name=made, entity=entity, members=members)
            )
    for gid, selections in adds.items():
        out.append(Edit(object=str(gid), ops=[
            EditOp(op="append", path="selections", value=s.model_dump(mode="json"))
            for s in selections]))  # fmt: skip
    weld = Weld(
        name=name, type=type, groove=groove, leg=leg, leg2=leg2,
        path=chosen[(planned.part, "edge", tuple(planned.edges))],
        faces=[chosen[(planned.part, "face", tuple(planned.leg_faces))],
               chosen[(planned.other, "face", tuple(planned.base_faces))]],
        pattern=pattern, segment=segment, pitch=pitch, filler=filler,
    )  # fmt: skip
    out.append(Edit(object=str(comp.id), ops=[
        EditOp(op="append", path="welds", value=weld.model_dump(mode="json", exclude_none=True))
    ]))  # fmt: skip
    return out


# --- modelled welds (ADR 0078 step 4) ------------------------------------------------

# Elements across a modelled bead's shorter leg: the throat section's force came to
# 88.7, 98.8 and 101.8 % of the lap joint's pull at 3, 6 and 12 (ADR 0078 step 4).
BEAD_DIVISIONS = 6
ROOT_GAP = 0.1  # of the shorter leg: the bead's root cut back, so the legs share no edge


def _corners(xyz, closed: bool, tol: float = 1e-6):
    """Indices of a station chain's corners: where its direction turns (all of them on a
    curve, so a curved path is followed at its stations), and its two ends if open."""
    import numpy as np

    n = len(xyz)
    keep = []
    for i in range(n):
        if not closed and i in (0, n - 1):
            keep.append(i)
            continue
        d0, d1 = xyz[i] - xyz[i - 1], xyz[(i + 1) % n] - xyz[i]
        cross = np.linalg.norm(np.cross(d0, d1)) / max(np.linalg.norm(d0) * np.linalg.norm(d1),
                                                       1e-300)  # fmt: skip
        if cross > tol:
            keep.append(i)
    return keep


def bead_geometry(project, comp, mesh, weld):
    """A light weld's bead as `weld_bead` steps (ADR 0078 step 4): its path through the
    weld's stations on the mesh, kept at its corners (a straight path is two points; a
    curve keeps every station), and at each corner the legs' directions from the weld's
    node pairs on the segments either side, mitred (a leg at a corner is leg / cos of half
    the turn, so the bead's faces stay flat). Closed paths loop; an intermittent weld is
    a step per segment, each along its share of the path (`s0`: where it starts). Fillets
    only."""
    import numpy as np

    from puzzle.adapters.weld_pairs import weld_pairs
    from puzzle.model.schema import Geometry, HistoryStep, NamedSelection

    if weld.type != "fillet":
        raise PuzzleError("a groove weld has no bead: modelled_commands ties its faces "
                          "over its depth", code="unsupported")  # fmt: skip
    wp = weld_pairs(project, comp, mesh, weld)
    xyz, closed = np.asarray(wp.xyz, float), bool(wp.closed)
    keep = _corners(xyz, closed)
    if len(keep) < 2:
        raise PuzzleError(f"weld '{weld.name}': its path has no length", code="invalid")
    poly = xyz[keep]
    ring = np.vstack([poly, poly[:1]]) if closed else poly
    seg = ring[1:] - ring[:-1]
    lengths = np.linalg.norm(seg, axis=1)
    tangents = seg / lengths[:, None]
    cum = np.concatenate([[0.0], np.cumsum(lengths)])
    # Each segment's leg directions: the pairs' arms on its stations, square to it.
    from puzzle.adapters.weld_pairs import _project

    xa, xb = wp.xa, mesh.node_coords(wp.b)
    sa, _da, na = _project(xa, ring, cum)
    sb, _db, nb = _project(xb, ring, cum)

    def unit(v):
        return v / np.linalg.norm(v)

    dirs = []
    for k in range(len(seg)):
        lo, hi, t = cum[k], cum[k + 1], tangents[k]
        out = []
        for s_, near, x in ((sa, na, xa), (sb, nb, xb)):
            on = (s_ > lo + 1e-9 * cum[-1]) & (s_ < hi - 1e-9 * cum[-1])
            if not on.any():
                on = (s_ >= lo) & (s_ <= hi)
            if not on.any():  # an intermittent weld's gap: the weld's pairs overall
                on = np.ones(len(s_), bool)
            arm = (x[on] - near[on]).sum(axis=0)
            out.append(unit(arm - (arm @ t) * t))
        dirs.append(out)
    legs = (weld.leg.to_si(), (weld.leg2 or weld.leg).to_si())

    def leg_at(i, which):
        """The leg vector at corner i: its segments' directions mitred."""
        n = len(poly)
        before = (i - 1) % len(seg) if (closed or i > 0) else None
        after = i if (closed or i < n - 1) else None
        ds = [dirs[k][which] for k in (before, after) if k is not None]
        v = unit(sum(ds))
        return v * legs[which] / max(float(v @ ds[0]), 0.2)

    va = [leg_at(i, 0) for i in range(len(poly))]
    vb = [leg_at(i, 1) for i in range(len(poly))]
    pieces = [(0.0, float(cum[-1]))]
    if weld.pattern == "intermittent":
        length, pitch = weld.segment.to_si(), weld.pitch.to_si()
        pieces = [(k * pitch, min(k * pitch + length, float(cum[-1])))
                  for k in range(int(cum[-1] // pitch) + 1) if k * pitch < cum[-1]]  # fmt: skip
        closed = False

    def at(sv):
        """The point and legs at distance sv along the ring, between corners."""
        k = min(int(np.searchsorted(cum, sv, side="right")) - 1, len(seg) - 1)
        f = (sv - cum[k]) / lengths[k]
        nxt = (k + 1) % len(poly)
        lerp = lambda u, w: (1 - f) * u + f * w  # noqa: E731
        return ring[k] + f * seg[k], lerp(va[k], va[nxt]), lerp(vb[k], vb[nxt])

    steps, members = [], []
    for n, (s0, s1) in enumerate(pieces, start=1):
        if s1 - s0 <= 1e-9:
            continue
        if weld.pattern == "intermittent":
            inner = [i for i, c in enumerate(cum[:-1]) if s0 < c < s1]
            rows = [at(s0)] + [(ring[i], va[i % len(poly)], vb[i % len(poly)]) for i in inner]
            rows.append(at(s1))
        else:
            rows = [(poly[i], va[i], vb[i]) for i in range(len(poly))]
        name = "Bead" if len(pieces) == 1 else f"Bead{n}"
        steps.append(HistoryStep(kind="weld_bead", name=name, args={
            "points": [list(map(float, r[0])) for r in rows],
            "a": [list(map(float, r[1])) for r in rows],
            "b": [list(map(float, r[2])) for r in rows],
            "closed": closed, "s0": float(s0), "root": ROOT_GAP * min(legs)}))  # fmt: skip
        members.append(name)

    def sel(label, face):
        return NamedSelection(name=label, entity="face", members=[f"{m}/{face}" for m in members])

    return Geometry(
        name=f"{weld.name} bead",
        history=steps,
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    sel("Leg A", "leg_a"), sel("Leg B", "leg_b"), sel("Face", "face"),
                    sel("Root", "root")],
    )  # fmt: skip


def modelled_commands(project, comp, mesh, name: str) -> list:
    """The batch drawing light weld `name` as a modelled bead (ADR 0078 step 4): its
    geometry and part, the filler (or the path's part's material) as its material, its
    legs tied to the two faces (nonconformal ties, so the root between the parts stays
    open, as an unfused fillet's root is), elements a third of the leg in it, and the
    weld marked `modelled`. One batch: one undo. `mesh` is the component's mesh (the
    legs' directions come from the light weld's pairs on it)."""
    from puzzle.commands.model import Create, Edit, EditOp
    from puzzle.model.schema import Contact, MaterialAssignment, PartInstance

    index = next((i for i, w in enumerate(comp.welds) if w.name == name), None)
    if index is None:
        raise PuzzleError(f"no weld '{name}' in {comp.name}", code="not_found")
    weld = comp.welds[index]
    if weld.formulation == "modelled":
        raise PuzzleError(f"weld '{name}' is modelled already", code="exists")
    if weld.type == "groove":  # no bead: its two faces tied over its depth (`with_grooves`)
        return [Edit(object=str(comp.id), ops=[
            EditOp(op="set", path=f"welds.{index}.formulation", value="modelled")])]  # fmt: skip
    geo = bead_geometry(project, comp, mesh, weld)
    part = f"{name} bead"
    if part in {p.name for p in comp.parts}:
        raise PuzzleError(f"{comp.name} has a part '{part}' already", code="exists")
    owner = weld.path.split("/", 1)[0]
    material = weld.filler or next(
        (a.material for a in comp.materials if a.selection.split("/", 1)[0] == owner), None
    )
    if material is None:
        raise PuzzleError(f"{owner} has no material for the bead", code="missing_input")
    ops = [
        EditOp(op="append", path="parts",
               value=PartInstance(name=part, geometry=geo.id).model_dump(mode="json")),
        EditOp(op="append", path="materials", value=MaterialAssignment(
            material=material, selection=f"{part}/Body").model_dump(mode="json")),
        *(EditOp(op="append", path="contacts", value=Contact(
            name=f"{name} {leg}", type="bonded", meshing="nonconformal",
            source=f"{part}/{leg}", target=face).model_dump(mode="json"))
          for leg, face in (("Leg A", weld.faces[0]), ("Leg B", weld.faces[1]))),
        EditOp(op="set", path=f"welds.{index}.formulation", value="modelled"),
    ]  # fmt: skip
    out = [Create(kind="geometry", data=geo.model_dump(mode="json")),
           Edit(object=str(comp.id), ops=ops)]  # fmt: skip
    recipe = project.objects.get(comp.mesh)
    if recipe is not None:
        size = min(weld.leg.to_si(), (weld.leg2 or weld.leg).to_si()) / BEAD_DIVISIONS
        control = {"kind": "size", "selection": f"{part}/Body",
                   "size": {"value": round(size * 1e3, 4), "unit": "mm"}}  # fmt: skip
        out.append(Edit(object=str(recipe.id),
                        ops=[EditOp(op="append", path="controls", value=control)]))  # fmt: skip
    return out


def line_commands(project, comp, name: str) -> list:
    """The batch turning modelled weld `name` back into a light weld: its bead's part,
    geometry, material, ties and mesh size removed."""
    from puzzle.commands.model import Delete, Edit, EditOp
    from puzzle.model.schema import Geometry

    index = next((i for i, w in enumerate(comp.welds) if w.name == name), None)
    if index is None or comp.welds[index].formulation != "modelled":
        raise PuzzleError(f"no modelled weld '{name}' in {comp.name}", code="not_found")
    if comp.welds[index].type == "groove":
        return [Edit(object=str(comp.id), ops=[
            EditOp(op="set", path=f"welds.{index}.formulation", value="line")])]  # fmt: skip
    part = f"{name} bead"
    parts = [p.model_dump(mode="json") for p in comp.parts if p.name != part]
    gone = next((p for p in comp.parts if p.name == part), None)
    ops = [
        EditOp(op="set", path="parts", value=parts),
        EditOp(op="set", path="materials", value=[
            a.model_dump(mode="json") for a in comp.materials
            if a.selection.split("/", 1)[0] != part]),
        EditOp(op="set", path="contacts", value=[
            c.model_dump(mode="json") for c in comp.contacts
            if c.source.split("/", 1)[0] != part]),
        EditOp(op="set", path=f"welds.{index}.formulation", value="line"),
    ]  # fmt: skip
    out: list = [Edit(object=str(comp.id), ops=ops)]
    if gone is not None and isinstance(project.objects.get(gone.geometry), Geometry):
        out.append(Delete(object=str(gone.geometry)))
    recipe = project.objects.get(comp.mesh)
    if recipe is not None:
        kept = [c.model_dump(mode="json") for c in recipe.controls
                if not getattr(c, "selection", "").startswith(f"{part}/")]  # fmt: skip
        if len(kept) != len(recipe.controls):
            out.append(Edit(object=str(recipe.id),
                            ops=[EditOp(op="set", path="controls", value=kept)]))  # fmt: skip
    return out


def _groove_rows(mesh, weld, face: str):
    """The face elements of `face` within a groove weld's depth of its path (and on its
    segments, when intermittent): (corner rows, quadratic rows or None)."""
    import numpy as np

    from puzzle.adapters.weld_pairs import _chain, _project

    corners, closed = _chain(mesh.groups[weld.path], mesh)
    poly = mesh.node_coords(corners)
    if closed:
        poly = np.vstack([poly, poly[:1]])
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])
    depth = weld.leg.to_si()
    rows = np.vstack([b.connectivity for b in mesh.groups[face].blocks(mesh) if b.dim == 2])
    n = 3 if rows.shape[1] in (3, 6) else 4
    xyz = mesh.node_coords(rows[:, :n].ravel()).reshape(len(rows), n, 3)
    s, d, _near = _project(xyz.reshape(-1, 3), poly, cum)
    s, d = s.reshape(len(rows), n), d.reshape(len(rows), n)
    keep = np.all(d <= depth * (1 + 1e-3) + 1e-9 * cum[-1], axis=1)
    if weld.pattern == "intermittent":
        mid = s.mean(axis=1)
        keep &= np.mod(mid, weld.pitch.to_si()) <= weld.segment.to_si()
    if not keep.any():
        raise PuzzleError(f"weld '{weld.name}': no element of {face} within its depth of "
                          "the path", code="invalid")  # fmt: skip
    if n == 4:
        raise PuzzleError(f"weld '{weld.name}': a groove's faces must be meshed in "
                          "triangles (tetrahedra)", code="unsupported")  # fmt: skip
    picked = rows[keep]
    return picked[:, :3], picked if picked.shape[1] == 6 else None


def with_grooves(project, component_id, mesh):
    """(the mesh with each modelled groove weld's faces over its depth as groups, the
    project with a bonded nonconformal tie between them): a groove weld modelled (ADR
    0078 step 4) is the joint fused over its depth, complete (the depth the thickness)
    or partial, the rest of the faces left open. Others come back as they are."""
    import dataclasses

    from puzzle.bolts import _face_group
    from puzzle.model.schema import Component, Contact

    comp = project.objects.get(component_id)
    if not isinstance(comp, Component):
        return mesh, project
    grooves = [w for w in comp.welds if w.type == "groove" and w.formulation == "modelled"]
    if not grooves:
        return mesh, project
    entity = 1 + max((b.entity[1] for b in mesh.blocks if b.entity[0] == 2), default=0)
    contacts = list(comp.contacts)
    for w in grooves:
        names = []
        for face in w.faces:
            if face not in mesh.groups:
                raise PuzzleError(f"weld '{w.name}': no selection '{face}' in the mesh",
                                  code="invalid")  # fmt: skip
            tris, quad = _groove_rows(mesh, w, face)
            name = f"{face} ({w.name} groove)"
            mesh = _face_group(mesh, name, tris, quad, entity)
            entity += 1
            names.append(name)
        contacts.append(Contact(name=f"{w.name} groove", type="bonded", meshing="nonconformal",
                                source=names[0], target=names[1]))  # fmt: skip
    objects = dict(project.objects)
    objects[comp.id] = comp.model_copy(update={"contacts": contacts})
    return mesh, dataclasses.replace(project, objects=objects)
