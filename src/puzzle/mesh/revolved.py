"""Modelled bolts and nuts meshed as bodies of revolution (ADR 0077 decision 3).

A part whose geometry history is one `bolt`, `nut` or `washer` step (`puzzle.solid_bolts`) is
rebuilt here in Gmsh's own OpenCASCADE geometry from the step's arguments: its meridian
profile (r, z) in the local xz half-plane, cut at the pretension section when it has
one, revolved about the local z axis in four quarter turns (OCC can't revolve a face a
whole turn and keep the layers) with `sectors / 4` layers each, recombined. The
quarters are fragmented together, which joins the last quarter's end to the profile
and keeps the extrusion constraints, so the whole is one conformal mesh: the profile
meshed in 2D (recombined into quadrilaterals where it can be, the recipe's sizes on
"<part>/Fillet" and "<part>/Thread" reaching it through their faces' boundaries) and
swept into hexahedra, with prisms where the profile touches the axis (and where a
triangle was left). The part is placed afterwards as any part is.

The reference shape (the history's BREP) still names the faces; each whole face of
revolution is found again as its four quarters by `generate._pieces`, the bodies by
`bodies` below. Other parts keep their tetrahedra: the bolt meets them only through
nonconformal contacts. Off with `MeshRecipe.revolved_bolts = False` (then the bolt
meshes as tetrahedra), and never for Netgen, hexahedral recipes or 2D models.
"""

from __future__ import annotations

import math

import gmsh
import numpy as np

KINDS = ("bolt", "nut", "washer")
QUARTERS = 4
CORE = 0.25  # a bolt's core strip about the axis, as a share of its minor radius


def step_of(project, geometry) -> tuple[str, dict] | None:
    """(kind, arguments in metres) when the geometry is one bolt, nut or washer step,
    else None."""
    steps = [s for s in geometry.history if not s.suppressed]
    if len(steps) != 1 or steps[0].kind not in KINDS:
        return None
    from puzzle.operators import parameter_values

    params = parameter_values(project)
    args = {}
    for k, v in steps[0].args.items():
        if isinstance(v, str):
            if v not in params:
                return None  # regenerating will report the unknown parameter
            v = params[v]
        args[k] = float(v) if v is not None else None
    return steps[0].kind, args


def layers(sectors: int) -> int:
    """Layers per quarter turn: `sectors` round the whole turn, rounded up to a
    multiple of four (at least one layer a quarter)."""
    return max(1, math.ceil(sectors / QUARTERS))


def build(kind: str, args: dict, sectors: int, *, translation, rotation) -> list[tuple[int, int]]:
    """The revolved part's volumes in Gmsh's OCC kernel, drawn where the part is placed:
    moving swept volumes afterwards (`model.place`) drops their layers."""
    from puzzle.geometry.model import rotation_matrix

    occ = gmsh.model.occ
    r = rotation_matrix(rotation)
    shift = np.asarray(translation, dtype=float)

    def at(x, z):  # a local point (x, 0, z), placed
        return tuple(float(c) for c in r @ np.array([x, 0.0, z]) + shift)

    profile = _bolt_profile(args) if kind == "bolt" else _nut_profile(args)
    section = args.get("section") if kind == "bolt" else None
    surfaces = _surfaces(profile, section, args, at, core_radius(kind, args))
    occ.synchronize()
    axis = r @ np.array([0.0, 0.0, 1.0])
    volumes: list[tuple[int, int]] = []
    current = [(2, s) for s in surfaces]
    n = layers(sectors)
    for _ in range(QUARTERS):
        out = occ.revolve(current, *shift, *axis, math.pi / 2, numElements=[n],
                          recombine=True)  # fmt: skip
        # For each input surface: its far end, the volume swept, then its sides.
        current = [out[j - 1] for j, e in enumerate(out) if e[0] == 3]
        volumes += [e for e in out if e[0] == 3]
    out, _ = occ.fragment(volumes, [])  # the last quarter's end is the profile again
    occ.synchronize()
    return [e for e in out if e[0] == 3]


def _local(points, translation, rotation) -> np.ndarray:
    """Placed points back in the part's own frame (placing rotates, then translates)."""
    from puzzle.geometry.model import rotation_matrix

    r = rotation_matrix(rotation)
    return (np.asarray(points, dtype=float) - np.asarray(translation, dtype=float)) @ r


def core_radius(kind: str, args: dict) -> float | None:
    """The radius of a bolt's core strip (`prepare`); a nut or washer has no axis to keep
    off."""
    return CORE * args["minor_radius"] if kind == "bolt" else None


