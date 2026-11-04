"""The deformed shape as real CAD by morphing (ADR 0043 steps 2.2 and 2.3).

Each solid part's CAD shape, placed as the mesher placed it, is converted to B-splines
(`BRepBuilderAPI_NurbsConvert`: every face a `Geom_BSplineSurface`, every edge a
`Geom_BSplineCurve`, the topology kept). The geometry is then changed in place, never
rebuilt, so faces, edges and vertices stay shared as they were and no sewing is needed:

- each surface and edge curve is refined first (degree raised to 3, a periodic one made
  clamped over its period, knots inserted uniformly until the poles are about the
  mesh's surface spacing apart, at most 64 spans a direction). None of this changes a
  B-spline's shape or parametrisation, so the edges' pcurves stay valid on the refined
  surfaces;
- every pole is moved by u × scale (−u when compensated), u interpolated in the part's
  own undeformed mesh (VTK's probe with a tight tolerance, the elements' shape
  functions). A point on or off the boundary (a rational arc's middle pole sits outside
  a cylinder) takes u from its nearest cell at the nearest point plus the displacement
  gradient there times the offset, so a rigid rotation moves it exactly;
- the poles are then corrected three times by what the moved curve or surface still
  misses at its Greville points (x + u wanted there): moving poles alone is a
  Schoenberg approximation, off by about spacing² u''/6; the corrections take it close
  to interpolation (a parabola's error fell from 8e-5 to 8e-7 of its depth);
- vertices move by u at their own position; `BRepLib.SameParameter` then sets the edge
  tolerances to how far the moved curves and surfaces disagree.

Moving poles is exact for an affine displacement (B-splines are affine invariant, the
rational ones too), so a uniform thermal expansion is morphed exactly. The morph is
linear in u, so exaggerating doesn't spoil it: the cantilever stays within 3e-5 of its
tip deflection of the mesh at any scale.

The export checks what it wrote against the deformed mesh, and refuses (PuzzleError
`export_failed`, naming the deviation and suggesting the faceted `deformed_step`) rather
than write a wrong solid: the shape must be a valid solid (`BRepCheck_Analyzer`, after
`ShapeFix_Shape` if needed), every deformed surface node of the mesh must lie within the
tolerance (1 % of the largest displacement, × scale; `tolerance` in the export's extra
options) of the morphed face at the same material point, and the volume change must
match the deformed mesh's to that tolerance times the surface area (this is what
refuses a deformation that folds the part over itself, such as a mass-normalised mode
at scale 1). The numbers go in the sidecar. What it can't follow is a deformation that
changes over less than the pole spacing: a kink or a jump (contact, a plastic hinge,
two materials in one part), or a long part whose mesh is finer than 64 spans allow.

Written as STEP AP214 with real surfaces, in millimetres (built in metres, OCCT
converts, as `deformed.py`), each part a named body; several parts as an assembly.

Refused: shell, beam and 2D parts (no solid to morph), faceted (STL/OBJ/3MF) parts.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np

from puzzle.errors import PuzzleError

TOLERANCE = 0.01  # of the largest displacement (× scale)
MAX_SPANS = 64  # per surface direction or curve
CORRECTIONS = 3  # pole corrections towards interpolation at the Greville points


def write_job(job):
    """`EXPORTS` writer: every solid part morphed, checked, written as one STEP."""
    from puzzle.post.exports.deformed import shape_notes

    bodies, checks = morphed_parts(job)
    write_step(bodies, job.path)
    notes = shape_notes(job, list(bodies)) | {"length unit": "mm"}
    for name, c in checks.items():
        notes[f"check {name}"] = c.text()
    return [job.path], notes


# --- the parts --------------------------------------------------------------------------


@dataclass
class Check:
    """How a morphed part compares with the deformed mesh (SI)."""

    deviation: float  # largest distance from a deformed surface node to the morphed faces
    tolerance: float
    volume_cad: float
    volume_morphed: float
    change_mesh: float  # the deformed mesh's volume change
    area: float

    @property
    def volume_error(self) -> float:
        return abs((self.volume_morphed - self.volume_cad) - self.change_mesh)

    def ok(self) -> bool:
        return self.deviation <= self.tolerance and self.volume_error <= self.tolerance * self.area

    def text(self) -> str:
        change = (self.volume_morphed - self.volume_cad) / self.volume_cad
        mesh = self.change_mesh / self.volume_cad
        return (
            f"largest deviation from the deformed mesh {self.deviation:.3g} m "
            f"(tolerance {self.tolerance:.3g} m); volume {self.volume_cad:.6g} -> "
            f"{self.volume_morphed:.6g} m3 ({change:+.4%}; the mesh's {mesh:+.4%})"
        )


def morphed_parts(job, tolerance: float | None = None) -> tuple[dict, dict[str, Check]]:
    """Part name -> morphed solid shape, and part name -> its check. Raises when a part
    can't be morphed within the tolerance."""
    import pyvista as pv

    from puzzle.geometry.contacts import _placement
    from puzzle.geometry.meshfile import is_mesh_file
    from puzzle.geometry.naming import named_geometry
    from puzzle.geometry.session import occ_lock
    from puzzle.model.schema import Component, Geometry, Study
    from puzzle.post.exports.deformed import deformed_surfaces
    from puzzle.post.vtk import to_grid

    project, mesh, step = job.project, job.mesh, job.step
    if "displacement" not in step.fields:
        raise PuzzleError("this step has no displacement to deform by", code="not_found")
    root = Path(job.root) if job.root is not None else None
    if root is None:
        raise PuzzleError("the morphed shape needs the project folder", code="invalid")
    study = project.get(UUID(str(job.record.study_id)), Study)
    comp = project.get(study.component, Component)
    rel = float(job.extra.get("tolerance", TOLERANCE) if tolerance is None else tolerance)
    sign = (-1.0 if job.compensate else 1.0) * job.scale
    u_all = np.asarray(step.fields["displacement"].values, float)
    largest = float(np.linalg.norm(u_all, axis=1).max()) * abs(job.scale)

    grid = to_grid(mesh, step)
    element_ids = np.asarray(grid.cell_data["element_id"])
    surfaces = deformed_surfaces(project, mesh, step, job.scale, job.compensate)
    undeformed = deformed_surfaces(project, mesh, step, 0.0)  # the same nodes, in order
    bodies, checks = {}, {}
    for part in comp.parts:
        group = mesh.groups.get(part.name)
        if group is None:  # suppressed
            continue
        if group.dim != 3 or getattr(mesh, "planar", False):
            raise PuzzleError(
                f"part {part.name} is not a solid: only solids are morphed; export the "
                "faceted shape (deformed_step) instead",
                code="unsupported",
            )
        geo = project.get(part.geometry, Geometry)
        if not geo.history and is_mesh_file(root / geo.source):
            raise PuzzleError(
                f"part {part.name} is faceted ({geo.source}): it has no B-rep to morph; "
                "export the faceted shape (deformed_step) instead",
                code="unsupported",
            )
        ids = np.concatenate([b.ids for b in group.blocks(mesh) if b.dim == 3])
        piece = grid.extract_cells(np.flatnonzero(np.isin(element_ids, ids)))
        piece = piece.compute_derivative(scalars="displacement", gradient="gradient")
        before = float(piece.volume)
        after = float(piece.warp_by_vector("displacement", factor=sign).volume)
        points, triangles = surfaces[part.name]
        spacing = _spacing(undeformed[part.name][0], triangles)  # not the stretched one
        tol = max(rel * largest, 1e-9)
        with occ_lock:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

            shape = named_geometry(project, geo, root).shape
            if any(part.translation) or any(part.rotation):
                placed = BRepBuilderAPI_Transform(
                    shape, _placement(part.translation, part.rotation), True
                )
                shape = placed.Shape()
            volume_cad, _ = _volume_area(shape)
            moved = morph(shape, displacer(piece, sign, pv), spacing)
            volume, area = _volume_area(moved)
            deviation = _deviation(shape, moved, undeformed[part.name][0], points)
        check = Check(deviation, tol, volume_cad, volume, after - before, area)
        if not check.ok():
            share = f"{rel * 100:g} % of the largest displacement"
            if check.deviation > tol:
                what = (f"is off the deformed mesh by {check.deviation:.3g} m, past the "
                        f"tolerance of {tol:.3g} m ({share})")  # fmt: skip
            else:
                what = (f"changes its volume by {volume - volume_cad:.3g} m3 where the "
                        f"deformed mesh's changes by {after - before:.3g} m3, more than the "
                        f"tolerance of {tol:.3g} m ({share}) over its faces allows; the "
                        "deformation may fold the part over itself (a smaller scale?)")  # fmt: skip
            raise PuzzleError(
                f"the morphed {part.name} {what}: morphing cannot follow this deformation; "
                "export the faceted shape (deformed_step) instead",
                code="export_failed",
                details={
                    "part": part.name,
                    "deviation_m": check.deviation,
                    "tolerance_m": tol,
                    "volume_error_m3": check.volume_error,
                    "instead": "deformed_step",
                },  # fmt: skip
            )
        bodies[part.name], checks[part.name] = moved, check
    if not bodies:
        raise PuzzleError("the component has no solid parts to export", code="not_found")
    return bodies, checks


