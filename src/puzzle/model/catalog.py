"""What each physics offers: its feature types, their values and units (for menus).

The adapters read these value names (`values["T"]`, `values["h"]`, ...); this table
is the one place a client (the GUI's add menus, an agent) learns them. Defaults are
placeholders for the user to change, in the units shown; a feature's `entity` says
what it is applied to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puzzle.model.schema import Feature, Quantity


@dataclass(frozen=True)
class FeatureSpec:
    type: str
    label: str
    entity: str = "face"  # "face" or "body"
    values: dict[str, tuple[float, str]] = field(default_factory=dict)  # name -> (value, unit)

    def feature(self, selection: str) -> Feature:
        values = {k: Quantity(value=v, unit=u) for k, (v, u) in self.values.items()}
        return Feature(type=self.type, selection=selection, values=values)


XYZ_FORCE = {"x": (0.0, "N"), "y": (0.0, "N"), "z": (0.0, "N")}
XYZ_DISP = {"x": (0.0, "mm"), "y": (0.0, "mm"), "z": (0.0, "mm")}

CATALOG: dict[str, list[FeatureSpec]] = {
    "solid_mechanics": [
        FeatureSpec("fixed", "Fixed support"),
        FeatureSpec("displacement", "Prescribed displacement", values=XYZ_DISP),
        FeatureSpec("force", "Force (total over the face)", values=XYZ_FORCE),
        FeatureSpec("pressure", "Pressure", values={"p": (0.0, "MPa")}),
    ],
    "heat_transfer": [
        FeatureSpec("temperature", "Temperature", values={"T": (20.0, "degC")}),
        FeatureSpec("heat_flux", "Heat flux (into the body)", values={"q": (0.0, "W/m^2")}),
        FeatureSpec(
            "convection",
            "Convection",
            values={"h": (10.0, "W/(m^2*K)"), "T_inf": (20.0, "degC")},
        ),
    ],
    "electrostatics": [
        FeatureSpec("electric_potential", "Electric potential", values={"V": (0.0, "V")}),
        FeatureSpec("ground", "Ground"),
        FeatureSpec("surface_charge", "Surface charge", values={"rho_s": (0.0, "C/m^2")}),
    ],
    "magnetic_fields": [
        FeatureSpec("coil_current", "Coil current", entity="body", values={"I": (1.0, "A")}),
        FeatureSpec("coil_start", "Coil current enters"),
        FeatureSpec("coil_end", "Coil current leaves"),
        FeatureSpec("magnetic_insulation", "Magnetic insulation"),
    ],
    "fluid_flow": [
        FeatureSpec("velocity_inlet", "Velocity inlet", values={"U": (1.0, "m/s")}),
        FeatureSpec("pressure_outlet", "Pressure outlet", values={"p": (0.0, "Pa")}),
        FeatureSpec("wall", "Wall (no slip)"),
        FeatureSpec("symmetry", "Symmetry"),
        FeatureSpec("mass_flow_inlet", "Mass flow inlet", values={"m_dot": (0.0, "kg/s")}),
    ],
    "custom_pde": [
        FeatureSpec("dirichlet", "Fixed value", values={"value": (0.0, "dimensionless")}),
        FeatureSpec("neumann", "Flux into the body", values={"value": (0.0, "dimensionless")}),
    ],
}

STEP_TYPES = ["stationary", "transient", "modal"]


def specs(physics: str) -> list[FeatureSpec]:
    return CATALOG.get(physics, [])


def spec(physics: str, feature: str) -> FeatureSpec | None:
    return next((s for s in specs(physics) if s.type == feature), None)
