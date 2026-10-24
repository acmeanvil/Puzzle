"""Supports and loads as symbols in the view (ADR 0035), from the model, not the solver
input: one glyph set per feature, drawn on the feature's faces.

- fixed support: cones pressing on the face, blue;
- prescribed displacement: arrows along each prescribed direction, blue;
- force: one arrow at the faces' centre, along the force, red;
- pressure: arrows into the face (out of it for a negative pressure), red;
- heat flux: arrows into the face, orange; temperature and convection: the face tinted;
- flow: inlet arrows into the face, outlet arrows out of it, green; walls and symmetry
  tinted;
- electromagnetics and custom equations: the face tinted.

Symbols are sized to the model (a fraction of its diagonal), never to the load: sizes
by value mislead when loads differ by orders of magnitude. At most `per_face` symbols
per face, spread over it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.mesh.adjacency import CORNERS
from puzzle.mesh.mesh import Mesh

BLUE, RED, ORANGE, GREEN, GREY, PURPLE = (
    "#3d7bd9",
    "#e5534b",
    "#e8912d",
    "#3fa564",
    "#8a94a3",
    "#9b6ad6",
)
TINTS = {
    "temperature": ORANGE,
    "convection": ORANGE,
    "wall": GREY,
    "symmetry": GREY,
    "electric_potential": PURPLE,
    "ground": PURPLE,
    "surface_charge": PURPLE,
    "coil_start": PURPLE,
    "coil_end": PURPLE,
    "magnetic_insulation": PURPLE,
    "dirichlet": GREY,
    "neumann": GREY,
    "coil_current": PURPLE,
}


@dataclass
class Glyphs:
    """One feature's symbols: arrows or cones at `points` along `directions` (unit
    vectors, pointing where the symbol points), or the `faces` tinted."""

    feature: str  # readable: "Force on Bar/Tip"
    kind: str  # "arrow", "cone" or "tint"
    colour: str
    points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    directions: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    faces: list = field(default_factory=list)


def _face_samples(mesh: Mesh, faces, per_face: int):
    """Points spread over the faces, with each one's outward normal."""
    order = np.argsort(mesh.node_ids)
    tags = {t for d, t in faces if d == 2}
    centre = mesh.coords.mean(axis=0)
    pts, normals = [], []
    for b in mesh.blocks:
        if b.dim != 2 or b.entity[1] not in tags or b.element not in CORNERS:
            continue
        corners = b.connectivity[:, : CORNERS[b.element]]
        xyz = mesh.coords[order[np.searchsorted(mesh.node_ids, corners.ravel(), sorter=order)]]
        xyz = xyz.reshape(len(corners), -1, 3)
        c = xyz.mean(axis=1)
        n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-300)
        flip = np.einsum("ij,ij->i", n, c - centre) < 0  # outward, roughly
        n[flip] *= -1
        step = max(1, len(c) // per_face)
        pts.append(c[::step][:per_face])
        normals.append(n[::step][:per_face])
    if not pts:
        return np.zeros((0, 3)), np.zeros((0, 3))
    return np.concatenate(pts), np.concatenate(normals)


def _vector(values: dict, keys=("x", "y", "z")) -> np.ndarray | None:
    v = np.array([values[k].to_si() if k in values else 0.0 for k in keys])
    return v if np.any(v) else None


def symbols(component, mesh: Mesh, entities_of, only: int | None = None, per_face: int = 10):
    """Glyph sets for the component's features (`only`: the n-th feature over all its
    physics). `entities_of(selection)` gives a selection's (dim, tag) faces."""
    from puzzle.gui.choices import label

    out: list[Glyphs] = []
    n = -1
    for ph in component.physics:
        for f in ph.features:
            n += 1
            if only is not None and n != only:
                continue
            faces = [e for e in entities_of(f.selection) if e[0] == 2]
            name = f"{label(f.type)} on {f.selection}"
            pts, normals = _face_samples(mesh, faces, per_face)
            if f.type == "fixed":
                out.append(Glyphs(name, "cone", BLUE, pts, -normals))
            elif f.type == "displacement":
                for axis, d in zip("xyz", np.eye(3), strict=True):
                    if axis in f.values:  # a prescribed component, whatever its value
                        sign = np.sign(f.values[axis].to_si()) or 1.0
                        out.append(
                            Glyphs(name, "arrow", BLUE, pts, np.tile(sign * d, (len(pts), 1)))
                        )
            elif f.type == "force":
                v = _vector(f.values)
                if v is not None and len(pts):
                    d = v / np.linalg.norm(v)
                    out.append(Glyphs(name, "arrow", RED, pts.mean(axis=0)[None], d[None]))
            elif f.type in ("gravity", "acceleration", "rotation"):
                out.append(_body_symbol(f, name, mesh))
            elif f.type in ("remote_force", "remote_force_rigid") and len(pts):
                from puzzle.adapters.common import remote_values

                force, moment, point = remote_values(f)
                at = (point if point is not None else pts.mean(axis=0))[None]
                v, colour = (force, RED) if np.any(force) else (moment, PURPLE)
                if np.any(v):
                    out.append(Glyphs(name, "arrow", colour, at, (v / np.linalg.norm(v))[None]))
            elif f.type == "remote_displacement":
                out.append(Glyphs(name, "tint", BLUE, faces=faces))
            elif f.type in ("pressure", "heat_flux", "velocity_inlet", "pressure_outlet"):
                key = {"pressure": "p", "heat_flux": "q", "velocity_inlet": "U"}.get(f.type)
                value = f.values[key].to_si() if key and key in f.values else 1.0
                inward = -normals if value >= 0 else normals
                if f.type == "pressure_outlet":
                    inward = normals
                colour = {"pressure": RED, "heat_flux": ORANGE}.get(f.type, GREEN)
                out.append(Glyphs(name, "arrow", colour, pts, inward))
            else:
                out.append(Glyphs(name, "tint", TINTS.get(f.type, GREY), faces=faces))
    return out


def _body_symbol(f, name: str, mesh: Mesh) -> Glyphs:
    """A body load (ADR 0037): one arrow at the model's centre along the load (gravity's
    pull, an acceleration's inertia), or along a rotation's axis."""
    from puzzle.adapters.common import body_load

    load = body_load(f)
    centre = mesh.coords.mean(axis=0)
    if load.uniform is not None:
        d = np.asarray(load.uniform, float)
        d = d / (np.linalg.norm(d) or 1.0)
        return Glyphs(name, "arrow", RED, centre[None], d[None])
    return Glyphs(
        name, "arrow", PURPLE, np.asarray(load.centre, float)[None], np.asarray(load.axis)[None]
    )
