"""Mesh morphing for shape optimisation (ADR 0065 phase 2): the mesh's nodes moved, its
connectivity kept, so an optimisation's points differ in node positions only. There
is no remeshing between points, so none of the mesh noise ADR 0063 warns of.

A study's `morph` names handles: a face selection whose nodes move a distance along a
direction (or the face's outward normal), the distance a number or a parameter's name,
so an optimisation's variable drives it. Held selections (default: the study's fixed
supports) stay put. Radial basis functions (thin-plate spline with a linear part,
scipy's `RBFInterpolator`) spread the handles' motion to every other node. A morph
that inverts an element, or leaves one below `quality_floor` (the shape quality, 1 a
regular tet), is refused with the count, as a remesh would be; bricks, wedges,
pyramids and faces by their corner simplices.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from puzzle.errors import PuzzleError

NEIGHBOURS = 64  # control points each node's interpolation reads beyond this many points
DENSE = 3000  # control points solved densely below this


def _distance(value, params: dict[str, float], what: str) -> float:
    if isinstance(value, str):
        if value not in params:
            raise PuzzleError(f"morph: no parameter '{value}' for {what}", code="not_found",
                              details={"parameters": sorted(params)})  # fmt: skip
        return float(params[value])
    return float(value)


def _nodes(mesh, selection: str) -> np.ndarray:
    from puzzle.post.probes import _group

    return _group(mesh, selection).node_ids


def _normal(mesh, selection: str) -> np.ndarray:
    """A face selection's mean outward normal, from its triangles' areas."""
    from puzzle.post.probes import _group

    group = _group(mesh, selection)
    total = np.zeros(3)
    centroid = mesh.coords.mean(axis=0)
    for b in group.blocks(mesh):
        if b.dim != 2:
            continue
        xyz = mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
        total += n.sum(axis=0)
        mid = xyz.mean(axis=(0, 1))
    if not total.any():
        raise PuzzleError(f"morph: '{selection}' has no faces to take a normal from",
                          code="invalid")  # fmt: skip
    n = total / np.linalg.norm(total)
    return -n if np.dot(n, mid - centroid) < 0 else n  # outward: away from the part


# Each element's corner simplices (Gmsh's node order), so every shape is checked for
# inversion and crushing (ADR-50): a corner tet at each corner of a brick, wedge or
# pyramid base, a corner triangle at each corner of a quadrilateral. Their signs are
# compared with the unmorphed mesh's, so the order's handedness doesn't matter.
CORNERS = {
    "hex": [(0, 1, 3, 4), (1, 2, 0, 5), (2, 3, 1, 6), (3, 0, 2, 7),
            (4, 7, 5, 0), (5, 4, 6, 1), (6, 5, 7, 2), (7, 6, 4, 3)],
    "wedge": [(0, 1, 2, 3), (1, 2, 0, 4), (2, 0, 1, 5), (3, 5, 4, 0), (4, 3, 5, 1),
              (5, 4, 3, 2)],
    "pyramid": [(0, 1, 3, 4), (1, 2, 0, 4), (2, 3, 1, 4), (3, 0, 2, 4)],
    "tri": [(0, 1, 2)],
    "quad": [(0, 1, 3), (1, 2, 0), (2, 3, 1), (3, 0, 2)],
}  # fmt: skip


def _family(element: str) -> str | None:
    for name in ("tet", "hex", "wedge", "pyramid", "tri", "quad"):
        if element.startswith(name):
            return name
    return None


def _shape(mesh, coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each tet's signed volume and shape quality (12 (3V)^(2/3) / Σl², 1 regular), then
    the same of every other solid's corner tets; faces' corner triangles (shells, 2D
    regions) as their area signed against the unmorphed normal and 4√3 A / Σl². The top
    dimension's elements only: a solid's faces move with it. A shape with no check is
    refused, never morphed unchecked."""
    vols, quals = [], []
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    top = max((b.dim for b in mesh.blocks if b.dim >= 2), default=0)
    for b in mesh.blocks:
        if b.dim != top:
            continue
        family = _family(b.element)
        if family is None:
            raise PuzzleError(f"morph: {b.element} elements can't be checked for inversion: "
                              "mesh with tetrahedra", code="unsupported")  # fmt: skip
        nodes = np.vectorize(index.get)(b.connectivity)
        for corner in CORNERS.get(family, [(0, 1, 2, 3)]):
            x = coords[nodes[:, list(corner)]]
            if len(corner) == 4:
                a, bb, c, d = x[:, 0], x[:, 1], x[:, 2], x[:, 3]
                v = np.einsum("ij,ij->i", bb - a, np.cross(c - a, d - a)) / 6
                pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
                sq = sum(np.sum((x[:, i] - x[:, j]) ** 2, axis=1) for i, j in pairs)
                vols.append(v)
                quals.append(12 * (3 * np.abs(v)) ** (2 / 3) / sq)
            else:
                x0 = mesh.coords[nodes[:, list(corner)]]
                ref = np.cross(x0[:, 1] - x0[:, 0], x0[:, 2] - x0[:, 0])
                ref /= np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-300)
                area = np.einsum("ij,ij->i", np.cross(x[:, 1] - x[:, 0], x[:, 2] - x[:, 0]),
                                 ref) / 2  # fmt: skip
                sq = sum(np.sum((x[:, i] - x[:, j]) ** 2, axis=1) for i, j in
                         [(0, 1), (0, 2), (1, 2)])  # fmt: skip
                vols.append(area)
                quals.append(4 * np.sqrt(3) * np.abs(area) / sq)
    if not vols:
        return np.array([]), np.array([])
    return np.concatenate(vols), np.concatenate(quals)