def displacer(piece, sign: float, pv) -> Callable[[np.ndarray], np.ndarray]:
    """u × sign at points: interpolated inside the part's undeformed mesh; off it, from
    the nearest point of the mesh plus the gradient there times the offset."""
    from vtkmodules.vtkCommonCore import reference

    u_nodes = np.asarray(piece.point_data["displacement"], float)
    # vtk's gradient of a vector is du/dx, du/dy, du/dz, dv/dx, ...: G[j, i] = du_j/dx_i
    g_nodes = np.asarray(piece.point_data["gradient"], float).reshape(-1, 3, 3)

    tight = 1e-8 * _size(np.asarray(piece.points, float))

    def at(points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, float).reshape(-1, 3)
        # A tight tolerance: VTK's default lets the probe take a neighbouring cell and
        # extrapolate it (0.1 % of a mode's largest displacement off at a node).
        probe = pv.PolyData(points).sample(piece, tolerance=tight)
        u = np.asarray(probe.point_data["displacement"], float).reshape(-1, 3).copy()
        inside = np.asarray(probe.point_data["vtkValidPointMask"], bool)
        out = np.flatnonzero(~inside)
        if len(out):
            # On or off the boundary (a probe misses points on a face): the nearest
            # point's cell, evaluated by its own shape functions there.
            cells, near = piece.find_closest_cell(points[out], return_closest_point=True)
            near = np.asarray(near, float).reshape(-1, 3)
            for k, (cid, c) in enumerate(zip(np.atleast_1d(cells), near, strict=True)):
                cell = piece.GetCell(int(cid))
                n = cell.GetNumberOfPoints()
                weights, closest, pcoords = [0.0] * n, [0.0] * 3, [0.0] * 3
                cell.EvaluatePosition(list(c), closest, reference(0), pcoords, reference(0.0),
                                      weights)  # fmt: skip
                ids = [cell.GetPointId(i) for i in range(n)]
                w = np.asarray(weights)
                u0 = w @ u_nodes[ids]
                grad = np.einsum("n,nji->ji", w, g_nodes[ids])
                u[out[k]] = u0 + grad @ (points[out[k]] - c)
        return sign * u

    return at


