"""A 2D model's mesh made one cell thick for the finite-volume solvers (ADR 0048 round 5).

OpenFOAM and Code_Saturne solve only in 3D, so a 2D CFD study is solved on its 2D mesh
extruded by one layer:

- **planar:** along z, from -t/2 to t/2 (t: the mean cell size, so cells stay near cubic),
  the two faces `FRONT` and `BACK` (OpenFOAM `empty`, Code_Saturne symmetry);
- **axisymmetric:** turned about Y by ±`WEDGE_DEG`/2 (OpenFOAM `wedge`, Code_Saturne
  symmetry, which on a wedge's sides is axisymmetry with no swirl). Nodes on the axis
  are shared by both sides, so cells touching it collapse: a triangle with one axis node
  becomes a pyramid, with two a tetrahedron, a quadrilateral with an axis edge a prism
  (OpenFOAM's own advice for wedges). A quadrilateral with one corner, or two opposite
  corners, on the axis is cut into two triangles first.

Each 3D cell keeps the id of the 2D element it came from (a cut quadrilateral's second
half gets a new id), so results come back to the 2D model by `to_2d`. Region groups
become volume groups and edge groups face groups under the same names, so features on
edges (inlets, walls, ...) need nothing. An edge group lying on the axis has no faces
(`on_axis`): nothing crosses the axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.mesh.mesh import CellBlock, Group, Mesh

FRONT, BACK = "_front", "_back"
WEDGE_DEG = 5.0
FILE = "layer-map.npz"


@dataclass
class Layered:
    mesh: Mesh  # the 3D mesh, one cell thick
    to_2d: dict[int, int]  # 3D cell id -> the 2D element it came from
    axisymmetric: bool
    on_axis: set[str] = field(default_factory=set)  # edge groups with no faces
    diameters: dict[str, float] = field(default_factory=dict)  # 4A/P of each edge group
    size: float = 0.0  # the 2D mesh's mean element size (m)

    def save(self, workdir: Path) -> None:
        ids = np.array(sorted(self.to_2d), dtype=np.int64)
        np.savez(workdir / FILE, ids=ids, to=np.array([self.to_2d[i] for i in ids]))


def first_order(mesh: Mesh, solver: str) -> None:
    from puzzle.adapters.common import InputError

    if any(b.element not in ("tri3", "quad4") for b in mesh.region_blocks()):
        raise InputError(f"{solver} needs a first-order mesh: set element_order = 1")


def extrude(mesh: Mesh, axisymmetric: bool | None = None) -> Layered:
    """The 2D mesh `mesh` (its region blocks) one cell thick; see the module's doc."""
    from puzzle.adapters.common import InputError

    axi = mesh.axisymmetric if axisymmetric is None else axisymmetric
    regions = mesh.region_blocks()
    if not regions:
        raise InputError("a 2D model's mesh has no regions to extrude")
    coords = mesh.coords.copy()
    mean = _mean_size(mesh, regions)
    size = float(np.linalg.norm(coords.max(axis=0) - coords.min(axis=0))) or 1.0
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    on_axis_node = np.abs(coords[:, 0]) < 1e-9 * size if axi else np.zeros(len(coords), bool)
    node_offset = int(mesh.node_ids.max())
    back_id = {
        int(n): (int(n) if on_axis_node[i] else int(n) + node_offset)
        for i, n in enumerate(mesh.node_ids)
    }

    # The two sides' coordinates.
    if axi:
        half = math.radians(WEDGE_DEG) / 2
        r = coords[:, 0]
        front = np.column_stack([r * math.cos(half), coords[:, 1], -r * math.sin(half)])
        back = np.column_stack([r * math.cos(half), coords[:, 1], r * math.sin(half)])
    else:
        t = mean
        front = np.column_stack([coords[:, 0], coords[:, 1], np.full(len(coords), -t / 2)])
        back = front.copy()
        back[:, 2] = t / 2
    ids_out = list(mesh.node_ids.astype(np.int64))
    xyz_out = [front]
    extra = [i for i, n in enumerate(mesh.node_ids) if back_id[int(n)] != int(n)]
    ids_out += [back_id[int(mesh.node_ids[i])] for i in extra]
    xyz_out.append(back[extra])
    node_ids = np.array(ids_out, dtype=np.int64)
    node_xyz = np.vstack(xyz_out)
    pos = {int(n): i for i, n in enumerate(node_ids)}

    def at(n: int) -> np.ndarray:
        return node_xyz[pos[n]]

    def axis(n: int) -> bool:
        return bool(on_axis_node[index[n]])

    entity_tags = [t for _d, t in {b.entity for b in mesh.blocks}]
    off = max(entity_tags, default=0) + 1
    next_elem = max((int(b.ids.max()) for b in mesh.blocks if len(b.ids)), default=0) + 1
    cells: dict[tuple, list] = {}  # (entity, element) -> [(id, nodes)]
    to_2d: dict[int, int] = {}

    def add(entity, element, eid, nodes):
        cells.setdefault((entity, element), []).append((eid, nodes))

    for b in regions:
        corners = b.connectivity[:, : 3 if b.element == "tri3" else 4]
        for eid, row in zip(b.ids, corners, strict=True):
            row = [int(n) for n in row]
            if _normal_z(at, row) < 0:  # turned so the layer goes along its normal
                row = row[::-1]
            pieces = _pieces(row, axis)
            for k, poly in enumerate(pieces):
                cid = int(eid) if k == 0 else next_elem
                if k:
                    next_elem += 1
                to_2d[cid] = int(eid)
                element, nodes = _cell(poly, axis, back_id)
                nodes = _positive(element, nodes, at)
                add((3, b.entity[1]), element, cid, nodes)
                for side, ent in ((FRONT, 2 * off), (BACK, 3 * off)):
                    face = poly if side == FRONT else [back_id[n] for n in poly]
                    add((2, ent + b.entity[1]), "tri3" if len(face) == 3 else "quad4",
                        None, face)  # fmt: skip

    line_entities = {b.entity for b in mesh.blocks if b.dim == 1}
    for b in mesh.blocks:
        if b.dim != 1:
            continue
        for row in b.connectivity[:, :2]:
            a, c = int(row[0]), int(row[1])
            ends = [n for n in (a, c) if not axis(n)]
            if not ends:
                continue  # on the axis: no face
            if len(ends) == 2:
                face = [a, c, back_id[c], back_id[a]]
            else:
                on, other = (a, c) if axis(a) else (c, a)
                face = [on, other, back_id[other]]
            add((2, off + b.entity[1]), "tri3" if len(face) == 3 else "quad4", None, face)

    blocks = []
    for (entity, element), rows in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        ids = []
        for eid, _nodes in rows:
            if eid is None:
                eid = next_elem
                next_elem += 1
            ids.append(eid)
        conn = np.array([nodes for _e, nodes in rows], dtype=np.int64)
        blocks.append(CellBlock(element, entity[0], entity, np.array(ids, np.int64), conn))

    groups: dict[str, Group] = {}
    on_axis: set[str] = set()
    for name, g in mesh.groups.items():
        if g.dim == 2:
            ents = [(3, t) for d, t in g.entities if d == 2]
        elif g.dim == 1:
            ents = [(2, off + t) for d, t in g.entities if d == 1 and (d, t) in line_entities]
        else:
            continue
        mine = [b for b in blocks if b.entity in set(ents)]
        if not mine:
            if g.dim == 1:
                on_axis.add(name)
            continue
        nodes = np.unique(np.concatenate([b.connectivity.ravel() for b in mine]))
        groups[name] = Group(name, g.dim + 1, sorted({b.entity for b in mine}), nodes)
    for side, ent in ((FRONT, 2 * off), (BACK, 3 * off)):
        mine = [b for b in blocks if b.dim == 2 and ent <= b.entity[1] < ent + off]
        nodes = np.unique(np.concatenate([b.connectivity.ravel() for b in mine]))
        groups[side] = Group(side, 2, sorted({b.entity for b in mine}), nodes)

    out = Mesh(node_ids, node_xyz, blocks, groups)
    diameters = {
        n: _diameter(g, mesh, axi) for n, g in mesh.groups.items() if g.dim == 1 and n in groups
    }
    return Layered(out, to_2d, axi, on_axis, diameters, mean)


