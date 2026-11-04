"""More tables (ADR 0064 phase 2) and the geometry queries that make or read points:

- results: the mesh as a table (each element's part, centre and shape), a probe's
  history, the modes, the contacts' and bolts' results, the nearest node to each of a
  table's points, a result over a plane's cut (and the force across it);
- the project: its objects as rows (parts, bolts, materials, selections, studies,
  features, contacts) and a study's runs;
- geometry: points along a path or a named edge, and the clearance between two parts.

Every table is SI with a quantity kind per column, as `frames`' are.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.operators.frames import _ev, _made, _names, _pl, _table


def _result(ctx, op, key: str = "result"):
    ev = _ev()
    got = ev._input(ctx, op, key)
    if not isinstance(got, ev.ResultValue):
        raise PuzzleError(f"'{op.name}' needs a result wired in", code="invalid")
    return got


def _run(op, got) -> str:
    if len(got.run_ids) != 1:
        raise PuzzleError(f"'{op.name}': this reads one run, not a derived result",
                          code="unsupported")  # fmt: skip
    return got.run_ids[0]


# --- the mesh ----------------------------------------------------------------------------


def _shape(corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each element's shape quality (1 for a regular tet or triangle, toward 0 as it
    flattens: 12 (3V)^(2/3) / Σl² for tets, 4√3 A / Σl² for triangles) and aspect
    (longest edge over shortest), from its corner nodes (n, k, 3)."""
    k = corners.shape[1]
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
    lengths = np.stack([np.linalg.norm(corners[:, i] - corners[:, j], axis=1)
                        for i, j in pairs], axis=1)  # fmt: skip
    aspect = lengths.max(axis=1) / np.maximum(lengths.min(axis=1), 1e-300)
    sq = (lengths**2).sum(axis=1)
    if k == 4:
        a, b, c, d = (corners[:, i] for i in range(4))
        vol = np.abs(np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a))) / 6
        shape = 12 * (3 * vol) ** (2 / 3) / sq
    else:
        a, b, c = (corners[:, i] for i in range(3))
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        shape = 4 * math.sqrt(3) * area / sq
    return shape, aspect


CORNERS = {"tet4": 4, "tet10": 4, "tri3": 3, "tri6": 3}


def mesh_table(ctx, op):
    """A run's mesh, one row per element of the model (ADR 0064A row G1): its id, type,
    part, centre, and for tets and triangles its shape quality and aspect, so "the 10
    worst elements and where" is a Sort and a Top N."""
    pl = _pl()
    got = _result(ctx, op)
    mesh = got.mesh
    owner = {e: name for name, g in mesh.groups.items() if "/" not in name for e in g.entities}
    rows = {k: [] for k in ("element", "type", "part", "x", "y", "z", "shape", "aspect")}
    keys = set(owner)
    for b in mesh.blocks:
        if b.entity not in keys:  # faces and edges of selections are not elements
            continue
        xyz = mesh.node_coords(b.connectivity.ravel()).reshape(*b.connectivity.shape, 3)
        centre = xyz.mean(axis=1)
        if b.element in CORNERS:
            shape, aspect = _shape(xyz[:, : CORNERS[b.element]])
        else:
            shape = aspect = np.full(len(b.ids), np.nan)
        n = len(b.ids)
        rows["element"] += b.ids.tolist()
        rows["type"] += [b.element] * n
        rows["part"] += [owner[b.entity]] * n
        for j, axis in enumerate("xyz"):
            rows[axis] += centre[:, j].tolist()
        rows["shape"] += shape.tolist()
        rows["aspect"] += aspect.tolist()
    frame = pl.DataFrame(rows, schema_overrides={"element": pl.Int64})
    return _made(frame, {"x": "length", "y": "length", "z": "length"})


# --- the project -------------------------------------------------------------------------

OBJECTS = ("parts", "bolts", "materials", "selections", "studies", "features", "contacts")


