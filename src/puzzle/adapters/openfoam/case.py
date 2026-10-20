"""Prepare an OpenFOAM case (simpleFoam) and read its results.

The case is written as files and an `Allrun` script: gmshToFoam converts mesh.msh
(Gmsh 2.2, one physical surface per boundary selection), foamDictionary sets each
patch's type (wall, symmetry), simpleFoam solves, and postProcess writes cell centres
so results map back to Puzzle's elements.

Features and values follow the Code_Saturne adapter, so one model runs on both:
velocity_inlet U (normal inflow speed, m/s; for turbulent flow intensity, a fraction,
default 0.05, and hydraulic_diameter, default 4A/P); pressure_outlet p (Pa, default 0);
wall (no slip); symmetry. Boundary faces outside every selection are walls. Heat
transfer (with fluid_flow) transports temperature as a passive scalar: temperature T on
an inlet or wall, heat_flux q (W/m^2 into the fluid) on a wall, other walls adiabatic.
fluid_flow settings: turbulence_model = laminar (default), k-epsilon or k-omega-SST,
and turbulent_prandtl (default 0.85). Step setting: iterations (SIMPLE, default 2000;
simpleFoam stops earlier once the residuals fall below 1e-6).

Results are per cell. OpenFOAM's pressure is kinematic (p / rho); it is stored in Pa,
and the turbulent viscosity as a dynamic viscosity (Pa s), as Code_Saturne reports it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.adapters.common import (
    FLUID,
    THERMAL,
    InputError,
    check_contacts,
    hydraulic_diameter,
    match_points,
)
from puzzle.adapters.openfoam.foam import latest_time, read_internal_field, write_dict
from puzzle.mesh.med import write_med
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project, Study
from puzzle.results.model import Field, Results, StepResult

TURBULENCE = {"laminar": None, "k-epsilon": "kEpsilon", "k-omega-SST": "kOmegaSST"}
PATCH_TYPES = {"wall": "wall", "symmetry": "symmetry"}
CMU = 0.09
# Puzzle field name, components, and the factor to SI (filled in per case: rho).
FIELDS = {
    "U": ("velocity", ["x", "y", "z"]),
    "p": ("pressure", [""]),
    "T": ("temperature", [""]),
    "k": ("turbulent_kinetic_energy", [""]),
    "epsilon": ("turbulent_dissipation", [""]),
    "omega": ("specific_dissipation", [""]),
    "nut": ("turbulent_viscosity", [""]),
}
SCALED = {"p", "nut"}  # kinematic in OpenFOAM: times density


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
    comp = project.get(study.component, Component)
    check_contacts(comp, set(), "OpenFOAM")
    steps = study.solve_steps()
    if len(steps) != 1 or steps[0].type != "stationary":
        raise InputError("the OpenFOAM adapter runs one stationary solve step for now")
    solved = set(steps[0].physics) or {p.type for p in comp.physics}
    if solved not in ({"fluid_flow"}, {"fluid_flow", "heat_transfer"}):
        raise InputError(
            "the OpenFOAM adapter solves fluid_flow, with or without heat_transfer, "
            f"not {sorted(solved)}"
        )
    heat = "heat_transfer" in solved
    if any(
        b.element not in ("tet4", "hex8", "prism6", "pyramid5") for b in mesh.blocks if b.dim == 3
    ):
        raise InputError("OpenFOAM needs a first-order mesh: set element_order = 1")
    if len(comp.materials) != 1:
        raise InputError("the OpenFOAM adapter needs exactly one fluid material for now")
    props = {
        k: q.to_si()
        for k, q in project.get(comp.materials[0].material, Material).properties.items()
    }
    needed = ["density", "dynamic_viscosity"] + (
        ["thermal_conductivity", "specific_heat"] if heat else []
    )
    missing = [k for k in needed if k not in props]
    if missing:
        raise InputError(f"the fluid material is missing {', '.join(missing)}")
    rho, nu = props["density"], props["dynamic_viscosity"] / props["density"]

    flow = next(p for p in comp.physics if p.type == "fluid_flow")
    features = [f for f in flow.features if f.type in FLUID]
    unsupported = [f.type for f in features if f.type == "mass_flow_inlet"]
    if unsupported:
        raise InputError("the OpenFOAM adapter does not support ['mass_flow_inlet'] yet")
    model = str(flow.settings.get("turbulence_model", "laminar"))
    if model not in TURBULENCE:
        raise InputError(
            f"unknown turbulence_model '{model}'",
            code="invalid",
            details={"models": sorted(TURBULENCE)},
        )
    turbulent = TURBULENCE[model] is not None
    thermal = [
        f
        for p in comp.physics
        if p.type == "heat_transfer" and heat
        for f in p.features
        if f.type in THERMAL
    ]
    for g in [comp.materials[0].selection] + [f.selection for f in features]:
        if g not in mesh.groups:
            raise InputError(f"no selection '{g}' in the mesh")

    patches: dict[str, dict] = {}
    for f in features:
        v = {k: q.to_si() for k, q in f.values.items()}
        patch = {"kind": f.type}
        if f.type == "velocity_inlet":
            patch["U"] = v["U"]
            if turbulent:
                patch["intensity"] = v.get("intensity", 0.05)
                patch["mixing_length"] = 0.07 * (
                    v.get("hydraulic_diameter")
                    or hydraulic_diameter(mesh.groups[f.selection], mesh)
                )
        elif f.type == "pressure_outlet":
            patch["p"] = v.get("p", 0.0)
        patches[f.selection] = patch
    for f in thermal:
        patch = patches.get(f.selection)
        if patch is None or patch["kind"] not in ("velocity_inlet", "wall"):
            raise InputError(
                f"{f.type} on '{f.selection}' needs an inlet or wall there in fluid_flow"
            )
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "temperature":
            patch["T"] = v["T"]
        elif patch["kind"] != "wall":
            raise InputError(f"{f.type} applies to walls, not the inlet '{f.selection}'")
        elif f.type == "heat_flux":
            patch["q"] = v["q"]
        else:
            raise InputError("the OpenFOAM adapter does not support convection walls yet")

    workdir.mkdir(parents=True, exist_ok=True)
    names = {s: _label(s) for s in patches}
    write_med(
        mesh, workdir / "mesh.msh", {comp.materials[0].selection: "fluid", **names}, msh_version=2.2
    )

    inlets = [p for p in patches.values() if p["kind"] == "velocity_inlet"]
    speed = max((p["U"] for p in inlets), default=0.0)
    k0, eps0, omega0 = _turbulence_state(speed, inlets)
    props_t = {"T": (props["thermal_conductivity"], props["specific_heat"])} if heat else {}
    _fields(
        workdir,
        patches,
        names,
        rho,
        props.get("thermal_conductivity", 1.0),
        turbulent,
        k0,
        eps0,
        omega0,
        heat,
    )
    _system(workdir, steps[0], turbulent, heat, nu, props_t, rho, flow.settings)
    write_dict(
        workdir / "constant" / "transportProperties", {"transportModel": "Newtonian", "nu": nu}
    )
    write_dict(
        workdir / "constant" / "turbulenceProperties",
        {"simulationType": "laminar"}
        if not turbulent
        else {
            "simulationType": "RAS",
            "RAS": {"RASModel": TURBULENCE[model], "turbulence": True, "printCoeffs": True},
        },
    )
    retype = "".join(
        f"foamDictionary constant/polyMesh/boundary -entry entry0/{names[s]}/type "
        f"-set {PATCH_TYPES[p['kind']]} > /dev/null\n"
        for s, p in patches.items()
        if p["kind"] in PATCH_TYPES
    )
    (workdir / "Allrun").write_text(
        "set -e\n"
        "gmshToFoam mesh.msh > log.gmshToFoam 2>&1\n"
        # Faces outside every selection land in gmshToFoam's defaultFaces: walls.
        "if grep -q defaultFaces constant/polyMesh/boundary; then\n"
        "  foamDictionary constant/polyMesh/boundary -entry entry0/defaultFaces/type "
        "-set wall > /dev/null\nfi\n"
        f"{retype}"
        "simpleFoam > log.simpleFoam 2>&1\n"
        "postProcess -func writeCellCentres -latestTime > log.postProcess 2>&1\n"
    )
    (workdir / "density.txt").write_text(f"{rho:.17g}\n")

    volume = [b for b in mesh.blocks if b.dim == 3]
    np.savetxt(workdir / "element-ids.txt", np.concatenate([b.ids for b in volume]), fmt="%d")
    np.save(
        workdir / "element-centroids.npy",
        np.vstack(
            [
                mesh.node_coords(b.connectivity.ravel())
                .reshape(*b.connectivity.shape, 3)
                .mean(axis=1)
                for b in volume
            ]
        ),
    )
    return workdir / "Allrun"


def command() -> list[str]:
    return ["bash", "Allrun"]


def read_results(workdir: Path) -> Results:
    final = latest_time(workdir)
    if final is None or not (final / "C").exists():
        raise FileNotFoundError("OpenFOAM wrote no results; see log.simpleFoam in the case")
    ids_ref = np.loadtxt(workdir / "element-ids.txt", dtype=np.int64, ndmin=1)
    n = len(ids_ref)
    rho = float((workdir / "density.txt").read_text())
    centres = read_internal_field(final / "C", n)
    # Node-average centres equal OpenFOAM's for tets and parallelepipeds; others are
    # close enough for the coordinate match's tolerance on fine meshes.
    ids = match_points(centres, np.load(workdir / "element-centroids.npy"), ids_ref)
    order = np.argsort(ids)
    step = StepResult(1, "stationary", 1.0)
    for name, (field_name, comps) in FIELDS.items():
        path = final / name
        if not path.exists():
            continue
        values = read_internal_field(path, n) * (rho if name in SCALED else 1.0)
        step.fields[field_name] = Field(field_name, "element", comps, ids[order], values[order])
    return Results(solver="openfoam", steps=[step])


def _turbulence_state(speed: float, inlets: list[dict]) -> tuple[float, float, float]:
    """Initial k, epsilon, omega from the first turbulent inlet (or small defaults)."""
    inlet = next((p for p in inlets if "intensity" in p), None)
    if inlet is None:
        return 1e-8, 1e-10, 1.0
    k = 1.5 * (inlet["U"] * inlet["intensity"]) ** 2
    length = inlet["mixing_length"]
    return k, CMU**0.75 * k**1.5 / length, k**0.5 / (CMU**0.25 * length)


def _fields(workdir, patches, names, rho, conductivity, turbulent, k0, eps0, omega0, heat) -> None:
    zero = workdir / "0"
    t_in = next((p["T"] for p in patches.values() if "T" in p and p["kind"] != "wall"), None)
    t0 = t_in if t_in is not None else next((p["T"] for p in patches.values() if "T" in p), 293.15)
    fields = {
        "U": ("volVectorField", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)"),
        "p": ("volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0"),
    }
    if turbulent:
        fields |= {
            "k": ("volScalarField", "[0 2 -2 0 0 0 0]", f"uniform {k0:.10g}"),
            "epsilon": ("volScalarField", "[0 2 -3 0 0 0 0]", f"uniform {eps0:.10g}"),
            "omega": ("volScalarField", "[0 0 -1 0 0 0 0]", f"uniform {omega0:.10g}"),
            "nut": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0"),
        }
    if heat:
        fields["T"] = ("volScalarField", "[0 0 0 1 0 0 0]", f"uniform {t0:.10g}")
    for name, (cls, dims, init) in fields.items():
        boundary = {"defaultFaces": _wall(name)}
        for selection, patch in patches.items():
            boundary[names[selection]] = _condition(name, patch, rho, conductivity)
        write_dict(
            zero / name,
            {"dimensions": dims, "internalField": init, "boundaryField": boundary},
            cls=cls,
        )


def _wall(name: str) -> dict:
    return {
        "U": {"type": "noSlip"},
        "k": {"type": "kqRWallFunction", "value": "$internalField"},
        "epsilon": {"type": "epsilonWallFunction", "value": "$internalField"},
        "omega": {"type": "omegaWallFunction", "value": "$internalField"},
        "nut": {"type": "nutkWallFunction", "value": "uniform 0"},
    }.get(name, {"type": "zeroGradient"})


def _condition(name: str, patch: dict, rho: float, conductivity: float) -> dict:
    kind = patch["kind"]
    if kind == "symmetry":
        return {"type": "symmetry"}
    if kind == "wall":
        if name == "T" and "T" in patch:
            return {"type": "fixedValue", "value": f"uniform {patch['T']:.10g}"}
        if name == "T" and "q" in patch:
            # q into the fluid = k dT/dn along the outward normal (OpenFOAM's snGrad).
            return {
                "type": "fixedGradient",
                "gradient": f"uniform {patch['q'] / conductivity:.10g}",
            }
        return _wall(name)
    if kind == "velocity_inlet":
        return {
            "U": {"type": "surfaceNormalFixedValue", "refValue": f"uniform {-patch['U']:.10g}"},
            "k": {
                "type": "turbulentIntensityKineticEnergyInlet",
                "intensity": patch.get("intensity", 0.05),
                "value": "$internalField",
            },
            "epsilon": {
                "type": "turbulentMixingLengthDissipationRateInlet",
                "mixingLength": patch.get("mixing_length", 1.0),
                "value": "$internalField",
            },
            "omega": {
                "type": "turbulentMixingLengthFrequencyInlet",
                "mixingLength": patch.get("mixing_length", 1.0),
                "value": "$internalField",
            },
            "nut": {"type": "calculated", "value": "uniform 0"},
            "T": {"type": "fixedValue", "value": f"uniform {patch.get('T', 293.15):.10g}"},
        }.get(name, {"type": "zeroGradient"})
    # pressure_outlet
    if name == "p":
        return {"type": "fixedValue", "value": f"uniform {patch['p'] / rho:.10g}"}
    if name == "nut":
        return {"type": "calculated", "value": "uniform 0"}
    if name == "U":
        return {"type": "inletOutlet", "inletValue": "uniform (0 0 0)", "value": "uniform (0 0 0)"}
    return {"type": "inletOutlet", "inletValue": "$internalField", "value": "$internalField"}


def _system(workdir, step, turbulent, heat, nu, props_t, rho, settings) -> None:
    iterations = int(step.settings.get("iterations", 2000))
    functions = {}
    if heat:
        k, cp = props_t["T"]
        prandtl = rho * nu * cp / k
        functions["T"] = {
            "type": "scalarTransport",
            "libs": "(solverFunctionObjects)",
            "field": "T",
            # Diffusivity = alphaD nu + alphaDt nut: 1/Pr and 1/Pr_t.
            "alphaD": 1.0 / prandtl,
            "alphaDt": 1.0 / float(settings.get("turbulent_prandtl", 0.85)),
            "nCorr": 1,
            "writeControl": "writeTime",
        }
    control = {
        "application": "simpleFoam",
        "startFrom": "startTime",
        "startTime": 0,
        "stopAt": "endTime",
        "endTime": iterations,
        "deltaT": 1,
        "writeControl": "timeStep",
        "writeInterval": iterations,
        "purgeWrite": 0,
        "writeFormat": "ascii",
        "writePrecision": 12,
        "writeCompression": False,
        "timeFormat": "general",
        "timePrecision": 8,
        "runTimeModifiable": False,
    }
    if functions:
        control["functions"] = functions
    write_dict(workdir / "system" / "controlDict", control)
    turb_div = "bounded Gauss limitedLinear 1"
    write_dict(
        workdir / "system" / "fvSchemes",
        {
            "ddtSchemes": {"default": "steadyState"},
            "gradSchemes": {"default": "Gauss linear"},
            "divSchemes": {
                "default": "none",
                "div(phi,U)": "bounded Gauss linearUpwind grad(U)",
                "div(phi,k)": turb_div,
                "div(phi,epsilon)": turb_div,
                "div(phi,omega)": turb_div,
                "div(phi,T)": "bounded Gauss linearUpwind grad(T)",
                "div((nuEff*dev2(T(grad(U)))))": "Gauss linear",
            },
            "laplacianSchemes": {"default": "Gauss linear limited corrected 0.5"},
            "interpolationSchemes": {"default": "linear"},
            "snGradSchemes": {"default": "limited corrected 0.5"},
            "wallDist": {"method": "meshWave"},
        },
    )
    smooth = {
        "solver": "smoothSolver",
        "smoother": "symGaussSeidel",
        "tolerance": 1e-9,
        "relTol": 0.1,
    }
    residuals = {"p": 1e-6, "U": 1e-7}
    if turbulent:
        residuals['"(k|epsilon|omega)"'] = 1e-6
    if heat:
        residuals["T"] = 1e-8  # or simpleFoam stops once the flow alone has converged
    write_dict(
        workdir / "system" / "fvSolution",
        {
            "solvers": {
                "p": {
                    "solver": "GAMG",
                    "smoother": "GaussSeidel",
                    "tolerance": 1e-9,
                    "relTol": 0.01,
                },
                '"(U|k|epsilon|omega)"': smooth,
                # The scalar is solved once per SIMPLE iteration: multigrid reaches the
                # slow axial conduction mode that Gauss-Seidel barely touches.
                "T": {
                    "solver": "GAMG",
                    "smoother": "GaussSeidel",
                    "tolerance": 1e-10,
                    "relTol": 0.0,
                },
            },
            "SIMPLE": {
                "nNonOrthogonalCorrectors": 0,
                "consistent": True,
                "residualControl": residuals,
            },
            # The scalar is linear in T for a given flow: no under-relaxation.
            "relaxationFactors": {"equations": {"U": 0.9, '".*"': 0.9, "T": 1.0}},
        },
    )


def _label(selection: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in selection)
