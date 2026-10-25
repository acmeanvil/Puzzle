"""Netgen, the second mesher (ADR 0019), run in its own environment.

Gmsh prepares the geometry as for its own mesh (parts placed, fragmented, named groups),
writes it as BREP, and Netgen meshes it in a subprocess (`netgen_run.py`: Netgen's
OpenCASCADE bindings clash with OCP's in one process). Its first-order mesh comes back
by the number of the edge, face or solid each element lies on; those are matched to
Gmsh's entities by centre and measure, and the nodes and elements are added to the Gmsh
model on them, with parametric coordinates. From there the usual pipeline carries on:
Gmsh raises the order (curving midside nodes onto the geometry), checks quality, and
the named groups pick up the elements.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import gmsh
import numpy as np

SCRIPT = Path(__file__).with_name("netgen_run.py")


def netgen_python() -> Path | None:
    """A Python whose environment has Netgen: PUZZLE_NETGEN_PYTHON, else one on
    PUZZLE_SOLVER_PATH, else the pixi `netgen` environment beside this one."""
    explicit = os.environ.get("PUZZLE_NETGEN_PYTHON")
    if explicit:
        return Path(explicit)
    dirs = [Path(d) for d in os.environ.get("PUZZLE_SOLVER_PATH", "").split(os.pathsep) if d]
    envs = Path(sys.prefix).parent
    dirs += [envs / "netgen" / "bin", envs / "netgen"]
    for d in dirs:
        for name in ("python", "python3", "python.exe"):
            exe = d / name
            prefix = exe.parent if os.name == "nt" else exe.parent.parent
            pattern = (
                "Lib/site-packages/netgen"
                if os.name == "nt"
                else "lib/python3*/site-packages/netgen"
            )
            if exe.exists() and any(prefix.glob(pattern)):
                return exe
    return None


def _signatures(dim: int) -> dict[int, np.ndarray]:
    """Gmsh entity tag -> [x, y, z, measure] of the OCC model's entities of a dimension."""
    out = {}
    for _, tag in gmsh.model.getEntities(dim):
        out[tag] = np.array(
            [*gmsh.model.occ.getCenterOfMass(dim, tag), gmsh.model.occ.getMass(dim, tag)]
        )
    return out


def _match(theirs: list, ours: dict[int, np.ndarray], size: float, what: str) -> list[int]:
    """Netgen's entities (in its order) as Gmsh tags, by centre and measure."""
    tags = list(ours)
    sig = np.array([ours[t] for t in tags]) if tags else np.zeros((0, 4))
    out = []
    for i, s in enumerate(theirs):
        s = np.asarray(s)
        d = np.linalg.norm(sig[:, :3] - s[:3], axis=1) / size
        m = np.abs(sig[:, 3] - s[3]) / max(abs(s[3]), 1e-300)
        ok = np.flatnonzero((d < 1e-6) & (m < 1e-6))
        if len(ok) == 0:
            from puzzle.mesh.generate import MeshError

            raise MeshError(f"Netgen's {what} {i} matches no {what} of the geometry")
        out.append(tags[int(ok[np.argmin(d[ok])])])
    return out


def mesh_with_netgen(recipe, out_dir: Path, sizes: list[dict], layers: list[dict]) -> None:
    """Mesh the current Gmsh OCC model with Netgen and load the mesh into it."""
    from puzzle.mesh.generate import MeshError, _model_size

    python = netgen_python()
    if python is None:
        raise MeshError(
            "Netgen was not found: install it (pixi install -e netgen), or set "
            "PUZZLE_NETGEN_PYTHON to a Python that has it",
            code="unsupported",
        )
    work = out_dir / "netgen"
    work.mkdir(parents=True, exist_ok=True)
    gmsh.write(str(work / "geometry.brep"))
    size = _model_size()
    case = {
        "brep": "geometry.brep",
        "maxh": recipe.global_size.to_si() if recipe.global_size else size / 10,
        "options": dict(recipe.netgen_options),
        "sizes": sizes,
        "layers": layers,
    }
    (work / "case.json").write_text(json.dumps(case))
    from puzzle.jobs.runtime import NativeRuntime

    env = NativeRuntime().environment([str(python)])
    # Run a copy beside the case: from its own folder, puzzle's modules would shadow
    # Netgen's packages.
    script = work / SCRIPT.name
    script.write_text(SCRIPT.read_text())
    run = subprocess.run(
        [str(python), str(script), str(work / "case.json")],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
    )
    (work / "netgen.log").write_text(run.stdout + run.stderr)
    if run.returncode != 0:
        tail = (run.stderr or run.stdout).strip().splitlines()[-5:]
        raise MeshError("Netgen failed: " + " / ".join(tail))
    data = np.load(work / "mesh.npz")
    entities = json.loads((work / "entities.json").read_text())
    _load(data, entities, size)