def prepare(volumes, core: float | None, *, translation, rotation) -> None:
    """Mesh settings on the placed part's profile: every profile face recombined, and a
    bolt's core strip (0 <= r <= `core`, `_surfaces`) one structured element across,
    its axis edge a copy of its other side (Gmsh's periodic curves), so every element
    touching the axis does so along an edge and sweeps into a prism. A free mesh there
    left quadrilaterals with one corner on the axis, which swept into hexahedra with a
    collapsed edge (48 of 4800, scaled Jacobian 0)."""
    from puzzle.geometry.model import rotation_matrix

    faces = profiles(volumes, translation=translation, rotation=rotation)
    tol = _tolerance()
    for f in faces:
        gmsh.model.mesh.setRecombine(2, f)
    if core is None:
        return
    shift = rotation_matrix(rotation) @ np.array([-core, 0.0, 0.0])
    affine = [1, 0, 0, shift[0], 0, 1, 0, shift[1], 0, 0, 1, shift[2], 0, 0, 0, 1]
    for f in faces:
        c = _local([gmsh.model.occ.getCenterOfMass(2, f)], translation, rotation)[0]
        if c[0] > core:
            continue
        sides = [abs(t) for _, t in gmsh.model.getBoundary([(2, f)], oriented=False)]
        ends = {t: _local([gmsh.model.getValue(0, abs(v), []) for _, v in
                           gmsh.model.getBoundary([(1, t)], oriented=False)],
                          translation, rotation) for t in sides}  # fmt: skip
        axis = [t for t, p in ends.items() if np.all(np.abs(p[:, 0]) < tol)]
        other = [t for t, p in ends.items() if np.all(np.abs(p[:, 0] - core) < tol)]
        across = [t for t in sides if t not in axis + other]
        if len(axis) != 1 or len(other) != 1 or len(across) != 2:
            continue  # not the four-sided strip expected: left free
        for t in across:
            gmsh.model.mesh.setTransfiniteCurve(t, 2)
        gmsh.model.mesh.setPeriodic(1, axis, other, affine)
        gmsh.model.mesh.setTransfiniteSurface(f)


def _made(dt, core, translation, rotation) -> bool:
    """A face or edge only the sweep made: a meridian face (its normal square to the
    axis), or a face or edge on the core strip's radius."""
    dim, tag = dt
    if dim == 3:
        return False
    lo, hi = gmsh.model.getParametrizationBounds(dim, tag)
    mid = [(a + b) / 2 for a, b in zip(lo, hi, strict=True)]
    p = _local([gmsh.model.getValue(dim, tag, mid)], translation, rotation)[0]
    tol = _tolerance()
    if core is not None and abs(math.hypot(p[0], p[1]) - core) < tol:
        return True
    if dim == 2 and gmsh.model.getType(2, tag) == "Plane":
        from puzzle.geometry.model import rotation_matrix

        n = np.asarray(gmsh.model.getNormal(tag, mid))
        axis = rotation_matrix(rotation) @ np.array([0.0, 0.0, 1.0])
        return abs(float(n @ axis)) < 1e-6
    return False


def _tolerance() -> float:
    box = np.asarray(gmsh.model.getBoundingBox(-1, -1))
    return 1e-6 * float(np.linalg.norm(box[3:] - box[:3])) + 1e-9


def profiles(volumes, *, translation, rotation) -> list[int]:
    """The profile surfaces Gmsh meshes in 2D and sweeps, once placed: the quarters'
    faces centred on the local xz half-plane at x > 0 (every other face is a quarter
    turned off it, or the meridians at 90°, 180° and 270°)."""
    from puzzle.mesh.generate import _closure

    faces = [dt for dt in _closure(volumes) if dt[0] == 2]
    centres = _local([gmsh.model.occ.getCenterOfMass(*f) for f in faces], translation, rotation)
    tol = _tolerance()
    return [t for (_, t), c in zip(faces, centres, strict=True) if abs(c[1]) < tol and c[0] > tol]


def _bolt_profile(a: dict) -> list:
    """The bolt's outline as segments [(kind, points)], counter-clockwise from the axis
    at the end, as `solid_bolts.bolt_tool` draws it (r = x, y = 0)."""
    rh, k, r = a["head_radius"], a["head_height"], a["fillet"]
    rs, rm, length, b = a["shank_radius"], a["minor_radius"], a["length"], a["thread"]
    runout = (rs - rm) / math.tan(math.radians(30))
    z_thread = -(length - b)
    z_shank = z_thread + runout
    return [
        ("line", [(0, -length), (rm, -length)]),
        ("line", [(rm, -length), (rm, z_thread)]),
        ("line", [(rm, z_thread), (rs, z_shank)]) if runout > 0 else None,
        ("line", [(rs, z_shank), (rs, -r)]),
        ("arc", [(rs, -r), (rs + r, -r), (rs + r, 0.0)]),  # start, centre, end
        ("line", [(rs + r, 0.0), (rh, 0.0)]),
        ("line", [(rh, 0.0), (rh, k)]),
        ("line", [(rh, k), (0, k)]),
        ("line", [(0, k), (0, -length)]),
    ]


