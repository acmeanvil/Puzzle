"""Alignment helpers for imported fields (ADR 0044 decision 2.2): where a file's points
go on the model, found from the data instead of typed.

Each helper returns a `Fit`: a `FieldPlacement` (a rotation in degrees about x, then y,
then z, about a point, then a move; the convention `fields.place` uses) and how well it
fits. They work on the field's *base* points (`base_points`: in metres, in its
coordinate system's axes if it names one, before the rotation and move), so the
placement they return replaces the field's own rather than adding to it.

- `from_point_pairs`: three or more data points and the model points they belong at,
  the best rigid transform by Kabsch's SVD, with the residual;
- `match_boxes`: the bounding box's centre onto the target's, optionally turning the
  axes so the longest side goes onto the longest side;
- `best_fit`: rigid ICP of the points onto the target faces (a surface, or its nodes),
  from the current placement, with the RMS distance before and after.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from puzzle.fields import FieldError


@dataclass
class Fit:
    """A placement found, and how well the points then sit: `rms` after, `before` with
    the placement it started from (point pairs: their residual), `largest` the worst."""

    placement: object  # FieldPlacement
    rms: float
    before: float | None = None
    largest: float = 0.0
    iterations: int = 0
    pairs: int = 0


# --- rotations -------------------------------------------------------------------------


def rotation(degrees) -> np.ndarray:
    """The matrix for degrees about x, then y, then z (fixed axes): Rz @ Ry @ Rx."""
    from puzzle.fields import _rotation

    return _rotation(degrees)


def degrees_xyz(r: np.ndarray) -> tuple[float, float, float]:
    """The angles (degrees about x, then y, then z) whose `rotation` is `r`. At ±90°
    about y (gimbal lock) the z angle is taken as zero."""
    r = np.asarray(r, dtype=float)
    sy = -float(np.clip(r[2, 0], -1.0, 1.0))
    b = math.asin(sy)
    if math.hypot(r[0, 0], r[1, 0]) > 1e-9:
        a = math.atan2(r[2, 1], r[2, 2])
        c = math.atan2(r[1, 0], r[0, 0])
    else:  # cos b = 0: x and z turn about the same line; all of it goes into x
        a = math.atan2(-r[1, 2], r[1, 1])
        c = 0.0
    out = tuple(math.degrees(v) for v in (a, b, c))
    return tuple(0.0 if abs(v) < 1e-12 else v for v in out)  # type: ignore[return-value]


def placement_of(r: np.ndarray, t, about=(0.0, 0.0, 0.0), axes=None):
    """The FieldPlacement that moves points by x = r p + t, written as a rotation about
    `about` and then a move (x = r (p - about) + about + move), keeping `axes`."""
    from puzzle.model.schema import FieldPlacement

    about = np.asarray(about, dtype=float)
    move = np.asarray(t, dtype=float) + np.asarray(r, float) @ about - about
    clean = lambda v: tuple(0.0 if abs(x) < 1e-15 else float(x) for x in v)  # noqa: E731
    return FieldPlacement(translation=clean(move), rotation=clean(degrees_xyz(r)),
                          about=clean(about), axes=axes)  # fmt: skip


def transform_of(placement) -> tuple[np.ndarray, np.ndarray]:
    """(r, t) with x = r p + t for a placement's rotation and move (its axes aside)."""
    r = rotation(placement.rotation)
    about = np.asarray(placement.about, dtype=float)
    return r, about - r @ about + np.asarray(placement.translation, dtype=float)


def apply(points: np.ndarray, r: np.ndarray, t) -> np.ndarray:
    return np.asarray(points, float) @ np.asarray(r, float).T + np.asarray(t, float)


# --- the field's points ------------------------------------------------------------------


def base_points(project, root: Path, f) -> np.ndarray:
    """The field's points in metres, in its coordinate system's axes when it names one,
    before its rotation and move: what the helpers fit."""
    from puzzle.fields import _si, place, read_table
    from puzzle.model.schema import FieldPlacement

    raw, _ = read_table(root, f)
    a, b, _ = _si(f.length_unit)
    return place(a * raw + b, FieldPlacement(axes=f.placement.axes), project)


# --- the helpers ---------------------------------------------------------------------------


def kabsch(source: np.ndarray, target: np.ndarray, weights=None) -> tuple[np.ndarray, np.ndarray]:
    """The rotation r and move t minimising sum |r s + t - q|² (no reflection)."""
    s, q = np.asarray(source, float), np.asarray(target, float)
    w = np.ones(len(s)) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    cs, cq = w @ s, w @ q
    h = ((s - cs) * w[:, None]).T @ (q - cq)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, cq - r @ cs


