"""Every material property Puzzle knows, and what needs it (ADR 0020).

One place defines a property: its key (the name adapters read), a readable name, the
kind of unit it is in (`units.DIMENSIONS`, for display), its SI unit, whether it may
be a temperature table, and the physics that use it. `needs` says which properties a
study needs from the materials it uses; adapters may add their own
(`Adapter.material_needs`). A test checks that every property an adapter reads is
registered here.

Material models beyond single values (plasticity, orthotropic elasticity,
hyperelasticity, anisotropic conductivity) live in `Material.models`; `MODELS` names
them for people, and each adapter's capabilities list the ones it writes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PropertyDef:
    key: str
    label: str
    unit: str  # SI
    kind: str | None  # units.DIMENSIONS kind, for display; None: shown as stored
    group: str  # what it is for, as the builder groups it
    table: bool = True  # may vary with temperature
    help: str = ""


PROPERTIES: list[PropertyDef] = [
    PropertyDef("density", "Density", "kg/m^3", "density", "General"),
    PropertyDef("youngs_modulus", "Young's modulus", "Pa", "pressure", "Structural"),
    PropertyDef("poissons_ratio", "Poisson's ratio", "dimensionless", None, "Structural"),
    PropertyDef(
        "thermal_expansion",
        "Thermal expansion coefficient",
        "1/K",
        "expansion",
        "Structural",
        help="Secant (mean) coefficient from the reference temperature",
    ),
    PropertyDef(
        "thermal_conductivity", "Thermal conductivity", "W/(m*K)", "conductivity", "Thermal"
    ),
    PropertyDef("specific_heat", "Specific heat", "J/(kg*K)", "specific_heat", "Thermal"),
    PropertyDef("dynamic_viscosity", "Dynamic viscosity", "Pa*s", None, "Fluid"),
    PropertyDef(
        "relative_permittivity", "Relative permittivity", "dimensionless", None, "Electromagnetic"
    ),
    PropertyDef(
        "relative_permeability", "Relative permeability", "dimensionless", None, "Electromagnetic"
    ),
    PropertyDef(
        "electrical_conductivity", "Electrical conductivity", "S/m", None, "Electromagnetic"
    ),
]
BY_KEY = {p.key: p for p in PROPERTIES}
GROUPS = list(dict.fromkeys(p.group for p in PROPERTIES))

# The physics each material model belongs to: a solver refuses a model only for a study
# that solves its physics (a plastic steel can still be used for heat transfer).
MODEL_PHYSICS = {
    "plasticity": "solid_mechanics",
    "orthotropic_elasticity": "solid_mechanics",
    "hyperelasticity": "solid_mechanics",
    "anisotropic_conductivity": "heat_transfer",
}
# Material models (Material.models) in words.
MODELS = {
    "plasticity": "Plasticity (isotropic hardening)",
    "orthotropic_elasticity": "Orthotropic elasticity",
    "hyperelasticity": "Hyperelasticity",
    "anisotropic_conductivity": "Anisotropic thermal conductivity",
}


def needs_for(
    physics: str, steps: set[str], present: set[str] = frozenset(), thermal_strain: bool = False
) -> set[str]:
    """The properties one physics needs for these step types. `present`: the other
    physics in the component (flow with heat needs cp); `thermal_strain`: a temperature
    load or the thermal-expansion coupling."""
    need: set[str] = set()
    if physics == "solid_mechanics":
        need |= {"youngs_modulus", "poissons_ratio"}
        if steps & {"modal", "transient", "frequency_domain"}:
            need.add("density")
        if thermal_strain:
            need.add("thermal_expansion")
    elif physics == "heat_transfer":
        need.add("thermal_conductivity")
        if "transient" in steps or "fluid_flow" in present:
            need |= {"density", "specific_heat"}
    elif physics == "fluid_flow":
        need |= {"density", "dynamic_viscosity"}
    elif physics == "electrostatics":
        need.add("relative_permittivity")
    elif physics == "magnetic_fields":
        need |= {"relative_permeability", "electrical_conductivity"}
    elif physics == "electric_currents":
        need.add("electrical_conductivity")
    return need


def needs(component, study, transfers=()) -> dict[str, set[str]]:
    """Physics name -> the properties every material of the component needs for this
    study (the steps it solves, its couplings, temperature loads transferred in)."""
    present = {p.type for p in component.physics}
    thermal_strain = "thermal_expansion" in component.couplings or any(
        t.field == "temperature" and t.role == "load" for t in transfers
    )
    out: dict[str, set[str]] = {}
    for physics in present:
        steps = {s.type for s in study.solve_steps() if s.solves(physics)}
        need = needs_for(physics, steps, present, thermal_strain) if steps else set()
        if need:
            out[physics] = need
    return out


ANALYSES = {  # (physics, step type) -> how the builder names the analysis
    ("solid_mechanics", "stationary"): "Static structural",
    ("solid_mechanics", "modal"): "Modal",
    ("solid_mechanics", "transient"): "Transient structural",
    ("heat_transfer", "stationary"): "Steady heat",
    ("heat_transfer", "transient"): "Transient heat",
    ("fluid_flow", "stationary"): "Steady flow",
    ("electrostatics", "stationary"): "Electrostatics",
    ("magnetic_fields", "stationary"): "Magnetostatics",
}


def completeness(material) -> list[dict]:
    """For each solver and each analysis it runs: what this material lacks, and the
    material models that solver would refuse. What the builder shows."""
    from puzzle.adapters import available_adapters

    out = []
    for solver, cls in sorted(available_adapters().items()):
        caps = cls.capabilities
        for (physics, step), analysis in ANALYSES.items():
            if step not in caps.physics.get(physics, frozenset()):
                continue
            lacking = missing(material, needs_for(physics, {step}))
            refused = [
                m
                for m in material.models.used()
                if MODEL_PHYSICS.get(m) == physics and m not in caps.material_models
            ]
            out.append(
                {"solver": solver, "analysis": analysis, "missing": lacking, "refused": refused}
            )
    return out


def missing(material, wanted: set[str]) -> list[str]:
    """The wanted properties a material has neither as a value nor as a table."""
    return sorted(k for k in wanted if k not in material.properties and k not in material.tables)


def label(key: str) -> str:
    p = BY_KEY.get(key)
    return p.label if p else key.replace("_", " ")
