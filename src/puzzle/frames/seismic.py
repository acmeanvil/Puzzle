"""Seismic loads on frames by ASCE 7-16 (ADR 0079 step 4): the design response spectrum
(11.4.6), the equivalent lateral force procedure (12.8), modal response spectrum
analysis (12.9.1) and story drift (12.8.6, 12.12).

Pure functions, SI with accelerations in g (as ASCE 7 tabulates S_DS and S_D1):

- `spectrum(T, sds, sd1, tl)`: S_a (g), Figure 11.4-1.
- `elf(design, weights, T)`: C_s (12.8-2 to 12.8-6), V = C_s W, the period used (the
  computed one capped at C_u T_a, Table 12.8-1, or T_a from Table 12.8-2), k, and each
  level's C_vx and F_x (12.8-11, 12.8-12).
- `cqc(responses, omegas, zeta)`: the complete quadratic combination (12.9.1.3) of each
  mode's response, ρ_ij by Der Kiureghian's formula for equal damping.
- `levels(...)`: a frame's nodes grouped into levels by elevation (the base the lowest
  held nodes) and the seismic weight at each: members' self weight half to each end,
  plus the mass cases' gravity loads.
- `drifts(levels, displacements, cd, ie, ...)`: each story's design drift Δ = C_d
  (δ_xe − δ_(x−1)e) / I_e at the levels' centres of mass, against Table 12.12-1.

The vertical axis is z; horizontal directions x and y.
"""

from __future__ import annotations

from dataclasses import dataclass, field

G = 9.80665  # m/s²

# Table 12.8-2: (C_t in SI, x) by structure type
PERIOD = {
    "steel_moment_frame": (0.0724, 0.8),
    "concrete_moment_frame": (0.0466, 0.9),
    "steel_eccentrically_braced": (0.0731, 0.75),
    "steel_buckling_restrained": (0.0731, 0.75),
    "other": (0.0488, 0.75),
}
# Table 12.12-1: Δ_a / h_sx by structure and risk category (I/II, III, IV)
DRIFT = {
    "low_rise_with_partitions": (0.025, 0.020, 0.015),
    "masonry_cantilever_shear_wall": (0.010, 0.010, 0.010),
    "other_masonry_shear_wall": (0.007, 0.007, 0.007),
    "other": (0.020, 0.015, 0.010),
}


def spectrum(t: float, sds: float, sd1: float, tl: float) -> float:
    """S_a (g) at period t (s): Figure 11.4-1, Eqs. 11.4-5 to 11.4-8."""
    ts = sd1 / sds
    t0 = 0.2 * ts
    if t < t0:
        return sds * (0.4 + 0.6 * t / t0)
    if t <= ts:
        return sds
    if t <= tl:
        return sd1 / t
    return sd1 * tl / t**2


def cu(sd1: float) -> float:
    """Table 12.8-1, interpolated."""
    table = [(0.1, 1.7), (0.15, 1.6), (0.2, 1.5), (0.3, 1.4), (0.4, 1.4)]
    if sd1 <= table[0][0]:
        return table[0][1]
    for (a, ca), (b, cb) in zip(table, table[1:], strict=False):
        if sd1 <= b:
            return ca + (cb - ca) * (sd1 - a) / (b - a)
    return 1.4


def approximate_period(structure: str, height: float) -> float:
    """T_a = C_t h_n^x (12.8-7), h_n in m."""
    ct, x = PERIOD[structure]
    return ct * height**x


@dataclass
class Elf:
    period: float  # s, used
    period_note: str
    cs: float
    cs_clause: str
    weight: float  # N
    base_shear: float  # N
    k: float
    levels: list[dict] = field(default_factory=list)  # z, weight, cvx, force, shear


def seismic_coefficient(d, t: float) -> tuple[float, str]:
    """C_s by 12.8-2, capped by 12.8-3/12.8-4, at least 12.8-5 and, where S1 ≥ 0.6 g,
    12.8-6."""
    ri = d.r / d.ie
    cs, clause = d.sds / ri, "12.8-2"
    cap = d.sd1 / (t * ri) if t <= d.tl else d.sd1 * d.tl / (t**2 * ri)
    if cap < cs:
        cs, clause = cap, "12.8-3" if t <= d.tl else "12.8-4"
    floor = max(0.044 * d.sds * d.ie, 0.01)
    if cs < floor:
        cs, clause = floor, "12.8-5"
    if d.s1 >= 0.6 and cs < 0.5 * d.s1 / ri:
        cs, clause = 0.5 * d.s1 / ri, "12.8-6"
    return cs, clause