def project_table(ctx, op):
    """The project's objects as rows (ADR 0064A row J5): `objects` parts (with their
    material and, from the exact geometry, volume and mass), bolts, materials,
    selections, studies, features or contacts."""
    from puzzle.model.schema import Component, Geometry, Material, Study

    pl = _pl()
    project = ctx.project
    names = {o.id: o.name for o in project.objects.values()}
    rows: list[dict] = []
    kinds: dict[str, str | None] = {}
    what = op.objects
    if what == "parts":
        for comp in project.of_type(Component):
            mats = {a.selection.split("/")[0]: names.get(a.material) for a in comp.materials}
            for part in comp.parts:
                row = {
                    "component": comp.name,
                    "part": part.name,
                    "geometry": names.get(part.geometry),
                    "material": mats.get(part.name),
                }
                row.update(_part_mass(ctx, comp, part, mats))
                rows.append(row)
        kinds = {"volume": "volume", "mass": "mass"}
    elif what == "bolts":
        from puzzle.bolts import resolved

        resolved_project = resolved(project)
        for comp in resolved_project.of_type(Component):
            for f in comp.connectors:
                pre = getattr(f, "preload", None)
                rows.append(
                    {
                        "component": comp.name,
                        "bolt": f.name,
                        "type": names.get(getattr(f, "bolt_type", None)),
                        "diameter": getattr(f, "diameter", None) and f.diameter.to_si(),
                        "preload": pre.to_si() if hasattr(pre, "to_si") else None,
                    }
                )
        kinds = {"diameter": "length", "preload": "force"}
    elif what == "materials":
        for m in project.of_type(Material):
            row = {"material": m.name}
            for key in ("density", "youngs_modulus", "poissons_ratio", "yield_strength",
                        "thermal_conductivity"):  # fmt: skip
                q = m.properties.get(key)
                row[key] = q.to_si() if q is not None and hasattr(q, "to_si") else None
            rows.append(row)
        kinds = {"density": "density", "youngs_modulus": "pressure",
                 "yield_strength": "pressure", "thermal_conductivity": None}  # fmt: skip
    elif what == "selections":
        for g in project.of_type(Geometry):
            for s in g.selections:
                rows.append({"geometry": g.name, "selection": s.name, "entity": s.entity,
                             "members": len(s.members), "by rule": s.rule is not None})  # fmt: skip
    elif what == "studies":
        for s in project.of_type(Study):
            rows.append({"study": s.name, "solver": s.solver,
                         "component": names.get(s.component),
                         "steps": len(s.steps)})  # fmt: skip
    elif what == "features":
        for comp in project.of_type(Component):
            for ph in comp.physics:
                for f in ph.features:
                    rows.append({"component": comp.name, "physics": ph.type,
                                 "feature": f.type, "selection": f.selection})  # fmt: skip
    elif what == "contacts":
        for comp in project.of_type(Component):
            for c in comp.contacts:
                rows.append({"component": comp.name, "contact": getattr(c, "name", ""),
                             "type": c.type, "source": c.source, "target": c.target})  # fmt: skip
    if not rows:
        return _made(pl.DataFrame(), {})
    return _made(pl.DataFrame(rows, strict=False), kinds)


def _part_mass(ctx, comp, part, mats) -> dict:
    """A part's volume and mass (its material's density), from the exact geometry; empty
    where it can't be worked out (no geometry kernel, no density)."""
    from puzzle.model.schema import Material

    try:
        from puzzle.geometry.measure import measure
        from puzzle.geometry.naming import named_geometry
        from puzzle.model.schema import Geometry

        geo = ctx.project.get(part.geometry, Geometry)
        named = named_geometry(ctx.project, geo, ctx.root or ".")
        m = measure(named, [("body", n) for n in named.of.get("body", {})])
        vol = float(sum(e.volume or 0.0 for e in m.entities))
    except Exception:  # noqa: BLE001 - a part the kernel can't read still gets its row
        return {"volume": None, "mass": None}
    density = None
    for a in comp.materials:
        if a.selection.split("/")[0] == part.name:
            mat = ctx.project.objects.get(a.material)
            if isinstance(mat, Material) and "density" in mat.properties:
                density = mat.properties["density"].to_si()
    return {"volume": vol, "mass": vol * density if density is not None else None}


