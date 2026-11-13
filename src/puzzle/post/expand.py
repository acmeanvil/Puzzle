"""Results of a part model drawn as the whole (ADR 0081 step 6).

A model cut by symmetry planes, one sector of a cyclic whole or one slice of a periodic
one is drawn, when Settings > Results > Symmetric models says "the whole", with copies:

- each symmetry plane mirrors the model (every combination of up to three planes);
- an antisymmetry plane mirrors it with the solution reversed;
- a cyclic sector is turned into every sector;
- a periodic slice is repeated along its line a few times.

Vectors (3 components) and tensors (6, xx yy zz xy yz zx) are turned or mirrored with the
copy, and reversed for antisymmetry; scalars are copied. The copies are for the view:
probes, picks and extremes read the original, whose node ids every copy keeps.
"""

from __future__ import annotations

import itertools

import numpy as np

MAX_COPIES = 64
PERIODIC_COPIES = 4
VOIGT = ((0, 0), (1, 1), (2, 2), (0, 1), (1, 2), (2, 0))


def transforms(mesh, component) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """(matrix, shift, sign) for each copy, the original first: x' = M x + shift; a
    solution's vectors become sign M v."""
    from puzzle.symmetry import plane

    planes = []
    for ph in getattr(component, "physics", []):
        if ph.type != "solid_mechanics" and ph.type not in ("heat_transfer", "fluid_flow"):
            continue
        for f in ph.features:
            if f.type in ("symmetry", "antisymmetry") and f.selection in mesh.groups:
                n, c, flat = plane(mesh.node_coords(mesh.groups[f.selection].node_ids),
                                   bool(getattr(mesh, "planar", False)))  # fmt: skip
                if flat:
                    planes.append((n, c, -1.0 if f.type == "antisymmetry" else 1.0))
    unique = []
    for n, c, s in planes:  # one copy per plane, however many selections lie on it
        if not any(abs(abs(n @ m) - 1) < 1e-6 and abs(n @ (c - d)) < 1e-9 for m, d, _ in unique):
            unique.append((n, c, s))
    mirrors = [(np.eye(3), np.zeros(3), 1.0)]
    for k in range(1, min(len(unique), 3) + 1):
        for chosen in itertools.combinations(unique[:3], k):
            m, shift, sign = np.eye(3), np.zeros(3), 1.0
            for n, c, s in chosen:
                reflect = np.eye(3) - 2 * np.outer(n, n)
                m, shift, sign = reflect @ m, reflect @ shift + 2 * (n @ c) * n, sign * s
            mirrors.append((m, shift, sign))
    turns = [(np.eye(3), np.zeros(3))]
    cyclic = getattr(component, "cyclic", None)
    if cyclic is not None:
        r, o = cyclic.rotation(), np.asarray(cyclic.origin, float)
        turns, m = [], np.eye(3)
        for _k in range(cyclic.sectors):
            turns.append((m, o - m @ o))
            m = r @ m
    periodic = getattr(component, "periodic", None)
    steps = [np.zeros(3)]
    if periodic is not None:
        steps = [j * np.asarray(periodic.translation, float) for j in range(PERIODIC_COPIES)]
    out = []
    for (tm, ts), (mm, ms, sign), step in itertools.product(turns, mirrors, steps):
        out.append((tm @ mm, tm @ ms + ts + step, sign))
        if len(out) == MAX_COPIES:
            break
    return out


def mark(mesh, component) -> None:
    """A mesh told the copies its whole is drawn with (the store keeps no model)."""
    copies = transforms(mesh, component)
    mesh.copies = copies if len(copies) > 1 else None


def shown_whole(mesh) -> list | None:
    """The copies to draw `mesh`'s results with, or None: Settings > Results says."""
    copies = getattr(mesh, "copies", None)
    if not copies:
        return None
    from puzzle.settings import current

    return copies if current().get("results.symmetric_view") == "whole" else None


def _turn(values: np.ndarray, m: np.ndarray, sign: float) -> np.ndarray:
    if values.ndim == 2 and values.shape[1] == 3:
        return sign * values @ m.T
    if values.ndim == 2 and values.shape[1] == 6:
        full = np.zeros((len(values), 3, 3))
        for k, (i, j) in enumerate(VOIGT):
            full[:, i, j] = full[:, j, i] = values[:, k]
        turned = sign * np.einsum("ij,njk,lk->nil", m, full, m)
        return np.stack([turned[:, i, j] for i, j in VOIGT], axis=1)
    return values


def expand(grid, copies):
    """`grid` with a copy per transform, its point and cell data carried."""
    import pyvista as pv

    parts = []
    for m, shift, sign in copies:
        part = grid.copy(deep=True)
        part.points = np.asarray(grid.points) @ m.T + shift
        for data in (part.point_data, part.cell_data):
            for name in list(data.keys()):
                if name in ("node_id", "element_id") or name.endswith("_magnitude"):
                    continue
                data[name] = _turn(np.asarray(data[name]), m, sign)
        parts.append(part)
    return pv.merge(parts, merge_points=False)
