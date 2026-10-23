"""High-cycle fatigue (ADR 0026), ported from Tension-meter's `engineering_core/lib/stress/
fatigue.dart` and `cicada_core/lib/profiles/material_details.dart`, so both apps give the
same lives. Stress life by Shigley's Mechanical Engineering Design, 10th ed., ch. 6:

- the endurance limit Se = ka·kb·kc·kd·ke·kf·S'e and its Marin factors (Eq. 6-18);
- Neuber's notch sensitivity (Eqs. 6-34, 6-35);
- the S-N line Sf = a·N^b from 10³ to 10⁶ cycles, Sut·N^(log f / 3) below (Eqs. 6-13 to
  6-17), infinite life at or below Se;
- mean-stress corrections: the completely reversed stress as damaging as (σa, σm);
- measured S-N curves read log-log; strain life by Coffin-Manson-Basquin or Langer
  (NUREG/CR-6909); Miner's rule.

SI throughout (Pa, m, °C for the temperature factor only). Functions on stresses take
numpy arrays as well as numbers, so the fatigue tool evaluates every node at once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

KSI = 6894757.293168

# Table 6-2: ka = a·Sut^b with Sut in MPa (a polished specimen has ka = 1).
SURFACE_FINISHES = {
    "polished": ("Polished (as the test specimen)", 1.0, 0.0),
    "ground": ("Ground", 1.58, -0.085),
    "machined": ("Machined or cold-drawn", 4.51, -0.265),
    "hot_rolled": ("Hot-rolled", 57.7, -0.718),
    "forged": ("As-forged", 272.0, -0.995),
}
SIZE_BASES = {
    "rotating_round": "Round, rotating",
    "non_rotating_round": "Round, not rotating (de = 0.370d)",
    "rectangle": "Rectangle in bending (de = 0.808√(hb))",
    "axial": "Axial loading only (kb = 1)",
    "none": "No size effect (kb = 1)",
}
# Table 6-5: reliability (%), za, ke for an 8 % standard deviation of the endurance limit.
RELIABILITY_FACTORS = [
    (50, 0, 1.000),
    (90, 1.288, 0.897),
    (95, 1.645, 0.868),
    (99, 2.326, 0.814),
    (99.9, 3.091, 0.753),
    (99.99, 3.719, 0.702),
    (99.999, 4.265, 0.659),
    (99.9999, 4.753, 0.620),
]
CORRECTIONS = {
    "goodman": ("Modified Goodman", "σa / (1 − σm/Sut)"),
    "gerber": ("Gerber", "σa / (1 − (σm/Sut)²)"),
    "asme_elliptic": ("ASME-elliptic", "σa / √(1 − (σm/Sy)²)"),
    "soderberg": ("Soderberg", "σa / (1 − σm/Sy)"),
    "smith_dolan": ("Smith-Dolan", "σa·(1 + σm/Sut)/(1 − σm/Sut)"),
    "none": ("None (σm ignored)", "σa"),
}


@dataclass
class Endurance:
    se: float
    se_prime: float
    factors: dict[str, float]  # ka, kb, kc, kd, ke, kf


def endurance_limit(
    sut: float,
    *,
    measured_se_prime: float | None = None,
    steel: bool = True,
    surface: str = "machined",
    size: str = "none",
    d: float | None = None,
    h: float | None = None,
    b: float | None = None,
    temperature_c: float = 20.0,
    reliability: float = 50.0,
    kf: float = 1.0,
) -> Endurance:
    """The part's endurance limit (Eq. 6-18 with kc = 1: axial and torsional loads enter
    through the von Mises stress, Sec. 6-14)."""
    if measured_se_prime is not None:
        se_prime = measured_se_prime
    else:
        if not steel:
            raise ValueError(
                "Eq. 6-8 estimates S'e for steels only: enter a measured endurance limit"
            )
        se_prime = 0.5 * sut if sut <= 1400e6 else 700e6
    _, a, bexp = SURFACE_FINISHES[surface]
    ka = 1.0 if surface == "polished" else a * (sut / 1e6) ** bexp
    de = None
    if size in ("rotating_round", "non_rotating_round"):
        if not d or d <= 0:
            raise ValueError("enter the diameter for the size factor")
        de = d if size == "rotating_round" else 0.370 * d
    elif size == "rectangle":
        if not h or not b or h <= 0 or b <= 0:
            raise ValueError("enter h and b for the size factor")
        de = 0.808 * math.sqrt(h * b)
    if de is not None:
        mm = de * 1000
        if mm < 2.79:
            raise ValueError("Eq. 6-20 starts at 2.79 mm")
        # (d/7.62)^-0.107 as Shigley works it (Ex. 6-4).
        kb = (mm / 7.62) ** -0.107 if mm <= 51 else 1.51 * mm**-0.157
    else:
        kb = 1.0
    tf = temperature_c * 9 / 5 + 32
    if tf <= 70:
        kd = 1.0
    else:
        if tf > 1000:
            raise ValueError("Eq. 6-27 covers 70 to 1000 °F (21 to 538 °C)")
        kd = 0.975 + 0.432e-3 * tf - 0.115e-5 * tf**2 + 0.104e-8 * tf**3 - 0.595e-12 * tf**4
    ke = 1 - 0.08 * normal_quantile(reliability / 100)
    se = ka * kb * kd * ke * kf * se_prime
    return Endurance(se, se_prime, {"ka": ka, "kb": kb, "kc": 1.0, "kd": kd, "ke": ke, "kf": kf})


def normal_quantile(p: float) -> float:
    """The standard normal quantile (Acklam's rational approximation, error < 1.2e-9)."""
    if not 0 < p < 1:
        raise ValueError("reliability must be between 0 and 100 %")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]  # fmt: skip
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]  # fmt: skip
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]  # fmt: skip
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]  # fmt: skip
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        return num / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        return -normal_quantile(1 - p)
    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    return num / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def notch_sensitivity(sut: float, radius: float, torsion: bool = False) -> float:
    """Neuber's notch sensitivity for steels: q = 1/(1 + √a/√r), √a from Sut in kpsi."""
    s = sut / KSI
    root_a = (
        0.190 - 2.51e-3 * s + 1.35e-5 * s**2 - 2.67e-8 * s**3
        if torsion
        else 0.246 - 3.08e-3 * s + 1.51e-5 * s**2 - 2.67e-8 * s**3
    )
    return 1 / (1 + root_a / math.sqrt(radius / 0.0254))


