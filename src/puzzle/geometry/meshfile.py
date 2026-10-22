"""Mesh-only geometry (G2): STL, OBJ and 3MF, as triangles.

These files have no exact surfaces, so OCCT's preparation does not apply. Gmsh groups
the triangles into surface patches by feature angle (`classifySurfaces`), builds a
volume from them and meshes it; the patches are the faces selections name ("face-N",
in Gmsh's order, stable for a given file and angle).

Units: 3MF states its unit; STL and OBJ do not, and are taken as millimetres (the
usual unit for them) unless the geometry's `source_unit` says otherwise.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

SUFFIXES = {".stl", ".obj", ".3mf"}
UNITS = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "um": 1e-6, "in": 0.0254, "ft": 0.3048}
TMF_UNITS = {
    "micron": 1e-6,
    "millimeter": 1e-3,
    "centimeter": 1e-2,
    "inch": 0.0254,
    "foot": 0.3048,
    "meter": 1.0,
}


class MeshFileError(ValueError):
    pass


def is_mesh_file(path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


def triangles(path: Path, unit: str | None = None) -> np.ndarray:
    """The file's triangles in metres, as an (n, 3, 3) array."""
    path = Path(path)
    if not path.exists():
        raise MeshFileError(f"no file {path}")
    suffix = path.suffix.lower()
    if suffix == ".3mf":
        tris, file_scale = _read_3mf(path)
        scale = UNITS[unit] if unit else file_scale
    else:
        tris = _read_stl(path) if suffix == ".stl" else _read_obj(path)
        if unit is not None and unit not in UNITS:
            raise MeshFileError(f"unit must be one of {sorted(UNITS)}, not '{unit}'")
        scale = UNITS[unit or "mm"]
    if len(tris) == 0:
        raise MeshFileError(f"no triangles in {path.name}")
    return tris * scale


def write_stl(tris: np.ndarray, path: Path) -> Path:
    """A binary STL of the triangles (what Gmsh merges)."""
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    record = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    record["n"], record["v"] = normals, tris
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"Puzzle facets".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        f.write(record.tobytes())
    return path


def _read_stl(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) >= 84:
        (n,) = struct.unpack("<I", data[80:84])
        if 84 + 50 * n == len(data):  # binary
            record = np.frombuffer(
                data[84:], dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]
            )
            return record["v"].astype(float)
    text = data.decode("latin-1")
    points = [
        [float(x) for x in line.split()[1:4]]
        for line in text.splitlines()
        if line.strip().startswith("vertex")
    ]
    return np.asarray(points, float).reshape(-1, 3, 3)


def _read_obj(path: Path) -> np.ndarray:
    vertices, faces = [], []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            vertices.append([float(x) for x in parts[1:4]])
        elif parts[0] == "f":
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(vertices) + i for i in idx]
            faces += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]  # a fan
    v = np.asarray(vertices, float)
    return v[np.asarray(faces, int)] if faces else np.zeros((0, 3, 3))


def _read_3mf(path: Path) -> tuple[np.ndarray, float]:
    """The build's triangles (objects placed by their item transforms) and the unit."""
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".model")), None)
        if name is None:
            raise MeshFileError(f"{path.name} has no 3D model part")
        root = ET.fromstring(z.read(name))
    ns = {"m": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {"m": ""}
    q = (lambda tag: f"m:{tag}") if ns["m"] else (lambda tag: tag)
    scale = TMF_UNITS.get(root.get("unit", "millimeter"), 1e-3)
    meshes = {}
    for obj in root.iter(f"{{{ns['m']}}}object" if ns["m"] else "object"):
        mesh = obj.find(q("mesh"), ns)
        if mesh is None:
            continue
        v = np.array(
            [[float(e.get(k)) for k in "xyz"] for e in mesh.find(q("vertices"), ns)], float
        )
        t = np.array(
            [[int(e.get(k)) for k in ("v1", "v2", "v3")] for e in mesh.find(q("triangles"), ns)],
            int,
        )
        meshes[obj.get("id")] = v[t]
    build = root.find(q("build"), ns)
    items = list(build) if build is not None else []
    placed = []
    for item in items or [None]:
        oid = item.get("objectid") if item is not None else next(iter(meshes), None)
        if oid not in meshes:
            continue
        tris = meshes[oid]
        transform = item.get("transform") if item is not None else None
        if transform:
            m = np.array([float(x) for x in transform.split()], float).reshape(4, 3)
            tris = tris @ m[:3] + m[3]
        placed.append(tris)
    if not placed:
        raise MeshFileError(f"{path.name} has no meshes in its build")
    return np.concatenate(placed), scale
