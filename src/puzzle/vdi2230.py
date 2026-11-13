"""VDI 2230 Part 1:2015, the systematic calculation of highly stressed bolted joints with
one cylindrical bolt (ADR 0050): the pieces Puzzle's bolt checks use, ported from Lacewing
Lab's `engineering_core/lib/fastening/vdi2230.dart` (where they were checked against the
guideline's Examples B1, B2 and B5 and Table A1; so are these, in tests/test_vdi2230.py).

SI throughout (m, N, Pa; resiliences m/N). The guideline writes its formulas in mm and
N/mm²; those few with dimensional constants (the endurance limit) convert inside.

- Thread geometry (Table A11, DIN 13): d2 = d − 0.649519 P, d3 = d − 1.226869 P,
  dS = (d2 + d3)/2, AS = π dS²/4, Ad3 and AN = π d²/4 (`thread`).
- The bolt's resilience δS = δSK + Σδi + δGew + δGM (Section 5.1.1, Eqs. 19-31,
  `bolt_resilience`): the head as lSK = 0.5 d of the nominal section for a hexagon head,
  0.4 d for a socket head cap screw (Eqs. 20, 21); each shank cylinder (22); the free
  loaded thread at the minor section (23); the engaged thread lG = 0.5 d at the minor
  section (Eqs. 24-26) and the nut or tapped hole lM = 0.4 d (a nut), 0.33 d (a tapped
  thread joint) at the nominal section with the nut's modulus (27-30). δG + δM is
  `threads.vdi_compliance` (ADR 0076), which a test holds equal. The bending resilience
  βS of the same elements gives the substitutional bending length lers (33-35).
- The clamped parts' resilience δP by the deformation cone(s) and sleeve (5.1.2,
  `plate_resilience`; tan φ Eqs. 42/43, DA,Gr Eq. 39, Eqs. 40/41), and the substitutional
  moment of gyration IBers (Eqs. 59-62, `moment_of_gyration`).
- The load introduction factor n (Table 2, joint types SV 1-6, `load_introduction_factor`)
  and the load factor ΦK, Φn and Φ*en (Eqs. 80-83, 67, 71, 91, 92, `load_factor`).
- The clamp load against opening (R2/3, `opening_clamp_load`), embedding (Table 5,
  `embedding`), the permissible assembly preload FMzul at ν = 0.9 (Eq. 144,
  `assembly_preload`), the tightening torque MA (Eq. 127, `tightening_torque`; its
  reverse `preload_from_torque`; the equivalent nut factor K = MA/(FM d) for
  `BoltType.nut_factor`, `nut_factor`), the working stress (R8, `working_stress`), the
  endurance limit (Eqs. 170-174, `endurance_limit`), the eccentric bolt stress (Eq. 188),
  the size estimate (Table A7), and the guide values of Tables A5 (friction classes), A8
  (tightening factors) and A9 (materials of clamped parts: limiting surface pressure).
- Not here: R11 (the minimum length of engagement) is a note in Lacewing Lab as well;
  Puzzle's tapped joints default to 1.5 d (`BoltType.engagement`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MM, MPA = 1e-3, 1e6
REFERENCE = "VDI 2230 Part 1:2015"

# --- the thread -------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricThread:
    """An ISO metric thread's basic sizes (m, m²), Table A11's formulas."""

    d: float
    p: float
    d2: float  # pitch diameter
    d3: float  # minor diameter of the bolt
    ds: float  # stress diameter (d2 + d3)/2
    stress_area: float  # AS
    minor_area: float  # Ad3
    nominal_area: float  # AN


def thread(d: float, p: float) -> MetricThread:
    """The basic sizes of a 60° thread of major diameter `d` and pitch `p` (m). Unified
    threads share the profile, so their d2 and d3 come out the same way."""
    d2, d3 = d - 0.649519 * p, d - 1.226869 * p
    ds = (d2 + d3) / 2
    return MetricThread(
        d, p, d2, d3, ds, math.pi / 4 * ds**2, math.pi / 4 * d3**2, math.pi / 4 * d**2
    )


# --- grades (DIN EN ISO 898-1, as VDI 2230 uses them) -------------------------------------


@dataclass(frozen=True)
class Grade:
    label: str
    rp02_small: float  # Pa, minimum 0.2 % proof stress up to M16
    rp02_large: float  # above M16
    rm_small: float  # minimum tensile strength up to M16
    rm_large: float
    shear_ratio: float  # τB/Rm (Table 7)

    def rp02(self, d: float) -> float:
        return self.rp02_small if d <= 16 * MM + 1e-12 else self.rp02_large

    def rm(self, d: float) -> float:
        return self.rm_small if d <= 16 * MM + 1e-12 else self.rm_large


GRADES = {
    "8.8": Grade("8.8", 640e6, 660e6, 800e6, 830e6, 0.65),
    "10.9": Grade("10.9", 940e6, 940e6, 1000e6, 1000e6, 0.62),
    "12.9": Grade("12.9", 1100e6, 1100e6, 1200e6, 1200e6, 0.60),
}

# --- the bolt's resilience (5.1.1) --------------------------------------------------------

HEADS = {"hex": 0.5, "socket": 0.4}  # lSK / d (Eqs. 20, 21)


@dataclass(frozen=True)
class Cylinder:
    """One cylindrical piece of the bolt between head and engaged thread (Figure 5)."""

    length: float
    diameter: float
    bore: float = 0.0


@dataclass(frozen=True)
class BoltResilience:
    delta_s: float  # m/N, the whole bolt
    head: float  # δSK
    shank: float  # Σδi
    free_thread: float  # δGew
    engaged: float  # δG, the engaged thread
    nut: float  # δM, the nut or tapped hole
    bending_length: float  # lers, m (Eq. 35)

    @property
    def engagement(self) -> float:
        """δGM = δG + δM (Eq. 24)."""
        return self.engaged + self.nut


def engaged_resilience(
    d: float,
    d3: float,
    e_bolt: float,
    e_nut: float | None = None,
    tapped: bool = False,
    bore: float = 0.0,
) -> tuple[float, float]:
    """(δG, δM) of the engaged thread and its nut or tapped hole (Eqs. 25-30): 0.5 d at the
    minor section, and 0.4 d (a nut) or 0.33 d (tapped) at the nominal section with the
    nut's modulus."""
    ab = math.pi / 4 * bore**2
    a_d3, a_n = math.pi / 4 * d3**2 - ab, math.pi / 4 * d**2 - ab
    l_m = (0.33 if tapped else 0.4) * d
    return 0.5 * d / (e_bolt * a_d3), l_m / ((e_nut or e_bolt) * a_n)


