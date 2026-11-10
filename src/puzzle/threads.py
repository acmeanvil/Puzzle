"""Threads as a correction on a contact between smooth cylinders (ADR 0076).

The one place the thread's geometry lives, pure and solver-neutral (numpy only): the
forms and their basic profiles, the flank normals on the helicoid, node pairs across
the two cylinders, tributary areas, the teeth's stiffness and per-thread binning.
Adapters get `ThreadPairs` and write only those (equations, springs or gaps along the
flank normals); they do no geometry.

Frame: the thread's axis is e_z (from the faces, or a coordinate system), e_r points
out from it and e_θ = e_z × e_r. The external thread (the screw) is the inner cylinder.
A flank's outward normal on the screw, for the flank facing +z ("upper") and −z
("lower"), at radius r, with s = +1 for a right hand and −1 for a left, L the lead:

    n_upper ∝ e_z − (s L / 2π r) e_θ + tan α_u e_r
    n_lower ∝ −e_z + (s L / 2π r) e_θ + tan α_l e_r

The e_θ term is the helix (a right-hand helix's surface z − Lθ/2π = const has gradient
e_z − (L/2πr) e_θ); the e_r term the flank's tilt from the radial plane, α being the
flank angle (30° for ISO and UN: the 60° form's half angle). A square thread has no e_r
term; in a 2D axisymmetric model e_θ drops out. The screw's upper flanks bear when it is
pulled toward +z out of its nut or boss.

Tooth compliance (`stiffness`, `yamamoto`): Yamamoto's (The theory and computation of
threads connection, Yokendo, Tokyo, 1980, as restated by Liu et al., Engineering
Structures 315 (2024) 118486, Eqs. 5-12) axial give of a loaded tooth, per unit of
axial load per unit length of thread, δ = (K / E) w, from its bending (δ1) and shear
(δ2), the root's inclination (δ3) and shear (δ4), and the nut's expansion or screw's
contraction (δ5). The tooth is a cantilever of root width a, width b where it is
loaded (the pitch line) and depth c from root to load. For the 60° forms a = 0.75 P,
b = 0.5 P, c = 0.2165 P (screw) and a = 0.875 P, c = 0.3248 P (nut), which give
Liu et al.'s coefficients (screw 0.015, 0.84, 0.106, 1.246; nut 0.067, 1.164, 0.293,
1.142, checked in tests/test_threads.py). Other forms take the same proportions of
their fundamental triangle. δ5 is left out by default: the smooth model's continuum
gives the screw's and nut's own radial give, and counting it again would soften the
thread twice.

VDI 2230-1 (2015, p. 40, Eqs. 20-27) gives the engaged thread's resilience δ_G = l_G /
(E_S A_d3), l_G = 0.5 d, and the nut's or tapped hole's δ_M = l_M / (E_M A_N), A_N =
π d²/4, l_M = 0.4 d (a through bolt's nut) or 0.33 d (a tapped thread joint): the
check of the whole engagement (`vdi_compliance`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Each basic profile in terms of the major diameter d and pitch P (m): the pitch
# diameter d2 = d - a2 P and the minor d1 = d - a1 P (the internal thread's minor, the
# engaged depth's root on the nut side), and the flank angles (load, other) in degrees.


@dataclass(frozen=True)
class Form:
    name: str
    standard: str
    angles: tuple[float, float]  # (load, other) flank angles, degrees from the radial plane
    a2: float  # d2 = d - a2 P
    a1: float  # d1 = d - a1 P
    taper: float = 0.0  # diameter per length (NPT, BSPT: 1/16)


FORMS: dict[str, Form] = {
    # 60°: H = (√3/2) P; d2 = d - (3/4) H · 2 = d - 0.649519 P, D1 = d - 1.082532 P
    "iso": Form("ISO metric", "ISO 68-1, ISO 724", (30.0, 30.0), 0.649519, 1.082532),
    "un": Form("Unified (UN)", "ASME B1.1", (30.0, 30.0), 0.649519, 1.082532),
    "acme": Form("Acme", "ASME B1.5", (14.5, 14.5), 0.5, 1.0),
    "stub_acme": Form("Stub Acme", "ASME B1.8", (14.5, 14.5), 0.3, 0.6),
    "trapezoidal": Form("Trapezoidal", "ISO 2904", (15.0, 15.0), 0.5, 1.0),
    # Buttress: h = 0.6 P engaged (ANSI B1.9), the 7° flank bearing.
    "buttress": Form("Buttress", "ANSI B1.9", (7.0, 45.0), 0.6, 1.2),
    "buttress_din": Form("Buttress (DIN)", "DIN 513", (3.0, 30.0), 0.75, 1.5),
    "square": Form("Square", "-", (0.0, 0.0), 0.5, 1.0),
    # Pipe threads: 60° and 55°, 1/16 on the diameter; heights 0.8 P and 0.640327 P.
    "npt": Form("NPT", "ASME B1.20.1", (30.0, 30.0), 0.8, 1.6, 1 / 16),
    "bspt": Form("BSPT", "ISO 7-1", (27.5, 27.5), 0.640327, 1.280654, 1 / 16),
}

# A scale on Yamamoto's compliance, tuned once (ADR 0076 step 2) against a meshed M16×2
# thread glued on its bearing flanks, the smooth one with both flanks tied: 0.25 gives
# the first thread 47.6 % against 50.0 % and the stiffness +2.6 %; 1.0 gave 36.8 % and
# -5 % (Yamamoto's cantilever teeth are softer than teeth in a continuum).
TEETH_FACTOR = 0.25
RIGID_FACTOR = 50.0  # rigid flanks: a penalty of 50 E / h, as Puzzle's contacts


def profile(form: str, major: float, pitch: float) -> dict[str, float]:
    """The basic diameters (m) of a form at a major diameter and pitch."""
    f = FORMS[form]
    return {"major": major, "pitch_diameter": major - f.a2 * pitch, "minor": major - f.a1 * pitch}


@dataclass
class Spec:
    """A thread's numbers in SI, resolved from the model's `Thread` (`spec`)."""

    form: str
    pitch: float
    starts: int = 1
    hand: int = 1  # +1 right, -1 left
    major: float = 0.0
    pitch_diameter: float = 0.0
    minor: float = 0.0
    angles: tuple[float, float] = (30.0, 30.0)  # (load, other)
    taper: float = 0.0
    clearance: float = 0.0

    @property
    def lead(self) -> float:
        return self.starts * self.pitch


def spec(thread) -> Spec:
    """A `model.schema.Thread` in SI with its defaults filled from its form and size."""
    from puzzle import bolts

    form = thread.form if thread.form != "custom" else None
    major = thread.major.to_si() if thread.major else None
    pitch = thread.pitch.to_si() if thread.pitch else None
    if thread.size:
        size = next((s for s in bolts.SIZES if s.name == thread.size), None)
        if size is None:
            raise ValueError(f"no thread size '{thread.size}'")
        major = major or size.diameter
        pitch = pitch or size.pitch
    if major is None or pitch is None:
        raise ValueError("a thread needs a size, or its major diameter and pitch")
    base = profile(form, major, pitch) if form else {"pitch_diameter": major, "minor": major}
    angles = tuple(thread.flank_angles) if thread.flank_angles else (
        FORMS[form].angles if form else (30.0, 30.0))  # fmt: skip
    return Spec(
        form=thread.form,
        pitch=pitch,
        starts=thread.starts,
        hand=1 if thread.hand == "right" else -1,
        major=major,
        pitch_diameter=thread.pitch_diameter.to_si() if thread.pitch_diameter
        else base["pitch_diameter"],
        minor=thread.minor.to_si() if thread.minor else base["minor"],
        angles=(float(angles[0]), float(angles[1])),
        taper=thread.taper if thread.taper is not None else (FORMS[form].taper if form else 0),
        clearance=thread.clearance.to_si() if thread.clearance else 0.0,
    )  # fmt: skip


# --- the axis ---------------------------------------------------------------------------


@dataclass
class Frame:
    """A thread's axis: a point on it, its direction (unit), the faces' radii (m) and
    the axial span (m along the axis from `origin`) where both faces overlap."""

    origin: np.ndarray
    axis: np.ndarray
    radii: tuple[float, float] = (0.0, 0.0)  # (source, target)
    overlap: tuple[float, float] = (0.0, 0.0)

    def cylindrical(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(r, θ, z) of points (n, 3), θ about the axis from a fixed perpendicular."""
        d = np.asarray(points, dtype=float) - self.origin
        z = d @ self.axis
        radial = d - np.outer(z, self.axis)
        e1, e2 = _perpendiculars(self.axis)
        return np.linalg.norm(radial, axis=1), np.arctan2(radial @ e2, radial @ e1), z

    def basis(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(e_r, e_θ, e_z) at each point, each (n, 3)."""
        d = np.asarray(points, dtype=float) - self.origin
        radial = d - np.outer(d @ self.axis, self.axis)
        e_r = radial / np.linalg.norm(radial, axis=1, keepdims=True)
        e_z = np.broadcast_to(self.axis, e_r.shape)
        return e_r, np.cross(e_z, e_r), np.array(e_z)


def _perpendiculars(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, helper)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(axis, e1)


def fit_axis(points: np.ndarray, normals: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """(a point on the axis, its unit direction, the radius) of a cylinder's points with
    their surface normals: the axis is the direction every normal is perpendicular to
    (the least eigenvector of Σ n nᵀ), the circle a least-squares fit in its plane."""
    n = np.asarray(normals, dtype=float)
    n = n / np.linalg.norm(n, axis=1, keepdims=True)
    _, vectors = np.linalg.eigh(n.T @ n)
    axis = vectors[:, 0]
    if axis @ np.array([0.0, 0.0, 1.0]) < 0 or (abs(axis[2]) < 1e-12 and axis.sum() < 0):
        axis = -axis
    e1, e2 = _perpendiculars(axis)
    p = np.asarray(points, dtype=float)
    u, v = p @ e1, p @ e2
    a = np.column_stack([u, v, np.ones_like(u)])  # Kåsa: u² + v² = 2 a u + 2 b v + c
    (cu, cv, c), *_ = np.linalg.lstsq(a, u**2 + v**2, rcond=None)
    cu, cv = cu / 2, cv / 2
    radius = math.sqrt(max(c + cu**2 + cv**2, 0.0))
    along = float(np.mean(p @ axis))
    return cu * e1 + cv * e2 + along * axis, axis, radius


def axis_frame(source, target, axis=None, origin=None) -> Frame:
    """The thread's frame from its two faces, each (points, normals): the axis fitted to
    both (or `axis`, `origin` given, a coordinate system's), the radii, the overlap."""
    ps, ns = (np.asarray(x, dtype=float) for x in source)
    pt, nt = (np.asarray(x, dtype=float) for x in target)
    if axis is None:
        o, a, _ = fit_axis(np.vstack([ps, pt]), np.vstack([ns, nt]))
    else:
        a = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
        o = np.asarray(origin if origin is not None else ps.mean(axis=0), dtype=float)
    frame = Frame(o, a)
    rs, _, zs = frame.cylindrical(ps)
    rt, _, zt = frame.cylindrical(pt)
    frame.radii = (float(np.mean(rs)), float(np.mean(rt)))
    frame.overlap = (float(max(zs.min(), zt.min())), float(min(zs.max(), zt.max())))
    return frame


# --- normals ----------------------------------------------------------------------------


def normals(points: np.ndarray, frame: Frame, thread: Spec, flank: str) -> np.ndarray:
    """The screw's outward unit normal on its `flank` ("upper", facing +z, or "lower")
    at each point (n, 3), in global components. Angle: the load flank's for upper
    when `load_up`, as `flank_angles` decides (see `flank_angle`)."""
    e_r, e_t, e_z = frame.basis(points)
    r, _, _ = frame.cylindrical(points)
    helix = (thread.hand * thread.lead / (2 * math.pi * r))[:, None]
    alpha = math.radians(flank_angle(thread, flank))
    sign = 1.0 if flank == "upper" else -1.0
    n = sign * e_z - sign * helix * e_t + math.tan(alpha) * e_r
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def flank_angle(thread: Spec, flank: str, load_up: bool = True) -> float:
    """The angle (degrees) of the screw's upper or lower flank: the load flank (the
    form's first angle) faces the way the screw is pulled (+z when `load_up`)."""
    load, other = thread.angles
    upper_is_load = load_up
    return load if (flank == "upper") == upper_is_load else other


def bearing_flank(load_direction: float) -> str:
    """Which of the screw's flanks bears: pulled toward +z (`load_direction` > 0) its
    upper flanks press the nut's lower ones. Which angle that flank has is the
    thread's `bearing` (the load flank's, or the other's): `flank_angle(load_up=)`."""
    return "upper" if load_direction >= 0 else "lower"


# --- pairs, areas, stiffness ------------------------------------------------------------


@dataclass
class ThreadPairs:
    """What an adapter writes for one threaded contact: node pairs, each pair's flank
    normals (global, from the screw outward), tributary flank areas (m²), the flank
    stiffness per pair (N/m) and the clearance (m). Pairs are numbered by their order."""

    source: np.ndarray  # (n,) node ids on the source face
    target: np.ndarray  # (n,) node ids on the target face
    z: np.ndarray  # (n,) m along the axis, from the frame's origin
    normals: dict[str, np.ndarray] = field(default_factory=dict)  # flank -> (n, 3)
    areas: dict[str, np.ndarray] = field(default_factory=dict)  # flank -> (n,) m²
    stiffness: dict[str, np.ndarray] = field(default_factory=dict)  # flank -> (n,) N/m
    clearance: float = 0.0
    flanks: tuple[str, ...] = ("upper",)


def pairs(source_xyz, source_ids, target_xyz, target_ids, frame: Frame, *, limit=None):
    """Each source node's nearest target node at the same (θ, z) (a KD-tree on the
    target's points, radii set equal), within `limit` m (default a tenth of the source's
    radius): (source ids, target ids, distances)."""
    from scipy.spatial import cKDTree

    def at_radius(xyz, r0):
        e_r, _, _ = frame.basis(xyz)
        r, _, _ = frame.cylindrical(xyz)
        return np.asarray(xyz, dtype=float) + (r0 - r)[:, None] * e_r

    r0 = frame.radii[0] or 1.0
    s = at_radius(source_xyz, r0)
    t = at_radius(target_xyz, r0)
    distance, index = cKDTree(t).query(s)
    keep = distance <= (limit if limit is not None else 0.1 * r0)
    return (np.asarray(source_ids)[keep], np.asarray(target_ids)[index[keep]], distance[keep])


def nodal_areas(faces: np.ndarray, xyz_of) -> dict[int, float]:
    """Each node's tributary area (m²) on a face mesh: three- and six-node triangles
    (a six-node triangle's consistent load lumps it on the midside nodes: a third each,
    corners nothing), four-node quadrilaterals a quarter each."""
    out: dict[int, float] = {}
    faces = np.asarray(faces)
    corners = 3 if faces.shape[1] in (3, 6) else 4
    for f in faces:
        p = np.array([xyz_of(int(n)) for n in f[:corners]])
        if corners == 3:
            area = 0.5 * np.linalg.norm(np.cross(p[1] - p[0], p[2] - p[0]))
        else:
            area = 0.5 * np.linalg.norm(np.cross(p[2] - p[0], p[3] - p[1]))
        if faces.shape[1] == 6:
            shares = [(int(n), area / 3) for n in f[3:6]]
        else:
            shares = [(int(n), area / corners) for n in f[:corners]]
        for n, a in shares:
            out[n] = out.get(n, 0.0) + a
    return out


def flank_areas(cylinder_areas: np.ndarray, thread: Spec, flank: str) -> np.ndarray:
    """The flank area (m²) a node's cylinder area stands for: a flank at α carries the
    axial load over area / cos α (the areas sum to π d L sec α)."""
    alpha = math.radians(flank_angle(thread, flank))
    return np.asarray(cylinder_areas, dtype=float) / math.cos(alpha)


def tooth(alpha: float, pitch: float, nut: bool) -> tuple[float, float, float]:
    """(a, b, c) of Yamamoto's tooth (m): root width, width at the load (the pitch line)
    and the depth from the root to it, for a form of flank angle `alpha` (degrees)."""
    t = math.tan(math.radians(alpha))
    h = pitch / (2 * t) if t > 1e-9 else pitch  # the fundamental triangle's height
    b = 0.5 * pitch
    c = (0.375 if nut else 0.25) * h
    return b + 2 * c * t, b, c


def yamamoto(alpha: float, pitch: float, nu: float, nut: bool, d2: float | None = None,
             radial: bool = False) -> float:  # fmt: skip
    """Yamamoto's K for one side (δ = K w / E, w the axial load per unit length of
    thread): δ1 + δ2 + δ3 + δ4, and δ5 with `radial` (it needs the pitch diameter d2)."""
    a, b, c = tooth(alpha, pitch, nut)
    al = math.radians(alpha)
    tan, cot = math.tan(al), 1 / math.tan(al) if alpha > 1e-9 else 1e9
    k1 = abs(0.75 * (1 - nu**2) * ((1 - (2 - b / a) ** 2 + 2 * math.log(a / b)) * cot**3
                                   - 4 * (c / a) ** 2 * tan))  # fmt: skip
    k2 = 1.2 * (1 + nu) * cot * math.log(a / b)
    k3 = (1 - nu**2) * 12 * c / (math.pi * a**2) * (c - b / 2 * tan)
    k4 = (1 - nu**2) * 2 / math.pi * (pitch / a * math.log((pitch + a / 2) / (pitch - a / 2))
                                      + 0.5 * math.log(4 * pitch**2 / a**2 - 1))  # fmt: skip
    k = k1 + k2 + k3 + k4
    if radial:
        if d2 is None:
            raise ValueError("δ5 needs the pitch diameter")
        k += 0.5 * d2 / pitch * tan**2 * (1 + nu if nut else 1 - nu)  # Liu et al. Eq. 11
    return k


def stiffness(areas: np.ndarray, thread: Spec, e_screw: float, e_nut: float,
              compliance="teeth", size: float | None = None, nu_screw: float = 0.3,
              nu_nut: float = 0.3, rigid_core: bool = False) -> np.ndarray:  # fmt: skip
    """Each pair's flank stiffness (N/m) along its normal for flank `areas` (m²):
    `teeth`, Yamamoto's teeth of both sides in series (module docstring) as a spring
    along n: a patch of cylinder area A_c (one pitch high) has axial stiffness
    A_c / (P (K_s/E_s + K_n/E_n)), and along n that is k = A_flank / (P ΣK/E cos α);
    `rigid`, a penalty of 50 E* A / h (h the thread's depth); or a stiffness per area
    (N/m³) given as a number. `rigid_core`: the screw is a beam bolt's core, rigid
    across its axis, so its radial contraction (Yamamoto's δ5 of the screw, which a
    solid screw's continuum gives) is added to the give, untuned."""
    a = np.asarray(areas, dtype=float)
    e_star = 1.0 / (1.0 / e_screw + 1.0 / e_nut)
    if compliance == "teeth":
        alpha = thread.angles[0]
        give = (yamamoto(alpha, thread.pitch, nu_screw, False) / e_screw
                + yamamoto(alpha, thread.pitch, nu_nut, True) / e_nut)  # fmt: skip
        give *= TEETH_FACTOR
        if rigid_core:
            d2 = thread.pitch_diameter
            with_radial = yamamoto(alpha, thread.pitch, nu_screw, False, d2=d2, radial=True)
            radial = with_radial - yamamoto(alpha, thread.pitch, nu_screw, False)
            give += radial / e_screw
        cos = math.cos(math.radians(alpha))
        return a / (thread.pitch * give * cos)
    if compliance == "rigid":
        h = size or (thread.major - thread.minor) / 2 or thread.pitch
        return RIGID_FACTOR * e_star * a / h
    return float(compliance) * a


def per_thread(z: np.ndarray, forces: np.ndarray, pitch: float, mouth: float,
               toward: float = 1.0) -> list[float]:  # fmt: skip
    """Forces binned by pitch from the mouth (the first engaged thread first): `z` each
    pair's place along the axis, `mouth` where the engagement starts, `toward` the
    direction (±1) the threads run from it."""
    k = np.floor((np.asarray(z, dtype=float) - mouth) * toward / pitch + 1e-9).astype(int)
    if not len(k):
        return []
    k = np.clip(k, 0, None)
    out = np.zeros(int(k.max()) + 1)
    np.add.at(out, k, np.asarray(forces, dtype=float))
    return out.tolist()


def vdi_compliance(d: float, d3: float, e_screw: float, e_nut: float,
                   tapped: bool = True) -> float:  # fmt: skip
    """VDI 2230's resilience (m/N) of an engaged thread and its nut or tapped hole:
    δ_G + δ_M = 0.5 d / (E_S A_d3) + l_M / (E_M A_N), l_M 0.33 d tapped or 0.4 d a nut
    (as commonly quoted; module docstring)."""
    a_d3 = math.pi * d3**2 / 4
    a_n = math.pi * d**2 / 4
    return 0.5 * d / (e_screw * a_d3) + (0.33 if tapped else 0.4) * d / (e_nut * a_n)


def minimum_pitch_nodes(z: np.ndarray, pitch: float) -> float:
    """Distinct node levels per pitch along the axis (Ansys's p/4 rule wants four)."""
    levels = np.unique(np.round(np.asarray(z, dtype=float) / (pitch * 1e-3)))
    span = float(np.ptp(z)) if len(z) else 0.0
    return len(levels) / max(span / pitch, 1.0)
