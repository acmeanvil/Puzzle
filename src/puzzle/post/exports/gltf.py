"""glTF 2.0 (ADR 0043 step 7): a result as a coloured 3D picture anyone can open (web
viewers, PowerPoint, AR), written as one binary `.glb` by hand, the spec being small.

Each part's outer surface is a mesh (a node named after the part), quadratic faces
split in four; each vertex is coloured by the chosen result (`quantity`) through the
legend's colour map (Settings > Results: map, inverted, bands), the range being the
result's over the model. With a displacement in the step the surface is moved by it
times `scale` (1 true size, 0 undeformed). The legend (result, unit, range, colour map)
is in the asset's `extras.legend`, and each vertex's value in the attribute `_VALUE`.

Units: glTF's are metres, which Puzzle's are; the axes are Puzzle's unchanged (glTF
calls +Y up). Colours are linear RGB, as glTF's COLOR_0 is: the colour map's sRGB
colours are converted, so a viewer shows them as Puzzle does. Values are SI.
"""

from __future__ import annotations

import json
import struct

import numpy as np

from puzzle.errors import PuzzleError

FLOAT, UINT = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
NAN_RGB = (0.5, 0.5, 0.5)


def write(job):
    from puzzle.post.legend import Legend

    item, values = node_values(job.results, job.step, job.quantity, job.mesh)
    lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
    legend = Legend.from_settings()
    deform = job.scale if "displacement" in job.step.fields else 0.0
    parts = surfaces(job.mesh, job.step, values, deform)
    unit = _unit(item.unit)
    extras = {
        "legend": {
            "result": item.label,
            "key": item.key,
            "unit": unit,
            "min": lo,
            "max": hi,
            "colormap": legend.cmap(),
            "bands": legend.bands,
        },
        "deformation": f"x {deform:g}" if deform else "undeformed",
        "units": "metres; values SI",
    }
    path = job.path.with_suffix(".glb")
    path.write_bytes(glb(parts, lambda v: colours(v, lo, hi, legend.cmap(), legend.bands), extras))
    return [path], {
        "result": f"{item.label} [{unit}]" if unit else item.label,
        "range": f"{lo:.6g} to {hi:.6g}",
        "colours": f"{legend.cmap()}" + (f", {legend.bands} bands" if legend.bands else ""),
        "deformation": extras["deformation"],
        "parts": ", ".join(parts),
    }


def _unit(kind: str | None) -> str:
    from puzzle.model.units import DIMENSIONS

    return DIMENSIONS[kind][1] if kind in DIMENSIONS else ""


def node_values(results, step, quantity: str | None, mesh):
    """The listed result (default: what a run shows first) at every mesh node, NaN
    where the field has no value."""
    from puzzle.post.derived import _items_of
    from puzzle.post.quantities import compute, default

    items = [i for i in _items_of(results) if i.field in step.fields]
    item = (
        default(items) if quantity is None else next((i for i in items if i.key == quantity), None)
    )
    if item is None:
        raise PuzzleError(f"no '{quantity}' in this step", code="not_found",
                          details={"quantities": [i.key for i in items]})  # fmt: skip
    field = step.fields[item.field]
    if field.location != "node":
        raise PuzzleError(f"{item.label} is per element; colours need nodes", code="unsupported")
    out = np.full(len(mesh.node_ids), np.nan)
    order = np.argsort(field.ids)
    pos = np.clip(np.searchsorted(field.ids, mesh.node_ids, sorter=order), 0, len(field.ids) - 1)
    found = field.ids[order[pos]] == mesh.node_ids
    out[found] = compute(item, field.values[order[pos[found]]], field.components)
    if not np.isfinite(out).any():
        raise PuzzleError(f"{item.label} has no values to colour by", code="not_found")
    return item, out