def bolt_resilience(
    th: MetricThread,
    e_bolt: float,
    shank: list[Cylinder] = (),
    free_thread: float = 0.0,
    head: str = "hex",
    tapped: bool = False,
    e_nut: float | None = None,
    bore: float = 0.0,
) -> BoltResilience:
    """δS (Eqs. 19-31) of a bolt with these shank pieces and `free_thread` (m) of loaded
    thread outside the nut, its `head` hex or socket, into a nut or a tapped hole (whose
    modulus is `e_nut`, default the bolt's); `bore` runs through all of it."""
    if head not in HEADS:
        raise ValueError(f"head '{head}': hex or socket")
    ab, ib = math.pi / 4 * bore**2, math.pi / 64 * bore**4
    a_n, a_d3 = th.nominal_area - ab, th.minor_area - ab
    i_n, i_3 = math.pi / 64 * th.d**4 - ib, math.pi / 64 * th.d3**4 - ib
    e_m = e_nut or e_bolt
    l_sk = HEADS[head] * th.d
    l_m = (0.33 if tapped else 0.4) * th.d
    d_head = l_sk / (e_bolt * a_n)
    d_g, d_m = engaged_resilience(th.d, th.d3, e_bolt, e_m, tapped, bore)
    d_free = free_thread / (e_bolt * a_d3)
    d_shank = sum(c.length / (e_bolt * math.pi / 4 * (c.diameter**2 - c.bore**2)) for c in shank)
    b_shank = sum(c.length / (e_bolt * math.pi / 64 * (c.diameter**4 - c.bore**4)) for c in shank)
    beta = (
        l_sk / (e_bolt * i_n)
        + b_shank
        + free_thread / (e_bolt * i_3)
        + 0.5 * th.d / (e_bolt * i_3)
        + l_m / (e_m * i_n)
    )
    total = d_head + d_shank + d_free + d_g + d_m
    return BoltResilience(total, d_head, d_shank, d_free, d_g, d_m, beta * e_bolt * i_3)


# --- the clamped parts (5.1.2) -------------------------------------------------------------


@dataclass(frozen=True)
class PlateResilience:
    delta_p: float  # m/N
    tan_phi: float
    limit_diameter: float  # DA,Gr, m (Eq. 39)
    w: float  # 1 a bolted joint (two cones), 2 a tapped thread joint (one)
    shape: str  # cones, cone_and_sleeve, sleeve