def run_history(ctx, op):
    """A study's runs, one row each, oldest first (ADR 0064A row A12): when, solver,
    status, seconds, the point's labels."""
    from puzzle.model.schema import Study

    pl = _pl()
    inp = op.inputs.get("source")
    study = ctx.project.objects.get(inp.source) if inp is not None and inp.source else None
    if not isinstance(study, Study):
        raise PuzzleError(f"'{op.name}': wire a study into it", code="inconsistent")
    runs = [r for r in ctx.store.runs() if r.study_id == str(study.id)]
    labels = sorted({k for r in runs for k in r.point_labels()})
    rows = []
    for i, r in enumerate(runs, 1):
        row = {"run": i, "id": r.run_id, "started": r.started_at,
               "solver": r.solver, "status": r.status, "seconds": float(r.duration_s)}  # fmt: skip
        row.update({k: r.point_labels().get(k) for k in labels})
        rows.append(row)
    return _made(pl.DataFrame(rows, strict=False) if rows else pl.DataFrame(), {"seconds": "time"})


# --- results ------------------------------------------------------------------------------


def history_table(ctx, op):
    """A result tool's value at every step (ADR 0032's history) as a table: step value
    (time, frequency, increment), mode, value."""
    from puzzle.model.schema import ResultProbe
    from puzzle.post import probes

    pl = _pl()
    inp = op.inputs.get("probe")
    probe = ctx.project.objects.get(inp.source) if inp is not None and inp.source else None
    if not isinstance(probe, ResultProbe):
        raise PuzzleError(f"'{op.name}': wire a result tool into it", code="inconsistent")
    rows = probes.history(ctx.project, ctx.store, probe)
    value = probes.evaluate(ctx.project, ctx.store, probe)
    kind = _kind(value.get("unit"))
    frame = pl.DataFrame({"step": [r["step"] for r in rows], "mode": [r["mode"] for r in rows],
                          probe.name: [r["value"] for r in rows]}, strict=False)  # fmt: skip
    return _made(frame, {probe.name: kind})


def _kind(unit) -> str | None:
    from puzzle.model.units import DIMENSIONS, kind_of

    if unit in DIMENSIONS:  # some carry the kind itself
        return unit
    return kind_of(unit) if unit and unit != "dimensionless" else None


def mode_table(ctx, op):
    """A modal (or buckling) run's modes: mode and frequency (or load factor)."""
    pl = _pl()
    got = _result(ctx, op)
    steps = [s for s in got.results.steps if s.mode is not None]
    if not steps:
        raise PuzzleError(f"'{op.name}': {got.label} has no modes", code="not_found")
    buckling = steps[0].kind == "buckling"
    col = "load factor" if buckling else "frequency"
    frame = pl.DataFrame({"mode": [s.mode for s in steps], col: [float(s.value) for s in steps]})
    return _made(frame, {col: None if buckling else "frequency"})


def contact_table(ctx, op):
    """Each contact's totals at the step (ADR 0056): normal and tangential force, the
    area closed and its share, the largest pressure, penetration, sliding and gap."""
    from puzzle.post.contact import ContactResult, run_contact_results

    pl = _pl()
    got = _result(ctx, op)
    found = run_contact_results(ctx.project, ctx.store, _run(op, got),
                                None if op.step == -1 else op.step)  # fmt: skip
    if not found:
        raise PuzzleError(f"'{op.name}': {got.label} has no contact results", code="not_found")
    cols = ["normal_force", "tangential_force", "area", "area_share", "max_pressure",
            "max_penetration", "max_sliding", "sticking_share", "max_gap"]  # fmt: skip
    frame = pl.DataFrame({"contact": [c.name for c in found],
                          **{k: [getattr(c, k) for c in found] for k in cols}})  # fmt: skip
    return _made(frame, {k: _kind(u) for k, u in ContactResult.UNITS.items()})


def bolt_table(ctx, op):
    """Each bolt's loads and use at the step (ADR 0050): axial, shear, torsion, the
    largest bending, its stress and that over its proof and yield strengths."""
    from puzzle.bolts import bolt_results

    pl = _pl()
    got = _result(ctx, op)
    found = bolt_results(ctx.project, ctx.store, _run(op, got),
                         None if op.step == -1 else op.step)  # fmt: skip
    if not found:
        raise PuzzleError(f"'{op.name}': {got.label} has no bolt results", code="not_found")
    frame = pl.DataFrame({
        "bolt": [b.name for b in found], "component": [b.component for b in found],
        "axial": [b.loads.axial[0] for b in found], "shear": [b.loads.shear[0] for b in found],
        "torsion": [b.loads.torsion[0] for b in found],
        "bending": [b.loads.largest_bending for b in found],
        "stress": [b.stress for b in found], "of proof": [b.proof for b in found],
        "of yield": [b.yielding for b in found]}, strict=False)  # fmt: skip
    return _made(frame, {"axial": "force", "shear": "force", "torsion": "moment",
                         "bending": "moment", "stress": "pressure"})  # fmt: skip


