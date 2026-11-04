"""Move a result field from one study's mesh to another's (ADR 0009 transfers).

A transfer reads a nodal field (e.g. temperature) from the source study's results
and maps it onto the target study's mesh nodes:

- identical meshes (same node ids and coordinates): copied node by node;
- otherwise `mapping="interpolate"`: linear interpolation inside the source mesh's
  tetrahedra (their corner nodes), the nearest source node for target nodes outside
  it; or `mapping="nearest"`: the nearest source node.

The mapped field reaches the target adapter as an `Input` (role "load",
"initial_condition" or "prestress").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field, Results


@dataclass(frozen=True)
class Input:
    """A field handed to a study by a transfer, on the target mesh's nodes."""

    field: Field
    role: str  # "load", "initial_condition", "prestress"
    selection: str | None = None  # where it applies; None: everywhere
    source: str = ""  # the transfer's name, for messages
    study: object = None  # the source study's id, for adapters that replay it (prestress)
    # ADR 0044, a field varying in time: the solve step (from 1) it applies in, None every
    # step; and for a transient step its times (s), one value column each. A field
    # varying with frequency in a harmonic step: its frequencies (Hz) in place of times.
    step: int | None = None
    times: tuple[float, ...] | None = None
    frequencies: tuple[float, ...] | None = None


def pick(results: Results, field: str, step: int = -1) -> Field:
    """The field at a solve-step result: `step` counts result steps, -1 the last."""
    steps = [s for s in results.steps if field in s.fields]
    if not steps:
        raise PuzzleError(
            f"the source results have no '{field}' field",
            code="not_found",
            details={"fields": sorted({n for s in results.steps for n in s.fields})},
        )
    try:
        return steps[step].fields[field]
    except IndexError:
        raise PuzzleError(
            f"the source has {len(steps)} '{field}' steps, not step {step}", code="not_found"
        ) from None


def map_field(field: Field, source: Mesh, target: Mesh, mapping: str = "interpolate") -> Field:
    """`field` (nodal, on `source`) on every node of `target`."""
    if field.location != "node":
        raise PuzzleError(
            f"only nodal fields transfer so far, not {field.location} {field.name}",
            code="unsupported",
        )
    values = field.at(source.node_ids)  # one row per source mesh node, in its order
    if _same(source, target):
        return Field(field.name, "node", field.components, target.node_ids.copy(), values)
    if mapping == "conservative":
        raise PuzzleError("conservative mapping is not implemented yet", code="unsupported")
    if mapping == "nearest":
        mapped = values[_nearest(source.coords, target.coords)]
    else:
        mapped = _interpolate(source, values, target.coords)
    return Field(field.name, "node", field.components, target.node_ids.copy(), mapped)


def _same(a: Mesh, b: Mesh) -> bool:
    return (
        len(a.node_ids) == len(b.node_ids)
        and np.array_equal(a.node_ids, b.node_ids)
        and np.allclose(a.coords, b.coords, rtol=0, atol=1e-12)
    )


def _cell_size(points: np.ndarray, per_cell: float = 8.0) -> float:
    span = np.ptp(points, axis=0).max() or 1.0
    return span * (per_cell / max(len(points), 1)) ** (1 / 3)