def _size(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.ptp(points, axis=0))) or 1.0


def _spacing(points: np.ndarray, triangles: np.ndarray) -> float:
    """The mesh's surface spacing: the median edge of its (subdivided) triangles."""
    a, b, c = (points[triangles[:, k]] for k in range(3))
    edges = np.concatenate([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1)])
    return float(np.median(edges)) or _size(points) / 10


# --- the morph (pure OCCT) -------------------------------------------------------------


def morph(shape, move: Callable[[np.ndarray], np.ndarray], spacing: float,
          convert: bool = True):  # fmt: skip
    """`shape` with its geometry moved by `move` (points (n, 3) -> displacements (n, 3)),
    poles about `spacing` apart. Raises when the moved shape isn't a valid solid.
    `convert` False: the shape is NURBS already and is moved in place (its faces stay
    the same objects, so names on them hold: ADR 0065's `deform`)."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepLib import BRepLib
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.history import _static

    nurbs = BRepBuilderAPI_NurbsConvert(shape, True).Shape() if convert else shape
    surface_of, curve_of = _static(BRep_Tool, "Surface"), _static(BRep_Tool, "Curve")
    pnt_of, tol_of = _static(BRep_Tool, "Pnt"), _static(BRep_Tool, "Tolerance")
    degenerated = _static(BRep_Tool, "Degenerated")
    pending: list[tuple[str, object, object]] = []  # (kind, geometry, location)
    kept: list = []
    for face in _unique(nurbs, TopAbs_FACE, _static(TopoDS, "Face")):
        loc = TopLoc_Location()
        s = surface_of(face, loc)
        if not hasattr(s, "SetPole"):
            raise PuzzleError("a face did not convert to a B-spline", code="unsupported")
        if any(s is k for k in kept):  # faces on one surface share it: move it once
            continue
        kept.append(s)
        _refine_surface(s, spacing)
        pending.append(("surface", s, loc))
    for edge in _unique(nurbs, TopAbs_EDGE, _static(TopoDS, "Edge")):
        if degenerated(edge):
            continue
        loc = TopLoc_Location()
        c = curve_of(edge, loc, 0.0, 0.0)
        if c is None or any(c is k for k in kept):
            continue
        if not hasattr(c, "SetPole"):
            raise PuzzleError("an edge did not convert to a B-spline", code="unsupported")
        kept.append(c)
        _refine_curve(c, spacing)
        pending.append(("curve", c, loc))
    vertices = _unique(nurbs, TopAbs_VERTEX, _static(TopoDS, "Vertex"))

    # Every pole, every pole's Greville point on the geometry, and every vertex, in
    # global coordinates, moved in one probe.
    sites = [_greville(kind, g) for kind, g, _ in pending]
    chunks = []
    for (kind, g, loc), at_sites in zip(pending, sites, strict=True):
        chunks += [_global(_poles(kind, g), loc), _global(_values(kind, g, at_sites), loc)]
    chunks.append(np.array([_xyz(pnt_of(v)) for v in vertices]).reshape(-1, 3))
    every = np.concatenate(chunks) if chunks else np.zeros((0, 3))
    moved = every + move(every)
    at = 0
    for (kind, g, loc), at_sites in zip(pending, sites, strict=True):
        n = len(at_sites)
        poles, target = moved[at : at + n], moved[at + n : at + 2 * n]
        at += 2 * n
        for _ in range(CORRECTIONS):  # towards interpolating x + u at the Greville points
            _set_poles(kind, g, _local(poles, loc))
            poles = poles + target - _global(_values(kind, g, at_sites), loc)
        _set_poles(kind, g, _local(poles, loc))
    builder = BRep_Builder()
    from OCP.gp import gp_Pnt

    for v, p in zip(vertices, moved[at:], strict=True):
        builder.UpdateVertex(v, gp_Pnt(*(float(c) for c in p)), tol_of(v))
    _static(BRepTools, "Clean")(nurbs)
    _static(BRepLib, "SameParameter")(nurbs, 1e-7, True)
    _static(BRepLib, "UpdateTolerances")(nurbs, False)
    if not BRepCheck_Analyzer(nurbs).IsValid():
        from OCP.ShapeFix import ShapeFix_Shape

        fix = ShapeFix_Shape(nurbs)
        fix.Perform()
        nurbs = fix.Shape()
        if not BRepCheck_Analyzer(nurbs).IsValid():
            raise PuzzleError(
                "the morphed shape is not a valid solid (its faces no longer meet): "
                "export the faceted shape (deformed_step) instead",
                code="export_failed",
                details={"instead": "deformed_step"},
            )
    return nurbs


def _unique(shape, kind, cast) -> list:
    from OCP.TopExp import TopExp

    from puzzle.geometry.history import _static

    try:
        from OCP.TopTools import TopTools_IndexedMapOfShape
    except ImportError:  # OCP 8
        from OCP.collections import (
            IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape,
        )
    found = TopTools_IndexedMapOfShape()
    _static(TopExp, "MapShapes")(shape, kind, found)
    return [cast(found.FindKey(i)) for i in range(1, found.Extent() + 1)]


def _xyz(p) -> tuple[float, float, float]:
    return (p.X(), p.Y(), p.Z())


def _uniform(lo: float, hi: float, n: int) -> list[float]:
    return [lo + (hi - lo) * k / n for k in range(1, n)]


def _spans(length: float, spacing: float) -> int:
    return max(1, min(MAX_SPANS, math.ceil(length / spacing)))


def _new_knots(knots: list[float], at: list[float]) -> list[float]:
    span = (knots[-1] - knots[0]) or 1.0
    return [u for u in at if min(abs(u - k) for k in knots) > 1e-9 * span]


def _refine_surface(s, spacing: float) -> None:
    """Degree 3 and about `spacing` between knots, the shape unchanged (a periodic
    surface made clamped over its period, so its Greville points are plain)."""
    s.IncreaseDegree(max(3, s.UDegree()), max(3, s.VDegree()))  # before: OCCT fails after
    if s.IsUPeriodic():
        s.SetUNotPeriodic()
    if s.IsVPeriodic():
        s.SetVNotPeriodic()
    u1, u2, v1, v2 = s.Bounds()
    us, vs = np.linspace(u1, u2, 9), np.linspace(v1, v2, 9)
    grid = np.array([[_xyz(s.Value(u, v)) for v in vs] for u in us])  # (u, v, 3)
    along_u = np.linalg.norm(np.diff(grid, axis=0), axis=2).sum(axis=0).max()
    along_v = np.linalg.norm(np.diff(grid, axis=1), axis=2).sum(axis=1).max()
    knots_u = [s.UKnot(i) for i in range(1, s.NbUKnots() + 1)]
    for u in _new_knots(knots_u, _uniform(u1, u2, _spans(along_u, spacing))):
        s.InsertUKnot(u, 1, 0.0, True)
    knots_v = [s.VKnot(i) for i in range(1, s.NbVKnots() + 1)]
    for v in _new_knots(knots_v, _uniform(v1, v2, _spans(along_v, spacing))):
        s.InsertVKnot(v, 1, 0.0, True)


def _refine_curve(c, spacing: float) -> None:
    c.IncreaseDegree(max(3, c.Degree()))
    if c.IsPeriodic():
        c.SetNotPeriodic()
    t1, t2 = c.FirstParameter(), c.LastParameter()
    points = np.array([_xyz(c.Value(t)) for t in np.linspace(t1, t2, 17)])
    length = np.linalg.norm(np.diff(points, axis=0), axis=1).sum()
    knots = [c.Knot(i) for i in range(1, c.NbKnots() + 1)]
    for t in _new_knots(knots, _uniform(t1, t2, _spans(length, spacing))):
        c.InsertKnot(t, 1, 0.0, True)


def _flat(knot, multiplicity, n: int) -> list[float]:
    return [knot(i) for i in range(1, n + 1) for _ in range(multiplicity(i))]


def _averages(flat: list[float], degree: int, n: int) -> list[float]:
    """The Greville abscissae: where each pole acts most (clamped knots)."""
    return [sum(flat[i + 1 : i + degree + 1]) / degree for i in range(n)]


def _greville(kind: str, g) -> list:
    """Each pole's Greville parameters, in pole order ((u, v) on a surface)."""
    if kind == "surface":
        us = _averages(_flat(g.UKnot, g.UMultiplicity, g.NbUKnots()), g.UDegree(), g.NbUPoles())
        vs = _averages(_flat(g.VKnot, g.VMultiplicity, g.NbVKnots()), g.VDegree(), g.NbVPoles())
        return [(u, v) for u in us for v in vs]
    return _averages(_flat(g.Knot, g.Multiplicity, g.NbKnots()), g.Degree(), g.NbPoles())