def plate_resilience(
    dw: float,
    dh: float,
    da: float,
    clamp_length: float,
    e_plate: float,
    tapped: bool = False,
    da_sub: float | None = None,
) -> PlateResilience:
    """δP of the clamped parts (5.1.2): the bearing diameter `dw`, hole `dh`, the parts'
    outside diameter `da` at the interface, clamp length lK, modulus EP; tan φ by Eq. 43
    (bolted) or 42 (tapped) with βL = lK/dW, y = D'A/dW (`da_sub`, default `da`)."""
    w = 2.0 if tapped else 1.0
    lk = clamp_length
    bl, y = lk / dw, (da_sub or da) / dw
    tan_phi = (
        0.348 + 0.013 * math.log(bl) + 0.193 * math.log(y)
        if tapped
        else 0.362 + 0.032 * math.log(bl / 2) + 0.153 * math.log(y)
    )
    gr = dw + w * lk * tan_phi
    if da <= dw:
        dp, shape = 4 * lk / (e_plate * math.pi * (da**2 - dh**2)), "sleeve"
    elif da >= gr:
        dp = (
            2
            * math.log(
                (dw + dh)
                * (dw + w * lk * tan_phi - dh)
                / ((dw - dh) * (dw + w * lk * tan_phi + dh))
            )
            / (w * e_plate * math.pi * dh * tan_phi)
        )
        shape = "cones"
    else:
        dp = (
            2 / (w * dh * tan_phi) * math.log((dw + dh) * (da - dh) / ((dw - dh) * (da + dh)))
            + 4 / (da**2 - dh**2) * (lk - (da - dw) / (w * tan_phi))
        ) / (e_plate * math.pi)
        shape = "cone_and_sleeve"
    return PlateResilience(dp, tan_phi, gr, w, shape)


def moment_of_gyration(
    dw: float,
    da: float,
    limit_diameter: float,
    tan_phi: float,
    w: float,
    clamp_length: float,
    s_sym: float = 0.0,
    b: float | None = None,
    c_t: float | None = None,
) -> float:
    """IBers (m⁴, Eqs. 59-62): the cone(s), displaced by `s_sym` (Eq. 60), of height
    lV = (DA − dW)/(2 tan φ), and where DA < DA,Gr the sleeve over the rest of the clamp
    length, IH = b cT³/12 (Eq. 61) with the interface's width `b` and depth `c_t`."""
    d = min(da, limit_diameter)
    iv = 0.147 * (d - dw) * dw**3 * d**3 / (d**3 - dw**3) + s_sym**2 * math.pi / 4 * d**2
    lv = (d - dw) / (2 * tan_phi)
    lh = clamp_length - 2 * lv / w
    if lh <= 1e-12 or b is None or c_t is None:
        return clamp_length / (2 / w * lv / iv + (lh / iv if lh > 1e-12 else 0.0))
    return clamp_length / (2 / w * lv / iv + lh / (b * c_t**3 / 12))


# --- the load factor (5.3) ------------------------------------------------------------------

# Table 2: n for joint types SV 1-6 (rows lA/h 0, 0.1, 0.2, ≥ 0.3; columns ak/h 0, 0.1,
# 0.3, ≥ 0.5).
_TABLE_2 = [
    [
        [0.70, 0.55, 0.30, 0.13],
        [0.52, 0.41, 0.22, 0.10],
        [0.34, 0.28, 0.16, 0.07],
        [0.16, 0.14, 0.12, 0.04],
    ],
    [
        [0.57, 0.46, 0.30, 0.13],
        [0.44, 0.36, 0.21, 0.10],
        [0.30, 0.25, 0.16, 0.07],
        [0.16, 0.14, 0.12, 0.04],
    ],
    [
        [0.44, 0.37, 0.26, 0.12],
        [0.35, 0.30, 0.20, 0.09],
        [0.26, 0.23, 0.15, 0.07],
        [0.16, 0.14, 0.12, 0.04],
    ],
    [
        [0.42, 0.34, 0.25, 0.12],
        [0.33, 0.27, 0.16, 0.08],
        [0.23, 0.19, 0.12, 0.06],
        [0.14, 0.13, 0.10, 0.03],
    ],
    [
        [0.30, 0.25, 0.22, 0.10],
        [0.24, 0.21, 0.15, 0.07],
        [0.19, 0.17, 0.12, 0.06],
        [0.14, 0.13, 0.10, 0.03],
    ],
    [
        [0.15, 0.14, 0.14, 0.07],
        [0.13, 0.12, 0.10, 0.06],
        [0.11, 0.11, 0.09, 0.06],
        [0.10, 0.10, 0.08, 0.03],
    ],
]
_AK, _LA = [0.0, 0.1, 0.3, 0.5], [0.0, 0.1, 0.2, 0.3]


