"""Standard structural sections for beams (ADR 0055): the AISC Shapes Database v16.0
and the EN 10365 / EN 10056-1 tables, shipped as JSON in SI (made by
`scripts/import_sections.py` from Lacewing Lab's tables; not edited by hand).

A `Section` gives what a beam element needs, in the section's own axes: its first axis
lies across the flanges (the strong axis's direction: EN y-y, AISC x-x), so the web
runs along its second. `I1` is the second moment about the first axis (the strong
one), `I2` about the second, `J` the torsion constant, `A1`/`A2` the shear areas for
shear along each axis (EN's Avz where tabulated; else the web's or flanges' areas).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

STANDARDS = {"aisc": "AISC (US)", "en": "EN / ISO (Europe)"}
# Shapes a beam takes so far; single angles bend about principal axes at an angle to
# their legs, which waits for its own benchmark.
SUPPORTED = {"I", "C", "T", "RHS", "CHS", "2L"}


@dataclass(frozen=True)
class Section:
    standard: str
    designation: str
    family: str
    shape: str  # I, C, T, L, 2L, RHS, CHS
    A: float
    I1: float  # m⁴, about the first axis (strong)
    I2: float  # about the second (weak)
    J: float
    A1: float  # m², shear along the first axis (the flanges)
    A2: float  # shear along the second (the web)
    mass: float | None  # kg/m
    sizes: dict = field(default_factory=dict)  # depth, width, thicknesses (m), for drawing

    @property
    def depth(self) -> float | None:
        return self.sizes.get("d") or self.sizes.get("Ht") or self.sizes.get("OD")

    @property
    def width(self) -> float | None:
        return (
            self.sizes.get("bf")
            or self.sizes.get("B")
            or self.sizes.get("b")
            or (self.sizes.get("OD"))
        )

    def describe(self) -> str:
        d, b = self.depth, self.width
        size = f"{d * 1e3:.0f} × {b * 1e3:.0f} mm, " if d and b else ""
        return (f"{self.designation}: {size}A {self.A * 1e4:.3g} cm², I {self.I1 * 1e8:.4g} / "
                f"{self.I2 * 1e8:.4g} cm⁴")  # fmt: skip


@cache
def _table(standard: str) -> dict[str, dict]:
    if standard not in STANDARDS:
        raise KeyError(f"no section standard '{standard}'")
    data = json.loads(files(__package__).joinpath(f"{standard}.json").read_text())
    return {row["designation"]: row for row in data["sections"]}


def source(standard: str) -> str:
    return json.loads(files(__package__).joinpath(f"{standard}.json").read_text())["source"]


def families(standard: str) -> list[str]:
    return list(dict.fromkeys(r["family"] for r in _table(standard).values()))


def designations(standard: str, family: str | None = None) -> list[str]:
    return [d for d, r in _table(standard).items() if family is None or r["family"] == family]


def _shear_areas(row: dict) -> tuple[float, float]:
    """(along the flanges, along the web): tabulated where the table gives one, else
    the parts' areas (Timoshenko's 5/6 is for solid rectangles, not thin walls)."""
    shape, a = row["shape"], row["A"]
    d, bf = row.get("d") or row.get("Ht") or 0.0, row.get("bf") or row.get("B") or row.get("b")
    tw, tf, t = row.get("tw"), row.get("tf"), row.get("t") or row.get("tdes")
    if shape == "CHS":
        return 2 * a / math.pi, 2 * a / math.pi
    if shape == "RHS":
        return 2 * (bf or 0.0) * t, 2 * d * t
    if shape == "2L":
        return 2 * (bf or 0.0) * t, 2 * d * t
    web = row.get("Avz") or (d * tw if tw else a / 2)
    flanges = (2 if shape in ("I", "C") else 1) * (bf or 0.0) * (tf or 0.0) or a / 2
    return min(flanges, a), min(web, a)


def get(standard: str, designation: str) -> Section:
    table = _table(standard)
    if designation not in table:
        raise KeyError(f"no {STANDARDS[standard]} section '{designation}'")
    row = table[designation]
    a1, a2 = _shear_areas(row)
    sizes = {k: row[k] for k in ("d", "Ht", "OD", "ID", "bf", "B", "b", "tw", "tf", "t", "tdes",
                                 "r", "x", "y", "eo") if k in row}  # fmt: skip
    return Section(standard, designation, row["family"], row["shape"], row["A"], row["Ix"],
                   row["Iy"], row.get("J") or 0.0, a1, a2, row.get("mass"), sizes)  # fmt: skip
