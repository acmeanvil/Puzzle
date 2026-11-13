"""A light weld's strength by AISC 360-16 Chapter J2 (ADR 0078 step 2), from its line
force at each station (`post/weld_fields`).

- Effective throat (J2.2a): a fillet's shortest distance from root to face, 0.707 w for
  equal legs, l1 l2 / √(l1² + l2²) for unequal; a PJP groove's depth of groove as given
  (Table J2.1's reductions for the process and groove angle are the user's to apply).
- Nominal strength per length (J2.4): R_n / l = F_nw t_e, F_nw = 0.60 F_EXX (Eq. J2-3,
  Table J2.5); with `directional`, F_nw = 0.60 F_EXX (1.0 + 0.50 sin^1.5 θ) (Eq. J2-5),
  θ the angle between the line force and the weld's axis, which J2.4(b) allows for
  linear welds and groups loaded in plane through their centre.
- LRFD φ = 0.75 and ASD Ω = 2.00: the utilisation is the line force over φ R_n / l, or
  over (R_n / l) / Ω, for loads factored for that method.
- F_EXX from the weld's filler (a material with a tensile strength), else the base
  metal's matching filler: E70XX for carbon and low-alloy steel; for stainless (AISC
  370-21 J2, the same F_nw, φ and Ω, its F_EXX from AWS A5.4/A5.9) E308L, E316L or
  E2209 by the base metal. Aluminium welds (the Aluminum Design Manual) are refused.
- Not checked here: a CJP groove (its base metal governs, J2.5), the base metal along
  the weld (J4), and Table J2.4's minimum sizes (they need the plates' thicknesses).
"""

from __future__ import annotations

import math

import numpy as np

PHI, OMEGA = 0.75, 2.00
# The matching filler for a base metal's group (AWS D1.1 Table 5.4's E70 for the common
# structural steels): its classification strength F_EXX.
MATCHING = {"Carbon Steel": ("E70XX", 482.6e6), "Low Alloy Steel": ("E70XX", 482.6e6)}
# AISC 370-21 J2 (ADR 0078 step 3): stainless welds, F_EXX from AWS A5.4/A5.9 (Table
# J2.5 note c); the matching filler by the base metal (Design Guide 27 Table 10-1:
# 308L for 304, 316L for 316, 2209 for the duplexes).
STAINLESS_MATCHING = (("2205", "E2209"), ("2101", "E2209"), ("2304", "E2209"),
                      ("duplex", "E2209"), ("316", "E316L"), ("", "E308L"))  # fmt: skip
STAINLESS_FEXX = {"E308L": 517.1e6, "E316L": 482.6e6, "E2209": 689.5e6}
# The Aluminum Design Manual 2015, J.2 (ADR 0078 step 6): fillet welds' weld metal at
# 0.6 (0.85 F_tuw) of its filler (Table A.3.6) on the throat, the base metal at 0.6 F_tuw
# (welded, Table A.3.3) on the leg; φ 0.75, Ω 1.95 (building-type structures).
ALUMINIUM_FILLERS = {"4043": 165e6, "5183": 275e6, "5356": 240e6, "5554": 215e6,
                     "5556": 290e6, "5654": 205e6, "1100": 75e6}  # fmt: skip
ADM_PHI, ADM_OMEGA = 0.75, 1.95
STAINLESS, ALUMINIUM = ("Stainless Steel",), ("Aluminium", "Aluminum")
ADM = "Aluminum Design Manual 2015 J.2"


def throat(weld) -> float:
    """The effective throat t_e (m)."""
    a = weld.leg.to_si()
    if weld.type == "groove":
        return a
    b = weld.leg2.to_si() if weld.leg2 is not None else a
    return a * b / math.hypot(a, b)


def directional_factor(along: np.ndarray, across: np.ndarray) -> np.ndarray:
    """1.0 + 0.50 sin^1.5 θ, θ between the line force and the weld's axis (Eq. J2-5)."""
    theta = np.arctan2(np.abs(across), np.abs(along))
    return 1.0 + 0.5 * np.sin(theta) ** 1.5


