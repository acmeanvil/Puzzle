"""Stress linearization along a line through a wall (ASME BPVC VIII-2, Annex 5-A).

A pressure-vessel check classes the stress across a wall, along a stress
classification line (SCL), into
- **membrane** (the mean through the thickness),
- **bending** (the linear part, equal and opposite at the two faces), and
- **peak** (what is left: the total less membrane and bending).

Each of the six components is linearized on its own, as Annex 5-A's equations do and
Ansys's PRSECT does:

    membrane  m_ij = (1/t) ∫ s_ij dx
    bending   b_ij = (6/t²) ∫ s_ij (t/2 − x) dx    (at the start; −b_ij at the end)
    peak      p_ij = s_ij − m_ij ∓ b_ij             (at either end)

with x along the line from its start, t its length. Integrals use Simpson's rule over
evenly spaced points (an odd count; Ansys uses 47).

**Axes.** Components are given in the global axes, or in the line's own (`line_frame`):
n through the wall along the line, h the hoop direction (chosen, else the global axis
most across the line), t = h × n the meridional one. Annex 5-A (5-A.4.1.2) computes
bending only for the hoop and meridional normal stresses, not for the stress along the
SCL or the shears; `normal_only` does that, in the line's axes. Equivalent stresses
don't depend on the axes, only on which parts carry bending.

**Equivalents.** von Mises (VIII-2 since 2007) and the stress intensity (Tresca: the
largest difference of principal stresses, the older rules), with the principal
stresses, for each part at both ends.

**Checks** (`checks`, VIII-2 5.2.2.4, elastic analysis) against an allowable S: the
membrane as general primary (Pm ≤ S) or local primary (PL ≤ 1.5 S), membrane + bending
as primary (PL + Pb ≤ 1.5 S) and as primary + secondary (PL + Pb + Q ≤ 3 S, when the
load includes secondary stress such as thermal). S_PL = 1.5 S and S_PS = 3 S here; where
Part 5 allows the yield strength instead, compare by hand. Which load is primary and
which secondary is the engineer's call: the numbers are the linearized stresses.
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
LOCAL = ("nn", "tt", "hh", "nt", "th", "hn")  # the line's axes: through, meridional, hoop
LOCAL_LABELS = {
    "nn": "Through the wall (n)", "tt": "Meridional (t)", "hh": "Hoop (h)",
    "nt": "Shear nt", "th": "Shear th", "hn": "Shear hn",
}  # fmt: skip
GLOBAL_LABELS = {c: c.upper() for c in ORDER}


def line_frame(start, end, hoop=None) -> np.ndarray:
    """The line's axes as columns (n, t, h): n along the line, h the hoop direction
    (`hoop`, else the global axis most across the line) made square to n, t = h × n."""
    n = np.asarray(end, float) - np.asarray(start, float)
    n /= np.linalg.norm(n)
    if hoop is None or not np.linalg.norm(hoop):
        hoop = np.eye(3)[int(np.argmin(np.abs(n)))]
    h = np.asarray(hoop, float) - (np.asarray(hoop, float) @ n) * n
    if np.linalg.norm(h) < 1e-9:
        raise ValueError("the hoop direction lies along the line")
    h /= np.linalg.norm(h)
    return np.column_stack([n, np.cross(h, n), h])


def rotate(tensors, frame: np.ndarray) -> np.ndarray:
    """(…, 6) global tensors in the axes `frame`'s columns give (Rᵀ σ R), same order."""
    m = np.einsum("ji,...jk,kl->...il", frame, _matrix(tensors), frame)
    return np.stack([m[..., 0, 0], m[..., 1, 1], m[..., 2, 2],
                     m[..., 0, 1], m[..., 1, 2], m[..., 2, 0]], -1)  # fmt: skip


def principal(v) -> np.ndarray:
    """Principal stresses S1 ≥ S2 ≥ S3 of (…, 6) tensors."""
    return np.linalg.eigvalsh(_matrix(v))[..., ::-1]


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


