"""Puzzle's Code_Saturne case builder. Runs with Code_Saturne's Python, not in Puzzle.

    python3 cs_setup.py        (reads case.json; writes setup.xml)

Uses Code_Saturne's own case model (the code behind its GUI), so setup.xml is valid
for the installed version. case.json:
    {"mesh": "mesh.msh",
     "density": 998.2, "viscosity": 1.002e-3,
     "iterations": 500,
     "zones": [{"label": "Inlet", "selection": "2", "nature": "inlet", "velocity": 0.001},
               {"label": "Outlet", "selection": "3", "nature": "outlet", "pressure": 0.0}, ...]}
Selections are Gmsh physical group numbers: Code_Saturne's Gmsh reader names groups
by number, not by name.
"""

import json
import os

from code_saturne.base import cs_package
from code_saturne.model.Boundary import Boundary
from code_saturne.model.FluidCharacteristicsModel import FluidCharacteristicsModel
from code_saturne.model.LocalizationModel import LocalizationModel, Zone
from code_saturne.model.SolutionDomainModel import SolutionDomainModel
from code_saturne.model.TimeStepModel import TimeStepModel
from code_saturne.model.TurbulenceModel import TurbulenceModel
from code_saturne.model.XMLengine import Case
from code_saturne.model.XMLinitialize import XMLinit


def main() -> None:
    with open("case.json") as f:
        spec = json.load(f)
    case = Case(package=cs_package.package(), file_name="")
    case["xmlfile"] = "setup.xml"
    XMLinit(case).initialize()

    SolutionDomainModel(case).addMesh((spec["mesh"], os.getcwd()))
    TurbulenceModel(case).setTurbulenceModel("off")  # laminar
    fluid = FluidCharacteristicsModel(case)
    fluid.setInitialValue("density", spec["density"])
    fluid.setInitialValue("molecular_viscosity", spec["viscosity"])
    steps = TimeStepModel(case)
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
        elif z["nature"] == "outlet" and z.get("pressure"):
            boundary.setReferencePressure(z["pressure"])
    case.xmlSaveDocument()


if __name__ == "__main__":
    main()