def _values(ctx, op, got, ids: np.ndarray) -> dict[str, tuple[list, str | None]]:
    """The chosen quantities (default the first) at node ids, at the operator's step."""
    from puzzle.operators.frames import _column_name
    from puzzle.post.quantities import compute, default

    ev = _ev()
    items = [next((i for i in got.items if i.key == k), None) for k in op.quantities] \
        if op.quantities else [default(got.items)]  # fmt: skip
    out = {}
    for item in items:
        if item is None:
            raise PuzzleError(f"'{op.name}': {got.label} has none of {op.quantities}",
                              code="not_found")  # fmt: skip
        step = ev._step(ctx, got.results, item, op.step)
        f = step.fields[item.field]
        if f.location != "node":
            continue
        vals = np.asarray(compute(item, f.values, f.components), float)
        lookup = dict(zip(f.ids.tolist(), vals.tolist(), strict=True))
        out[_column_name(item)] = ([lookup.get(int(i), math.nan) for i in ids], item.unit)
    return out


def nearest(ctx, op):
    """Each of a table's points (x, y, z) at its nearest mesh node (ADR 0064A rows D4,
    G2): the node, its distance, and the results there; the points' other columns kept.
    Test gauges matched to the model, or a probe grid read in one go."""
    from scipy.spatial import cKDTree

    pl = _pl()
    t = _table(op, _ev()._input(ctx, op, "points"))
    _names(op, ["x", "y", "z"], t)
    got = _result(ctx, op)
    mesh = got.mesh
    tree = cKDTree(mesh.coords)
    pts = t.frame.select(["x", "y", "z"]).to_numpy().astype(float)
    dist, at = tree.query(pts)
    ids = mesh.node_ids[at]
    kinds = dict(zip(t.columns, t.kinds, strict=False))
    cols = {"node": ids.astype(np.int64), "distance": dist}
    kinds["distance"] = "length"
    for name, (vals, kind) in _values(ctx, op, got, ids).items():
        cols[name] = vals
        kinds[name] = kind
    return _made(pl.concat([t.frame, pl.DataFrame(cols)], how="horizontal"), kinds)


def section(ctx, op) -> dict[str, Any]:
    """A result over a construction plane's cut (ADR 0064A row F10): the cut's points
    with the chosen quantities (a table), and the force and moment carried across it
    (numbers), as the plane and section tools give them."""
    from puzzle.model.schema import ConstructionGeometry
    from puzzle.operators.frames import _column_name
    from puzzle.post import probes
    from puzzle.post.quantities import compute, default

    ev = _ev()
    pl = _pl()
    got = _result(ctx, op)
    plane = ctx.project.objects.get(op.target) if op.target else None
    if not isinstance(plane, ConstructionGeometry) or plane.type != "plane":
        raise PuzzleError(f"'{op.name}': pick a construction plane", code="inconsistent")
    origin, normal = plane.frame(ctx.project)
    item = next((i for i in got.items if i.key == op.quantities[0]), None) \
        if op.quantities else default(got.items)  # fmt: skip
    step = ev._step(ctx, got.results, item, op.step)
    cut, _faces, _areas, _centres, _n = probes._cut(got.mesh, step, origin, normal)
    xyz = np.asarray(cut.points, float)
    cols: dict[str, Any] = {"x": xyz[:, 0], "y": xyz[:, 1], "z": xyz[:, 2]}
    kinds: dict[str, str | None] = {"x": "length", "y": "length", "z": "length"}
    if item.field in cut.point_data:
        f = step.fields[item.field]
        comps = np.asarray(cut.point_data[item.field], float).reshape(cut.n_points, -1)
        cols[_column_name(item)] = np.asarray(compute(item, comps, f.components), float)
        kinds[_column_name(item)] = item.unit
    out = {"out": _made(pl.DataFrame(cols), kinds)}
    if "stress" in step.fields:
        force = probes.section_force(got.mesh, step, origin, normal)
        out["force"] = ev.Number(force["normal_force"], "force", "Normal force across")
        out["moment"] = ev.Number(force["moment_total"], "moment", "Moment across")
    return out


# --- geometry ------------------------------------------------------------------------------