def _mean_size(mesh: Mesh, regions) -> float:
    """The mean element size: the square root of the mean area."""
    areas = []
    for b in regions:
        c = mesh.node_coords(b.connectivity[:, : 3 if b.element == "tri3" else 4].ravel())
        c = c.reshape(len(b.ids), -1, 3)
        x, y = c[..., 0], c[..., 1]
        areas.append(0.5 * np.abs(np.sum(x * np.roll(y, -1, 1) - np.roll(x, -1, 1) * y, axis=1)))
    return float(np.sqrt(np.concatenate(areas).mean()))


def _normal_z(at, row) -> float:
    p = np.array([at(n) for n in row])
    x, y = p[:, 0], p[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _pieces(row: list[int], axis) -> list[list[int]]:
    """The 2D element as one polygon, or a quadrilateral cut in two where its corners on
    the axis would not make a standard cell."""
    on = [axis(n) for n in row]
    if sum(on) >= 3:  # flat on the axis: nothing to sweep
        from puzzle.adapters.common import InputError

        raise InputError("an element lies on the axis: refine the mesh there")
    if len(row) == 4:
        k = sum(on)
        adjacent = k == 2 and any(on[i] and on[(i + 1) % 4] for i in range(4))
        if k == 1 or (k == 2 and not adjacent):
            i = on.index(True)  # cut along the diagonal from an axis corner
            q = row[i:] + row[:i]
            return [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return [row]


def _cell(poly: list[int], axis, back_id) -> tuple[str, list[int]]:
    """The 3D cell between a polygon and its copy on the back side."""
    up = [back_id[n] for n in poly]
    on = [axis(n) for n in poly]
    if not any(on):
        return ("wedge6" if len(poly) == 3 else "hex8"), poly + up
    if len(poly) == 3:
        if sum(on) == 1:  # a pyramid: the off-axis edge swept, the axis node its apex
            i = on.index(True)
            a, b, c = poly[i:] + poly[:i]
            return "pyramid5", [b, c, back_id[c], back_id[b], a]
        i = on.index(False)  # a tetrahedron
        c = poly[i]
        a, b = (n for n in poly if n != c)
        return "tet4", [a, b, c, back_id[c]]
    i = next(i for i in range(4) if on[i] and on[(i + 1) % 4])
    q = poly[i:] + poly[:i]  # q0, q1 on the axis: a prism with triangular ends
    return "wedge6", [q[0], q[3], back_id[q[3]], q[1], q[2], back_id[q[2]]]


def _newell(p: np.ndarray) -> np.ndarray:
    return np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)


def _volume(element: str, p: np.ndarray) -> float:
    """The sign of a cell's volume in Gmsh's order: its first face's normal (right hand)
    points toward the rest of the cell, as in Gmsh's reference elements."""
    if element == "tet4":
        return float(np.dot(np.cross(p[1] - p[0], p[2] - p[0]), p[3] - p[0]))
    n = {"pyramid5": 4, "wedge6": 3, "hex8": 4}[element]
    base, rest = p[:n], p[n:]
    return float(np.dot(_newell(base), rest.mean(axis=0) - base.mean(axis=0)))


def _positive(element: str, nodes: list[int], at) -> list[int]:
    """The cell's nodes in the order that gives it a positive volume."""
    p = np.array([at(n) for n in nodes])
    if _volume(element, p) > 0:
        return nodes
    if element == "tet4":
        return [nodes[1], nodes[0], *nodes[2:]]
    if element == "pyramid5":
        return [nodes[3], nodes[2], nodes[1], nodes[0], nodes[4]]
    n = 3 if element == "wedge6" else 4
    return nodes[n:] + nodes[:n]


def _diameter(group: Group, mesh: Mesh, axi: bool) -> float:
    """4A/P of an edge group as the face it sweeps: a planar edge of length L gives 2L
    (two plates); an axisymmetric one 4 ∫r dl / (r1 + r2), 2R for a disc of radius R and
    2 (b - a) for an annulus."""
    keys = set(group.entities)
    length, moment, ends = 0.0, 0.0, {}
    for b in mesh.blocks:
        if b.entity not in keys or b.dim != 1:
            continue
        for row in b.connectivity[:, :2]:
            p, q = mesh.node_coords(row)
            dl = float(np.linalg.norm(q - p))
            length += dl
            moment += dl * (p[0] + q[0]) / 2
            for n in row:
                ends[int(n)] = ends.get(int(n), 0) + 1
    if not axi:
        return 2 * length
    tips = [n for n, k in ends.items() if k == 1]
    radii = sum(float(mesh.node_coords(np.array([n]))[0, 0]) for n in tips) or 1e-30
    return 4 * moment / radii


def match(centres: np.ndarray, reference: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Each solver cell's id: the nearest reference centre, one to one. A layered mesh's
    node-average centres differ from the solvers' volume centres by a fraction of a cell
    (irregular quadrilaterals, the axis's pyramids), more than `match_points` allows."""
    from scipy.spatial import cKDTree

    from puzzle.adapters.common import InputError

    _d, nearest = cKDTree(reference).query(centres)
    if len(np.unique(nearest)) != len(nearest):
        raise InputError("the solver's cells do not match the layered mesh's one to one")
    return ids[nearest]


def to_2d(workdir: Path, results):
    """Results on the layered mesh's cells back on the 2D elements (a cut element's two
    halves averaged); results unchanged when the case was not layered."""
    path = workdir / FILE
    if not path.exists():
        return results
    data = np.load(path)
    table = dict(zip(data["ids"].tolist(), data["to"].tolist(), strict=True))
    for step in results.steps:
        for name, f in list(step.fields.items()):
            if f.location != "element":
                continue
            mapped = np.array([table[int(i)] for i in f.ids], dtype=np.int64)
            out_ids, inverse = np.unique(mapped, return_inverse=True)
            values = np.asarray(f.values, dtype=float)
            values = values.reshape(len(mapped), -1)
            summed = np.zeros((len(out_ids), values.shape[1]))
            np.add.at(summed, inverse, values)
            counts = np.bincount(inverse, minlength=len(out_ids))[:, None]
            out = summed / counts
            if np.asarray(f.values).ndim == 1:
                out = out[:, 0]
            step.fields[name] = type(f)(f.name, f.location, f.components, out_ids, out)
    return results


def off_axis(features: list, layered: Layered) -> list:
    """Features less those on an axisymmetric model's axis, which has no faces: a symmetry
    or wall there says nothing more; anything else is refused."""
    from puzzle.adapters.common import InputError

    out = []
    for f in features:
        if f.selection in layered.on_axis:
            if f.type not in ("symmetry", "wall"):
                raise InputError(f"{f.type} on '{f.selection}' lies on the axis, where "
                                 "nothing flows in or out", code="unsupported")  # fmt: skip
            continue
        out.append(f)
    return out