def linearize(distance, tensors, normal_only: bool = False) -> dict:
    """Membrane, bending and peak parts of `tensors` ((n, 6), SI) sampled at `distance`
    ((n,), evenly spaced from the line's start). Returns each part's six components at
    the start ("start") and at the end ("end"); membrane is the same at both.
    `normal_only` (tensors in the line's axes): bending for the meridional and hoop
    normal stresses only, as Annex 5-A asks."""
    x = np.asarray(distance, float) - float(distance[0])
    s = np.asarray(tensors, float)
    t = float(x[-1])
    if t <= 0:
        raise ValueError("the line has no length")
    membrane = simpson(s, x) / t
    bending = 6.0 / t**2 * simpson(s * (t / 2.0 - x)[:, None], x)
    if normal_only:
        bending = bending * np.array([0.0, 1.0, 1.0, 0.0, 0.0, 0.0])
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


def report(
    distance,
    tensors,
    kind: str = "von_mises",
    frame: np.ndarray | None = None,
    normal_only: bool = False,
) -> dict:
    """`linearize`, with each part at both ends as components, principal stresses and
    both equivalents, the total at the middle, and the distributions along the line
    (total, membrane, membrane + bending) for a plot. `frame`: give the components in
    those axes (`line_frame`); `value` is the largest membrane + bending equivalent of
    `kind`, the usual governing number."""
    x = np.asarray(distance, float) - float(distance[0])
    s = np.asarray(tensors, float) if frame is None else rotate(tensors, frame)
    names = ORDER if frame is None else LOCAL
    parts = linearize(distance, s, normal_only and frame is not None)
    t = float(x[-1])

    def row(v) -> dict:
        return {
            "components": dict(zip(names, np.asarray(v).tolist(), strict=True)),
            "principal": principal(v).tolist(),
            "von_mises": float(equivalent(v, "von_mises")),
            "tresca": float(equivalent(v, "tresca")),
            "equivalent": float(equivalent(v, kind)),
        }

    rows = {end: {part: row(parts[end][part]) for part in PARTS} for end in ("start", "end")}
    m = parts["start"]["membrane"]
    b = parts["start"]["bending"]
    linear = m[None, :] + b[None, :] * (1.0 - 2.0 * x / t)[:, None]
    middle = np.array([np.interp(t / 2, x, s[:, i]) for i in range(6)])
    return {
        "parts": rows,
        "middle": row(middle),
        "equivalent": kind,
        "components_in": "global" if frame is None else "line",
        "frame": None if frame is None else np.asarray(frame).tolist(),
        "bending": "normal" if normal_only and frame is not None else "all",
        "thickness": t,
        "value": max(
            rows["start"]["membrane_bending"]["equivalent"],
            rows["end"]["membrane_bending"]["equivalent"],
        ),  # fmt: skip
        "series": {
            "total": equivalent(s, kind).tolist(),
            "membrane": [rows["start"]["membrane"]["equivalent"]] * len(x),
            "membrane_bending": equivalent(linear, kind).tolist(),
        },
    }


def checks(found: dict, allowable: float, membrane: str = "general") -> list[dict]:
    """VIII-2 5.2.2.4's elastic checks on a linearization (`report`), with the
    equivalent it used: each {name, value, limit, ratio, ok}. `membrane`: "general"
    (Pm ≤ S) or "local" (PL ≤ 1.5 S) primary membrane."""
    s = float(allowable)
    parts = found["parts"]
    pm = parts["start"]["membrane"]["equivalent"]
    mb = max(parts[e]["membrane_bending"]["equivalent"] for e in ("start", "end"))
    rows = [
        ("Primary membrane, Pm ≤ S", pm, s) if membrane == "general"
        else ("Local primary membrane, PL ≤ 1.5 S", pm, 1.5 * s),
        ("Primary membrane + bending, PL + Pb ≤ 1.5 S", mb, 1.5 * s),
        ("Primary + secondary, PL + Pb + Q ≤ 3 S", mb, 3.0 * s),
    ]  # fmt: skip
    return [{"name": n, "value": v, "limit": lim, "ratio": v / lim, "ok": v <= lim}
            for n, v, lim in rows]  # fmt: skip