def surfaces(mesh, step, values: np.ndarray, deform: float) -> dict:
    """Each part's outer surface: name -> (points (n, 3), triangles (m, 3), values (n,))."""
    from puzzle.post.vtk import to_grid

    grid = to_grid(mesh, None)
    grid.point_data["_value"] = values
    if deform:
        u = step.fields["displacement"]
        moved = np.zeros((len(mesh.node_ids), 3))
        order = np.argsort(u.ids)
        pos = np.clip(np.searchsorted(u.ids, mesh.node_ids, sorter=order), 0, len(u.ids) - 1)
        found = u.ids[order[pos]] == mesh.node_ids
        moved[found] = u.values[order[pos[found]]]
        grid.points = grid.points + deform * moved
    element_ids = np.asarray(grid.cell_data["element_id"])
    out = {}
    for name, own in mesh.groups.items():
        if "/" in name or own.dim not in (2, 3):
            continue
        ids = np.concatenate([b.ids for b in own.blocks(mesh) if b.dim == own.dim] or [[]])
        cells = np.flatnonzero(np.isin(element_ids, ids))
        if not len(cells):
            continue
        surface = grid.extract_cells(cells).extract_surface(
            nonlinear_subdivision=1, algorithm="dataset_surface"
        )
        surface = surface.triangulate().clean()
        faces = np.asarray(surface.faces).reshape(-1, 4)[:, 1:]
        out[name] = (
            np.asarray(surface.points, float),
            faces.astype(np.int64),
            np.asarray(surface.point_data["_value"], float),
        )
    if not out:
        raise PuzzleError("the mesh has no solids or shells to export", code="not_found")
    return out


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def colours(values: np.ndarray, lo: float, hi: float, cmap: str, bands: int = 0) -> np.ndarray:
    """Linear RGB (n, 3) for each value: the colour map from lo to hi; with bands, the
    colour of each band (as the view's n_colors); NaN grey."""
    import matplotlib

    t = np.clip((values - lo) / (hi - lo), 0.0, 1.0) if hi > lo else np.zeros_like(values)
    if bands:
        t = np.minimum(np.floor(t * bands), bands - 1) / max(bands - 1, 1)
    rgb = matplotlib.colormaps[cmap](np.nan_to_num(t))[:, :3]
    rgb[~np.isfinite(values)] = NAN_RGB
    return srgb_to_linear(rgb)


def glb(parts: dict, colour, extras: dict) -> bytes:
    """The binary glTF: one mesh and node per part (POSITION with min/max, COLOR_0,
    _VALUE, uint32 indices), every view 4-byte aligned, chunks padded as the spec says."""
    blob = bytearray()
    views, accessors, meshes, nodes = [], [], [], []

    def view(data: bytes, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data),
                      "target": target})  # fmt: skip
        blob.extend(data)
        return len(views) - 1

    def accessor(array: np.ndarray, kind: str, component: int, target: int, bounds=False) -> int:
        entry = {"bufferView": view(array.tobytes(), target), "componentType": component,
                 "count": int(len(array)), "type": kind}  # fmt: skip
        if bounds:
            entry["min"] = [float(v) for v in array.min(axis=0)]
            entry["max"] = [float(v) for v in array.max(axis=0)]
        accessors.append(entry)
        return len(accessors) - 1

    for name, (points, triangles, values) in parts.items():
        position = np.ascontiguousarray(points, np.float32)
        rgb = np.ascontiguousarray(colour(values), np.float32)
        attributes = {
            "POSITION": accessor(position, "VEC3", FLOAT, ARRAY_BUFFER, bounds=True),
            "COLOR_0": accessor(rgb, "VEC3", FLOAT, ARRAY_BUFFER),
            "_VALUE": accessor(
                np.ascontiguousarray(values, np.float32), "SCALAR", FLOAT, ARRAY_BUFFER
            ),  # fmt: skip
        }
        indices = accessor(np.ascontiguousarray(triangles.ravel(), np.uint32), "SCALAR", UINT,
                           ELEMENT_ARRAY_BUFFER)  # fmt: skip
        meshes.append({"name": name, "primitives": [
            {"attributes": attributes, "indices": indices, "material": 0, "mode": 4}]})  # fmt: skip
        nodes.append({"name": name, "mesh": len(meshes) - 1})
    while len(blob) % 4:
        blob.append(0)
    document = {
        "asset": {"version": "2.0", "generator": "Puzzle (ADR 0043)", "extras": extras},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [
            {
                "name": "result colours",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1, 1, 1, 1],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
                "doubleSided": True,
            }
        ],  # fmt: skip
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)  # the JSON chunk is padded with spaces
    total = 12 + 8 + len(text) + 8 + len(blob)
    return b"".join([
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(text), b"JSON"), text,
        struct.pack("<I4s", len(blob), b"BIN\x00"), bytes(blob),
    ])  # fmt: skip
