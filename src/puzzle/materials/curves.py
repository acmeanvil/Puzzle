"""Stress-strain curves for plasticity: Ramberg-Osgood and ASME VIII-2 Annex 3-D.

Ported from the maintainer's Tension-meter app (`cicada_core/profiles/stress_strain.dart`)
so both give the same curves. Stresses are in Pa, strains are fractions (not %).

- `RambergOsgood`: e = s/E + offset (s/sy)^n, n given or fitted through a second
  point (`fit_through`).
- `Asme3DCurve`: the Annex 3-D true stress - true strain model from E, yield and
  ultimate strength and the material class (Table 3-D.1).
- `true_to_engineering` / `engineering_to_true`: e = exp(et) - 1, s = st / (1 + e).
- `post_yield`: the plastic part from a start stress, as (plastic strain, stress)
  rising from (0, start): the multilinear isotropic hardening table solvers take.
- `bilinear`: yield stress and tangent modulus from the 0.2% offset yield to the end.

Solvers take the hardening table as TRUE stress against TRUE (logarithmic) plastic
strain (CalculiX *PLASTIC, Code_Aster TRACTION): a curve given in engineering terms is
converted with `engineering_to_true` before `post_yield`. Below a few per cent strain
the two differ little; beyond, the difference is the whole point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

Point = tuple[float, float]  # (strain, stress)


class Asme3DClass(Enum):
    """ASME VIII-2 Table 3-D.1 (2023): m2 coefficient, r0, proportional-limit strain,
    upper temperature (°C)."""

    FERRITIC = ("Ferritic steel", 0.60, 1.00, 2.0e-5, 480)
    STAINLESS = ("Stainless steel and nickel base alloys", 0.75, 1.00, 2.0e-5, 480)
    DUPLEX = ("Duplex stainless steel", 0.70, 0.95, 2.0e-5, 480)
    PH_NICKEL = ("Precipitation-hardening, nickel-based austenitic alloys", 1.09, 0.93, 2.0e-5, 540)
    ALUMINUM = ("Aluminum", 0.52, 0.98, 5.0e-6, 120)
    COPPER = ("Copper", 0.50, 1.00, 5.0e-6, 65)
    TITANIUM = ("Titanium and zirconium", 0.50, 0.98, 2.0e-5, 260)

    def __init__(self, label, m2_coefficient, r0, epsilon_p, temperature_limit_c):
        self.label = label
        self.m2_coefficient = m2_coefficient
        self.r0 = r0
        self.epsilon_p = epsilon_p
        self.temperature_limit_c = temperature_limit_c


@dataclass(frozen=True)
class Asme3DCurve:
    """The Annex 3-D model (eqs. 3-D.1 to 3-D.14) for one material at one temperature."""

    modulus: float
    yield_strength: float
    ultimate_strength: float
    material_class: Asme3DClass

    EPSILON_YS = 0.002

    def __post_init__(self) -> None:
        if not 0 < self.yield_strength < self.ultimate_strength:
            raise ValueError("the Annex 3-D model needs 0 < yield < ultimate strength")

    @property
    def r(self) -> float:
        return self.yield_strength / self.ultimate_strength

    @property
    def m2(self) -> float:
        return self.material_class.m2_coefficient * (self.material_class.r0 - self.r)

    @property
    def is_valid(self) -> bool:
        """The model works only when m2 > 0 (R below the class's r0)."""
        return self.m2 > 0

    @property
    def k(self) -> float:
        r = self.r
        return 1.5 * r**1.5 - 0.5 * r**2.5 - r**3.5

    @property
    def m1(self) -> float:
        ep, eys = self.material_class.epsilon_p, self.EPSILON_YS
        return (math.log(self.r) + (ep - eys)) / math.log(math.log(1 + ep) / math.log(1 + eys))

    @property
    def a1(self) -> float:
        return (
            self.yield_strength * (1 + self.EPSILON_YS) / math.log(1 + self.EPSILON_YS) ** self.m1
        )

    @property
    def a2(self) -> float:
        return self.ultimate_strength * math.exp(self.m2) / self.m2**self.m2

    @property
    def true_ultimate_stress(self) -> float:
        """True ultimate stress at the true ultimate strain (eq. 3-D.14): the curve ends
        there (beyond it the code takes the material as perfectly plastic)."""
        return self.ultimate_strength * math.exp(self.m2)

    def true_strain(self, sigma: float) -> float:
        """Total true strain at true stress `sigma` (eqs. 3-D.1, 3-D.3 to 3-D.13)."""
        sy, su, k = self.yield_strength, self.ultimate_strength, self.k
        h = 2 * (sigma - (sy + k * (su - sy))) / (k * (su - sy))
        e1 = (sigma / self.a1) ** (1 / self.m1)
        e2 = (sigma / self.a2) ** (1 / self.m2)
        g1 = e1 / 2 * (1 - math.tanh(h))
        g2 = e2 / 2 * (1 + math.tanh(h))
        plastic = g1 + g2
        # eq. 3-D.2: the plastic terms count only once they pass the proportional limit.
        return sigma / self.modulus + (plastic if plastic > self.material_class.epsilon_p else 0)

    def true_curve(self, count: int = 200) -> list[Point]:
        """True stress - true strain from zero to the true ultimate point, evenly in stress."""
        top = self.true_ultimate_stress
        return [
            (0.0 if i == 0 else self.true_strain(top * i / count), top * i / count)
            for i in range(count + 1)
        ]


@dataclass(frozen=True)
class RambergOsgood:
    """e = s/E + offset (s/sy)^n: `offset` (usually 0.002) is the plastic strain at the
    reference stress `yield_strength`; equivalently e/e0 = s/s0 + alpha (s/s0)^n with
    s0 = sy, e0 = sy/E, alpha = offset E / sy."""

    modulus: float
    yield_strength: float
    n: float
    offset: float = 0.002

    @staticmethod
    def fit_through(
        modulus: float, yield_strength: float, stress: float, strain: float, offset: float = 0.002
    ) -> RambergOsgood | None:
        """n through the offset yield and one more point (stress, total strain) on the
        curve: the usual two-point fit. None when the point can't be on such a curve."""
        plastic = strain - stress / modulus
        if plastic <= 0 or stress <= yield_strength:
            return None
        n = math.log(plastic / offset) / math.log(stress / yield_strength)
        return (
            RambergOsgood(modulus, yield_strength, n, offset)
            if math.isfinite(n) and n > 0
            else None
        )

    @property
    def alpha(self) -> float:
        return self.offset * self.modulus / self.yield_strength

    @property
    def strength_coefficient(self) -> float:
        """K' of s = K' ep^(1/n)."""
        return self.yield_strength / self.offset ** (1 / self.n)

    def plastic_strain(self, stress: float) -> float:
        return self.offset * (stress / self.yield_strength) ** self.n

    def strain(self, stress: float) -> float:
        return stress / self.modulus + self.plastic_strain(stress)

    def tangent_modulus(self, stress: float) -> float:
        """ds/de at `stress`."""
        sy = self.yield_strength
        return 1 / (1 / self.modulus + self.offset * self.n / sy * (stress / sy) ** (self.n - 1))

    def curve(self, max_stress: float, count: int = 200) -> list[Point]:
        return [
            (self.strain(max_stress * i / count), max_stress * i / count) for i in range(count + 1)
        ]


def true_to_engineering(curve: list[Point]) -> list[Point]:
    """Uniform deformation, up to the ultimate: e = exp(et) - 1, s = st / (1 + e)."""
    return [(math.exp(e) - 1, s / math.exp(e)) for e, s in curve]


def engineering_to_true(curve: list[Point]) -> list[Point]:
    """et = ln(1 + e), st = s (1 + e)."""
    return [(math.log(1 + e), s * (1 + e)) for e, s in curve]


def tangent_moduli(curve: list[Point]) -> list[float]:
    """ds/de along the curve (central differences), one per point."""
    n, out = len(curve), []
    for i in range(n):
        a, b = max(i - 1, 0), min(i + 1, n - 1)
        de = curve[b][0] - curve[a][0]
        out.append((curve[b][1] - curve[a][1]) / de if de else math.nan)
    return out


def post_yield(curve: list[Point], modulus: float, start_stress: float) -> list[Point]:
    """The plastic part from `start_stress` on: (plastic strain, stress), counted from
    that stress so the table starts at exactly (0, start_stress) and rises: what solvers
    call multilinear isotropic hardening. Start at the yield strength to take the 0.2%
    offset as the initial yield."""

    def plastic(p: Point) -> float:
        return p[0] - p[1] / modulus

    offset = 0.0
    for a, b in zip(curve, curve[1:], strict=False):
        if a[1] <= start_stress <= b[1] and b[1] > a[1]:
            f = (start_stress - a[1]) / (b[1] - a[1])
            offset = plastic(a) + f * (plastic(b) - plastic(a))
            break
    out = [(0.0, start_stress)]
    for p in curve:
        if p[1] <= start_stress:
            continue
        e = plastic(p) - offset
        if e <= out[-1][0]:
            continue
        out.append((e, p[1]))
    return out


def bilinear(curve: list[Point], modulus: float, yield_strength: float) -> tuple[float, float]:
    """(yield stress, tangent modulus) from the 0.2% offset yield point to the curve's end."""
    end = curve[-1]
    yield_strain = yield_strength / modulus + 0.002
    tangent = (end[1] - yield_strength) / (end[0] - yield_strain) if end[0] > yield_strain else 0.0
    return yield_strength, tangent


def thin(points: list[Point], count: int) -> list[Point]:
    """At most `count` points of a table, the first and last kept: solvers step through
    every point, and a few dozen describe a smooth curve well."""
    if len(points) <= count:
        return points
    step = (len(points) - 1) / (count - 1)
    return [points[round(i * step)] for i in range(count)]


def csv(curve: list[Point], strain_label: str = "Strain") -> str:
    """Strain and stress (MPa) with the tangent modulus (MPa), for spreadsheets."""
    lines = [f"{strain_label},Stress (MPa),Tangent modulus (MPa)"]
    for (e, s), et in zip(curve, tangent_moduli(curve), strict=True):
        lines.append(f"{e:.8g},{s / 1e6:.8g},{'' if math.isnan(et) else f'{et / 1e6:.8g}'}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Hardening:
    """A hardening table built from a model, with the curves it came from."""

    table: list[Point]  # (true plastic strain, true stress), from (0, yield): what solvers take
    true_curve: list[Point]  # total true strain, true stress
    engineering_curve: list[Point]  # the same points in engineering terms
    note: str  # how it was made, for the material's sources


def hardening(
    model: str,
    modulus: float,
    yield_strength: float,
    *,
    n: float | None = None,
    fit_stress: float | None = None,
    fit_strain: float | None = None,
    ultimate_strength: float | None = None,
    material_class: Asme3DClass | None = None,
    tangent_modulus: float | None = None,
    basis: str = "engineering",
    max_stress: float | None = None,
    points: int = 25,
) -> Hardening:
    """A hardening table from a model (what the builder's curve builder does).

    model: "ramberg_osgood" (n, or a fit point beyond yield), "asme_3d" (ultimate strength
    and the class), "bilinear" (the tangent modulus after yield). `basis` says whether the
    model's parameters describe the engineering or the true curve; the table is always
    true stress against true plastic strain. ValueError says what is missing or wrong.
    """
    if modulus <= 0 or yield_strength <= 0:
        raise ValueError("give Young's modulus and the yield strength")
    if model == "ramberg_osgood":
        if n is None:
            if fit_stress is None or fit_strain is None:
                raise ValueError("give the exponent n, or a second point to fit it through")
            ro = RambergOsgood.fit_through(modulus, yield_strength, fit_stress, fit_strain)
            if ro is None:
                raise ValueError("the fit point must lie beyond yield, with plastic strain")
        else:
            ro = RambergOsgood(modulus, yield_strength, n)
        top = max_stress or ultimate_strength or fit_stress or 1.5 * yield_strength
        curve = ro.curve(top, 200)
        true = engineering_to_true(curve) if basis == "engineering" else curve
        note = f"Ramberg-Osgood, n = {ro.n:.4g}, offset 0.2 %, {basis} parameters"
    elif model == "asme_3d":
        if ultimate_strength is None or material_class is None:
            raise ValueError("the Annex 3-D model needs the ultimate strength and the class")
        c = Asme3DCurve(modulus, yield_strength, ultimate_strength, material_class)
        if not c.is_valid:
            raise ValueError(
                f"the Annex 3-D model needs yield / ultimate below {c.material_class.r0} "
                "for this class"
            )
        true = c.true_curve(200)  # the model is a true curve already
        note = f"ASME VIII-2 Annex 3-D, {material_class.label}"
    elif model == "bilinear":
        if tangent_modulus is None or not 0 <= tangent_modulus < modulus:
            raise ValueError("give a tangent modulus between 0 and Young's modulus")
        plastic_modulus = modulus * tangent_modulus / (modulus - tangent_modulus)
        end = 0.2
        sy = (
            yield_strength * (1 + yield_strength / modulus)
            if basis == "engineering"
            else yield_strength
        )
        table = [(0.0, sy), (end, sy + plastic_modulus * end)]
        true = [(0.0, 0.0), (sy / modulus, sy), (end + table[1][1] / modulus, table[1][1])]
        return Hardening(
            table,
            true,
            true_to_engineering(true),
            f"Bilinear, tangent modulus {tangent_modulus / 1e6:.4g} MPa",
        )
    else:
        raise ValueError(f"no curve model '{model}'")
    start = next((s for e, s in true if e - s / modulus >= 0.002 - 1e-12), yield_strength)
    start = min(start, true[-1][1])
    table = post_yield(true, modulus, start_stress=start)
    if len(table) < 2:
        raise ValueError("the curve ends before it yields: raise its end stress")
    return Hardening(thin(table, points), true, true_to_engineering(true), note)
