"""SubD from a control cage (ADR 0065 item 7b, phase 4): a quad mesh refined by
Catmull–Clark subdivision to its limit surface, and turned into B-spline patches sewn
into a solid, so it meshes and measures as any B-rep part.

Each cage face's patch is tracked as a grid of vertices through the subdivision (a
quad's grid of 2 × 2 becomes 3 × 3, 5 × 5, ...). After the last level every vertex is
projected to the limit surface (Catmull–Clark's limit masks), and each patch's grid of
limit points is fitted by one bicubic B-spline surface with uniform parameters. Two
patches share their boundary row of points, and with uniform parameters their
boundary curves are the same, so the patches sew into a closed shell. Around
extraordinary vertices (not four edges) the fit is G1-close, not exact, within its
tolerance.

Faces are named `<step>/face-<k>` after the cage face they come from. The cage is an
OBJ file of quads (`file`), or `points` and `faces` given inline.
"""

from __future__ import annotations

import numpy as np


def _h():
    from puzzle.geometry import history

    return history


def _edges(faces: list[tuple]) -> tuple[dict, dict]:
    """Each edge (sorted vertex pair) -> its faces; each vertex -> its edges."""
    edge_faces: dict[tuple, list[int]] = {}
    for f, quad in enumerate(faces):
        for a, b in zip(quad, quad[1:] + quad[:1], strict=True):
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(f)
    vertex_edges: dict[int, list[tuple]] = {}
    for e in edge_faces:
        for v in e:
            vertex_edges.setdefault(v, []).append(e)
    return edge_faces, vertex_edges


def _cells(grids: list[np.ndarray]) -> list[tuple]:
    """Every current quad, from the patch grids, as (a, b, c, d) counter-clockwise."""
    out = []
    for g in grids:
        for i in range(g.shape[0] - 1):
            for j in range(g.shape[1] - 1):
                out.append((int(g[i, j]), int(g[i, j + 1]), int(g[i + 1, j + 1]),
                            int(g[i + 1, j])))  # fmt: skip
    return out


def subdivide(points: np.ndarray, grids: list[np.ndarray]):
    """One Catmull–Clark level: new points, new grids (each (2m+1) square)."""
    faces = _cells(grids)
    edge_faces, vertex_edges = _edges(faces)
    nv = len(points)
    fpts = np.array([points[list(q)].mean(axis=0) for q in faces])
    edges = list(edge_faces)
    eindex = {e: nv + k for k, e in enumerate(edges)}
    epts = []
    for e in edges:
        a, b = e
        fs = edge_faces[e]
        if len(fs) == 2:
            epts.append((points[a] + points[b] + fpts[fs[0]] + fpts[fs[1]]) / 4)
        else:  # a boundary edge: its midpoint
            epts.append((points[a] + points[b]) / 2)
    vfaces: dict[int, list[int]] = {}
    for f, q in enumerate(faces):
        for v in q:
            vfaces.setdefault(v, []).append(f)
    vpts = points.copy()
    for v, es in vertex_edges.items():
        boundary = [e for e in es if len(edge_faces[e]) == 1]
        if boundary:  # boundary vertex: (prev + 6 v + next) / 8
            ends = [e[0] if e[1] == v else e[1] for e in boundary[:2]]
            vpts[v] = (points[ends[0]] + 6 * points[v] + points[ends[-1]]) / 8
            continue
        n = len(es)
        q = fpts[vfaces[v]].mean(axis=0)
        r = np.mean([(points[a] + points[b]) / 2 for a, b in es], axis=0)
        vpts[v] = (q + 2 * r + (n - 3) * points[v]) / n
    new = np.vstack([vpts, np.array(epts), fpts])
    findex = {q: nv + len(edges) + f for f, q in enumerate(faces)}
    out = []
    for g in grids:
        m = g.shape[0] - 1
        ng = np.zeros((2 * m + 1, 2 * m + 1), np.int64)
        for i in range(m + 1):
            for j in range(m + 1):
                ng[2 * i, 2 * j] = g[i, j]
                if j < m:
                    a, b = int(g[i, j]), int(g[i, j + 1])
                    ng[2 * i, 2 * j + 1] = eindex[(min(a, b), max(a, b))]
                if i < m:
                    a, b = int(g[i, j]), int(g[i + 1, j])
                    ng[2 * i + 1, 2 * j] = eindex[(min(a, b), max(a, b))]
                if i < m and j < m:
                    q = (int(g[i, j]), int(g[i, j + 1]), int(g[i + 1, j + 1]), int(g[i + 1, j]))
                    ng[2 * i + 1, 2 * j + 1] = findex[q]
        out.append(ng)
    return new, out