def _nut_profile(a: dict) -> list:
    ro, ri, h = a["outer_radius"], a["inner_radius"], a["height"]
    return [
        ("line", [(ri, 0.0), (ro, 0.0)]),
        ("line", [(ro, 0.0), (ro, h)]),
        ("line", [(ro, h), (ri, h)]),
        ("line", [(ri, h), (ri, 0.0)]),
    ]


def _radius_at(a: dict, z: float) -> float:
    """The bolt's outer radius at height z below the fillet (thread, run-out or shank)."""
    rs, rm = a["shank_radius"], a["minor_radius"]
    z_thread = -(a["length"] - a["thread"])
    if z <= z_thread:
        return rm
    return min(rs, rm + (z - z_thread) * math.tan(math.radians(30)))


def _surfaces(profile, section, args, at, core) -> list[int]:
    """The profile as plane surfaces, cut along r = `core` (a bolt's core strip about
    the axis, `prepare`) and at the section across the bolt."""
    occ = gmsh.model.occ
    points: dict[tuple[float, float], int] = {}

    def point(r, z):
        key = (round(r, 12), round(z, 12))
        if key not in points:
            points[key] = occ.addPoint(*at(r, z))
        return points[key]

    curves = []
    for seg in profile:
        if seg is None:
            continue
        kind, pts = seg
        if kind == "arc":
            centre = occ.addPoint(*at(*pts[1]))
            curves.append(occ.addCircleArc(point(*pts[0]), centre, point(*pts[2])))
            occ.remove([(0, centre)])  # or it stays a lone node in the mesh
        else:
            curves.append(occ.addLine(point(*pts[0]), point(*pts[1])))
    face = occ.addPlaneSurface([occ.addCurveLoop(curves)])
    tools = []
    if core is not None:
        top = max(z for seg in profile if seg for _, z in seg[1])
        bottom = min(z for seg in profile if seg for _, z in seg[1])
        tools.append(occ.addLine(point(core, bottom), point(core, top)))
    if section is not None:
        z = float(section)
        tools.append(occ.addLine(point(0.0, z), point(_radius_at(args, z), z)))
    if not tools:
        return [face]
    out, _ = occ.fragment([(2, face)], [(1, t) for t in tools])
    return [t for d, t in out if d == 2]


def names(reference, volumes, core: float | None, *, translation, rotation) -> dict:
    """The reference's entities (`import_step` of the history's BREP) among the placed
    revolved part's: whole matches first, then each body, face or edge of revolution as
    the pieces of its kind whose centres, back in the local frame, lie in its bounding
    box, their measures adding up to its own. (Centres, not boxes: OCC's boxes round a
    torus's quarters reach past the reference's.)"""
    from puzzle.geometry.model import describe, match
    from puzzle.mesh.generate import _closure

    tol = 1e-5 * reference.size
    closure = _closure(volumes)
    found = match(reference.entities, closure, translation=translation, rotation=rotation,
                  tol=tol)  # fmt: skip
    kept = [dt for dt in closure if dt[0] > 0 and not _made(dt, core, translation, rotation)]
    pool = sorted(kept, key=lambda dt: (-dt[0], dt[1]))
    described = describe(pool)
    centres = _local([e.center for e in described], translation, rotation)
    pad = tol + 1e-6  # OCC pads bounding boxes by ~1e-7 m
    for ref in reference.entities:
        if ref.name in found or ref.dim == 0:
            continue
        lo, hi = np.asarray(ref.bbox[:3]) - pad, np.asarray(ref.bbox[3:]) + pad
        inside = [(e, dt) for e, dt, c in zip(described, pool, centres, strict=True)
                  if e.dim == ref.dim and e.kind == ref.kind
                  and np.all(c >= lo) and np.all(c <= hi)]  # fmt: skip
        total = sum(e.measure for e, _ in inside)
        if inside and abs(total - ref.measure) <= 1e-5 * max(ref.measure, 1e-30):
            found[ref.name] = [dt for _, dt in inside]
    return found