def notch_factor(kt: float, q: float) -> float:
    """The fatigue stress-concentration factor Kf = 1 + q(Kt − 1) (Eq. 6-32)."""
    return 1 + q * (kt - 1)


def fatigue_fraction(sut: float) -> float:
    """f, the fraction of Sut that is the fatigue strength at 10³ cycles (Eqs. 6-10 to
    6-12, σ'F = Sut + 345 MPa, the curve of Fig. 6-18); 0.9 below 70 kpsi."""
    if sut < 70 * KSI:
        return 0.9
    sigma_f, se_prime = sut + 345e6, (0.5 * sut if sut <= 1400e6 else 700e6)
    b = -math.log(sigma_f / se_prime) / math.log(2e6)
    return sigma_f / sut * 2e3**b


def reversed_stress(correction: str, sa, sm, sut, sy):
    """The completely reversed stress as damaging as (σa, σm): the criterion's line
    through (σm, σa) read on the σa axis (Shigley Ex. 6-12). A compressive mean gets no
    benefit (σrev = σa, as Fig. 6-27 draws ductile materials); infinity where the mean
    alone reaches the criterion's limit."""
    sa, sm = np.asarray(sa, float), np.asarray(sm, float)
    sut, sy = np.asarray(sut, float), np.asarray(sy, float)
    if correction == "none":
        return sa.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = {
            "goodman": lambda: 1 - sm / sut,
            "gerber": lambda: 1 - (sm / sut) ** 2,
            "asme_elliptic": lambda: 1 - (sm / sy) ** 2,
            "soderberg": lambda: 1 - sm / sy,
            "smith_dolan": lambda: (1 - sm / sut) / (1 + sm / sut),
        }[correction]()
        out = np.where(
            ratio > 0, sa / (np.sqrt(ratio) if correction == "asme_elliptic" else ratio), np.inf
        )
    return np.where(sm <= 0, sa, out)