def _bracket(xs: list[float], x: float) -> tuple[int, float]:
    v = min(max(x, xs[0]), xs[-1])
    i = 0
    while i < len(xs) - 2 and v > xs[i + 1]:
        i += 1
    return i, (v - xs[i]) / (xs[i + 1] - xs[i])


def load_introduction_factor(joint_type: int, ak_over_h: float, la_over_h: float) -> float:
    """n from Table 2 for joint type SV `joint_type` (1, where the load enters near head
    and nut, to 6, at the interface), interpolated linearly in ak/h and lA/h."""
    if joint_type not in range(1, 7):
        raise ValueError("joint type: SV 1 to SV 6")
    g = _TABLE_2[joint_type - 1]
    i, fi = _bracket(_LA, la_over_h)
    j, fj = _bracket(_AK, ak_over_h)

    def row(r: int) -> float:
        return g[r][j] + (g[r][j + 1] - g[r][j]) * fj

    return row(i) + (row(i + 1) - row(i)) * fi


@dataclass(frozen=True)
class LoadFactor:
    phi_k: float  # concentric, load at the head and nut (Eq. 80)
    phi: float  # Φn = n ΦK, or Φ*en eccentric (Eqs. 83, 91, 92)
    delta_p_star: float  # δP* (Eq. 67)
    delta_p_star2: float  # δP** (Eq. 71)
    delta_pzu: float  # (w − 1) δM (Eq. 81)


def load_factor(
    delta_s: float,
    delta_p: float,
    delta_m: float,
    w: float,
    n: float = 1.0,
    clamp_length: float = 0.0,
    e_plate: float = 0.0,
    s_sym: float = 0.0,
    a: float = 0.0,
    i_bers: float | None = None,
) -> LoadFactor:
    """ΦK = (δP + δPZu)/(δS + δP), δPZu = (w − 1) δM; Φn = n ΦK; with eccentric clamping at
    `s_sym` and loading at `a` (signed by Table 4) and IBers: Φ*en = n (δP** + δPZu)/(δS + δP*)."""
    zu = (w - 1) * delta_m
    ecc = bool(i_bers) and s_sym != 0
    dps = delta_p + s_sym**2 * clamp_length / (e_plate * i_bers) if ecc else delta_p
    dpss = delta_p + a * s_sym * clamp_length / (e_plate * i_bers) if ecc else delta_p
    return LoadFactor(
        (delta_p + zu) / (delta_s + delta_p), n * (dpss + zu) / (delta_s + dps), dps, dpss, zu
    )


def opening_clamp_load(
    f_a: float, a: float, s_sym: float, u: float, a_d: float, i_bt: float, m_b: float = 0.0
) -> float:
    """R2/3: the clamp load (N) that keeps the interface from opening on one side under
    the axial load `f_a` at `a` and moment `m_b`, the bolt at `s_sym`, the edge at risk
    at `u` (signs by Table 4), the interface's area AD and moment of area IBT."""
    den = i_bt + s_sym * u * a_d
    return f_a * a_d * (a * u - s_sym * u) / den + m_b * u * a_d / den


# Table 5: embedding (µm) in the thread, per head or nut bearing area, per inner interface,
# by Rz < 10, 10-40, 40-160 µm; tension/compression and shear.
_EMBED = {
    False: [(3.0, 2.5, 1.5), (3.0, 3.0, 2.0), (3.0, 4.0, 3.0)],
    True: [(3.0, 3.0, 2.0), (3.0, 4.5, 2.5), (3.0, 6.5, 3.5)],
}


def embedding(rz: float, shear: bool = False, interfaces: int = 1, tapped: bool = False) -> float:
    """The amount of embedding fZ (m) by Table 5: the thread, each bearing face (one in a
    tapped joint, two with a nut) and each inner interface, at roughness `rz` (m)."""
    i = 0 if rz < 10e-6 else (1 if rz < 40e-6 else 2)
    t, bearing, inner = _EMBED[shear][i]
    return (t + bearing * (1 if tapped else 2) + inner * interfaces) * 1e-6


def preload_loss(f_z: float, delta_s: float, delta_p: float) -> float:
    """FZ = fZ/(δS + δP) (R4): the preload embedding takes."""
    return f_z / (delta_s + delta_p)


# --- assembly and tightening (5.4.3) --------------------------------------------------------


