"""A 6-DOF device's counts as a camera change (ADR 0069): pure, no Qt, no hardware.

`shaped` turns raw counts (view frame, `devices.to_view`) into six values in [-1, 1]:
dead zone, response curve, locks, dominant axis, inversion. `move` applies them to a
camera (position, focal point, view up) over a time step:

- object mode (the default): push the cap and the model goes that way, so the camera
  goes the other; camera mode the reverse;
- pan in the view plane and zoom along the view, both proportional to the camera's
  distance from what it looks at, so a bolt and a bridge feel the same;
- rotation about a pivot (the selection, or the model point under the view's centre),
  which the camera, its focal point and its up turn around together;
- everything scaled by the time step (clamped), so speed doesn't follow the frame rate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

FULL_SCALE = 350.0  # counts at the cap's stop (SpaceMouse devices report about ±350)
PAN_RATE = 1.2  # view distances per second at full push, speed 1
ZOOM_RATE = 1.0  # e-folds of distance per second at full push
TURN_RATE = 2.0  # radians per second at full twist
MAX_STEP = 0.1  # s: a stall isn't a jump


@dataclass(frozen=True)
class Settings:
    speed: float = 1.0
    dead_zone: float = 0.06  # of full scale
    curve: float = 1.6  # 1 linear; larger is gentler near rest and as fast at the stop
    mode: str = "object"  # or "camera"
    translate: bool = True
    rotate: bool = True
    dominant: bool = False  # only the largest of the six
    invert: tuple[bool, bool, bool, bool, bool, bool] = (False,) * 6


@dataclass(frozen=True)
class Camera:
    position: tuple[float, float, float]
    focal_point: tuple[float, float, float]
    up: tuple[float, float, float]
    # An orthographic view's half height: there zoom scales the view, since moving the
    # camera nearer shows nothing larger (the stutter seen zooming). None: perspective.
    parallel_scale: float | None = None

    @classmethod
    def of(cls, triple, parallel_scale: float | None = None) -> Camera:
        """From pyvista's camera_position: (position, focal point, view up)."""
        p, f, u = (tuple(float(v) for v in row) for row in triple)
        return cls(p, f, u, parallel_scale)

    def triple(self) -> list[tuple[float, float, float]]:
        return [self.position, self.focal_point, self.up]


DEFAULT = Settings()


def shaped(counts, settings: Settings = DEFAULT) -> np.ndarray:
    """Six values in [-1, 1] from view-frame counts (translations, then rotations)."""
    v = np.clip(np.asarray(counts, float) / FULL_SCALE, -1.0, 1.0)
    dz = settings.dead_zone
    mag = np.clip((np.abs(v) - dz) / (1.0 - dz), 0.0, 1.0) ** settings.curve
    v = np.sign(v) * mag
    v = np.where(settings.invert, -v, v)
    if not settings.translate:
        v[:3] = 0.0
    if not settings.rotate:
        v[3:] = 0.0
    if settings.dominant and np.any(v):
        keep = int(np.argmax(np.abs(v)))
        v = np.where(np.arange(6) == keep, v, 0.0)
    return v


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n else v


def _turn(vector, axis, angle: float) -> np.ndarray:
    """Rodrigues: `vector` turned by `angle` about the unit `axis`."""
    v, k = np.asarray(vector, float), np.asarray(axis, float)
    return v * np.cos(angle) + np.cross(k, v) * np.sin(angle) + k * (k @ v) * (1 - np.cos(angle))


def frame(camera: Camera) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """The view's right, up and back (toward the viewer) directions, and the distance."""
    p, f = np.asarray(camera.position, float), np.asarray(camera.focal_point, float)
    back = p - f
    distance = float(np.linalg.norm(back)) or 1.0
    back = back / distance
    right = _unit(np.cross(camera.up, back))
    up = np.cross(back, right)
    return right, up, back, distance


def move(camera: Camera, values, dt: float, pivot=None, settings: Settings = DEFAULT) -> Camera:
    """`camera` after `dt` seconds of the cap held at `values` (from `shaped`)."""
    v = np.asarray(values, float)
    if not np.any(v):
        return camera
    dt = min(max(dt, 0.0), MAX_STEP) * settings.speed
    sign = -1.0 if settings.mode == "object" else 1.0  # object mode: the camera goes opposite
    right, up, back, distance = frame(camera)
    p = np.asarray(camera.position, float)
    f = np.asarray(camera.focal_point, float)
    u = np.asarray(camera.up, float)
    # Pan in the view plane, and zoom: the cap toward you brings the model nearer.
    ortho = camera.parallel_scale
    span = 2.0 * ortho if ortho else distance  # what a full push pans across per second
    shift = sign * PAN_RATE * dt * span * (v[0] * right + v[1] * up)
    p, f = p + shift, f + shift
    nearer = 1.0 - np.exp(-ZOOM_RATE * dt * v[2] * (-sign))  # object: toward you = nearer
    if ortho:
        ortho = ortho * (1.0 - nearer)  # a smaller half height shows the model larger
    else:
        p = p + (f - p) * nearer
    # Turn about the pivot: the cap's twist axis, in the view's frame, as a world axis.
    spin = v[3] * right + v[4] * up + v[5] * back
    angle = float(np.linalg.norm(spin)) * TURN_RATE * dt
    if angle:
        axis = spin / np.linalg.norm(spin)
        angle *= sign
        centre = f if pivot is None else np.asarray(pivot, float)
        p = centre + _turn(p - centre, axis, angle)
        f = centre + _turn(f - centre, axis, angle)
        u = _turn(u, axis, angle)
    return Camera(tuple(p), tuple(f), tuple(_unit(u)), ortho)


SMOOTH_S = 0.06  # readings eased over about this long: no jumps from an uneven report rate
STOP_AT = 0.6  # of the dead zone: once moving, it stops only below this (no flicker at rest)


class Filter:
    """Eases raw counts toward each new reading, with a dead zone that is sticky: a cap
    held near rest doesn't switch motion on and off many times a second."""

    def __init__(self) -> None:
        self.value = np.zeros(6)
        self.moving = np.zeros(6, bool)

    def __call__(self, counts, dt: float, settings: Settings = DEFAULT) -> np.ndarray:
        target = np.clip(np.asarray(counts, float) / FULL_SCALE, -1.0, 1.0)
        dz = settings.dead_zone
        size = np.abs(target)
        self.moving = np.where(self.moving, size > dz * STOP_AT, size > dz)
        edge = np.where(self.moving, dz * STOP_AT, dz)  # past the zone, from 0 smoothly
        target = np.where(self.moving, np.sign(target) * (size - edge) / (1.0 - edge), 0.0)
        ease = min(1.0, max(dt, 0.0) / SMOOTH_S)
        self.value = self.value + (target - self.value) * ease
        self.value[np.abs(self.value) < 1e-4] = 0.0
        # the curve, locks and inversion; the dead zone is done above
        return shaped(self.value * FULL_SCALE, replace(settings, dead_zone=0.0))

    def resting(self) -> bool:
        return not self.value.any()