def filler(project, comp, weld) -> tuple[str, float, str]:
    """(name, F_EXX in Pa, code) of the weld's filler, or a ValueError saying why none."""
    from puzzle.model.schema import Material

    groups, bases = set(), []
    for face in weld.faces:
        part = face.split("/", 1)[0]
        for a in comp.materials:
            if a.selection.split("/", 1)[0] == part:
                m = project.objects.get(a.material)
                groups.add((getattr(m, "info", None) or {}).get("group", "") or "")
                bases.append(m)
    code = ("AISC 370-21 J2" if groups & set(STAINLESS) else ADM if groups & set(ALUMINIUM)
            else "AISC 360-16 J2")  # fmt: skip
    if weld.filler is not None:
        m = project.objects.get(weld.filler)
        if not isinstance(m, Material) or "tensile_strength" not in m.properties:
            raise ValueError(f"weld '{weld.name}': its filler has no tensile strength (F_EXX)")
        return m.name, m.properties["tensile_strength"].to_si(), code
    if groups & set(STAINLESS):
        names = " ".join((getattr(m, "name", "") or "").lower() for m in bases
                         if (getattr(m, "info", None) or {}).get("group") in STAINLESS)  # fmt: skip
        filler = next(f for key, f in STAINLESS_MATCHING if key in names)
        return filler, STAINLESS_FEXX[filler], code
    if groups & set(ALUMINIUM):
        return "ER4043", ALUMINIUM_FILLERS["4043"], ADM
    for g in groups:
        if g in MATCHING:
            name, fexx = MATCHING[g]
            return name, fexx, "AISC 360-16 J2"
    raise ValueError(f"weld '{weld.name}': no filler given and no matching one for its base "
                     f"metal ({', '.join(sorted(g for g in groups if g)) or 'none'})")  # fmt: skip


def check(weld, fexx: float, along, across, magnitude, directional: bool = False) -> dict:
    """The J2 check at each station: capacity per length (N/m) for LRFD and ASD, and the
    utilisations; the governing station's index."""
    if weld.type == "groove" and weld.groove == "cjp":
        return {"checked": False, "why": "a CJP groove weld's strength is its base metal's "
                "(AISC 360 J2.5, Table J2.5): check the base metal"}  # fmt: skip
    t_e = throat(weld)
    factor = directional_factor(np.asarray(along), np.asarray(across)) if directional else 1.0
    nominal = 0.60 * fexx * factor * t_e * np.ones(len(magnitude))
    q = np.abs(np.asarray(magnitude, float))
    lrfd, asd = q / (PHI * nominal), q / (nominal / OMEGA)
    at = int(np.argmax(lrfd))
    return {
        "checked": True,
        "throat_m": t_e,
        "nominal_N_per_m": nominal,
        "utilisation_lrfd": lrfd,
        "utilisation_asd": asd,
        "governing": at,
        "clause": "AISC 360-16 J2.4, Eq. J2-5" if directional else "AISC 360-16 J2.4, Eq. J2-3",
    }


# --- the instantaneous centre of rotation (AISC 360-10 J2.4(b), the Manual's Part 8) ----


def _cross(a, b):
    return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]


def _deformations(theta_deg, w):
    """Δ_m and Δ_u (J2.4(b)) for elements at θ (degrees) from their axes, leg w."""
    dm = 0.209 * (theta_deg + 2) ** -0.32 * w
    du = np.minimum(1.087 * (theta_deg + 6) ** -0.65 * w, 0.17 * w)
    return dm, du


def _fp(p):
    """J2-9: f(p) = [p (1.9 − 0.9p)]^0.3."""
    p = np.clip(p, 0.0, None)
    return np.maximum(p * (1.9 - 0.9 * p), 0.0) ** 0.3


def _element_forces(centre, points, axes, lengths, fexx, te, w):
    """Each element's nominal force vector (N) for rotation about `centre` (2D): normal to
    its radius, J2-8 and J2-9 with Δ_i proportioned from the critical element."""
    r = points - centre
    dist = np.linalg.norm(r, axis=1)
    dist = np.where(dist < 1e-12, 1e-12, dist)
    direction = np.stack([-r[:, 1], r[:, 0]], axis=1) / dist[:, None]  # counter-clockwise
    cos = np.abs(np.einsum("ij,ij->i", direction, axes))
    theta = np.degrees(np.arccos(np.clip(cos, 0.0, 1.0)))
    dm, du = _deformations(theta, w)
    critical = int(np.argmin(du / dist))
    delta = dist * du[critical] / dist[critical]
    stress = 0.60 * fexx * (1.0 + 0.50 * np.sin(np.radians(theta)) ** 1.5) * _fp(delta / dm)
    return stress[:, None] * te * lengths[:, None] * direction, dist