def _nearest(points: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Index of the nearest point for each query (grid buckets, growing rings)."""
    h = _cell_size(points)
    origin = points.min(axis=0)
    keys = np.floor((points - origin) / h).astype(np.int64)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for i, k in enumerate(map(tuple, keys)):
        buckets.setdefault(k, []).append(i)
    out = np.empty(len(queries), dtype=np.int64)
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, k) in enumerate(zip(queries, qkeys, strict=True)):
        ring = 1
        while True:
            near = [
                i
                for dx in range(-ring, ring + 1)
                for dy in range(-ring, ring + 1)
                for dz in range(-ring, ring + 1)
                for i in buckets.get((k[0] + dx, k[1] + dy, k[2] + dz), ())
            ]
            if near:
                cand = np.asarray(near)
                d = np.linalg.norm(points[cand] - q, axis=1)
                # A point in the next ring could be nearer than one found at the edge.
                if d.min() <= ring * h:
                    out[qi] = cand[int(np.argmin(d))]
                    break
            ring += 1
    return out


def _interpolate(source: Mesh, values: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Linear interpolation in the source tetrahedra's corners; nearest node outside."""
    tets = [b for b in source.blocks if b.dim == 3 and b.element.startswith("tet")]
    if not tets:
        return values[_nearest(source.coords, queries)]
    order = np.argsort(source.node_ids)

    def rows(ids: np.ndarray) -> np.ndarray:
        return order[np.searchsorted(source.node_ids, ids, sorter=order)]

    corners = np.concatenate([rows(b.connectivity[:, :4]) for b in tets])  # (n, 4) rows
    xyz = source.coords[corners]  # (n, 4, 3)
    lo, hi = xyz.min(axis=1), xyz.max(axis=1)
    h = _cell_size(source.coords, per_cell=2.0)
    origin = source.coords.min(axis=0)
    # Bucket every tetrahedron in each grid cell its bounding box touches.
    buckets: dict[tuple[int, int, int], list[int]] = {}
    klo = np.floor((lo - origin) / h).astype(np.int64)
    khi = np.floor((hi - origin) / h).astype(np.int64)
    for t in range(len(corners)):
        for i in range(klo[t, 0], khi[t, 0] + 1):
            for j in range(klo[t, 1], khi[t, 1] + 1):
                for k in range(klo[t, 2], khi[t, 2] + 1):
                    buckets.setdefault((i, j, k), []).append(t)
    # Barycentric coordinates via the inverse of each tetrahedron's edge matrix.
    edges = np.stack([xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 0]], axis=2)
    inverse = np.linalg.inv(edges)
    out = np.empty((len(queries), values.shape[1]))
    missing = []
    tol = 1e-9
    qkeys = np.floor((queries - origin) / h).astype(np.int64)
    for qi, (q, key) in enumerate(zip(queries, qkeys, strict=True)):
        cand = buckets.get(tuple(key))
        found = False
        if cand:
            cand = np.asarray(cand)
            local = np.einsum("nij,nj->ni", inverse[cand], q - xyz[cand, 0])
            weights = np.column_stack([1 - local.sum(axis=1), local])
            inside = np.flatnonzero((weights >= -tol).all(axis=1))
            if len(inside):
                t, w = cand[inside[0]], weights[inside[0]]
                out[qi] = w @ values[corners[t]]
                found = True
        if not found:
            missing.append(qi)
    if missing:
        idx = np.asarray(missing)
        out[idx] = values[_nearest(source.coords, queries[idx])]
    return out


def losses_to_nodes(field: Field, mesh: Mesh) -> Field:
    """An element loss (or force) density as nodal values: each node the mean of the
    elements around it carrying any (a value not zero), so a conductor's surface keeps
    its value instead of being averaged with the air beside it (ADR 0047)."""
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    width = field.values.shape[1]  # a vector (a force density) goes component by component
    by_element = dict(zip(field.ids.tolist(), field.values, strict=True))
    total, count = np.zeros((len(mesh.node_ids), width)), np.zeros(len(mesh.node_ids))
    for b in mesh.blocks:
        if b.dim != 3:
            continue
        values = np.array([by_element.get(int(e), np.zeros(width)) for e in b.ids])
        losing = np.abs(values).sum(axis=1) > 0
        rows = np.vectorize(index.__getitem__, otypes=[np.int64])(b.connectivity[losing])
        for corner in range(rows.shape[1]):
            np.add.at(total, rows[:, corner], values[losing])
            np.add.at(count, rows[:, corner], 1.0)
    values = total / np.where(count > 0, count, 1.0)[:, None]
    return Field(field.name, "node", list(field.components), mesh.node_ids.copy(), values)


def _tet_rule(n: int = 4):
    """Points (barycentric ξ, η, ζ) and weights on the unit tetrahedron (volume 1/6): a
    collapsed n³ Gauss–Legendre rule, exact far past a curved tet10's cubic Jacobian."""
    g, w = np.polynomial.legendre.leggauss(n)
    g, w = (g + 1) / 2, w / 2
    u, v, t = np.meshgrid(g, g, g, indexing="ij")
    wu, wv, wt = np.meshgrid(w, w, w, indexing="ij")
    xi, eta, zeta = u, v * (1 - u), t * (1 - u) * (1 - v)
    weight = wu * wv * wt * (1 - u) ** 2 * (1 - v)
    return np.column_stack([xi.ravel(), eta.ravel(), zeta.ravel()]), weight.ravel()


