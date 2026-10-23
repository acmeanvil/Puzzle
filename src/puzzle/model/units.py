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


@cache
def known(unit: str) -> bool:
    """Whether pint can read the unit ("mm", "W/(m^2*K)", "degC", "dimensionless")."""
    try:
        registry().Unit(unit)
    except Exception:  # noqa: BLE001 - pint raises several kinds for a bad unit
        return False
    return True


# --- display units (ADR 0024): what people see; values stay SI inside -------------------

# Quantity kinds people see, each with its SI unit.
DIMENSIONS: dict[str, tuple[str, str]] = {
    "length": ("Length", "m"),
    "area": ("Area", "m^2"),
    "volume": ("Volume", "m^3"),
    "mass": ("Mass", "kg"),
    "density": ("Density", "kg/m^3"),
    "force": ("Force", "N"),
    "pressure": ("Pressure and stress", "Pa"),
    "energy": ("Energy", "J"),
    "power": ("Power", "W"),
    "temperature": ("Temperature", "K"),
    "time": ("Time", "s"),
    "velocity": ("Velocity", "m/s"),
    "heat_flux": ("Heat flux", "W/m^2"),
    "conductivity": ("Thermal conductivity", "W/(m*K)"),
    "specific_heat": ("Specific heat", "J/(kg*K)"),
    "film": ("Heat transfer coefficient", "W/(m^2*K)"),
    "expansion": ("Thermal expansion", "1/K"),
}
# Unit systems: kind -> unit (kinds left out show in SI).
SYSTEMS: dict[str, tuple[str, dict[str, str]]] = {
    "si": ("SI (m, kg, s, Pa, K)", {}),
    "engineering": (
        "SI with mm, MPa and °C (engineering)",
        {
            "length": "mm",
            "area": "mm^2",
            "volume": "mm^3",
            "pressure": "MPa",
            "temperature": "degC",
        },
    ),
    "consistent_mm": (
        "SI consistent in mm, N, MPa, tonne (as many solvers take it)",
        {
            "length": "mm",
            "area": "mm^2",
            "volume": "mm^3",
            "mass": "tonne",
            "density": "tonne/mm^3",
            "pressure": "MPa",
            "energy": "mJ",
            "power": "mW",
            "velocity": "mm/s",
            "heat_flux": "mW/mm^2",
            "conductivity": "mW/(mm*K)",
            "specific_heat": "mJ/(tonne*K)",
            "film": "mW/(mm^2*K)",
        },
    ),
    "imperial": (
        "Imperial (in, lb, psi, °F)",
        {
            "length": "in",
            "area": "in^2",
            "volume": "in^3",
            "mass": "lb",
            "density": "lb/in^3",
            "force": "lbf",
            "pressure": "psi",
            "energy": "ft*lbf",
            "power": "BTU/hour",
            "temperature": "degF",
            "velocity": "in/s",
            "heat_flux": "BTU/(hour*ft^2)",
            "conductivity": "BTU/(hour*ft*delta_degF)",
            "specific_heat": "BTU/(lb*delta_degF)",
            "film": "BTU/(hour*ft^2*delta_degF)",
            "expansion": "1/delta_degF",
        },
    ),
}
# The units offered per kind when the user picks their own.
CHOICES: dict[str, list[str]] = {
    "length": ["m", "mm", "cm", "um", "in", "ft"],
    "area": ["m^2", "mm^2", "cm^2", "in^2", "ft^2"],
    "volume": ["m^3", "mm^3", "cm^3", "L", "in^3", "ft^3"],
    "mass": ["kg", "g", "tonne", "lb"],
    "density": ["kg/m^3", "g/cm^3", "tonne/mm^3", "lb/in^3", "lb/ft^3"],
    "force": ["N", "kN", "lbf", "kip"],
    "pressure": ["Pa", "kPa", "MPa", "GPa", "bar", "psi", "ksi"],
    "energy": ["J", "kJ", "mJ", "ft*lbf", "BTU"],
    "power": ["W", "kW", "mW", "BTU/hour"],
    "temperature": ["K", "degC", "degF"],
    "time": ["s", "ms", "min", "hour"],
    "velocity": ["m/s", "mm/s", "km/hour", "in/s", "ft/s"],
    "heat_flux": ["W/m^2", "mW/mm^2", "BTU/(hour*ft^2)"],
    "conductivity": ["W/(m*K)", "mW/(mm*K)", "BTU/(hour*ft*delta_degF)"],
    "specific_heat": ["J/(kg*K)", "kJ/(kg*K)", "mJ/(tonne*K)", "BTU/(lb*delta_degF)"],
    "film": ["W/(m^2*K)", "mW/(mm^2*K)", "BTU/(hour*ft^2*delta_degF)"],
    "expansion": ["1/K", "1/delta_degF"],
}


@cache
def kind_of(unit: str) -> str | None:
    """Which kind of quantity a unit measures ("mm" -> "length"), or None."""
    try:
        dim = registry().Unit(unit).dimensionality
    except Exception:  # noqa: BLE001
        return None
    for kind, (_, si) in DIMENSIONS.items():
        if registry().Unit(si).dimensionality == dim:
            return kind
    return None


def convert(value: float, unit: str, target: str) -> float:
    """`value` in `unit` expressed in `target` (offset temperatures handled)."""
    if unit == target:
        return value
    return float(registry().Quantity(value, unit).to(target).magnitude)


def shown(value: float, unit: str, units: dict[str, str]) -> tuple[float, str]:
    """A value as it is displayed: in the display unit of its kind, if there is one."""
    kind = kind_of(unit) if unit and unit != "dimensionless" else None
    target = units.get(kind) if kind else None
    if not target:
        return value, unit
    try:
        return convert(value, unit, target), target
    except Exception:  # noqa: BLE001 - not convertible after all: show as stored
        return value, unit


def pretty(unit: str) -> str:
    """A unit as people write it: "mm^2" -> "mm²", "degC" -> "°C", "W/(m*K)" -> "W/(m·K)"."""
    out = unit.replace("delta_degF", "°F").replace("degC", "°C").replace("degF", "°F")
    out = out.replace("^2", "²").replace("^3", "³").replace("*", "·").replace("um", "µm")
    return out.replace("hour", "h")


def system_units(system: str, custom: dict[str, str] | None = None) -> dict[str, str]:
    """Kind -> display unit for a system: SI for every kind, then the system's own (or,
    for "custom", the user's choice per kind)."""
    units = {kind: si for kind, (_, si) in DIMENSIONS.items()}
    if system == "custom":
        units.update({k: v for k, v in (custom or {}).items() if k in units and known(v)})
    else:
        units.update(SYSTEMS.get(system, SYSTEMS["si"])[1])
    return units


def plain(unit: str) -> str:
    """The inverse of `pretty`: what people type back ("mm²", "°C") as pint reads it."""
    out = unit.strip().replace("²", "^2").replace("³", "^3").replace("·", "*")
    out = out.replace("µm", "um").replace("°C", "degC").replace("°F", "degF")
    return out