@dataclass(frozen=True)
class AssemblyPreload:
    stress: float  # σMzul, Pa
    force: float  # FMzul, N
    area: float  # A0, m²


def assembly_preload(
    th: MetricThread,
    rp02: float,
    mu_g: float,
    nu: float = 0.9,
    d0: float | None = None,
    bore: float = 0.0,
) -> AssemblyPreload:
    """FMzul (Eq. 144 with 145): the assembly preload using ν (0.9) of Rp0.2min at the
    smallest thread friction μG,min, on the smallest section `d0` (dS for a shank bolt, a
    reduced shank's dT) less a `bore`; the plastic torsion reserve gives the 3/2."""
    d = d0 or th.ds
    a0 = math.pi / 4 * (d**2 - bore**2)
    k = 1.5 * th.d2 * d / (d**2 + bore**2) * (th.p / (math.pi * th.d2) + 1.155 * mu_g)
    sigma = nu * rp02 / math.sqrt(1 + 3 * k**2)
    return AssemblyPreload(sigma, sigma * a0, a0)


def friction_diameter(dw: float, dh: float) -> float:
    """DKm = (dW + dh)/2 (Eqs. 128, 129): the head's or nut's mean friction diameter on a
    flat annulus from the hole (or its chamfer) to dW."""
    return (dw + dh) / 2


def tightening_torque(th: MetricThread, f_m: float, mu_g: float, mu_k: float, dkm: float) -> float:
    """MA = FM (0.16 P + 0.58 d2 μG + DKm/2 μK) (Eq. 127, R13/1), N·m."""
    return f_m * (0.16 * th.p + 0.58 * th.d2 * mu_g + dkm / 2 * mu_k)


def preload_from_torque(
    th: MetricThread, m_a: float, mu_g: float, mu_k: float, dkm: float
) -> float:
    """Eq. 127 solved for FM: the preload a torque `m_a` gives."""
    return m_a / tightening_torque(th, 1.0, mu_g, mu_k, dkm)


def nut_factor(th: MetricThread, mu_g: float, mu_k: float, dkm: float) -> float:
    """K = MA/(FM d): the short-form torque coefficient (Shigley Eq. 8-27, the
    `BoltType.nut_factor` of Puzzle's torque preload) that Eq. 127 implies."""
    return tightening_torque(th, 1.0, mu_g, mu_k, dkm) / th.d


# --- working stress and fatigue (5.5) -----------------------------------------------------


@dataclass(frozen=True)
class WorkingStress:
    axial: float  # σz, Pa
    torsion: float  # τ, Pa
    equivalent: float  # σred,B, Pa


def working_stress(
    th: MetricThread,
    f_s_max: float,
    f_m_zul: float,
    mu_g: float,
    k_tau: float = 0.5,
    d0: float | None = None,
    bore: float = 0.0,
) -> WorkingStress:
    """R8: σred,B = √(σz² + 3 (kτ τ)²), σz = FSmax/A0, τ = MG/WP, MG = FMzul d2/2 (P/(π d2)
    + 1.155 μG,min), WP = π d0³/16 (a tube π (d0⁴ − db⁴)/(16 d0)), kτ = 0.5."""
    d = d0 or th.ds
    m_g = f_m_zul * th.d2 / 2 * (th.p / (math.pi * th.d2) + 1.155 * mu_g)
    sz = f_s_max / (math.pi / 4 * (d**2 - bore**2))
    tau = m_g / (math.pi * (d**4 - bore**4) / (16 * d))
    return WorkingStress(sz, tau, math.sqrt(sz**2 + 3 * (k_tau * tau) ** 2))


def endurance_limit(
    d: float,
    rolled_after_heat_treatment: bool = False,
    fsm_over_f02: float = 0.5,
    cycles: float | None = None,
) -> float:
    """The thread's endurance limit, a stress amplitude (Pa; Eqs. 170-174): rolled before
    heat treatment σASV = 0.85 (150/d + 45) N/mm² (d in mm); rolled after, (2 − FSm/F0.2min)
    σASV for 0.3 ≤ FSm/F0.2min < 1; below 2·10⁶ cycles × (2·10⁶/N)^(1/3) rolled before,
    ^(1/6) rolled after (never below rolled before)."""
    asv = 0.85 * (150 / (d / MM) + 45) * MPA
    asg = (2 - min(max(fsm_over_f02, 0.3), 1.0)) * asv
    if cycles is None or cycles >= 2e6:
        return asg if rolled_after_heat_treatment else asv
    n = max(cycles, 1e4)
    zsv, zsg = asv * (2e6 / n) ** (1 / 3), asg * (2e6 / n) ** (1 / 6)
    return max(zsg, zsv) if rolled_after_heat_treatment else zsv


