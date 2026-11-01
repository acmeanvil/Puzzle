"""Field-driven model data for the adapters (ADR 0044 stage 3): an initial stress at
the integration points, and values per element (a graded material, a shell's
thickness) binned into a few levels.

- `element_values`: per element, the mean of its nodes' values. For a field linear in
  space that is the value at the element's centroid (a tet10's six edge nodes average
  to its corners' mean).
- `binned`: elements grouped into levels so each element's value is within
  `tolerance` of its level (relative): the harmonic mean of the level's extremes, so a
  uniform field keeps its value exactly. CalculiX and Code_Aster write one material (or
  one shell thickness) per level.
- `integration_points`: a solid element's shape functions at CalculiX's integration
  points (gauss.f), in Gmsh's node order, for `*INITIAL CONDITIONS, TYPE=STRESS`.
"""

from __future__ import annotations

import numpy as np

from puzzle.adapters.common import InputError

TOLERANCE = 0.005  # a graded value's largest relative error from binning: 0.5 %


def given(inputs) -> dict:
    """The "feature" inputs by key (fields.feature_key, material_key, shell_key)."""
    return {i.source: i.field for i in inputs if i.role == "feature"}


def nodal(field, nodes: np.ndarray) -> np.ndarray:
    """A nodal field's values (n, components) at node ids; refused where it has none."""
    order = np.argsort(field.ids)
    at = np.searchsorted(field.ids, nodes, sorter=order)
    at = np.clip(at, 0, len(order) - 1)
    rows = order[at]
    missing = field.ids[rows] != nodes
    if np.any(missing):
        raise InputError(
            f"{field.name} has no value at {int(np.sum(missing))} of the nodes it is used on"
        )
    return np.asarray(field.values, dtype=float)[rows]


def element_values(field, block) -> np.ndarray:
    """Per element of `block`, the mean of its nodes' values (n, components)."""
    conn = np.asarray(block.connectivity)
    values = nodal(field, conn.ravel()).reshape(*conn.shape, -1)
    return values.mean(axis=1)


def binned(
    values: np.ndarray, tolerance: float = TOLERANCE
) -> tuple[np.ndarray, np.ndarray, float]:
    """Positive values grouped into levels: (each value's level index, the levels, the
    largest relative error). Within a level the largest value is at most (1 + tol) /
    (1 - tol) times the smallest, and the level is their harmonic mean, so no value is
    more than `tolerance` from it."""
    v = np.asarray(values, dtype=float).ravel()
    if not len(v):
        return np.zeros(0, dtype=int), np.zeros(0), 0.0
    if np.any(v <= 0):
        raise InputError("graded values must be positive", code="invalid")
    ratio = (1 + tolerance) / (1 - tolerance)
    raw = np.floor(np.log(v / v.min()) / np.log(ratio) + 1e-12).astype(int)
    used, labels = np.unique(raw, return_inverse=True)
    levels = np.empty(len(used))
    for k in range(len(used)):
        members = v[labels == k]
        lo, hi = members.min(), members.max()
        levels[k] = 2 * lo * hi / (lo + hi)
    error = float(np.max(np.abs(v - levels[labels]) / v))
    return labels, levels, error


# CalculiX's integration points (gauss.f): gauss3d4 (C3D4), gauss3d5 (C3D10), gauss3d2
# (C3D8), in its natural coordinates.
_A, _B = 0.138196601125011, 0.585410196624968
_TET4 = np.array([[_A, _A, _A], [_B, _A, _A], [_A, _B, _A], [_A, _A, _B]])
_G = 0.577350269189626
_HEX8 = np.array([[x, y, z] for z in (-_G, _G) for y in (-_G, _G) for x in (-_G, _G)])
# Gmsh's tet10 edge nodes join corners (0-1, 1-2, 2-0, 0-3, 2-3, 1-3).
_TET10_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3))
_HEX_CORNERS = np.array(
    [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
     [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float
)  # fmt: skip


def integration_points(element: str) -> np.ndarray:
    """Shape functions (points, nodes) at the element's CalculiX integration points, its
    nodes in Gmsh's order (corners first: CalculiX's own order for these elements)."""
    if element == "tet4":
        return np.full((1, 4), 0.25)
    if element == "tet10":
        rows = []
        for xi, et, ze in _TET4:
            corners = np.array([1 - xi - et - ze, xi, et, ze])
            n = [c * (2 * c - 1) for c in corners]
            n += [4 * corners[a] * corners[b] for a, b in _TET10_EDGES]
            rows.append(n)
        return np.array(rows)
    if element == "hex8":
        return np.prod(1 + _HEX8[:, None, :] * _HEX_CORNERS[None, :, :], axis=2) / 8
    raise InputError(
        f"an initial stress is written on tet4, tet10 and hex8 solids, not {element}",
        code="unsupported",
    )


def initial_stresses(comp, study, solver: str) -> list:
    """The component's initial_stress features a study's structural steps start from;
    refused where they aren't written: in other than static steps, in some steps only,
    in local axes."""
    features = [f for ph in comp.physics if ph.type == "solid_mechanics"
                for f in ph.features if f.type == "initial_stress"]  # fmt: skip
    steps = [s for s in study.solve_steps() if s.solves("solid_mechanics")]
    if not features or not steps:
        return []
    other = sorted({s.type for s in steps if s.type != "stationary"})
    if other:
        raise InputError(
            f"{solver} starts static steps from an initial stress; not {', '.join(other)} "
            "steps yet",
            code="unsupported",
        )
    for f in features:
        if f.steps:
            raise InputError(
                f"an initial stress (on {f.selection}) is the state the model starts from: "
                "it applies in every step",
                code="invalid",
            )
        if f.axes is not None:
            raise InputError(
                f"an initial stress (on {f.selection}) is given in global axes, or turned "
                "by its field's placement",
                code="unsupported",
            )
    return features


def stress_field(feature, by_key: dict):
    """The initial stress's nodal field (run_study maps it), or None for uniform values
    in a deck written outside a run."""
    from puzzle.fields import feature_key

    field = by_key.get(feature_key(feature, "stress"))
    if field is None and feature.fields:
        raise InputError(f"initial stress on '{feature.selection}': its field was not mapped")
    return field


def uniform_stress(feature) -> np.ndarray:
    """A uniform initial stress (Pa; xx, yy, zz, xy, yz, zx) from its values."""
    from puzzle.fields import STRESS

    return np.array([feature.values[k].to_si() if k in feature.values else 0.0 for k in STRESS])


def solid_blocks(group, mesh) -> list:
    """A body selection's solid element blocks; refused when it has none (2D, shells)."""
    blocks = [b for b in group.blocks(mesh) if b.dim == 3]
    if not blocks:
        raise InputError(
            f"'{group.name}' has no solid elements: this is written on 3D bodies only",
            code="unsupported",
        )
    return blocks