def _rms(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    d = np.linalg.norm(np.asarray(a, float) - np.asarray(b, float), axis=1)
    return float(np.sqrt(np.mean(d**2))) if len(d) else 0.0, float(d.max()) if len(d) else 0.0


def from_point_pairs(source, target, about=(0.0, 0.0, 0.0), axes=None) -> Fit:
    """Three or more data points (base frame, m) and where each belongs on the model (m):
    the rigid placement that fits them best, and the pairs' residual (zero when the
    distances between the points agree)."""
    s, q = np.asarray(source, float).reshape(-1, 3), np.asarray(target, float).reshape(-1, 3)
    if len(s) != len(q):
        raise FieldError(f"{len(s)} data points but {len(q)} model points: give them in pairs")
    if len(s) < 3:
        raise FieldError("three point pairs at least: two leave a turn about their line free")
    size = max(float(np.linalg.norm(np.ptp(s, axis=0))), 1e-300)
    sv = np.linalg.svd(s - s.mean(axis=0), compute_uv=False)
    if sv[1] < 1e-9 * size * math.sqrt(len(s)):
        raise FieldError("the data points lie on one line: pick three that make a triangle")
    r, t = kabsch(s, q)
    rms, largest = _rms(apply(s, r, t), q)
    return Fit(placement_of(r, t, about, axes), rms, before=rms, largest=largest, pairs=len(s))


def _box(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(points, float)
    lo, hi = p.min(axis=0), p.max(axis=0)
    return (lo + hi) / 2, hi - lo


def match_boxes(source, target, order: bool = False, about=(0.0, 0.0, 0.0), axes=None) -> Fit:
    """The data's bounding box centred on the target's. With `order`, the axes are also
    turned (a quarter-turn rotation) so the data's longest side lies along the target's
    longest, and so on: for a file written in other axes. `rms` is half the boxes' sizes'
    difference per axis (zero when they are the same box)."""
    s, q = np.asarray(source, float), np.asarray(target, float)
    cs, es = _box(s)
    cq, eq = _box(q)
    r = np.eye(3)
    if order:
        r = np.zeros((3, 3))
        for a, b in zip(np.argsort(es), np.argsort(eq), strict=True):
            r[b, a] = 1.0
        if np.linalg.det(r) < 0:  # no mirror: turn the shortest side the other way
            r[:, int(np.argsort(es)[0])] *= -1.0
    t = cq - r @ cs
    gap = np.abs(np.abs(r) @ es - eq)  # how far the boxes' sizes still differ, per axis
    rms = float(np.sqrt(np.mean((gap / 2) ** 2)))
    return Fit(placement_of(r, t, about, axes), rms, largest=float(gap.max()) / 2)


def _closest(target, points: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Each point's closest point on the target, and the face's normal there: a
    pyvista surface, or its nodes (no normals)."""
    if hasattr(target, "find_closest_cell"):
        cells, closest = target.find_closest_cell(points, return_closest_point=True)
        normals = np.asarray(target.cell_normals, float)[np.asarray(cells)]
        return np.asarray(closest, float), normals
    from scipy.spatial import cKDTree

    nodes = np.asarray(target, float)
    _, i = cKDTree(nodes).query(points)
    return nodes[i], None


def _turn(w: np.ndarray) -> np.ndarray:
    """The rotation by the vector `w` (radians about its direction; Rodrigues)."""
    angle = float(np.linalg.norm(w))
    if angle < 1e-300:
        return np.eye(3)
    k = w / angle
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


def _plane_step(moved, closest, normals, centre) -> tuple[np.ndarray, np.ndarray]:
    """One point-to-plane step: the small turn (about `centre`) and move that bring the
    points onto the faces' planes, by least squares; directions the faces don't hold
    (sliding along a flat face) are left as they are."""
    p = moved - centre
    a = np.hstack([np.cross(p, normals), normals])
    b = np.einsum("ij,ij->i", closest - moved, normals)
    x = np.linalg.lstsq(a, b, rcond=1e-10)[0]
    r = _turn(x[:3])
    return r, centre - r @ centre + x[3:]


def best_fit(source, target, start=None, iterations: int = 100, trim: float = 3.0) -> Fit:
    """Rigid ICP: the data points (base frame, m) moved onto the target, a pyvista
    surface (point to plane: closest points on its faces) or an array of its nodes
    (point to point), starting from the placement `start` (the field's current one).
    Pairs farther than `trim` times the median distance are left out of each step, so
    data past the target's edge doesn't drag it, and so are points whose closest point
    is on a face's edge rather than across from them (they would pull sideways). Gives
    the RMS distance before and after."""
    from puzzle.model.schema import FieldPlacement

    start = start or FieldPlacement()
    s = np.asarray(source, float)
    if len(s) < 3:
        raise FieldError("a best fit needs three points at least")
    r, t = transform_of(start)
    before, _ = _rms(apply(s, r, t), _closest(target, apply(s, r, t))[0])
    size = max(float(np.linalg.norm(np.ptp(s, axis=0))), 1e-300)
    rms, done = before, 0
    for done in range(1, iterations + 1):  # noqa: B007 - how many it took
        moved = apply(s, r, t)
        q, normals = _closest(target, moved)
        d = np.linalg.norm(moved - q, axis=1)
        keep = d <= max(trim * float(np.median(d)), 1e-12 * size)
        if normals is not None:  # past a face's edge the closest point is on the edge:
            gap = q - moved  # such pairs pull sideways, so they sit this step out
            along = np.einsum("ij,ij->i", gap, normals)
            sideways = np.linalg.norm(gap - along[:, None] * normals, axis=1)
            inside = sideways <= 0.01 * np.abs(along) + 1e-9 * size
            if (keep & inside).sum() >= 6:
                keep &= inside
        if keep.sum() < 3:
            keep = np.ones(len(s), bool)
        if normals is None:
            r, t = kabsch(s[keep], q[keep])
        else:
            dr, dt = _plane_step(moved[keep], q[keep], normals[keep], moved[keep].mean(axis=0))
            r, t = dr @ r, dr @ t + dt
        new, _ = _rms(apply(s, r, t)[keep], _closest(target, apply(s, r, t)[keep])[0])
        if abs(rms - new) <= 1e-12 * size or new <= 1e-14 * size:
            rms = new
            break
        rms = new
    moved = apply(s, r, t)
    rms, largest = _rms(moved, _closest(target, moved)[0])
    return Fit(placement_of(r, t, start.about, start.axes), rms, before=before,
               largest=largest, iterations=done, pairs=len(s))  # fmt: skip