def limit(points: np.ndarray, grids: list[np.ndarray]) -> np.ndarray:
    """Every vertex projected to the Catmull–Clark limit surface: interior
    (n² v + 4 Σ edge neighbours + Σ diagonal neighbours) / (n (n + 5)); boundary
    (prev + 4 v + next) / 6."""
    faces = _cells(grids)
    edge_faces, vertex_edges = _edges(faces)
    vfaces: dict[int, list[tuple]] = {}
    for q in faces:
        for v in q:
            vfaces.setdefault(v, []).append(q)
    out = points.copy()
    for v, es in vertex_edges.items():
        nbrs = [e[0] if e[1] == v else e[1] for e in es]
        boundary = [e for e in es if len(edge_faces[e]) == 1]
        if boundary:
            ends = [e[0] if e[1] == v else e[1] for e in boundary[:2]]
            out[v] = (points[ends[0]] + 4 * points[v] + points[ends[-1]]) / 6
            continue
        n = len(es)
        diag = [q[(q.index(v) + 2) % 4] for q in vfaces[v]]
        out[v] = (n * n * points[v] + 4 * points[nbrs].sum(axis=0)
                  + points[diag].sum(axis=0)) / (n * (n + 5))  # fmt: skip
    return out


def read_cage(path) -> tuple[np.ndarray, list[tuple]]:
    """An OBJ cage's vertices and quads (`f` lines of four)."""
    pts, faces = [], []
    from pathlib import Path

    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        bits = line.split()
        if not bits:
            continue
        if bits[0] == "v":
            pts.append([float(x) for x in bits[1:4]])
        elif bits[0] == "f":
            faces.append(tuple(int(b.split("/")[0]) - 1 for b in bits[1:]))
    return np.asarray(pts, float), faces


def cage_solid(points: np.ndarray, faces: list[tuple], levels: int = 3, tolerance=None):
    """The cage's limit surface as a sewn B-rep (a solid when the cage is closed), and
    each cage face's B-rep face."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_Sewing,
    )
    from OCP.collections import Array2_gp_Pnt
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopoDS import TopoDS

    h = _h()
    if any(len(f) != 4 for f in faces):
        raise h.GeometryError("a SubD cage is made of quads (four corners each) for now")
    if not 1 <= levels <= 6:
        raise h.GeometryError("a SubD's levels are 1 to 6")
    grids = [np.array([[a, b], [d, c]], np.int64) for a, b, c, d in faces]
    pts = np.asarray(points, float)
    for _ in range(levels):
        pts, grids = subdivide(pts, grids)
    lim = limit(pts, grids)
    size = float(np.linalg.norm(np.ptp(lim, axis=0))) or 1.0
    tol = tolerance or 1e-5 * size
    sew = BRepBuilderAPI_Sewing(tol)
    made = []
    for g in grids:
        n = g.shape[0]
        arr = Array2_gp_Pnt(1, n, 1, n)
        for i in range(n):
            for j in range(n):
                arr.SetValue(i + 1, j + 1, gp_Pnt(*lim[g[i, j]]))
        fit = GeomAPI_PointsToBSplineSurface()
        fit.Interpolate(arr, False)
        if not fit.IsDone():
            raise h.GeometryError("a SubD patch didn't fit (a folded cage face?)")
        face = BRepBuilderAPI_MakeFace(fit.Surface(), 1e-9).Face()
        made.append(face)
        sew.Add(face)
    sew.Perform()
    shell = sew.SewedShape()
    shells = h._subshapes(shell, TopAbs_SHELL)
    shape = shell
    if len(shells) == 1:
        mk = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shells[0]))
        if mk.IsDone():
            shape = mk.Solid()
            from OCP.BRepLib import BRepLib

            BRepLib.OrientClosedSolid_s(shape)
    faces_out = [h._list([sew.Modified(f)])[0] if sew.IsModified(f) else f for f in made]
    return shape, faces_out


def _subd(state, a):
    """The history step `subd` (ADR 0065 item 7b): a SubD from its control cage."""
    from pathlib import Path

    if a.get("file"):
        path = Path(a["file"])
        if not path.is_absolute() and state.root is not None:
            path = Path(state.root) / path
        points, faces = read_cage(path)
        points = points * float(a.get("scale", 1.0))
    else:
        points = np.asarray(a.get("points") or [], float)
        faces = [tuple(int(i) for i in f) for f in a.get("faces") or []]
    shape, made = cage_solid(points, faces, int(a.get("levels", 3)))
    return shape, {f"face-{k}": [f] for k, f in enumerate(made, start=1)}
