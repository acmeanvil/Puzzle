"""Section cuts and animation frames for results (ADR 0038), shared by the GUI, the
`puzzle show` command and agents.

A cut is a plane across one axis at a fraction of the model's extent, or a placed plane:
a construction plane (ADR 0046) or one fitted to faces (`face_plane`: a picked face or a
face selection), either moved along its normal by an offset. `clip` keeps the side
behind it, coloured through by the result, and gives the rest for a ghost.
Animation is either over the deformation (0 → full → 0, as a sine, for a static result)
or over the result's steps; frames go to a GIF with Pillow (MP4 waits for ffmpeg).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

AXES = {"x": 0, "y": 1, "z": 2}


@dataclass(frozen=True)
class Cut:
    axis: str = "x"  # the plane's normal: x, y or z
    fraction: float = 0.5  # where across the model's extent, 0..1
    flip: bool = False  # keep the other side
    ghost: bool = True  # show the cut-away side faintly
    origin: tuple | None = None  # a placed plane (a construction plane, ADR 0046) ...
    normal: tuple | None = None  # ... instead of axis and fraction
    offset: float = 0.0  # a placed plane moved along its normal (m)
    source: str | None = None  # what placed it, for the view's record ("face", "plane", ...)

    def plane(self, bounds) -> tuple[np.ndarray, np.ndarray]:
        """The plane's origin and normal for a model of these (xmin, xmax, ...) bounds."""
        if self.origin is not None and self.normal is not None:
            n = np.asarray(self.normal, float)
            n = n / np.linalg.norm(n)
            origin = np.asarray(self.origin, float) + self.offset * n
            return origin, (-n if self.flip else n)
        i = AXES[self.axis]
        lo, hi = bounds[2 * i], bounds[2 * i + 1]
        origin = np.array([(bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2,
                           (bounds[4] + bounds[5]) / 2])  # fmt: skip
        origin[i] = lo + self.fraction * (hi - lo)
        normal = np.zeros(3)
        normal[i] = -1.0 if self.flip else 1.0
        return origin, normal


def face_plane(mesh, entities) -> tuple[np.ndarray, np.ndarray]:
    """The plane of some faces (Gmsh (2, tag) entities): through their area-weighted
    centroid, along their area-weighted normal. A flat face gives its own plane; a
    curved one (where the normals cancel, as round a cylinder) the plane that fits its
    points best."""
    keys = {tuple(e) for e in entities}
    tris = []
    for b in mesh.blocks:
        if b.dim == 2 and tuple(b.entity) in keys:
            corners = b.connectivity[:, :3]  # a triangle's corners, also a quad's first three
            tris.append(mesh.node_coords(corners.ravel()).reshape(-1, 3, 3))
    if not tris:
        raise ValueError("no faces to place a plane on")
    t = np.concatenate(tris)
    cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])  # 2 x area, along the normal
    area = np.linalg.norm(cross, axis=1)
    centre = (t.mean(axis=1) * area[:, None]).sum(0) / area.sum()
    normal = cross.sum(0)
    if np.linalg.norm(normal) < 1e-6 * area.sum():
        points = t.reshape(-1, 3) - centre
        normal = np.linalg.svd(points, full_matrices=False)[2][-1]
    return centre, normal / np.linalg.norm(normal)


def face_at(mesh, point) -> tuple[int, int] | None:
    """The face (Gmsh (2, tag)) of the mesh's surface elements nearest a point."""
    best, found = np.inf, None
    point = np.asarray(point, float)
    for b in mesh.blocks:
        if b.dim != 2:
            continue
        centres = mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3).mean(axis=1)
        d = float(np.min(np.linalg.norm(centres - point, axis=1)))
        if d < best:
            best, found = d, tuple(b.entity)
    return found


def from_saved(saved, project=None, mesh=None) -> Cut:
    """The `Cut` a saved section (`SectionCut`) stands for, placed on the shown mesh
    (its face selections) and the project (its construction planes)."""
    if saved.plane in AXES:
        return Cut(saved.plane, saved.fraction, saved.flip, saved.ghost)
    if saved.plane == "selection":
        if mesh is None or saved.selection not in mesh.groups:
            raise ValueError(f"section '{saved.name}': no face selection '{saved.selection}' "
                             "on the shown mesh")  # fmt: skip
        origin, normal = face_plane(mesh, mesh.groups[saved.selection].entities)
    elif saved.plane == "construction":
        plane = project.objects.get(saved.construction) if project is not None else None
        if plane is None:
            raise ValueError(f"section '{saved.name}': its construction plane is gone")
        origin, normal = plane.frame(project)
    else:
        origin, normal = np.asarray(saved.origin, float), np.asarray(saved.normal, float)
    return Cut(flip=saved.flip, ghost=saved.ghost, origin=tuple(np.asarray(origin).tolist()),
               normal=tuple(np.asarray(normal).tolist()), offset=saved.offset,
               source=saved.plane)  # fmt: skip


def clip(grid, cut: Cut, bounds=None):
    """(the part kept, the part cut away) of a VTK grid. `bounds` places the plane (the
    undeformed model's, so a cut stays put as the deformation scale changes)."""
    origin, normal = cut.plane(bounds if bounds is not None else grid.bounds)
    kept = grid.clip(normal=normal, origin=origin, crinkle=False)
    away = grid.clip(normal=-normal, origin=origin, crinkle=False)
    return kept, away


def deformation_frames(n: int = 24) -> list[float]:
    """Deformation factors for one loop: 0 → 1 → 0 along a sine, `n` frames."""
    return [float(np.sin(np.pi * k / n)) for k in range(n)]


def save_gif(frames: list, path: Path, fps: float = 15.0) -> Path:
    """RGB frames (h × w × 3 arrays) to a looping GIF."""
    from PIL import Image

    if not frames:
        raise ValueError("no frames to save")
    images = [Image.fromarray(np.asarray(f, dtype=np.uint8)) for f in frames]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], duration=int(1000 / fps), loop=0)
    return path
