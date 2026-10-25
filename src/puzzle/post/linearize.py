"""Stress linearization along a line through a wall (ASME BPVC VIII-2, Annex 5-A).

A pressure-vessel check classes the stress across a wall into
- **membrane** (the mean through the thickness),
- **bending** (the linear part, equal and opposite at the two faces), and
- **peak** (what is left: the total less membrane and bending).

Each of the six components is linearized on its own, as Annex 5-A's equations do and
Ansys's PRSECT does:

    membrane  m_ij = (1/t) ∫ s_ij dx
    bending   b_ij = (6/t²) ∫ s_ij (t/2 − x) dx    (at the start; −b_ij at the end)
    peak      p_ij = s_ij − m_ij ∓ b_ij             (at either end)

with x along the line from its start, t its length. Equivalent values are then taken
from the linearized tensors: von Mises (VIII-2 since 2007) or the stress intensity
(Tresca: the largest difference of principal stresses; the older rules). Integrals use
Simpson's rule over evenly spaced points (an odd count; Ansys uses 47).

Components are in the global axes. An equivalent stress doesn't depend on the axes,
so the classification checks (Pm, PL + Pb, ...) read the same in any frame.
"""

from __future__ import annotations

import numpy as np

ORDER = ("xx", "yy", "zz", "xy", "yz", "zx")
PARTS = ("membrane", "bending", "membrane_bending", "peak", "total")
LABELS = {
    "membrane": "Membrane",
    "bending": "Bending",
    "membrane_bending": "Membrane + bending",
    "peak": "Peak",
    "total": "Total",
}
EQUIVALENTS = {"von_mises": "von Mises", "tresca": "Stress intensity (Tresca)"}


def simpson(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """∫ y dx over evenly spaced x (axis 0), Simpson's rule; an even count of points
    ends with one trapezoid."""
    n = len(x)
    if n < 3:
        return np.trapezoid(y, x, axis=0)
    h = (x[-1] - x[0]) / (n - 1)
    m = n if n % 2 else n - 1
    w = np.ones(m)
    w[1:-1:2], w[2:-1:2] = 4.0, 2.0
    out = (h / 3.0) * np.tensordot(w, y[:m], axes=(0, 0))
    if m < n:
        out = out + 0.5 * h * (y[m - 1] + y[m])
    return out


def _matrix(v: np.ndarray) -> np.ndarray:
    """(…, 6) xx, yy, zz, xy, yz, zx -> (…, 3, 3)."""
    xx, yy, zz, xy, yz, zx = np.moveaxis(np.asarray(v, float), -1, 0)
    return np.stack(
        [np.stack([xx, xy, zx], -1), np.stack([xy, yy, yz], -1), np.stack([zx, yz, zz], -1)],
        -2,
    )


def equivalent(v: np.ndarray, kind: str = "von_mises") -> np.ndarray:
    """von Mises or Tresca stress intensity of (…, 6) tensors."""
    v = np.asarray(v, float)
    if kind == "tresca":
        principal = np.linalg.eigvalsh(_matrix(v))
        return principal[..., -1] - principal[..., 0]
    xx, yy, zz, xy, yz, zx = np.moveaxis(v, -1, 0)
    return np.sqrt(
        0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy**2 + yz**2 + zx**2)
    )


def linearize(distance, tensors) -> dict:
    """Membrane, bending and peak parts of `tensors` ((n, 6), xx…zx, SI) sampled at
    `distance` ((n,), evenly spaced from the line's start). Returns each part's six
    components at the start ("start") and at the end ("end"); membrane is the same at
    both."""
    x = np.asarray(distance, float) - float(distance[0])
    s = np.asarray(tensors, float)
    t = float(x[-1])
    if t <= 0:
        raise ValueError("the line has no length")
    membrane = simpson(s, x) / t
    bending = 6.0 / t**2 * simpson(s * (t / 2.0 - x)[:, None], x)
    out = {}
    for end, sign, total in (("start", 1.0, s[0]), ("end", -1.0, s[-1])):
        mb = membrane + sign * bending
        out[end] = {
            "membrane": membrane,
            "bending": sign * bending,
            "membrane_bending": mb,
            "peak": total - mb,
            "total": total,
        }
    return out


def report(distance, tensors, kind: str = "von_mises") -> dict:
    """`linearize`, with each part's equivalent at both ends and the distributions along
    the line (total, membrane, membrane + bending) for a plot. `value` is the largest
    membrane + bending equivalent, the usual governing number."""
    x = np.asarray(distance, float) - float(distance[0])
    parts = linearize(distance, tensors)
    t = float(x[-1])
    rows = {}
    for end in ("start", "end"):
        rows[end] = {
            part: {
                "components": dict(zip(ORDER, parts[end][part].tolist(), strict=True)),
                "equivalent": float(equivalent(parts[end][part], kind)),
            }
            for part in PARTS
        }
    m = parts["start"]["membrane"]
    b = parts["start"]["bending"]
    linear = m[None, :] + b[None, :] * (1.0 - 2.0 * x / t)[:, None]
    return {
        "parts": rows,
        "equivalent": kind,
        "thickness": t,
        "value": max(
            rows["start"]["membrane_bending"]["equivalent"],
            rows["end"]["membrane_bending"]["equivalent"],
        ),  # fmt: skip
        "series": {
            "total": equivalent(tensors, kind).tolist(),
            "membrane": [rows["start"]["membrane"]["equivalent"]] * len(x),
            "membrane_bending": equivalent(linear, kind).tolist(),
        },
    }