def _values(kind: str, g, sites: list) -> np.ndarray:
    if kind == "surface":
        return np.array([_xyz(g.Value(u, v)) for u, v in sites])
    return np.array([_xyz(g.Value(t)) for t in sites])


def _poles(kind: str, g) -> np.ndarray:
    if kind == "surface":
        return np.array(
            [
                _xyz(g.Pole(i, j))
                for i in range(1, g.NbUPoles() + 1)
                for j in range(1, g.NbVPoles() + 1)
            ]  # fmt: skip
        )
    return np.array([_xyz(g.Pole(i)) for i in range(1, g.NbPoles() + 1)])


def _set_poles(kind: str, g, points: np.ndarray) -> None:
    from OCP.gp import gp_Pnt

    if kind == "surface":
        nv = g.NbVPoles()
        for k, p in enumerate(points):
            g.SetPole(k // nv + 1, k % nv + 1, gp_Pnt(*(float(c) for c in p)))
        return
    for k, p in enumerate(points):
        g.SetPole(k + 1, gp_Pnt(*(float(c) for c in p)))


def _matrix(loc) -> tuple[np.ndarray, np.ndarray]:
    t = loc.Transformation()
    rot = np.array([[t.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    shift = np.array(_xyz(t.TranslationPart()))
    return rot, shift


def _global(points: np.ndarray, loc) -> np.ndarray:
    if loc.IsIdentity():
        return points
    rot, shift = _matrix(loc)
    return points @ rot.T + shift


def _local(points: np.ndarray, loc) -> np.ndarray:
    if loc.IsIdentity():
        return points
    rot, shift = _matrix(loc)
    return np.linalg.solve(rot, (points - shift).T).T


# --- checks ----------------------------------------------------------------------------


def _volume_area(shape) -> tuple[float, float]:
    """Volume and area to 1e-9 (OCCT's default integration is off by 1e-4 on B-splines,
    as much as a thermal expansion changes the volume)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    v, a = GProp_GProps(), GProp_GProps()
    _static(BRepGProp, "VolumeProperties")(shape, v, 1e-9, False)
    _static(BRepGProp, "SurfaceProperties")(shape, a, 1e-9)
    return abs(v.Mass()), a.Mass()


def _deviation(shape, moved, before: np.ndarray, after: np.ndarray) -> float:
    """The largest distance between the deformed mesh's surface nodes (`after`) and
    the morphed faces at the same material points: each node's undeformed position
    (`before`) is found on the part's CAD face (nearest face on a triangulation, then
    OCCT's projection, `ShapeAnalysis_Surface`), and the morphed face is evaluated at
    those parameters. Morphing keeps every face's parametrisation, so this measures
    the morph itself, and bounds the plain distance between the two shapes; it stays
    well posed when a large exaggeration folds the shape, where a nearest-point
    distance on the deformed shape (or BRepExtrema, which measured a B-spline
    cylinder's distance to its edges) does not."""
    import pyvista as pv
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from vtkmodules.vtkCommonCore import reference
    from vtkmodules.vtkCommonDataModel import vtkCellLocator

    from puzzle.geometry.history import _static

    face = _static(TopoDS, "Face")
    plain = BRepBuilderAPI_NurbsConvert(shape, True).Shape()  # the morph's parameters
    faces, morphed = _unique(plain, TopAbs_FACE, face), _unique(moved, TopAbs_FACE, face)
    if len(faces) != len(morphed):
        raise PuzzleError("the morphed shape lost faces", code="export_failed")
    size = _size(before)
    BRepMesh_IncrementalMesh(plain, 1e-3 * size, False, 0.2, True)
    triangulation = _static(BRep_Tool, "Triangulation")
    nodes, cells, owner, base = [], [], [], 0
    for k, f in enumerate(faces):
        loc = TopLoc_Location()
        tri = triangulation(f, loc)
        if tri is None:
            continue
        t = loc.Transformation()
        nodes += [_xyz(tri.Node(i).Transformed(t)) for i in range(1, tri.NbNodes() + 1)]
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            cells.append((3, base + a - 1, base + b - 1, base + c - 1))
            owner.append(k)
        base += tri.NbNodes()
    if not cells:
        raise PuzzleError("the part's faces could not be triangulated", code="export_failed")
    locator = vtkCellLocator()
    locator.SetDataSet(pv.PolyData(np.array(nodes), np.array(cells).ravel()))
    locator.BuildLocator()
    surface_of, bounds = _static(BRep_Tool, "Surface"), _static(BRepTools, "UVBounds")
    analysers: dict[int, tuple] = {}

    def on(k: int, p) -> tuple[float, float, float] | None:
        """(gap, u, v) of p projected on face k, or None outside its parameter range."""
        if k not in analysers:
            analysers[k] = (ShapeAnalysis_Surface(surface_of(faces[k])), bounds(faces[k]))
        analyser, (u1, u2, v1, v2) = analysers[k]
        uv = analyser.ValueOfUV(p, 1e-9 * size)
        slack = 1e-6 * max(u2 - u1, v2 - v1)
        if not (u1 - slack <= uv.X() <= u2 + slack and v1 - slack <= uv.Y() <= v2 + slack):
            return None
        return analyser.Gap(), uv.X(), uv.Y()

    closest, cell, sub, d2 = [0.0] * 3, reference(0), reference(0), reference(0.0)
    worst = 0.0
    for x, y in zip(before, after, strict=True):
        locator.FindClosestPoint([float(c) for c in x], closest, cell, sub, d2)
        p = gp_Pnt(*(float(c) for c in x))
        hit = on(owner[int(cell)], p)
        k = owner[int(cell)]
        if hit is None or hit[0] > 1e-6 * size:  # by an edge: the face it is really on
            tries = [(h, j) for j in range(len(faces)) if (h := on(j, p)) is not None]
            if not tries:
                continue
            hit, k = min(tries, key=lambda t: t[0][0])
        q = surface_of(morphed[k]).Value(hit[1], hit[2])
        worst = max(worst, float(np.linalg.norm(np.array(_xyz(q)) - y)))
    return worst


# --- STEP -------------------------------------------------------------------------------


def write_step(bodies: dict, path: Path) -> list[Path]:
    """STEP AP214 with real surfaces, in millimetres: each part a body named after it;
    several parts as an assembly of them. Built in metres; OCCT converts on writing."""
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from puzzle.geometry.history import _static
    from puzzle.geometry.session import occ_lock

    with occ_lock:
        _static(STEPControl_Controller, "Init")()
        set_c = _static(Interface_Static, "SetCVal")
        set_c("write.step.schema", "AP214IS")
        set_c("xstep.cascade.unit", "M")
        set_c("write.step.unit", "MM")
        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        set_unit = getattr(XCAFDoc_DocumentTool, "SetLengthUnit_s", None) or getattr(
            XCAFDoc_DocumentTool, "SetLengthUnit", None
        )
        if set_unit is not None:  # the document is in metres
            set_unit(doc, 1.0)
        tool = _static(XCAFDoc_DocumentTool, "ShapeTool")(doc.Main())
        name = _static(TDataStd_Name, "Set")
        if len(bodies) == 1:
            ((part, shape),) = bodies.items()
            name(tool.AddShape(_solid(shape), False), TCollection_ExtendedString(part))
        else:
            assembly = tool.NewShape()
            name(assembly, TCollection_ExtendedString(path.stem))
            from OCP.TopLoc import TopLoc_Location

            for part, shape in bodies.items():
                label = tool.AddShape(_solid(shape), False)
                name(label, TCollection_ExtendedString(part))
                instance = tool.AddComponent(assembly, label, TopLoc_Location())
                name(instance, TCollection_ExtendedString(part))
            tool.UpdateAssemblies()
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        if (
            not writer.Transfer(doc, STEPControl_AsIs)
            or str(writer.Write(str(path))).split(".")[-1] != "IFSelect_RetDone"
        ):
            raise PuzzleError(f"OCCT could not write {path}", code="export_failed")
    return [path]


def _solid(shape):
    """One part's shape as one solid when it is one (NurbsConvert returns a compound)."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.history import _static

    solids = _unique(shape, TopAbs_SOLID, _static(TopoDS, "Solid"))
    return solids[0] if len(solids) == 1 else shape