def _load(data, entities: dict, size: float) -> None:
    """Add Netgen's nodes and elements to the Gmsh model: each node on the lowest
    entity it lies on (point, edge, face, else its solid), with parametric
    coordinates on edges and faces, so Gmsh can curve higher-order nodes."""
    edges = _match(entities["1"], _signatures(1), size, "edge")
    faces = _match(entities["2"], _signatures(2), size, "face")
    solids = _match(entities["3"], _signatures(3), size, "solid")
    points = data["points"]
    n = len(points)
    ids = np.arange(1, n + 1)
    owner = np.full(n + 1, -1, dtype=np.int64)  # node -> (dim * 10**9 + tag)
    # Geometric vertices first: the node at each point's position.
    for _, tag in gmsh.model.getEntities(0):
        at = np.array(gmsh.model.getValue(0, tag, []))
        hit = np.flatnonzero(np.linalg.norm(points - at, axis=1) < 1e-9 * size + 1e-12)
        for h in hit:
            owner[h + 1] = 0 * 10**9 + tag
    for nodes, numbers, dim, tags in (
        (data["segments"], data["seg_edge"], 1, edges),
        (data["tris"], data["tri_face"], 2, faces),
        (data["quads"], data["quad_face"], 2, faces),
    ):
        for row, k in zip(nodes, numbers, strict=True):
            if k < 0:
                continue  # made by Netgen itself (inside a boundary layer): on no entity
            for v in row:
                if owner[v] < 0:
                    owner[v] = dim * 10**9 + tags[k]
    volumes = [t for _, t in gmsh.model.getEntities(3)]
    tet_owner = np.array([solids[k] for k in data["tet_solid"]], dtype=np.int64)
    prism_owner = np.array(
        [solids[k] if k >= 0 else _containing(points[row - 1].mean(axis=0), volumes)
         for row, k in zip(data["prisms"], data["prism_solid"], strict=True)],
        dtype=np.int64,
    )  # fmt: skip
    for rows, where in ((data["tets"], tet_owner), (data["prisms"], prism_owner)):
        for row, tag in zip(rows, where, strict=True):
            for v in row:
                if owner[v] < 0:
                    owner[v] = 3 * 10**9 + tag
    for key in np.unique(owner[1:]):
        if key < 0:
            continue  # an unused point
        dim, tag = int(key // 10**9), int(key % 10**9)
        mine = ids[owner[1:] == key]
        xyz = points[mine - 1]
        param = (
            gmsh.model.getParametrization(dim, tag, xyz.ravel().tolist()) if dim in (1, 2) else []
        )
        gmsh.model.mesh.addNodes(dim, tag, mine.tolist(), xyz.ravel().tolist(), list(param))
    next_id = 1
    on_edge, on_face, on_quad = data["seg_edge"] >= 0, data["tri_face"] >= 0, data["quad_face"] >= 0
    for rows, owners, gmsh_type in (
        (data["segments"][on_edge], [edges[k] for k in data["seg_edge"][on_edge]], 1),
        (data["tris"][on_face], [faces[k] for k in data["tri_face"][on_face]], 2),
        (data["quads"][on_quad], [faces[k] for k in data["quad_face"][on_quad]], 3),
        (data["tets"], tet_owner, 4),
        (data["prisms"], prism_owner, 6),
    ):
        owners = np.asarray(owners, dtype=np.int64)
        if gmsh_type == 4:
            rows = _positive(rows, points)
        elif gmsh_type == 6:
            rows = _positive_prisms(rows, points)
        for tag in np.unique(owners):
            mine = rows[owners == tag]
            elem = list(range(next_id, next_id + len(mine)))
            next_id += len(mine)
            gmsh.model.mesh.addElementsByType(int(tag), gmsh_type, elem, mine.ravel().tolist())


def _positive(tets: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Tets turned the way Gmsh counts them (positive volume): Netgen's go the other way."""
    xyz = points[tets - 1]
    volume = np.einsum(
        "ij,ij->i", np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), xyz[:, 3] - xyz[:, 0]
    )
    out = tets.copy()
    flip = volume < 0
    out[flip, 1], out[flip, 2] = tets[flip, 2], tets[flip, 1]
    return out


def _positive_prisms(prisms: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Prisms with the top triangle on the side the bottom one faces (Gmsh's order)."""
    if not len(prisms):
        return prisms
    xyz = points[prisms - 1]
    normal = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    up = xyz[:, 3:].mean(axis=1) - xyz[:, :3].mean(axis=1)
    out = prisms.copy()
    flip = np.einsum("ij,ij->i", normal, up) < 0
    out[flip] = prisms[flip][:, [0, 2, 1, 3, 5, 4]]
    return out


def _containing(point, volumes: list[int]) -> int:
    for tag in volumes:
        if gmsh.model.isInside(3, tag, list(point)):
            return tag
    return volumes[0]
