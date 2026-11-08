"""Anisotropic adaptation with MMG (ADR 0066 item 5, phase 4): elements stretched
along a metric from a field's Hessian, for shear and boundary layers in flow and thin
features.

The metric: the field's gradient recovered at the nodes (each element's gradient,
averaged by volume), then the gradient of that, symmetrised, as the Hessian H. Its
eigenvalues give the sizes along its directions for an interpolation error ε =
target × the field's range: λ = c |μ| / ε, c = 2/9 (linear tets), clamped between
1/hmax² and 1/hmin² and to an aspect ratio of at most `stretch`.

MMG (`mmg3d_O3`, LGPL, from the pixi `mmg` environment, found as Netgen is) runs as a
separate program on Medit files. The mesh written keeps Gmsh's entities as MMG's
references: tetrahedra by body, triangles by face (interfaces between bodies too),
edges by curve (as ridges), geometric points as required corners. The mesh read back is
rebuilt by those references into the same blocks and named groups, so selections,
loads and supports still find their faces; second order adds a node at each edge's
middle (MMG's elements are straight-sided).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.post import estimates as est

C_LINEAR = 2 / 9  # the interpolation error constant for linear tetrahedra in 3D
TRI6 = [(0, 1), (1, 2), (2, 0)]


def mmg_binary() -> Path | None:
    """mmg3d: PUZZLE_MMG, the pixi `mmg` environment beside this one, or PATH."""
    if os.environ.get("PUZZLE_MMG"):
        return Path(os.environ["PUZZLE_MMG"])
    from puzzle.jobs.runtime import environment

    env = environment("mmg")
    for where in [env / "bin", env / "Library" / "bin", env] if env else []:
        for name in ("mmg3d_O3", "mmg3d_O3.exe", "mmg3d", "mmg3d.exe"):
            if (where / name).exists():
                return where / name
    here = Path(__file__).resolve()
    for parent in here.parents:
        for sub in ("bin", "Library/bin", ""):
            cand = parent / ".pixi" / "envs" / "mmg" / sub / "mmg3d_O3"
            if cand.exists():
                return cand
    found = shutil.which("mmg3d_O3") or shutil.which("mmg3d")
    return Path(found) if found else None


# --- the metric ---------------------------------------------------------------------


def _tets(mesh: Mesh) -> tuple[np.ndarray, list[CellBlock]]:
    blocks = [b for b in mesh.blocks if b.dim == 3]
    bad = sorted({b.element for b in blocks} - {"tet4", "tet10"})
    if bad:
        raise PuzzleError(f"MMG adapts tetrahedra only; this mesh has {', '.join(bad)} (a "
                          "boundary layer stays its own control)", code="unsupported")  # fmt: skip
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    corners = np.concatenate([np.vectorize(pos.__getitem__)(b.connectivity[:, :4])
                              for b in blocks])  # fmt: skip
    return corners, blocks


def recovered_gradient(coords: np.ndarray, tets: np.ndarray, values: np.ndarray):
    """Each node's gradient of `values` (n, c) -> (n, c, 3), from the linear tets'
    constant gradients averaged by volume."""
    x = coords[tets]
    _, dn = est.shape("tet4", np.array([[0.25, 0.25, 0.25]]))
    g, det = est._gradients(x, dn)  # (e, 1, 4, 3), (e, 1)
    grad = np.einsum("enc,enj->ecj", values[tets], g[:, 0])  # (e, c, 3)
    vol = det[:, 0] / 6
    total = np.zeros((len(coords), *grad.shape[1:]))
    weight = np.zeros(len(coords))
    for k in range(4):
        np.add.at(total, tets[:, k], grad * vol[:, None, None])
        np.add.at(weight, tets[:, k], vol)
    return total / np.maximum(weight, 1e-300)[:, None, None]


def metric(coords, tets, values, target: float, hmin: float, hmax: float,
           stretch: float = 50.0) -> np.ndarray:  # fmt: skip
    """The metric per node (n, 3, 3) for a scalar field (n,): sizes from its recovered
    Hessian for an interpolation error of target × its range."""
    f = np.asarray(values, float).reshape(len(coords), 1)
    span = float(f.max() - f.min())
    if span <= 0:
        raise PuzzleError("the field is the same everywhere: nothing to adapt to",
                          code="invalid")  # fmt: skip
    eps = target * span
    g = recovered_gradient(coords, tets, f)[:, 0, :]  # (n, 3)
    h = recovered_gradient(coords, tets, g)  # (n, 3, 3)
    h = 0.5 * (h + np.swapaxes(h, 1, 2))
    mu, vec = np.linalg.eigh(h)
    lam = np.clip(C_LINEAR * np.abs(mu) / eps, 1 / hmax**2, 1 / hmin**2)
    lam = np.maximum(lam, lam.max(axis=1, keepdims=True) / stretch**2)
    return np.einsum("nij,nj,nkj->nik", vec, lam, vec)


# --- Medit files --------------------------------------------------------------------


def write_medit(mesh: Mesh, path: Path) -> np.ndarray:
    """The mesh's corner nodes, tets, triangles, edges and geometric points as a Medit
    file, Gmsh's entity tags as references. Returns the node indices written, in
    order (the metric is per written vertex)."""
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    idx = np.vectorize(pos.__getitem__)

    def corners(block, k):
        return idx(block.connectivity[:, :k]) if len(block.ids) else np.zeros((0, k), int)

    vols = [(b.entity[1], corners(b, 4)) for b in mesh.blocks if b.dim == 3]
    tris = [(b.entity[1], corners(b, 3)) for b in mesh.blocks
            if b.dim == 2 and b.element in ("tri3", "tri6")]  # fmt: skip
    lines = [(b.entity[1], corners(b, 2)) for b in mesh.blocks
             if b.dim == 1 and b.element in ("line2", "line3")]  # fmt: skip
    points = [(b.entity[1], corners(b, 1)) for b in mesh.blocks if b.dim == 0]
    used = np.unique(np.concatenate([c.reshape(-1) for _, c in vols]))
    local = np.full(len(mesh.node_ids), -1)
    local[used] = np.arange(len(used))
    refs = np.zeros(len(used), int)
    for tag, c in points:
        hit = local[c[:, 0]]
        refs[hit[hit >= 0]] = tag
    out = ["MeshVersionFormatted 2", "Dimension 3", "Vertices", str(len(used))]
    out += [f"{x:.15g} {y:.15g} {z:.15g} {r}" for (x, y, z), r in
            zip(mesh.coords[used], refs, strict=True)]  # fmt: skip

    def section(name, items, width):
        rows = [(tag, local[c]) for tag, c in items]
        rows = [(t, c[(c >= 0).all(axis=1)]) for t, c in rows]
        n = sum(len(c) for _, c in rows)
        if not n:
            return []
        text = [name, str(n)]
        for t, c in rows:
            text += [" ".join(str(v + 1) for v in row) + f" {t}" for row in c]
        return text, n

    for name, items, width in (("Tetrahedra", vols, 4), ("Triangles", tris, 3),
                               ("Edges", lines, 2)):  # fmt: skip
        got = section(name, items, width)
        if got:
            out += got[0]
            if name == "Edges":
                out += ["Ridges", str(got[1])] + [str(i + 1) for i in range(got[1])]
    corner_ids = [int(local[c[0]]) + 1 for _, c in points if local[c[0]] >= 0]
    if corner_ids:
        for name in ("Corners", "RequiredVertices"):
            out += [name, str(len(corner_ids))] + [str(i) for i in corner_ids]
    out.append("End")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return used


def write_metric(m: np.ndarray, path: Path) -> None:
    """A Medit tensor solution per vertex: m11 m12 m22 m13 m23 m33 (Medit's order)."""
    rows = [f"{a[0, 0]:.9g} {a[0, 1]:.9g} {a[1, 1]:.9g} {a[0, 2]:.9g} {a[1, 2]:.9g} "
            f"{a[2, 2]:.9g}" for a in m]  # fmt: skip
    path.write_text("\n".join(["MeshVersionFormatted 2", "Dimension 3", "SolAtVertices",
                               str(len(m)), "1 3", *rows, "End"]) + "\n",
                    encoding="utf-8")  # fmt: skip


def read_medit(path: Path) -> dict:
    """Vertices (with refs), and each element section's rows with their refs."""
    words = path.read_text(encoding="utf-8").split()
    out: dict = {}
    i = 0
    widths = {"Vertices": 4, "Tetrahedra": 5, "Triangles": 4, "Edges": 3}
    while i < len(words):
        w = words[i]
        if w in widths:
            n = int(words[i + 1])
            k = widths[w]
            arr = np.array(words[i + 2 : i + 2 + n * k], float).reshape(n, k)
            out[w] = arr
            i += 2 + n * k
        elif w in ("Corners", "RequiredVertices", "Ridges", "RequiredEdges"):
            n = int(words[i + 1])
            out[w] = np.array(words[i + 2 : i + 2 + n], int)
            i += 2 + n
        else:
            i += 1
    return out


# --- back to a Puzzle mesh ------------------------------------------------------------


def to_mesh(data: dict, old: Mesh, order: int) -> Mesh:
    """The MMG mesh as Puzzle's: blocks per reference (Gmsh's entity), the old mesh's
    groups rebuilt from them, second order by edge midpoints."""
    v = data["Vertices"]
    coords = [v[:, :3]]
    n0 = len(v)
    mids: dict[tuple[int, int], int] = {}

    def mid(a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        if key not in mids:
            mids[key] = n0 + len(mids)
        return mids[key]

    blocks: list[CellBlock] = []
    next_id = 1

    def add(element, dim, tag, conn):
        nonlocal next_id
        ids = np.arange(next_id, next_id + len(conn))
        next_id += len(conn)
        blocks.append(CellBlock(element, dim, (dim, int(tag)), ids, conn + 1))

    for name, dim, k, pairs, (lo, hi) in (
        ("Tetrahedra", 3, 4, est.EDGES10, ("tet4", "tet10")),
        ("Triangles", 2, 3, TRI6, ("tri3", "tri6")),
        ("Edges", 1, 2, [(0, 1)], ("line2", "line3")),
    ):
        rows = data.get(name)
        if rows is None or not len(rows):
            continue
        conn = rows[:, :k].astype(np.int64) - 1
        refs = rows[:, k].astype(int)
        for tag in np.unique(refs):
            c = conn[refs == tag]
            if order == 2:
                extra = np.array([[mid(int(r[a]), int(r[b])) for a, b in pairs] for r in c])
                add(hi, dim, tag, np.hstack([c, extra]))
            else:
                add(lo, dim, tag, c)
    refs = v[:, 3].astype(int)
    required = set(data.get("RequiredVertices", np.zeros(0, int)).tolist()) | set(
        data.get("Corners", np.zeros(0, int)).tolist())  # fmt: skip
    for i in np.flatnonzero(refs > 0):
        if int(i) + 1 in required:
            add("vertex", 0, refs[i], np.array([[i]]))
    if mids:
        pts = coords[0]
        keys = sorted(mids, key=mids.get)
        coords.append((pts[[a for a, _ in keys]] + pts[[b for _, b in keys]]) / 2)
    xyz = np.vstack(coords)
    node_ids = np.arange(1, len(xyz) + 1)
    mesh = Mesh(node_ids, xyz, blocks, planar=old.planar, axisymmetric=old.axisymmetric)
    by_entity: dict[tuple[int, int], list[np.ndarray]] = {}
    for b in blocks:
        by_entity.setdefault(b.entity, []).append(b.connectivity.reshape(-1))
    for name, g in old.groups.items():
        nodes = [n for e in g.entities for n in by_entity.get(tuple(e), [])]
        ids = np.unique(np.concatenate(nodes)) if nodes else np.zeros(0, np.int64)
        mesh.groups[name] = Group(name, g.dim, list(g.entities), ids)
    return mesh


def quality(mesh: Mesh) -> float:
    """The smallest mean-ratio quality of the tets' corners (1 = regular, 0 = flat)."""
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    worst = 1.0
    for b in mesh.blocks:
        if b.dim != 3:
            continue
        x = mesh.coords[np.vectorize(pos.__getitem__)(b.connectivity[:, :4])]
        vol = np.einsum("ij,ij->i", np.cross(x[:, 1] - x[:, 0], x[:, 2] - x[:, 0]),
                        x[:, 3] - x[:, 0]) / 6  # fmt: skip
        l2 = sum(((x[:, i] - x[:, j]) ** 2).sum(axis=1)
                 for i in range(4) for j in range(i + 1, 4))  # fmt: skip
        q = 12 * np.cbrt(9 * vol**2) / np.maximum(l2, 1e-300)
        worst = min(worst, float(q.min()))
    return worst


def adapt_mesh(mesh: Mesh, values: np.ndarray, workdir: Path, *, target: float,
               hmin: float, hmax: float, order: int, hausd: float | None = None,
               stretch: float = 50.0) -> Mesh:  # fmt: skip
    """One MMG pass: the metric of `values` (per mesh node), the mesh written, MMG run,
    the new mesh read back in Puzzle's form."""
    binary = mmg_binary()
    if binary is None:
        raise PuzzleError("MMG was not found: install it (pixi install -e mmg), or set "
                          "PUZZLE_MMG to mmg3d", code="unsupported")  # fmt: skip
    workdir.mkdir(parents=True, exist_ok=True)
    used = write_medit(mesh, workdir / "in.mesh")
    tets, _ = _tets(mesh)
    local = np.full(len(mesh.node_ids), -1)
    local[used] = np.arange(len(used))
    m = metric(mesh.coords[used], local[tets], np.asarray(values, float)[used], target,
               hmin, hmax, stretch)  # fmt: skip
    write_metric(m, workdir / "in.sol")
    size = float(np.linalg.norm(np.ptp(mesh.coords, axis=0)))
    cmd = [str(binary), str(workdir / "in.mesh"), "-sol", str(workdir / "in.sol"),
           "-out", str(workdir / "out.mesh"), "-hausd", f"{hausd or 1e-3 * size:.6g}",
           "-hgrad", "1.3", "-v", "1"]  # fmt: skip
    done = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600
    )
    (workdir / "mmg.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    if done.returncode != 0 or not (workdir / "out.mesh").exists():
        tail = (done.stdout + done.stderr).strip().splitlines()[-5:]
        raise PuzzleError("MMG failed: " + " ".join(tail), code="mesh_failed")
    new = to_mesh(read_medit(workdir / "out.mesh"), mesh, order)
    q = quality(new)
    if q <= 0:
        raise PuzzleError("MMG's mesh has an inverted element", code="mesh_failed")
    return new