class SnLine:
    """A part's S-N line (Shigley Eqs. 6-13 to 6-17)."""

    def __init__(self, se: float, sut: float) -> None:
        self.se, self.sut, self.f = se, sut, fatigue_fraction(sut)
        self.a = (self.f * sut) ** 2 / se
        self.b = -math.log10(self.f * sut / se) / 3

    def cycles(self, rev):
        """Cycles to failure at a completely reversed stress: inf at or below Se, 0 at
        or above Sut."""
        rev = np.asarray(rev, float)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            low = (rev / self.sut) ** (3 / math.log10(self.f))  # below 10³ cycles
            mid = (rev / self.a) ** (1 / self.b)
        out = np.where(rev >= self.f * self.sut, low, mid)
        out = np.where(rev >= self.sut, 0.0, out)
        return np.where(rev <= self.se, np.inf, out)


def cycles_on_curve(points: list[tuple[float, float]], stress):
    """Cycles to failure at a stress amplitude on a measured S-N curve (cycles, stress),
    log-log between its points. Below the curve's lowest stress: infinite (beyond its
    last point); above its highest: the first segment extended (flagged by the caller)."""
    pts = sorted(points)
    n = np.log([p[0] for p in pts])
    s = np.log([p[1] for p in pts])
    x = np.log(np.maximum(np.asarray(stress, float), 1e-300))
    out = np.full(x.shape, np.inf)
    for i in range(len(pts) - 1):
        s0, s1 = s[i], s[i + 1]
        lo, hi = min(s0, s1), max(s0, s1)
        inside = (x >= lo) & (x <= hi) & np.isinf(out)
        t = (x - s0) / (s1 - s0) if s1 != s0 else np.ones_like(x)
        out = np.where(inside, np.exp(n[i] + t * (n[i + 1] - n[i])), out)
    if len(pts) >= 2 and s[1] != s[0]:
        above = x > s.max()
        t = (x - s[0]) / (s[1] - s[0])
        out = np.where(above, np.exp(n[0] + t * (n[1] - n[0])), out)
    return out


@dataclass
class StrainLife:
    """Coffin-Manson-Basquin (εa = σf'/E (2N)^b + εf' (2N)^c) or Langer (NUREG/CR-6909:
    ln N = A − B ln(εa% − C))."""

    model: str  # "coffin_manson_basquin" or "langer"
    fatigue_strength: float | None = None  # σf' (Pa)
    strength_exponent: float | None = None  # b
    fatigue_ductility: float | None = None  # εf'
    ductility_exponent: float | None = None  # c
    modulus: float | None = None  # E (Pa)
    langer_a: float | None = None
    langer_b: float | None = None
    langer_c: float | None = None

    def strain_amplitude(self, cycles):
        cycles = np.asarray(cycles, float)
        if self.model == "langer":
            return (self.langer_c + np.exp((self.langer_a - np.log(cycles)) / self.langer_b)) / 100
        reversals = 2 * cycles
        return (
            self.fatigue_strength / self.modulus * reversals**self.strength_exponent
            + self.fatigue_ductility * reversals**self.ductility_exponent
        )

    def cycles(self, strain_amplitude, longest: float = 1e12):
        """Cycles to failure at a strain amplitude: the model inverted by bisection on
        log N (it falls with N); inf beyond `longest`."""
        ea = np.asarray(strain_amplitude, float)
        if self.model == "langer":
            with np.errstate(invalid="ignore", divide="ignore"):
                n = np.exp(self.langer_a - self.langer_b * np.log(ea * 100 - self.langer_c))
            return np.where((ea * 100 > self.langer_c) & (n <= longest), n, np.inf)
        lo, hi = np.zeros_like(ea), np.full_like(ea, math.log(longest))
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            above = self.strain_amplitude(np.exp(mid)) > ea  # life is longer than mid
            lo, hi = np.where(above, mid, lo), np.where(above, hi, mid)
        n = np.exp(0.5 * (lo + hi))
        return np.where(self.strain_amplitude(longest) >= ea, np.inf, np.where(n <= 1.0, 1.0, n))


def miner(blocks: list[tuple[float, float]]) -> tuple[float, float | None]:
    """Miner's rule (Eq. 6-58): damage of (cycles applied, cycles to failure; inf =
    infinite life) blocks, and how many times the blocks can repeat."""
    d = sum(n / life for n, life in blocks if np.isfinite(life))
    return d, (1 / d if d > 0 else None)
