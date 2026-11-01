"""Synthetic fluid meshes and cell results for the fluid export tests (no solver)."""

from __future__ import annotations

import numpy as np

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult

H, LENGTH, WIDTH = 0.01, 0.04, 0.004
U, MU, RHO, K = 0.001, 1.0e-3, 998.0, 0.6
G = 12 * MU * U / H**2  # -dp/dx of plane Poiseuille flow
T_WALL, T_SLOPE, T_CURVE = 350.0, -2000.0, 4.0e4  # T = T_w + a y + b y^2 near y = 0


def channel(nx: int = 8, ny: int = 10, nz: int = 2) -> Mesh:
    """Hexahedra on [0, L] x [0, H] x [0, W]: walls at y = 0 and H, inlet x = 0,
    outlet x = L, sides z = 0 and W."""
    xs, ys, zs = (np.linspace(0, d, n + 1) for d, n in ((LENGTH, nx), (H, ny), (WIDTH, nz)))
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    coords = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    node = (np.arange(len(coords)) + 1).reshape(nx + 1, ny + 1, nz + 1)
    i, j, k = (a.ravel() for a in np.meshgrid(range(nx), range(ny), range(nz), indexing="ij"))
    hexes = np.column_stack([
        node[i, j, k], node[i + 1, j, k], node[i + 1, j + 1, k], node[i, j + 1, k],
        node[i, j, k + 1], node[i + 1, j, k + 1], node[i + 1, j + 1, k + 1], node[i, j + 1, k + 1],
    ])  # fmt: skip
    blocks = [CellBlock("hex8", 3, (3, 1), np.arange(1, len(hexes) + 1), hexes)]
    next_id = len(hexes) + 1

    def quads(rows, tag):
        nonlocal next_id
        rows = np.asarray(rows)
        b = CellBlock("quad4", 2, (2, tag), np.arange(next_id, next_id + len(rows)), rows)
        next_id += len(rows)
        blocks.append(b)

    a, c = (x.ravel() for x in np.meshgrid(range(nx), range(nz), indexing="ij"))
    quads(np.column_stack([node[a, 0, c], node[a + 1, 0, c], node[a + 1, 0, c + 1],
                           node[a, 0, c + 1]]), 1)  # fmt: skip
    quads(np.column_stack([node[a, ny, c], node[a, ny, c + 1], node[a + 1, ny, c + 1],
                           node[a + 1, ny, c]]), 2)  # fmt: skip
    b_, c_ = (x.ravel() for x in np.meshgrid(range(ny), range(nz), indexing="ij"))
    quads(np.column_stack([node[0, b_, c_], node[0, b_ + 1, c_], node[0, b_ + 1, c_ + 1],
                           node[0, b_, c_ + 1]]), 3)  # fmt: skip
    quads(np.column_stack([node[nx, b_, c_], node[nx, b_, c_ + 1], node[nx, b_ + 1, c_ + 1],
                           node[nx, b_ + 1, c_]]), 4)  # fmt: skip
    mesh = Mesh(node.ravel().astype(np.int64), coords, blocks)

    def group(name, dim, tags):
        entities = [(dim, t) for t in tags]
        nodes = np.unique(np.concatenate(
            [b.connectivity.ravel() for b in blocks if b.entity in entities]))  # fmt: skip
        mesh.groups[name] = Group(name, dim, entities, nodes)

    group("Channel", 3, [1])
    group("Channel/Fluid", 3, [1])
    group("Channel/Walls", 2, [1, 2])
    group("Channel/Inlet", 2, [3])
    group("Channel/Outlet", 2, [4])
    return mesh


def centroids(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    b = mesh.blocks[0]
    return b.ids, mesh.node_coords(b.connectivity.ravel()).reshape(-1, 8, 3).mean(1)


def poiseuille(mesh: Mesh, steps: int = 1) -> Results:
    """Exact Poiseuille velocity and pressure at the cell centres; a temperature that
    is quadratic in the distance from either wall; a nodal field too."""
    ids, c = centroids(mesh)
    x, y = c[:, 0], c[:, 1]
    eta = np.minimum(y, H - y)
    out = Results(solver="code_saturne")
    for s in range(steps):
        scale = 1.0 + s
        u = np.zeros((len(ids), 3))
        u[:, 0] = scale * 6 * U * y * (H - y) / H**2
        p = scale * G * (LENGTH - x)
        t = T_WALL + T_SLOPE * eta + T_CURVE * eta**2
        stress = np.column_stack([x, y, c[:, 2], x + y, y + 2 * c[:, 2], 3 * x])
        nodes = mesh.coords
        step = StepResult(s + 1, "transient" if steps > 1 else "stationary", float(s + 1))
        step.fields = {
            "velocity": Field("velocity", "element", ["x", "y", "z"], ids, u),
            "pressure": Field("pressure", "element", [""], ids, p[:, None]),
            "temperature": Field("temperature", "element", [""], ids, t[:, None]),
            "stress": Field("stress", "element", ["xx", "yy", "zz", "xy", "yz", "zx"], ids,
                            stress * scale),  # fmt: skip
            "wall_distance": Field("wall_distance", "node", [""], mesh.node_ids,
                                   np.minimum(nodes[:, 1], H - nodes[:, 1])[:, None] * scale),
        }  # fmt: skip
        out.steps.append(step)
    return out


def mixed() -> Mesh:
    """One of each volume element (tet, pyramid, wedge, hex), unit-sized, apart from
    each other, with known volumes 1/6, 1/3, 1/2 and 1, and a triangle face group."""
    unit = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                     [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)  # fmt: skip
    coords, blocks, nid, eid = [], [], 1, 1
    shapes = [
        ("tet4", [0, 1, 3, 4]),
        ("pyramid5", [0, 1, 2, 3, 4]),
        ("wedge6", [0, 1, 3, 4, 5, 7]),
        ("hex8", list(range(8))),
    ]
    for k, (element, corners) in enumerate(shapes):
        pts = unit[corners] + [2.0 * k, 0, 0]
        coords.append(pts)
        conn = np.arange(nid, nid + len(pts))[None, :]
        blocks.append(CellBlock(element, 3, (3, k + 1), np.array([eid]), conn))
        nid, eid = nid + len(pts), eid + 1
    tri = np.array([[1, 2, 3]])  # the tet's bottom face
    blocks.append(CellBlock("tri3", 2, (2, 1), np.array([eid]), tri))
    mesh = Mesh(np.arange(1, nid), np.vstack(coords), blocks)
    mesh.groups["Shapes"] = Group("Shapes", 3, [(3, t) for t in range(1, 5)], mesh.node_ids)
    mesh.groups["Shapes/Floor"] = Group("Shapes/Floor", 2, [(2, 1)], np.array([1, 2, 3]))
    return mesh


VOLUMES = [1 / 6, 1 / 3, 1 / 2, 1.0]
