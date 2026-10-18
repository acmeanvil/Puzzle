"""Write an Elmer solver input file (.sif) and its mesh from a study.

Feature values follow the same names as the CalculiX adapter: displacement x/y/z,
force x/y/z (total, applied as a uniform traction), pressure p (positive pushes into
the surface), temperature T, heat_flux q (positive into the body), convection h and
T_inf, electric_potential V, ground, surface_charge rho_s (C/m^2). Magnetostatics: a
coil_current I (total, A) on a conductor body, entering through its coil_start face and
leaving through coil_end; magnetic_insulation keeps the flux tangential. Everything is SI.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.common import (
    ELECTROSTATIC,
    MAGNETIC,
    STRUCTURAL,
    THERMAL,
    InputError,
    initial_temperature,
    surface_area,
    thermal_tables,
    time_scheme,
    time_settings,
)
from puzzle.adapters.elmer.meshfiles import ElmerMesh, write_mesh
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Feature, Material, Project, Study

MESH_DIR = "mesh"
EPSILON_0 = 8.8541878128e-12  # F/m, CODATA 2018
OUTPUT = "case"
TIMES = "puzzle-times.txt"  # output time of each case_t*.vtu, in order
ROOM_K = 293.15


def _iterative(method: str, preconditioner: str, tolerance: float) -> list[str]:
    return [
        '  Linear System Solver = "Iterative"',
        f"  Linear System Iterative Method = {method}",
        f"  Linear System Preconditioning = {preconditioner}",
        "  Linear System Max Iterations = 5000",
        f"  Linear System Convergence Tolerance = {tolerance:g}",
        "  Linear System Residual Output = 50",
    ]


DIRECT = ["  Linear System Solver = Direct", "  Linear System Direct Method = Umfpack"]


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> tuple[Path, ElmerMesh]:
    comp = project.get(study.component, Component)
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the Elmer adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    allowed = {
        "solid_mechanics": ("stationary", "modal"),
        "heat_transfer": ("stationary", "transient"),
        "electrostatics": ("stationary",),
        "magnetic_fields": ("stationary",),
    }
    if len(solved) != 1 or next(iter(solved)) not in allowed:
        raise InputError(
            f"the Elmer adapter solves one of {sorted(allowed)} at a time for now, "
            f"not {sorted(solved)}"
        )
    if step.type not in allowed[next(iter(solved))]:
        raise InputError(f"the Elmer adapter does not run {step.type} {sorted(solved)} steps yet")
    transient = step.type == "transient"

    wanted = {
        "solid_mechanics": STRUCTURAL,
        "heat_transfer": THERMAL,
        "electrostatics": ELECTROSTATIC,
        "magnetic_fields": MAGNETIC,
    }[next(iter(solved))]
    features = [
        f for ph in comp.physics if ph.type in solved for f in ph.features if f.type in wanted
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in ("fixed", "displacement")]
    # Coil currents act on bodies; every other feature is a boundary condition.
    coils = [f for f in features if f.type == "coil_current"]
    if len(coils) > 1 or (solved == {"magnetic_fields"} and not coils):
        raise InputError("the Elmer adapter solves magnetic fields driven by one coil for now")
    features = [f for f in features if f.type != "coil_current"]
    boundary_names = list(dict.fromkeys(f.selection for f in features))
    body_names = [a.selection for a in comp.materials]
    if not body_names:
        raise InputError(f"component '{comp.name}' has no material assignments")
    coil_bodies = {f.selection for f in coils}
    if coil_bodies - set(body_names):
        raise InputError(
            f"a coil current must be on a body with a material, not {sorted(coil_bodies)}"
        )
    emesh = write_mesh(mesh, workdir / MESH_DIR, body_names, boundary_names)

    out = [
        "Header",
        "  CHECK KEYWORDS Warn",
        f'  Mesh DB "." "{MESH_DIR}"',
        "End",
        "",
        "Simulation",
        "  Max Output Level = 5",
        "  Coordinate System = Cartesian 3D",
        *_time_block(step),
        "End",
        "",
        "Constants",
        "  Stefan Boltzmann = 5.670374419e-08",
        f"  Permittivity Of Vacuum = {EPSILON_0}",
        "End",
        "",
    ]
    for i, assignment in enumerate(comp.materials, start=1):
        conductor = assignment.selection in coil_bodies
        out += [f"Body {i}", f"  Target Bodies(1) = {i}", f"  Equation = {2 - conductor}"]
        out.append(f"  Material = {i}")
        if conductor:
            out.append("  Body Force = 1")
        if "heat_transfer" in solved:
            out.append("  Initial Condition = 1")
        out += ["End", ""]
        material = project.get(assignment.material, Material)
        tables = thermal_tables(material, comp) if "heat_transfer" in solved else {}
        out += _material(i, material, solved, tables)
    if "heat_transfer" in solved:
        heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
        out += [
            "Initial Condition 1",
            f"  Temperature = {initial_temperature(heat):.10g}",
            "End",
            "",
        ]

    solvers = _solvers(step, solved, coils)
    if solved == {"magnetic_fields"}:
        # Conductors also run the coil solver; everything else only the field solvers.
        out += ["Equation 1", "  Active Solvers(3) = 1 2 3", "End", ""]
        out += ["Equation 2", "  Active Solvers(2) = 2 3", "End", ""]
        out += ["Body Force 1", '  Electric Potential = Equals "CoilPot"', "End", ""]
    else:
        out += ["Equation 1", "  Active Solvers(1) = 1", "End", ""]
        out += ["Equation 2", "  Active Solvers(1) = 1", "End", ""]
    for number, block in enumerate(solvers, start=1):
        out += [f"Solver {number}", *block, "End", ""]
    out += [
        f"Solver {len(solvers) + 1}",
        f"  Exec Solver = {'After Timestep' if transient else 'After Simulation'}",
        '  Equation = "Result Output"',
        '  Procedure = "ResultOutputSolve" "ResultOutputSolver"',
        f'  Output File Name = "{OUTPUT}"',
        "  Vtu Format = True",
        "  Ascii Output = True",
        "End",
        "",
    ]
    for name in boundary_names:
        out += _boundary(
            emesh.boundaries[name], name, [f for f in features if f.selection == name], mesh
        )

    if transient:
        dt, _, n = time_settings(step)
        times = [k * dt for k in range(1, n + 1)]
    else:
        times = [1.0]
    (workdir / MESH_DIR / TIMES).write_text("\n".join(f"{t:.12g}" for t in times) + "\n")
    sif = workdir / f"{OUTPUT}.sif"
    sif.write_text("\n".join(out) + "\n")
    return sif, emesh


def _material(
    index: int, m: Material, solved: set[str], tables: dict[str, list[tuple[float, float]]]
) -> list[str]:
    p = {k: q.to_si() for k, q in m.properties.items()}

    def need(*keys: str) -> list[float]:
        missing = [k for k in keys if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        return [p[k] for k in keys]

    out = [f"Material {index}", f'  Name = "{m.name}"']

    def value(keyword: str, prop: str) -> list[str]:
        # A table becomes a piecewise-linear function of temperature.
        if prop in tables:
            rows = [f"    {t:.10g} {v:.10g}" for t, v in tables[prop]]
            return [f"  {keyword} = Variable Temperature", "    Real", *rows, "    End"]
        return [f"  {keyword} = {p[prop]:.10g}"]

    if "density" in p or "density" in tables:
        out += value("Density", "density")
    if "solid_mechanics" in solved:
        e, nu = need("youngs_modulus", "poissons_ratio")
        out += [f"  Youngs Modulus = {e:.10g}", f"  Poisson Ratio = {nu:.10g}"]
    if "heat_transfer" in solved:
        if "thermal_conductivity" not in tables:
            need("thermal_conductivity")
        out += value("Heat Conductivity", "thermal_conductivity")
        if "specific_heat" in p or "specific_heat" in tables:
            out += value("Heat Capacity", "specific_heat")
    if "electrostatics" in solved:
        (eps_r,) = need("relative_permittivity")
        out.append(f"  Relative Permittivity = {eps_r:.10g}")
    if "magnetic_fields" in solved:
        mu_r, sigma = need("relative_permeability", "electrical_conductivity")
        out.append(f"  Relative Permeability = {mu_r:.10g}")
        out.append(f"  Electric Conductivity = {sigma:.10g}")
    return out + ["End", ""]


def _solvers(step, solved: set[str], coils: list[Feature]) -> list[list[str]]:
    """The solver blocks, without their Solver/End lines, in solver order."""
    if solved == {"solid_mechanics"}:
        block = [
            '  Equation = "Linear elasticity"',
            '  Procedure = "StressSolve" "StressSolver"',
            "  Variable = -dofs 3 Displacement",
            *DIRECT,
        ]
        if step.type == "modal":
            block += [
                "  Eigen Analysis = True",
                f"  Eigen System Values = {int(step.settings.get('modes', 10))}",
            ]
        else:
            block.append("  Calculate Stresses = True")
        return [block]
    if solved == {"electrostatics"}:
        return [
            [
                '  Equation = "Stat Elec Solver"',
                '  Procedure = "StatElecSolve" "StatElecSolver"',
                "  Variable = Potential",
                "  Calculate Electric Field = True",
                "  Calculate Electric Energy = True",
                *DIRECT,
            ]
        ]
    if solved == {"magnetic_fields"}:
        current = coils[0].values["I"].to_si()
        coil = [
            '  Equation = "CoilSolver"',
            '  Procedure = "CoilSolver" "CoilSolver"',
            f"  Desired Coil Current = Real {current:.10g}",
            *_iterative("BiCGStabL", "ILU1", 1e-12),
        ]
        av = [
            '  Equation = "MGDynamics"',
            '  Variable = "AV"',
            '  Procedure = "MagnetoDynamics" "WhitneyAVSolver"',
            "  Linear System Symmetric = True",
            "  Use Magnetic Norm = Logical True",
            *DIRECT,  # Elmer gauges a direct A-V solve with a spanning tree; GCR stalls here
        ]
        fields = [
            '  Equation = "MGDynamicsCalc"',
            '  Procedure = "MagnetoDynamics" "MagnetoDynamicsCalcFields"',
            '  Potential Variable = String "AV"',
            "  Calculate Current Density = Logical True",
            "  Calculate Nodal Fields = Logical True",
            "  Calculate Elemental Fields = Logical False",
            "  Impose Body Force Potential = Logical True",
            "  Impose Body Force Current = Logical True",
            "  Linear System Symmetric = True",
            *_iterative("CG", "None", 1e-8),
        ]
        return [coil, av, fields]
    return [
        [
            '  Equation = "Heat Equation"',
            '  Procedure = "HeatSolve" "HeatSolver"',
            "  Variable = Temperature",
            # Temperature tables make the system nonlinear; a linear one stops after one.
            "  Nonlinear System Max Iterations = 50",
            "  Nonlinear System Convergence Tolerance = 1e-10",
            *DIRECT,
        ]
    ]


def _boundary(index: int, name: str, features: list[Feature], mesh: Mesh) -> list[str]:
    out = [f"Boundary Condition {index}", f"  Target Boundaries(1) = {index}", f'  Name = "{name}"']
    for f in features:
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "fixed":
            out += [f"  Displacement {d} = 0" for d in (1, 2, 3)]
        elif f.type == "displacement":
            out += [
                f"  Displacement {d} = {v[a]:.10g}"
                for d, a in ((1, "x"), (2, "y"), (3, "z"))
                if a in v
            ]
        elif f.type == "force":
            area = surface_area(mesh.groups[f.selection], mesh)
            out += [
                f"  Force {d} = {v[a] / area:.10g}"
                for d, a in ((1, "x"), (2, "y"), (3, "z"))
                if v.get(a)
            ]
        elif f.type == "pressure":
            # Elmer's Normal Force pulls along the outward normal; pressure pushes inward.
            out.append(f"  Normal Force = {-v['p']:.10g}")
        elif f.type == "temperature":
            out.append(f"  Temperature = {v['T']:.10g}")
        elif f.type == "heat_flux":
            out.append(f"  Heat Flux = {v['q']:.10g}")
        elif f.type == "convection":
            out += [
                f"  Heat Transfer Coefficient = {v['h']:.10g}",
                f"  External Temperature = {v['T_inf']:.10g}",
            ]
        elif f.type == "electric_potential":
            out.append(f"  Potential = {v['V']:.10g}")
        elif f.type == "ground":
            out.append("  Potential = 0")
        elif f.type == "surface_charge":
            out.append(f"  Surface Charge Density = {v['rho_s']:.10g}")
        elif f.type in ("coil_start", "coil_end"):
            flag = "Coil Start" if f.type == "coil_start" else "Coil End"
            out += [f"  {flag} = Logical True", "  AV {e} = Real 0", "  AV = Real 0"]
        elif f.type == "magnetic_insulation":
            out.append("  AV {e} = Real 0")  # flux stays tangential to the boundary
    return out + ["End", ""]


def _time_block(step) -> list[str]:
    if step.type != "transient":
        return [
            "  Simulation Type = Steady state",
            "  Steady State Max Iterations = 1",
            "  Output Intervals(1) = 1",
        ]
    dt, _, n = time_settings(step)
    scheme, theta = time_scheme(step)
    method = {
        "backward_euler": ["  Timestepping Method = BDF", "  BDF Order = 1"],
        "bdf2": ["  Timestepping Method = BDF", "  BDF Order = 2"],
        "crank_nicolson": ["  Timestepping Method = Crank-Nicolson"],
        # Elmer's Newmark method on a first-order system is the theta method.
        "theta": ["  Timestepping Method = Newmark", f"  Newmark Beta = {theta}"],
    }[scheme]
    return [
        "  Simulation Type = Transient",
        *method,
        f"  Timestep Sizes(1) = {dt:.12g}",
        f"  Timestep Intervals(1) = {n}",
        "  Output Intervals(1) = 1",
    ]