def eccentric_bolt_stress(
    th: MetricThread,
    phi: float,
    f_a: float,
    s_sym: float,
    a: float,
    clamp_length: float,
    bending_length: float,
    e_bolt: float,
    e_plate: float,
    i_bers_tilde: float,
) -> float:
    """Eq. 188: the bolt's added stress (Pa) from an eccentric axial load `f_a` at `a`, the
    bolt at `s_sym` (Table 4's signs), ĪBers = IBers − π dh⁴/64 (Eq. 64)."""
    return (
        (
            1
            + (1 / phi - s_sym / a)
            * (clamp_length / bending_length)
            * (e_bolt / e_plate)
            * (math.pi * a * th.ds**3 / (8 * i_bers_tilde))
        )
        * phi
        * f_a
        / th.stress_area
    )


# --- guide values -------------------------------------------------------------------------

# Table A8: the tightening factor αA (method, low, high).
TIGHTENING_FACTORS = [
    ("Elongation controlled, ultrasound", 1.1, 1.2),
    ("Mechanical elongation measurement", 1.1, 1.5),
    ("Hydraulic frictionless tightening", 1.1, 1.4),
    ("Yield-point or rotation-angle controlled (dimension for FMmin)", 1.2, 1.4),
    ("Torque wrench or nut runner, setpoint found by experiment on the joint", 1.4, 1.6),
    ("Torque wrench or nut runner, setpoint from estimated friction (class B)", 1.6, 2.0),
    ("Torque wrench or nut runner, setpoint from estimated friction (class A)", 1.7, 2.5),
    ("Impact wrench or by hand", 2.5, 4.0),
]

# Table A5: friction coefficient classes for μG and μK (class, low, high).
FRICTION_CLASSES = {
    "A": (0.04, 0.10),
    "B": (0.08, 0.16),
    "C": (0.14, 0.24),
    "D": (0.20, 0.35),
    "E": (0.30, 0.30),
}

# Table A9: materials of bolted parts: Rm,min, Rp0.2,min, τB,min, limiting surface pressure
# pG and E, all Pa.
PLATE_MATERIALS = {
    "S235JRG1": (340e6, 230e6, 290e6, 490e6, 205e9),
    "E295": (470e6, 270e6, 380e6, 710e6, 205e9),
    "S355J0": (490e6, 325e6, 390e6, 760e6, 205e9),
    "S315MC": (390e6, 315e6, 420e6, 540e6, 205e9),
    "S420MC": (480e6, 420e6, 370e6, 670e6, 205e9),
    "Cq 45": (700e6, 430e6, 460e6, 770e6, 205e9),
    "38MnSiVS5-BY": (900e6, 600e6, 580e6, 990e6, 205e9),
    "34CrNiMo6": (1100e6, 900e6, 720e6, 1430e6, 205e9),
    "37Cr4": (850e6, 630e6, 720e6, 1105e6, 205e9),
    "16MnCr5": (1000e6, 850e6, 850e6, 1300e6, 205e9),
    "42CrMo4": (1000e6, 750e6, 660e6, 1300e6, 205e9),
    "34CrMo4": (900e6, 650e6, 590e6, 1170e6, 205e9),
    "SINT-D30": (510e6, 370e6, 300e6, 450e6, 130e9),
    "X5CrNi18-10": (520e6, 210e6, 410e6, 630e6, 200e9),
    "X4CrNi18-12": (500e6, 220e6, 400e6, 630e6, 200e9),
    "X2CrNi18-9": (520e6, 200e6, 410e6, 630e6, 200e9),
    "X5CrNiMo17-12-2": (530e6, 220e6, 410e6, 630e6, 200e9),
    "X6NiCrTiMoVB25-15-2": (960e6, 660e6, 670e6, 1200e6, 211e9),
    "NiCr20TiAl": (1000e6, 600e6, 650e6, 1000e6, 222e9),
    "MP35N": (1580e6, 1480e6, 970e6, 1500e6, 235e9),
    "GJL-250": (250e6, 165e6, 290e6, 850e6, 110e9),
    "GJS-400": (400e6, 280e6, 360e6, 600e6, 169e9),
    "GJS-500": (500e6, 350e6, 450e6, 750e6, 169e9),
    "GJS-600": (600e6, 420e6, 540e6, 900e6, 174e9),
    "GJV-300": (300e6, 210e6, 270e6, 480e6, 137e9),
    "GJV-500": (500e6, 350e6, 450e6, 950e6, 152e9),
    "AlMgSi1 F28": (260e6, 200e6, 150e6, 325e6, 75e9),
    "AlMgSi1 F31": (290e6, 250e6, 170e6, 360e6, 75e9),
    "AlMg4.5Mn F27": (260e6, 110e6, 150e6, 325e6, 75e9),
    "AlZnMgCu1.5": (540e6, 470e6, 330e6, 540e6, 75e9),
    "G-AlSi9Cu3": (160e6, 100e6, 85e6, 200e6, 75e9),
    "GK-AlSi9Cu3": (180e6, 110e6, 95e6, 225e6, 75e9),
    "GD-AlSi9Cu3": (240e6, 140e6, 125e6, 300e6, 75e9),
    "G-AlSi7Mg0.3": (230e6, 190e6, 120e6, 290e6, 75e9),
    "GK-AlSi7Mg wa": (250e6, 200e6, 130e6, 310e6, 70e9),
    "G-AlSi10Mg": (220e6, 180e6, 115e6, 275e6, 73e9),
    "GD-AZ91 (MgAl9Zn1)": (200e6, 150e6, 120e6, 280e6, 45e9),
    "GK-AZ91-T4": (240e6, 120e6, 140e6, 290e6, 45e9),
    "GD-AS41 (MgAl4Si)": (190e6, 120e6, 120e6, 230e6, 45e9),
    "TiAl6V4": (890e6, 820e6, 600e6, 1340e6, 110e9),
}