def divide(ctx, op):
    """Points along a construction path or a named edge (ADR 0064A row E5), evenly by
    length: x, y, z and s (the distance along). The `count` input (default 11)."""
    from puzzle.model.schema import ConstructionGeometry, Geometry

    pl = _pl()
    ev = _ev()
    count = ev._typed_number(ctx, op, "count")
    n = max(2, int(count.value) if count is not None else 11)
    obj = ctx.project.objects.get(op.target) if op.target else None
    if isinstance(obj, ConstructionGeometry) and obj.type == "path":
        a, b = obj.frame(ctx.project)
        s = np.linspace(0.0, 1.0, n)
        xyz = a[None, :] + s[:, None] * (b - a)[None, :]
        along = s * float(np.linalg.norm(b - a))
    elif isinstance(obj, Geometry):
        xyz, along = _edge_points(ctx, op, obj, n)
    else:
        raise PuzzleError(f"'{op.name}': pick a construction path or a geometry and its "
                          "edge", code="inconsistent")  # fmt: skip
    frame = pl.DataFrame({"x": xyz[:, 0], "y": xyz[:, 1], "z": xyz[:, 2], "s": along})
    return _made(frame, {"x": "length", "y": "length", "z": "length", "s": "length"})


def _edge_points(ctx, op, geo, n: int):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint, GCPnts_UniformAbscissa

    from puzzle.geometry.naming import by_rule, named_geometry

    named = named_geometry(ctx.project, geo, ctx.root or ".")
    sel = next((s for s in geo.selections if s.name == op.selection), None)
    if sel is not None:
        names = by_rule(named, sel.entity, sel.rule) if sel.rule else list(sel.members)
    else:
        names = [op.selection]
    edges = [e for name in names for e in named.of.get("edge", {}).get(name, [])]
    if not edges:
        raise PuzzleError(f"'{op.name}': {geo.name} has no edge '{op.selection}'",
                          code="not_found")  # fmt: skip
    curve = BRepAdaptor_Curve(edges[0])

    uniform = GCPnts_UniformAbscissa(curve, n)
    pts, along = [], []
    for i in range(1, uniform.NbPoints() + 1):
        u = uniform.Parameter(i)
        p = curve.Value(u)
        pts.append((p.X(), p.Y(), p.Z()))
        along.append(GCPnts_AbscissaPoint.Length_s(curve, curve.FirstParameter(), u))
    return np.asarray(pts, float), np.asarray(along, float)


def clearance(ctx, op):
    """The least distance between two parts or selections of a component, where they are
    placed (ADR 0064A row F11): 0 where they touch. A constraint for an optimisation
    ("keep the bracket 2 mm off the plate") with no solve."""
    from puzzle.geometry.measure import MeasureError, measure_placed
    from puzzle.model.schema import Component

    ev = _ev()
    comp = ctx.project.objects.get(op.target) if op.target else None
    if not isinstance(comp, Component):
        raise PuzzleError(f"'{op.name}': pick a component", code="inconsistent")

    def pick(text: str):
        part, _, sel = text.partition("/")
        if not sel:
            return (part, "body", "Body")
        from puzzle.model.schema import Geometry

        p = next((x for x in comp.parts if x.name == part), None)
        geo = ctx.project.objects.get(p.geometry) if p is not None else None
        s = next((x for x in getattr(geo, "selections", []) if x.name == sel), None) \
            if isinstance(geo, Geometry) else None  # fmt: skip
        if s is None:
            raise PuzzleError(f"'{op.name}': no selection '{text}'", code="not_found")
        return (part, s.entity, s.members[0] if s.members else sel)

    if not op.selection or not op.other:
        raise PuzzleError(f"'{op.name}': pick the two parts", code="inconsistent")
    try:
        m = measure_placed(ctx.project, comp, ctx.root or ".",
                           [pick(op.selection), pick(op.other)])  # fmt: skip
    except MeasureError as exc:
        raise PuzzleError(f"'{op.name}': {exc}", code="invalid") from None
    d = m.pair.get("minimum_distance")
    if d is None:
        raise PuzzleError(f"'{op.name}': no distance between them", code="not_found")
    return ev.Number(float(d), "length", f"Clearance {op.selection} to {op.other}")