def morphed(project, study, mesh):
    """The mesh with the study's morph applied (node positions only), or the mesh as it
    is when the study has none. Refused when it inverts or crushes an element."""
    from scipy.interpolate import RBFInterpolator

    from puzzle.model.schema import Component
    from puzzle.operators import parameter_values

    morph = getattr(study, "morph", None)
    if morph is None or not morph.handles:
        return mesh
    params = parameter_values(project)
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    moves: dict[int, np.ndarray] = {}
    for h in morph.handles:
        d = _distance(h.distance, params, h.selection)
        direction = np.asarray(h.direction, float) if h.direction is not None \
            else _normal(mesh, h.selection)  # fmt: skip
        direction = direction / np.linalg.norm(direction)
        for n in _nodes(mesh, h.selection):
            moves[int(n)] = moves.get(int(n), 0) + d * direction
    held = list(morph.fixed)
    if not held:  # the supports, by default
        comp = project.objects.get(study.component)
        if isinstance(comp, Component):
            held = [f.selection for ph in comp.physics for f in ph.features
                    if f.type in ("fixed", "displacement")]  # fmt: skip
    if not held:
        raise PuzzleError("morph: hold something still (its fixed selections, or a support)",
                          code="inconsistent")  # fmt: skip
    fixed = {int(n) for s in held for n in _nodes(mesh, s)} - set(moves)
    ids = list(moves) + sorted(fixed)
    disp = np.array([moves[i] for i in moves] + [np.zeros(3)] * len(fixed), float)
    points = mesh.coords[[index[i] for i in ids]]
    try:
        rbf = RBFInterpolator(points, disp, kernel="thin_plate_spline", degree=1,
                              neighbors=None if len(points) <= DENSE else NEIGHBOURS)  # fmt: skip
        new = mesh.coords + rbf(mesh.coords)
    except np.linalg.LinAlgError:  # the linear part needs control points off one plane
        raise PuzzleError("morph: the handles and held nodes all lie in one plane (a 2D "
                          "model, or a flat sheet), which the morph's interpolation can't "
                          "take", code="unsupported") from None  # fmt: skip
    rows = [index[i] for i in ids]
    new[rows] = mesh.coords[rows] + disp  # the control points exactly where they were sent
    before, was = _shape(mesh, mesh.coords)
    vols, quals = _shape(mesh, new)
    if len(vols):
        flipped = int(np.sum(np.sign(vols) != np.sign(before)))
        if flipped:
            raise PuzzleError(f"morph: it inverts elements ({flipped} tets or element "
                              "corners turned inside out); move the handles less, or hold "
                              "fewer nodes near them", code="invalid")  # fmt: skip
        # Crushed by the morph: below the floor and under half what it was. An element the
        # mesher made poor (Linux's Gmsh left a sliver of 0.001 in a beam) is the mesh's.
        crushed = (quals < morph.quality_floor) & (quals < 0.5 * was)
        if crushed.any():
            worst = int(np.argmin(np.where(crushed, quals, np.inf)))
            raise PuzzleError(f"morph: an element's quality falls to {quals[worst]:.3f} (it was "
                              f"{was[worst]:.3f}), below {morph.quality_floor}",
                              code="invalid")  # fmt: skip
    return replace(mesh, coords=new)