def _tet10_shapes(p):
    """Gmsh tet10 shape functions and their gradients at reference points `p` (m, 3)."""
    x, y, z = p.T
    l0 = 1 - x - y - z
    lam = [l0, x, y, z]
    dlam = np.array([[-1, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    n = [li * (2 * li - 1) for li in lam] + [4 * lam[i] * lam[j] for i, j in EDGES]
    grads = [np.outer(4 * li - 1, dlam[k]) for k, li in enumerate(lam)]
    grads += [4 * (np.outer(lam[j], dlam[i]) + np.outer(lam[i], dlam[j])) for i, j in EDGES]
    return np.array(n).T, np.stack(grads, axis=1)  # (m, 10), (m, 10, 3)


def _integral(mesh: Mesh, selection: str | None, values_at) -> float:
    """∫ q dV over a body (every volume element when None) of the nodal values'
    interpolant on each element's own geometry: a linear tetrahedron's corner mean
    times its volume; a quadratic one by quadrature over its curved shape (edge nodes
    on a curved face: the volume a solver integrates)."""
    from puzzle.adapters.eddy import tet_volumes

    blocks = mesh.groups[selection].blocks(mesh) if selection else mesh.blocks
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    total = 0.0
    for b in (b for b in blocks if b.dim == 3):
        values = values_at(b.connectivity)
        if values.shape[1] == 10:
            points, weights = _tet_rule()
            shape, grad = _tet10_shapes(points)
            xyz = mesh.node_coords(b.connectivity.ravel()).reshape(-1, 10, 3)
            jac = np.einsum("enc,qnd->eqcd", xyz, grad)  # dx_c/dξ_d per element, point
            det = np.abs(np.linalg.det(jac))
            total += float(np.einsum("en,qn,eq,q->", values, shape, det, weights))
            continue
        local = np.vectorize(index.__getitem__, otypes=[np.int64])(b.connectivity[:, :4])
        total += float((values.mean(axis=1) * tet_volumes(mesh.coords, local)).sum())
    return total


EDGES = ((0, 1), (1, 2), (0, 2), (0, 3), (2, 3), (1, 3))  # tet10 edge nodes 4..9, Gmsh order


def linear_in_elements(mesh: Mesh, field: Field) -> Field:
    """The field with each quadratic tetrahedron's edge nodes set to their corners' mean:
    linear inside every element, so it has one integral whatever the solver's quadrature
    (the 10 nodes' mean, the quadratic weights, the corners'), and a total kept by
    rescaling stays kept (ADR 0047)."""
    values = field.values.copy()
    row = {int(n): i for i, n in enumerate(field.ids)}
    for b in mesh.blocks:
        if b.dim != 3 or b.connectivity.shape[1] != 10:
            continue
        rows = np.vectorize(row.__getitem__, otypes=[np.int64])(b.connectivity)
        for k, (i, j) in enumerate(EDGES):
            values[rows[:, 4 + k]] = 0.5 * (values[rows[:, i]] + values[rows[:, j]])
    return Field(field.name, field.location, list(field.components), field.ids, values)


def heat_from_losses(transfer, field: Field, source: Mesh, target: Mesh):
    """A loss density (W/m³ per element) as a heat generation on the transfer's target
    body: nodal (`losses_to_nodes`), mapped onto the target mesh, then scaled so the
    target body takes the power lost in it at the source (the same body by name there;
    else every conductor's). Returns the Input and the heat generation feature it drives."""
    from puzzle.adapters.common import InputError
    from puzzle.fields import feature_key
    from puzzle.model.schema import Feature, FieldUse, Quantity

    body = transfer.target_selection
    if not body or body not in target.groups or target.groups[body].dim != 3:
        raise InputError(f"transfer '{transfer.name}': a loss density heats a body: set its "
                         "target selection to one", code="invalid_input")  # fmt: skip
    nodal = losses_to_nodes(field, source)
    mapped = linear_in_elements(target, map_field(nodal, source, target, transfer.mapping))
    by_element = dict(zip(field.ids.tolist(), field.values[:, 0], strict=True))
    blocks = source.groups[body].blocks(source) if body in source.groups else source.blocks
    from puzzle.adapters.eddy import tet_volumes

    index = {int(n): i for i, n in enumerate(source.node_ids)}
    lost = 0.0
    for b in (b for b in blocks if b.dim == 3):
        local = np.vectorize(index.__getitem__, otypes=[np.int64])(b.connectivity[:, :4])
        lost += float((np.array([by_element.get(int(e), 0.0) for e in b.ids])
                       * tet_volumes(source.coords, local)).sum())  # fmt: skip
    taken = _integral(target, body, lambda conn: mapped.at(conn.ravel()).reshape(conn.shape))
    if taken > 0:
        mapped = Field(mapped.name, "node", [""], mapped.ids, mapped.values * (lost / taken))
    feature = Feature(type="heat_generation", selection=body,
                      values={"q": Quantity(value=0.0, unit="W/m^3")},
                      fields={"q": FieldUse(field=transfer.id, column=field.name)})  # fmt: skip
    key = feature_key(feature, "q")
    return Input(mapped, "feature", body, key, transfer.source), feature


def _body_total(mesh: Mesh, selection: str | None, by_element: dict, width: int) -> np.ndarray:
    """Σ value × volume of a body's elements (every volume element when it is not in
    the mesh), from per-element values."""
    from puzzle.adapters.eddy import tet_volumes

    blocks = mesh.groups[selection].blocks(mesh) if selection in mesh.groups else mesh.blocks
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    total = np.zeros(width)
    for b in (b for b in blocks if b.dim == 3):
        local = np.vectorize(index.__getitem__, otypes=[np.int64])(b.connectivity[:, :4])
        values = np.array([by_element.get(int(e), np.zeros(width)) for e in b.ids])
        total += (values * tet_volumes(mesh.coords, local)[:, None]).sum(axis=0)
    return total


def forces_from_lorentz(transfer, field: Field, source: Mesh, target: Mesh):
    """A Lorentz force density (N/m³ per element) as a body force on the transfer's target
    body (ADR 0047 item 6): nodal, mapped, then each component scaled so the body takes
    the total force on it at the source. Returns the Inputs (fx, fy, fz) and the
    body_force feature they drive."""
    from puzzle.adapters.common import InputError
    from puzzle.fields import feature_key
    from puzzle.model.schema import Feature, FieldUse, Quantity

    body = transfer.target_selection
    if not body or body not in target.groups or target.groups[body].dim != 3:
        raise InputError(f"transfer '{transfer.name}': a force density acts on a body: set "
                         "its target selection to one", code="invalid_input")  # fmt: skip
    mapped = linear_in_elements(
        target, map_field(losses_to_nodes(field, source), source, target, transfer.mapping)
    )
    wanted = _body_total(source, body, dict(zip(field.ids.tolist(), field.values, strict=True)), 3)
    taken = np.array([_integral(target, body, lambda conn, c=c: mapped.at(conn.ravel())[:, c]
                                .reshape(conn.shape)) for c in range(3)])  # fmt: skip
    scale = np.ones(3)
    big = np.abs(wanted).max() or 1.0
    for c in range(3):  # a component that carries force keeps its total
        if abs(wanted[c]) > 1e-6 * big and abs(taken[c]) > 1e-9 * big:
            scale[c] = wanted[c] / taken[c]
    names = ("fx", "fy", "fz")
    feature = Feature(type="body_force", selection=body,
                      values={n: Quantity(value=0.0, unit="N/m^3") for n in names},
                      fields={n: FieldUse(field=transfer.id, column=n) for n in names})  # fmt: skip
    inputs = [
        Input(
            Field(n, "node", [""], mapped.ids, mapped.values[:, [c]] * scale[c]),
            "feature",
            body,
            feature_key(feature, n),
            transfer.source,
        )  # fmt: skip
        for c, n in enumerate(names)
    ]
    return inputs, feature