def _placed(ctx, comp, text: str):
    """A component's part or "Part/Selection" as shapes where the part is placed."""
    from OCP.TopLoc import TopLoc_Location

    from puzzle.geometry.contacts import _placement
    from puzzle.geometry.measure import _cast
    from puzzle.geometry.naming import by_rule, named_geometry
    from puzzle.model.schema import Geometry

    part_name, _, sel = text.partition("/")
    part = next((p for p in comp.parts if p.name == part_name), None)
    if part is None:
        raise PuzzleError(f"{comp.name} has no part '{part_name}'", code="not_found")
    geo = ctx.project.get(part.geometry, Geometry)
    named = named_geometry(ctx.project, geo, ctx.root or ".")
    if sel:
        s = next((x for x in geo.selections if x.name == sel), None)
        if s is None:
            raise PuzzleError(f"{geo.name} has no selection '{sel}'", code="not_found")
        members = by_rule(named, s.entity, s.rule) if s.rule else list(s.members)
        entity = s.entity
    else:
        entity, members = "body", list(named.of.get("body", {}))
    shapes = [m for name in members for m in named.of.get(entity, {}).get(name, [])]
    if any(part.translation) or any(part.rotation):
        where = TopLoc_Location(_placement(part.translation, part.rotation))
        shapes = [_cast(entity, m.Moved(where)) for m in shapes]
    return shapes


def intersection(ctx, op):
    """Where two parts or selections of a component meet (ADR 0065 item 5, phase 3), or
    where a construction plane (`other`, by name) cuts one: the curves as points evenly
    along them (the `count` input per curve, default 21: a weld line, a section's
    outline) with s, the distance along, and `curve`, each curve's number; or the
    points where an edge crosses a face. Recomputed with the geometry."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.GCPnts import GCPnts_AbscissaPoint, GCPnts_UniformAbscissa
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    from puzzle.geometry.history import TopAbs_EDGE, TopAbs_VERTEX, _subshapes
    from puzzle.geometry.measure import _compound
    from puzzle.model.schema import Component, ConstructionGeometry

    pl = _pl()
    ev = _ev()
    comp = ctx.project.objects.get(op.target) if op.target else None
    if not isinstance(comp, Component):
        raise PuzzleError(f"'{op.name}': pick a component", code="inconsistent")
    a = _compound(_placed(ctx, comp, op.selection))
    plane = next((c for c in ctx.project.of_type(ConstructionGeometry)
                  if c.name == op.other and c.type == "plane"), None)  # fmt: skip
    if plane is not None:
        o, n = plane.frame(ctx.project)
        b = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*o), gp_Dir(*n))).Face()
    else:
        b = _compound(_placed(ctx, comp, op.other))
    sec = BRepAlgoAPI_Section(a, b)
    sec.Build()
    if not sec.IsDone():
        raise PuzzleError(f"'{op.name}': the intersection failed", code="invalid")
    result = sec.Shape()
    count = ev._typed_number(ctx, op, "count")
    n = max(2, int(count.value) if count is not None else 21)
    rows = {"curve": [], "x": [], "y": [], "z": [], "s": []}
    for k, e in enumerate(_subshapes(result, TopAbs_EDGE), start=1):
        curve = BRepAdaptor_Curve(e)
        u = GCPnts_UniformAbscissa(curve, n)
        for i in range(1, u.NbPoints() + 1):
            q = curve.Value(u.Parameter(i))
            rows["curve"].append(k)
            rows["x"].append(q.X())
            rows["y"].append(q.Y())
            rows["z"].append(q.Z())
            rows["s"].append(GCPnts_AbscissaPoint.Length_s(curve, curve.FirstParameter(),
                                                            u.Parameter(i)))  # fmt: skip
    if not rows["x"]:  # an edge through a face: points only
        from OCP.BRep import BRep_Tool

        for k, v in enumerate(_subshapes(result, TopAbs_VERTEX), start=1):
            q = BRep_Tool.Pnt_s(v)
            rows["curve"].append(k)
            rows["x"].append(q.X())
            rows["y"].append(q.Y())
            rows["z"].append(q.Z())
            rows["s"].append(0.0)
    if not rows["x"]:
        raise PuzzleError(f"'{op.name}': {op.selection} and {op.other} don't meet",
                          code="not_found")  # fmt: skip
    return _made(pl.DataFrame(rows), {"x": "length", "y": "length", "z": "length",
                                      "s": "length"})  # fmt: skip