# Table A7: how a working load reaches the joint (steps up the load column, step B).
LOAD_CASES = {
    "static_concentric": 0,
    "dynamic_concentric": 1,
    "static_eccentric": 1,
    "dynamic_eccentric": 2,
    "transverse": 4,
}
_A7_LOADS = [
    250,
    400,
    630,
    1000,
    1600,
    2500,
    4000,
    6300,
    10000,
    16000,
    25000,
    40000,
    63000,
    100000,
    160000,
    250000,
    400000,
    630000,
]
_A7_SIZES = {
    "12.9": [None, None, None, 3, 3, 3, 4, 4, 5, 6, 8, 10, 12, 16, 20, 24, 30, 36],
    "10.9": [None, None, None, 3, 3, 3, 4, 5, 6, 8, 10, 12, 14, 18, 22, 27, 33, 39],
    "8.8": [None, None, None, 3, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 30, 36, None],
}


def estimate_diameter(
    load: float, load_case: str, tightening_steps: int, grade: str
) -> tuple[float, float, float | None]:
    """Table A7: (FMmin, FMmax in N, the bolt diameter in m or None) for a working load
    `load` (N): the next column-1 load at or above it (A), raised by the load case's steps
    to FMmin (B) and by `tightening_steps` (2 a simple spindle, 1 a torque wrench, 0 angle
    or yield controlled) to FMmax (C), read in the grade's column (D)."""
    i = next((k for k, v in enumerate(_A7_LOADS) if v >= load), None)
    if i is None:
        return math.nan, math.nan, None
    last = len(_A7_LOADS) - 1
    i_min = min(i + LOAD_CASES[load_case], last)
    i_max = min(i_min + tightening_steps, last)
    d = _A7_SIZES[grade][i_max]
    return float(_A7_LOADS[i_min]), float(_A7_LOADS[i_max]), (d * MM if d else None)


# --- one joint, for agents and the CLI -----------------------------------------------------