def exponent(t: float) -> float:
    """k of 12.8-12: 1 to 0.5 s, 2 from 2.5 s, linear between."""
    return 1.0 if t <= 0.5 else 2.0 if t >= 2.5 else 1.0 + (t - 0.5) / 2.0


def period_used(d, height: float, computed: float | None) -> tuple[float, str]:
    """12.8.2: the computed period capped at C_u T_a, else T_a itself."""
    ta = approximate_period(d.structure, height)
    if d.period is not None:
        computed = d.period
    if computed is None:
        return ta, f"T_a = C_t h_n^x = {ta:.4g} s (12.8-7)"
    limit = cu(d.sd1) * ta
    if computed > limit:
        return limit, f"computed {computed:.4g} s capped at C_u T_a = {limit:.4g} s (12.8.2)"
    return computed, f"computed {computed:.4g} s (below C_u T_a = {limit:.4g} s)"


def elf(d, levels: list[dict], height: float, computed: float | None = None) -> Elf:
    """The ELF base shear and its vertical distribution over `levels` ([{z, weight}]
    above the base, heights from it)."""
    t, note = period_used(d, height, computed)
    cs, clause = seismic_coefficient(d, t)
    w = sum(lv["weight"] for lv in levels)
    v = cs * w
    k = exponent(t)
    total = sum(lv["weight"] * lv["height"] ** k for lv in levels)
    out = Elf(t, note, cs, clause, w, v, k)
    shear = v
    for lv in sorted(levels, key=lambda x: x["height"]):
        cvx = lv["weight"] * lv["height"] ** k / total if total else 0.0
        out.levels.append({**lv, "cvx": cvx, "force": cvx * v})
    for lv in out.levels:  # story shear below each level (12.8.4)
        lv["shear"] = shear
        shear -= lv["force"]
    return out


def correlation(wi: float, wj: float, zeta: float = 0.05) -> float:
    """CQC's ρ_ij for equal damping ζ (Der Kiureghian 1981)."""
    r = wj / wi
    return (8 * zeta**2 * (1 + r) * r**1.5) / ((1 - r**2) ** 2 + 4 * zeta**2 * r * (1 + r) ** 2)


def cqc(responses, omegas, zeta: float = 0.05):
    """√(Σ_i Σ_j ρ_ij R_i R_j) over the modes (axis 0 of `responses`, any shape after)."""
    import numpy as np

    r = np.asarray(responses, float)
    w = np.asarray(omegas, float)
    rho = np.array([[correlation(a, b, zeta) for b in w] for a in w])
    flat = r.reshape(len(w), -1)
    total = np.einsum("ik,ij,jk->k", flat, rho, flat)
    return np.sqrt(np.maximum(total, 0.0)).reshape(r.shape[1:])


def group_levels(z: dict[str, float], base: float, tolerance: float) -> list[list[str]]:
    """Node names grouped by elevation above `base` (within `tolerance`), lowest first."""
    above = sorted((v, n) for n, v in z.items() if v > base + tolerance)
    levels: list[list[str]] = []
    last = None
    for v, n in above:
        if last is None or v - last > tolerance:
            levels.append([n])
            last = v
        else:
            levels[-1].append(n)
    return levels


def allowable_drift(d) -> float:
    """Table 12.12-1: Δ_a / h_sx."""
    k = {"I": 0, "II": 0, "III": 1, "IV": 2}[d.risk_category]
    return DRIFT[d.drift_structure][k]


def drifts(levels: list[dict], deflection: dict[str, float], d, scale: float = 1.0) -> list[dict]:
    """Each story's design drift at the levels' centres of mass (12.8.6): δ_x = C_d δ_xe /
    I_e, Δ = δ_x − δ_(x−1), against Δ_a = ratio h_sx (Table 12.12-1). `deflection` is each
    node's elastic displacement in the direction (m); `scale` the drift scaling of
    12.9.1.4.2 where it applies."""
    ratio = allowable_drift(d)
    out, below, z_below = [], 0.0, 0.0
    for lv in sorted(levels, key=lambda x: x["height"]):
        w = [lv["node_weights"][n] for n in lv["nodes"]]
        total = sum(w) or 1.0
        de = sum(deflection.get(n, 0.0) * wi for n, wi in zip(lv["nodes"], w, strict=True))
        de /= total
        dx = d.cd * de * scale / d.ie
        hsx = lv["height"] - z_below
        drift = abs(dx - below)
        allowed = ratio * hsx
        out.append({"height_m": lv["height"], "story_height_m": hsx, "deflection_m": dx,
                    "drift_m": drift, "allowed_m": allowed, "ratio": drift / allowed,
                    "drift_ratio": drift / hsx})  # fmt: skip
        below, z_below = dx, lv["height"]
    return out