def ic_capacity(points, axes, lengths, fexx, te, w, direction, line_point) -> dict:
    """The nominal strength P_n (N) of a fillet weld group in its plane (2D, m) for a load
    along unit `direction` acting through `line_point`, by the instantaneous centre of
    rotation: the IC sought where the elements' forces balance the load; a load through
    the group's centroid along a line of symmetry falls back to the concentric case."""
    from scipy.optimize import least_squares

    points, axes = np.asarray(points, float), np.asarray(axes, float)
    lengths = np.asarray(lengths, float)
    u = np.asarray(direction, float) / np.linalg.norm(direction)
    a = np.asarray(line_point, float)
    centroid = np.average(points, axis=0, weights=lengths)
    size = float(np.max(np.linalg.norm(points - centroid, axis=1))) or 1.0
    arm = (a - centroid)[0] * u[1] - (a - centroid)[1] * u[0]  # the load's moment arm

    def concentric() -> dict:  # every element deforms alike
        cos = np.abs(axes @ u)
        theta = np.degrees(np.arccos(np.clip(cos, 0.0, 1.0)))
        dm, du = _deformations(theta, w)
        delta = float(np.min(du))
        stress = 0.60 * fexx * (1 + 0.5 * np.sin(np.radians(theta)) ** 1.5) * _fp(delta / dm)
        pn = float(np.sum(stress * te * lengths))  # every element's force along the load
        return {"Pn_N": pn, "centre": None, "method": "concentric (all elements alike)"}

    if abs(arm) < 1e-6 * size:
        return concentric()
    total_capacity = float(np.sum(0.6 * fexx * 1.5 * te * lengths))

    def state(c):
        forces, _dist = _element_forces(c, points, axes, lengths, fexx, te, w)
        moment = float(np.sum(_cross(points - c, forces)))
        lever = (a - c)[0] * u[1] - (a - c)[1] * u[0]  # P u's moment about c is P × lever
        p = -moment / lever if abs(lever) > 1e-12 else 0.0
        return (forces.sum(axis=0) + p * u) / total_capacity, p

    # First along the line through the centroid normal to the load, on the far side
    # from it (where the IC is for a group symmetric about that line): the root nearest
    # the group. Far out, the elements turn parallel and the group looks concentric,
    # which is a false root at infinity.
    from scipy.optimize import brentq

    normal = np.array([-u[1], u[0]])
    side = -np.sign(float((a - centroid) @ normal))  # away from the load's line
    along = lambda d: float(state(centroid + side * normal * d)[0] @ u)  # noqa: E731
    grid = size * np.geomspace(1e-4, 1e3, 400)
    values = [along(d) for d in grid]
    start = None
    for d0, d1, v0, v1 in zip(grid, grid[1:], values, values[1:], strict=False):
        if np.sign(v0) != np.sign(v1):
            start = brentq(along, d0, d1, xtol=1e-12 * size)
            break
    if start is None:  # the IC lies past a thousand sizes away: concentric, in effect
        return concentric()
    centre = centroid + side * normal * start
    if float(np.linalg.norm(state(centre)[0])) > 1e-6:  # not symmetric: refine in 2D
        sol = least_squares(
            lambda c: state(c)[0], centre, method="lm", xtol=1e-14, ftol=1e-14
        )  # MINPACK: no SVD (gesdd fails to load on some Windows envs)
        if np.linalg.norm(sol.x - centroid) < 100 * size:
            centre = sol.x
    residual, p = state(centre)
    return {"Pn_N": abs(float(p)), "centre": [float(v) for v in centre],
            "method": "instantaneous centre",
            "residual": float(np.linalg.norm(residual))}  # fmt: skip


def ic_moment_capacity(points, axes, lengths, fexx, te, w) -> float:
    """M_n (N·m) of a group in pure moment: rotation about its centroid."""
    points = np.asarray(points, float)
    lengths = np.asarray(lengths, float)
    centroid = np.average(points, axis=0, weights=lengths)
    forces, _dist = _element_forces(centroid, points, np.asarray(axes, float), lengths, fexx,
                                    te, w)  # fmt: skip
    return float(abs(np.sum(_cross(points - centroid, forces))))


