"""Puzzle's Code_Saturne case builder. Runs with Code_Saturne's Python, not in Puzzle.

    python3 cs_setup.py        (reads case.json; writes setup.xml)

Uses Code_Saturne's own case model (the code behind its GUI), so setup.xml is valid
for the installed version. case.json:
    {"mesh": "mesh.msh",
     "density": 998.2, "viscosity": 1.002e-3,
     "iterations": 500,
     "zones": [{"label": "Inlet", "selection": "2", "nature": "inlet", "velocity": 0.001},
               {"label": "Outlet", "selection": "3", "nature": "outlet", "pressure": 0.0}, ...],
     "turbulence": "off" | "k-epsilon-PL" | "k-omega-SST",
     "thermal": null | {"conductivity": k, "specific_heat": cp}}
Inlet zones may add temperature, intensity (percent) and hydraulic_diameter; walls
temperature, heat_flux (W/m^2 into the fluid) or exchange [h, T_ext].
Selections are Gmsh physical group numbers: Code_Saturne's Gmsh reader names groups
by number, not by name.
"""

import json
import os

from code_saturne.base import cs_package
from code_saturne.model.Boundary import Boundary
from code_saturne.model.FluidCharacteristicsModel import FluidCharacteristicsModel
from code_saturne.model.LocalizationModel import LocalizationModel, Zone
from code_saturne.model.NumericalParamEquationModel import NumericalParamEquationModel
from code_saturne.model.SolutionDomainModel import SolutionDomainModel
from code_saturne.model.ThermalScalarModel import ThermalScalarModel
from code_saturne.model.TimeStepModel import TimeStepModel
from code_saturne.model.TurbulenceModel import TurbulenceModel
from code_saturne.model.XMLengine import Case
from code_saturne.model.XMLinitialize import XMLinit

# Code_Saturne's imposed boundary flux is positive leaving the fluid; Puzzle's heat_flux
# is positive into it (checked by the heated-channel benchmark's energy balance).
FLUX_SIGN = -1.0


def main() -> None:
    with open("case.json") as f:
        spec = json.load(f)
    case = Case(package=cs_package.package(), file_name="")
    case["xmlfile"] = "setup.xml"
    XMLinit(case).initialize()

    SolutionDomainModel(case).addMesh((spec["mesh"], os.getcwd()))
    TurbulenceModel(case).setTurbulenceModel(spec.get("turbulence", "off"))
    thermal = spec.get("thermal")
    if thermal:
        ThermalScalarModel(case).setThermalModel("temperature_kelvin")
    fluid = FluidCharacteristicsModel(case)
    fluid.setInitialValue("density", spec["density"])
    fluid.setInitialValue("molecular_viscosity", spec["viscosity"])
    if thermal:
        fluid.setInitialValue("specific_heat", thermal["specific_heat"])
        fluid.setInitialValue("thermal_conductivity", thermal["conductivity"])
        # Gauss-Seidel leaves the long axial conduction mode barely touched each steady
        # iteration (convergence took thousands); multigrid damps it directly.
        NumericalParamEquationModel(case).setSolverChoice("temperature", "multigrid")
    steps = TimeStepModel(case)
    if spec.get("pseudo_time_step"):
        # March in time with a large constant step towards the steady state: much faster
        # than the steady algorithm when a slow diffusive mode (axial conduction) remains.
        steps.setTimePassing(0)
        steps.setTimeStep(spec["pseudo_time_step"])
    else:
        steps.setTimePassing(-1)  # steady (SIMPLE)
    steps.setStopCriterion("iterations", int(spec["iterations"]))

    zones = LocalizationModel("BoundaryZone", case)
    for z in spec["zones"]:
        zones.addZone(
            Zone("BoundaryZone", label=z["label"], localization=z["selection"], nature=z["nature"])
        )
    for z in spec["zones"]:
        boundary = Boundary(z["nature"], z["label"], case)
        if z["nature"] == "inlet":
            boundary.setVelocityChoice("norm")
            boundary.setVelocity(z["velocity"])
            if "hydraulic_diameter" in z:
                boundary.setTurbulenceChoice("turbulent_intensity")
                boundary.setHydraulicDiameter(z["hydraulic_diameter"])
                boundary.setTurbulentIntensity(z["intensity"])
        elif z["nature"] == "outlet" and z.get("pressure"):
            boundary.setReferencePressure(z["pressure"])
        if not thermal:
            continue
        if "temperature" in z:
            boundary.setScalarChoice("temperature", "dirichlet")
            boundary.setScalarValue("temperature", "dirichlet", z["temperature"])
        elif "heat_flux" in z:
            boundary.setScalarChoice("temperature", "neumann")
            boundary.setScalarValue("temperature", "neumann", FLUX_SIGN * z["heat_flux"])
        elif "exchange" in z:
            h, t_ext = z["exchange"]
            boundary.setScalarChoice("temperature", "exchange_coefficient")
            boundary.setScalarValue("temperature", "dirichlet", t_ext)
            boundary.setScalarValue("temperature", "exchange_coefficient", h)
    case.xmlSaveDocument()


if __name__ == "__main__":
    main()
