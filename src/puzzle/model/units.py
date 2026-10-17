"""Unit conversion. Everything leaves Puzzle in SI; most solvers are unitless."""

from __future__ import annotations

from functools import cache

import pint


@cache
def registry() -> pint.UnitRegistry:
    return pint.UnitRegistry()


def to_si(value: float, unit: str) -> float:
    """Convert `value` in `unit` to base SI magnitude, e.g. (2, "mm") -> 0.002."""
    return float(registry().Quantity(value, unit).to_base_units().magnitude)