# --- fatigue by AISC 360-16 Appendix 3 (ADR 0078 step 5) ------------------------------
# Table A-3.1's constants (C_f as the table prints it, for Eq. A-3-1's 6,900 (C_f/n)^0.333
# MPa form) and thresholds F_TH (MPa). AWS D1.1 (clause 2, nontubular) uses the same
# categories and curves. Category F is shear on a fillet's throat (Eq. A-3-2).
CATEGORIES = {"A": (25.0, 165.0), "B": (12.0, 110.0), "B'": (6.1, 83.0), "C": (4.4, 69.0),
              "D": (2.2, 48.0), "E": (1.1, 31.0), "E'": (0.39, 18.0)}  # fmt: skip


def allowable_range(category: str, cycles: float) -> float:
    """F_SR (Pa) for `cycles` stress range fluctuations: Eq. A-3-1M, or A-3-2M for F."""
    if cycles <= 0:
        raise ValueError("the number of cycles must be positive")
    if category == "F":
        return max(690.0 * (1.5 / cycles) ** 0.167, 55.0) * 1e6
    if category not in CATEGORIES:
        raise ValueError(f"no fatigue category {category!r}: A, B, B', C, D, E, E' or F")
    cf, fth = CATEGORIES[category]
    return max(6900.0 * (cf / cycles) ** 0.333, fth) * 1e6


def welded_base_strength(project, comp, weld) -> float:
    """The weakest welded F_tuw (Pa) of an aluminium weld's base metals (ADM Table A.3.3),
    from their `welded_tensile_strength`."""
    from puzzle.model.schema import Material

    values = []
    for face in weld.faces:
        part = face.split("/", 1)[0]
        for a in comp.materials:
            if a.selection.split("/", 1)[0] == part:
                m = project.objects.get(a.material)
                if isinstance(m, Material):
                    q = m.properties.get("welded_tensile_strength")
                    if q is None:
                        raise ValueError(f"weld '{weld.name}': '{m.name}' has no welded tensile "
                                         "strength (F_tuw, the ADM's Table A.3.3)")  # fmt: skip
                    values.append(q.to_si())
    if not values:
        raise ValueError(f"weld '{weld.name}': its parts have no materials")
    return min(values)


def check_aluminium(weld, filler_ftuw, base_ftuw, along, across, magnitude,
                    directional: bool = False) -> dict:  # fmt: skip
    """The ADM's J.2 fillet check at each station: the lesser of the weld metal's
    0.6 (0.85 F_tuw,filler) t_e (× 1.36 where `directional` and the line force runs
    mostly across the weld: Table J.2.2 note 1's transverse loading) and the base metal's
    0.6 F_tuw,base × leg, per length; φ 0.75, Ω 1.95."""
    if weld.type != "fillet":
        return {"checked": False, "why": "the ADM's groove welds aren't checked yet"}
    t_e = throat(weld)
    leg = min(weld.leg.to_si(), weld.leg2.to_si() if weld.leg2 is not None else weld.leg.to_si())
    along, across = np.abs(np.asarray(along, float)), np.abs(np.asarray(across, float))
    factor = np.where(directional & (across >= along), 1.36, 1.0) if directional else 1.0
    metal = 0.6 * 0.85 * filler_ftuw * t_e * factor * np.ones(len(along))
    base = 0.6 * base_ftuw * leg * np.ones(len(along))
    nominal = np.minimum(metal, base)
    q = np.abs(np.asarray(magnitude, float))
    lrfd, asd = q / (ADM_PHI * nominal), q / (nominal / ADM_OMEGA)
    at = int(np.argmax(lrfd))
    return {"checked": True, "throat_m": t_e, "nominal_N_per_m": nominal,
            "utilisation_lrfd": lrfd, "utilisation_asd": asd, "governing": at,
            "governed_by": "weld metal" if metal[at] <= base[at] else "base metal",
            "clause": "ADM 2015 J.2.2, Table J.2.2"}  # fmt: skip


# ASME BPVC VIII-2 (2023) 5.5.5 and Annex 3-F.2: welded joints by the equivalent
# structural stress range. Table 3-F.2M (SI: MPa and mm), C and h per statistical basis.
ASME_CURVES = {
    "steel": {"mean": 19930.2, "+1sigma": 23885.8, "-1sigma": 16629.7, "+2sigma": 28626.5,
              "-2sigma": 13875.7, "+3sigma": 34308.1, "-3sigma": 11577.9},
    "aluminium": {"mean": 3495.13, "+1sigma": 4293.19, "-1sigma": 2845.42, "+2sigma": 5273.48,
                  "-2sigma": 2316.48, "+3sigma": 6477.60, "-3sigma": 1885.87},
}  # fmt: skip
ASME_H = {"steel": 0.31950, "aluminium": 0.27712}
M_SS = 3.6  # Eq. (5.60)
ASME = "ASME BPVC VIII-2 5.5.5, Annex 3-F.2"