def check(
    size: str,
    clamp_length: float,
    grade: str | None = "10.9",
    head: str = "hex",
    tapped: bool = False,
    shank_length: float = 0.0,
    e_bolt: float = 205e9,
    e_plate: float = 205e9,
    e_nut: float | None = None,
    rp02: float | None = None,
    bearing: float | None = None,
    hole: float | None = None,
    outer_diameter: float | None = None,
    mu_thread: float = 0.12,
    mu_head: float = 0.12,
    n: float = 1.0,
    axial_load: float | None = None,
    rz: float = 16e-6,
    interfaces: int = 1,
    tightening_factor: float | None = None,
) -> dict:
    """A concentric single-bolt joint by VDI 2230 (R1-R9, R13): the bolt's and parts'
    resiliences, the load factor, FMzul and its torque, embedding and, with an axial
    working load, the bolt's load, working and alternating stress; with αA the least
    preload and the residual clamp load. `size` is a `bolts.SIZES` name (M12, M12x1.25,
    1/2-13 UNC); lengths m, moduli and strengths Pa, loads N. Defaults (each named in
    `notes` when used): the bearing diameter from the size's hex dW (a socket head's from
    its table), the hole ISO 273 medium, the parts' outside diameter 3 dW, the bolt's
    shank all thread (lGew = lK − shank)."""
    from puzzle import bolts, fasteners

    found = next((s for s in bolts.SIZES if s.name == size), None)
    if found is None:
        raise ValueError(f"no bolt size '{size}' (bolt_sizes lists them)")
    if rp02 is None:
        if grade not in GRADES:
            raise ValueError(f"grade '{grade}': {', '.join(GRADES)}, or give rp02")
        rp02 = GRADES[grade].rp02(found.diameter)
    if clamp_length <= 0 or not 0 <= shank_length <= clamp_length:
        raise ValueError("clamp_length > 0, and 0 <= shank_length <= clamp_length")
    notes: list[str] = []
    th = thread(found.diameter, found.pitch)
    if bearing is None:
        part = (
            fasteners.head(head, found.diameter, found.standard.startswith("iso"))
            if head != "hex"
            else None
        )
        bearing = part.bearing if part is not None and part.bearing else found.bearing
        notes.append(
            f"bearing diameter dW {bearing * 1e3:.2f} mm from the size's "
            f"{'hex' if part is None else part.table} table"
        )
    if hole is None:
        hole = fasteners.clearance_hole(found.diameter)
        notes.append(f"hole dh {hole * 1e3:.2f} mm (ISO 273 medium)")
    if outer_diameter is None:
        outer_diameter = 3 * bearing
        notes.append("the parts' outside diameter DA not given: taken as 3 dW")
    shank = [Cylinder(shank_length, found.diameter)] if shank_length > 0 else []
    b = bolt_resilience(
        th,
        e_bolt,
        shank,
        clamp_length - shank_length,
        "socket" if head == "socket" else "hex",
        tapped,
        e_nut,
    )
    p = plate_resilience(bearing, hole, outer_diameter, clamp_length, e_plate, tapped)
    lf = load_factor(b.delta_s, p.delta_p, b.nut, p.w, n)
    fm = assembly_preload(th, rp02, mu_thread)
    dkm = friction_diameter(bearing, hole)
    torque = tightening_torque(th, fm.force, mu_thread, mu_head, dkm)
    fz = embedding(rz, interfaces=interfaces, tapped=tapped)
    loss = preload_loss(fz, b.delta_s, p.delta_p)
    out = {
        "size": found.name,
        "rp02_Pa": rp02,
        "thread": {"d2_m": th.d2, "d3_m": th.d3, "stress_area_m2": th.stress_area},
        "bearing_m": bearing,
        "hole_m": hole,
        "outer_diameter_m": outer_diameter,
        "resilience_m_per_N": {
            "bolt": b.delta_s,
            "head": b.head,
            "shank": b.shank,
            "free_thread": b.free_thread,
            "engaged_thread": b.engaged,
            "nut_or_tapped": b.nut,
            "parts": p.delta_p,
        },
        "stiffness_N_per_m": {"bolt": 1 / b.delta_s, "parts": 1 / p.delta_p},
        "cone": {"tan_phi": p.tan_phi, "limit_diameter_m": p.limit_diameter, "shape": p.shape},
        "load_factor": {"phi_k": lf.phi_k, "n": n, "phi_n": lf.phi},
        "assembly_preload_N": fm.force,
        "assembly_stress_Pa": fm.stress,
        "tightening_torque_Nm": torque,
        "nut_factor_K": torque / (fm.force * th.d),
        "embedding_m": fz,
        "preload_loss_N": loss,
        "notes": notes,
    }
    if axial_load is not None:
        fsa = lf.phi * axial_load
        ws = working_stress(th, fm.force + fsa, fm.force, mu_thread)
        amp = fsa / (2 * th.stress_area)  # a load cycling 0 to FA
        limit = endurance_limit(th.d)
        out["working"] = {
            "bolt_extra_load_N": fsa,
            "plate_relief_N": (1 - lf.phi) * axial_load,
            "bolt_max_load_N": fm.force + fsa,
            "equivalent_stress_Pa": ws.equivalent,
            "of_rp02": ws.equivalent / rp02,
            "stress_amplitude_Pa": amp,
            "endurance_limit_Pa": limit,
            "fatigue_safety": limit / amp if amp > 0 else None,
        }
        notes.append("the alternating stress assumes the load cycles 0 to FA, concentric")
    if tightening_factor is not None:
        f_min = fm.force / tightening_factor
        out["least_preload_N"] = f_min
        if axial_load is not None:
            out["residual_clamp_N"] = f_min - (1 - lf.phi) * axial_load - loss
    return out
