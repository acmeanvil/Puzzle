"""Unit conversion. Everything leaves Puzzle in SI; most solvers are unitless."""

from __future__ import annotations

import math
import re
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
    # what curves give (ADR 0071): areas under them and their slopes
    "force_per_length": ("Force per length", "N/m"),
    "stress_gradient": ("Stress gradient", "Pa/m"),
    "temperature_gradient": ("Temperature gradient", "K/m"),
    "temperature_rate": ("Rate of temperature change", "K/s"),
    "impulse": ("Impulse", "N*s"),
    # a section's and a crack's (OP-14): a formula over them keeps its unit
    "second_moment": ("Second moment of area", "m^4"),
    "stress_intensity": ("Stress intensity factor", "Pa*m^0.5"),
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
            "force_per_length": "N/mm",
            "stress_gradient": "MPa/mm",
            "temperature_gradient": "K/mm",
            "second_moment": "mm^4",
            "stress_intensity": "MPa*m^0.5",
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
    "force_per_length": ["N/m", "N/mm", "kN/m", "lbf/in"],
    "stress_gradient": ["Pa/m", "MPa/mm", "MPa/m", "psi/in"],
    "temperature_gradient": ["K/m", "K/mm", "delta_degF/in"],
    "temperature_rate": ["K/s", "K/min", "K/hour"],
    "impulse": ["N*s", "kN*s", "lbf*s"],
    "second_moment": ["m^4", "mm^4", "cm^4", "in^4"],
    "stress_intensity": ["Pa*m^0.5", "MPa*m^0.5", "ksi*in^0.5"],
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


_NUMBER = re.compile(r"^\s*([-+]?(?:\d[\d.,]*|[.,]\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")


def parse_quantity(text: str, unit: str, kind: str | None = None) -> tuple[float, str]:
    """What a person typed for a quantity, as (number, unit pint reads): "5 mm", "5mm",
    "2,5 mm" (one decimal comma) or a bare "5", which is in `unit`, the unit shown
    (ADR 0024). The unit must be known and of `kind` (a key of DIMENSIONS; "any" for
    no check), or of the same dimension as `unit` when no kind is given. Raises
    ValueError in words: every form takes quantities through this (ADR 0090 package 10)."""
    raw = text.strip()
    match = _NUMBER.match(raw)
    if not raw or match is None:
        raise ValueError(f"'{raw}': type a number, then its unit (e.g. 5 {pretty(unit)})"
                         if raw else "type a number")  # fmt: skip
    number, given = match.group(1), match.group(2)
    if "," in number:  # a decimal comma, as many keypads type it; never a thousands one
        if "." in number or number.count(",") > 1:
            raise ValueError(f"'{raw}': write the number with one decimal point")
        number = number.replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        raise ValueError(f"'{raw}': '{number}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"'{raw}': the value must be finite")
    given = plain(given) if given else plain(unit)
    if not known(given):
        raise ValueError(f"'{raw}': '{given}' is not a unit Puzzle knows")
    expected = DIMENSIONS[kind][1] if kind in DIMENSIONS else (plain(unit) or None)
    if kind == "any":
        expected = None
    if expected and not same_dimension(given, expected):
        what = DIMENSIONS[kind][0].lower() if kind in DIMENSIONS else None
        what = what or (DIMENSIONS[kind_of(expected)][0].lower() if kind_of(expected) else None)
        raise ValueError(f"'{raw}': {pretty(given)} is not a "
                         f"{what or 'unit like ' + pretty(expected)}")  # fmt: skip
    return value, given


def parse_number(text: str, what: str = "") -> float:
    """A plain number as typed ("2.5", "2,5"), finite; ValueError naming `what`, never
    Python's "could not convert string to float" (PANEL-17)."""
    raw = str(text).strip()
    lead = f"{what}: " if what else ""
    if not raw:
        raise ValueError(f"{lead}type a number")
    number = raw.replace(",", ".") if raw.count(",") == 1 and "." not in raw else raw
    try:
        value = float(number)
    except ValueError:
        raise ValueError(f"{lead}'{raw}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{lead}the value must be finite")
    return value


@cache
def same_dimension(a: str, b: str) -> bool:
    """Whether two units measure the same kind of thing ("mm" and "in"; not "mm" and "kN")."""
    try:
        return registry().Unit(a).dimensionality == registry().Unit(b).dimensionality
    except Exception:  # noqa: BLE001
        return False


def plain(unit: str) -> str:
    """The inverse of `pretty`: what people type back ("mm²", "°C") as pint reads it."""
    out = unit.strip().replace("²", "^2").replace("³", "^3").replace("·", "*")
    out = out.replace("µm", "um").replace("°C", "degC").replace("°F", "degF")
    return out