def t_ess(t_mm: float) -> float:
    """Eqs. (5.61)-(5.63): the structural stress effective thickness (mm)."""
    return min(max(t_mm, 16.0), 150.0)


def i_factor(dsm: float, dsb: float) -> float:
    """I^(1/m_ss), Eqs. (5.64) and (5.65), from the membrane and bending ranges."""
    rb = abs(dsb) / (abs(dsm) + abs(dsb)) if abs(dsm) + abs(dsb) else 0.0
    return (1.23 - 0.364 * rb - 0.17 * rb**2) / (1.007 - 0.306 * rb - 0.178 * rb**2)


def asme_cycles(dsm: float, dsb: float, t: float, *, smax: float = 0.0, smin: float = 0.0,
                sy: float | None = None, e: float = 200e9, nu: float = 0.3,
                material: str = "steel", basis: str = "-3sigma", f_i: float = 1.0,
                f_e: float = 1.0, f_mt: float = 1.0, k_css: float | None = None,
                n_css: float | None = None) -> dict:  # fmt: skip
    """The permissible cycles of one cycle's structural stress ranges (5.5.5.2 Steps 4 to
    7), in SI (Pa, m): the membrane and bending ranges `dsm`, `dsb` normal to the crack
    plane, the plate's thickness `t`, the cycle's largest and smallest structural stress
    for the mean-stress factor (Eqs. 5.66-5.68, with the yield strength `sy`).

    Step 5: Neuber's rule with the hysteresis curve Δε = Δσ/E + 2 (Δσ/2K)^(1/n) when
    `k_css` and `n_css` (Annex 3-D) are given, else elastic (Δε = Δσᵉ/E); then Eq. (5.58),
    Δσ = E/(1 - ν²) Δε. Step 6: ΔS_ess = Δσ / (t_ess^((2 - m)/2m) I^(1/m) f_M), MPa and mm.
    Step 7: N = f_I/f_E (f_MT C / ΔS_ess)^(1/h), Table 3-F.2M; the lower 99 % interval
    (-3σ) unless agreed otherwise (3-F.2.2(a))."""
    from scipy.optimize import brentq

    if material not in ASME_CURVES:
        raise ValueError(f"material: {', '.join(ASME_CURVES)}, not {material!r}")
    if basis not in ASME_CURVES[material]:
        raise ValueError(f"basis: {', '.join(ASME_CURVES[material])}, not {basis!r}")
    elastic = abs(dsm) + abs(dsb)  # Eq. (5.54), on the surface where they add
    strain_e = elastic / e  # Eq. (5.55)
    if elastic and k_css and n_css:  # Eqs. (5.56), (5.57)

        def neuber(ds):
            return ds * (ds / e + 2 * (ds / (2 * k_css)) ** (1 / n_css)) - elastic * strain_e

        ds = brentq(neuber, 1e-12 * elastic, elastic)
        strain = ds / e + 2 * (ds / (2 * k_css)) ** (1 / n_css)
    else:
        strain = strain_e
    stress = e / (1 - nu**2) * strain  # Eq. (5.58)
    f_m = 1.0
    r = smin / smax if smax else 0.0
    if sy is not None and (smax + smin) / 2 >= 0.5 * sy and r > 0 and elastic <= 2 * sy:
        f_m = (1 - r) ** (1 / M_SS)  # Eq. (5.66)
    te = t_ess(t * 1e3)
    i = i_factor(dsm, dsb)
    s_ess = stress / 1e6 / (te ** ((2 - M_SS) / (2 * M_SS)) * i * f_m)  # Eq. (5.59)
    c, h = ASME_CURVES[material][basis], ASME_H[material]
    n = f_i / f_e * (f_mt * c / s_ess) ** (1 / h) if s_ess > 0 else float("inf")
    return {"structural_stress_range_Pa": float(stress), "elastic_range_Pa": float(elastic),
            "t_ess_mm": te, "I_factor": float(i), "f_M": float(f_m),
            "equivalent_range": float(s_ess), "cycles": float(n), "basis": basis,
            "clause": ASME}  # fmt: skip
