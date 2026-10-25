"""Section cuts and animation frames for results (ADR 0038), shared by the GUI, the
`puzzle show` command and agents.

A cut is a plane across one axis at a fraction of the model's extent; `clip` keeps the
side behind it, coloured through by the result, and gives the rest for a ghost.
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

    def plane(self, bounds) -> tuple[np.ndarray, np.ndarray]:
        """The plane's origin and normal for a model of these (xmin, xmax, ...) bounds."""
        if self.origin is not None and self.normal is not None:
            n = np.asarray(self.normal, float)
            return np.asarray(self.origin, float), (-n if self.flip else n)
        i = AXES[self.axis]
        lo, hi = bounds[2 * i], bounds[2 * i + 1]
        origin = np.array([(bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2,
                           (bounds[4] + bounds[5]) / 2])  # fmt: skip
        origin[i] = lo + self.fraction * (hi - lo)
        normal = np.zeros(3)
        normal[i] = -1.0 if self.flip else 1.0
        return origin, normal


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
