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

import numpy as np

from puzzle.adapters.common import (
    BODY_LOADS,
    ELECTROSTATIC,
    HOLDING,
    MAGNETIC,
    STRUCTURAL,
    THERMAL,
    InputError,
    body_load,
    body_load_on_material,
    check_contacts,
    cylindrical_expressions,
    globalize,
    initial_temperature,
    local_axes,
    refuse_large_deflection,
    remote_load,
    remote_values,
    rigid_motion,
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


RESTART = "puzzle-temperature.result"  # a transferred temperature field, as a restart


def write_case(
    project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
) -> tuple[Path, ElmerMesh]:
    comp = project.get(study.component, Component)
    check_contacts(comp, set(), "Elmer")
    refuse_large_deflection(study.solve_steps(), "Elmer")
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
        features = [f for f in features if f.type in HOLDING]
    # Coil currents act on bodies; every other feature is a boundary condition.
    coils = [f for f in features if f.type == "coil_current"]
    if len(coils) > 1 or (solved == {"magnetic_fields"} and not coils):
        raise InputError("the Elmer adapter solves magnetic fields driven by one coil for now")
    features = [f for f in features if f.type != "coil_current"]
    # Body loads (ADR 0037) act on bodies, as body forces; the rest are boundaries.
    loads = [f for f in features if f.type in BODY_LOADS]
    for f in loads:
        body_load_on_material(comp, f, "Elmer")
    features = globalize(project, [f for f in features if f.type not in BODY_LOADS])
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
    thermal_load = next((i.field for i in inputs if i.field.name == "temperature"), None)
    restart = []
    if thermal_load is not None:
        if solved != {"solid_mechanics"} or step.type != "stationary":
            raise InputError(
                "a temperature load applies to static structural steps", code="unsupported"
            )
        _write_restart(workdir / MESH_DIR / RESTART, thermal_load, emesh)
        restart = [f'  Restart File = "{RESTART}"', "  Restart Position = 1"]

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
        *restart,
        "End",
        "",
        "Constants",
        "  Stefan Boltzmann = 5.670374419e-08",
        f"  Permittivity Of Vacuum = {EPSILON_0}",
        "End",
        "",
    ]
    forces: list[list[str]] = []  # body force blocks for body loads, in body order
    for i, assignment in enumerate(comp.materials, start=1):
        conductor = assignment.selection in coil_bodies
        out += [f"Body {i}", f"  Target Bodies(1) = {i}", f"  Equation = {2 - conductor}"]
        out.append(f"  Material = {i}")
        if conductor:
            out.append("  Body Force = 1")
        mine = [f for f in loads if f.selection == assignment.selection]
        if mine:
            material = project.get(assignment.material, Material)
            forces.append(_body_force(mine, material))
            out.append(f"  Body Force = {len(forces)}")
        if "heat_transfer" in solved:
            out.append("  Initial Condition = 1")
        out += ["End", ""]
        material = project.get(assignment.material, Material)
        tables = thermal_tables(material, comp) if "heat_transfer" in solved else {}
        out += _material(i, material, solved, tables)
        if thermal_load is not None:  # thermal strain alpha (T - T_ref) in StressSolve
            alpha = material.properties.get("thermal_expansion")
            if alpha is None:
                raise InputError(f"a temperature load needs thermal_expansion on '{material.name}'")
            out[-2:-2] = [  # inside the Material block, before its End
                f"  Heat Expansion Coefficient = {alpha.to_si():.10g}",
                f"  Reference Temperature = {_reference(comp):.10g}",
            ]
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
        for k, block in enumerate(forces, start=1):
            out += [f"Body Force {k}", *block, "End", ""]
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
    from puzzle.model.schema import Amplitude

    amplitudes = {
        f.amplitude: project.get(f.amplitude, Amplitude).points for f in features if f.amplitude
    }
    for name in boundary_names:
        out += _boundary(
            emesh.boundaries[name],
            name,
            [f for f in features if f.selection == name],
            mesh,
            amplitudes,
            {f.selection: local_axes(project, f) for f in features if f.axes is not None},
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


def _body_force(loads: list[Feature], material: Material) -> list[str]:
    """Stress Bodyforce per component: rho times the summed load fields; a rotation's
    centrifugal field is a MATC expression in the coordinates (ADR 0037)."""
    rho = material.properties["density"].to_si()
    uniform = np.zeros(3)
    spin = []
    for f in loads:
        load = body_load(f)
        if load.uniform is not None:
            uniform += np.asarray(load.uniform)
        elif load.omega:
            spin.append(load)
    out = []
    for i in range(3):
        if not spin:
            out.append(f"  Stress Bodyforce {i + 1} = {rho * uniform[i]:.10g}")
            continue
        terms = [f"{rho * uniform[i]:.10g}"]
        for load in spin:
            (cx, cy, cz), (nx, ny, nz) = load.centre, load.axis
            d = [f"(tx({j})-({c:.10g}))" for j, c in enumerate((cx, cy, cz))]
            dot = f"({d[0]}*({nx:.10g})+{d[1]}*({ny:.10g})+{d[2]}*({nz:.10g}))"
            n_i = (nx, ny, nz)[i]
            terms.append(f"{rho * load.omega**2:.10g}*({d[i]}-{dot}*({n_i:.10g}))")
        out += [
            f"  Stress Bodyforce {i + 1} = Variable Coordinate 1, Coordinate 2, Coordinate 3",
            f'    Real MATC "{" + ".join(terms)}"',
        ]
    return out


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


def _boundary(
    index: int, name: str, features: list[Feature], mesh: Mesh, amplitudes=None, axes_of=None
) -> list[str]:
    """A boundary's conditions; `amplitudes` (id -> points) make a value follow time."""
    out = [f"Boundary Condition {index}", f"  Target Boundaries(1) = {index}", f'  Name = "{name}"']
    for f in features:
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "fixed":
            out += [f"  Displacement {d} = 0" for d in (1, 2, 3)]
        elif f.type == "displacement" and f.axes is not None:  # cylindrical, all three
            exprs = cylindrical_expressions(
                axes_of[f.selection], [v[a] for a in "xyz"], ("tx(0)", "tx(1)", "tx(2)")
            )
            for i, e in enumerate(exprs, start=1):
                out += [
                    f"  Displacement {i} = Variable Coordinate 1, Coordinate 2, Coordinate 3",
                    f'    Real MATC "{e}"',
                ]
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
        elif f.type == "elastic_support":  # springs to ground per area (ADR 0037)
            out += _springs(f, mesh)
        elif f.type == "remote_force":  # its RBE3-equivalent traction (ADR 0037)
            load = remote_load(mesh.groups[f.selection], mesh, *remote_values(f))
            out += _matc_cross("Force", load.uniform, load.twist, load.centroid)
        elif f.type == "remote_displacement":  # a rigid motion of the face
            motion = rigid_motion(f, mesh.groups[f.selection], mesh)
            out += _matc_cross("Displacement", motion.u0, motion.theta, motion.point)
        elif f.type == "pressure":
            # Elmer's Normal Force pulls along the outward normal; pressure pushes inward.
            out.append(f"  Normal Force = {-v['p']:.10g}")
        elif f.type == "temperature":
            out.append(f"  Temperature = {v['T']:.10g}")
        elif f.type == "heat_flux":
            out += _in_time("Heat Flux", v["q"], (amplitudes or {}).get(f.amplitude))
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


def _springs(f: Feature, mesh: Mesh) -> list[str]:
    """Elmer's Spring i: a stiffness per area along each global axis. A support's normal
    must lie along an axis to be written that way; others are refused."""
    from puzzle.adapters.common import support_nodes, support_stiffness

    kn, kt = support_stiffness(f)
    _, _, normals = support_nodes(mesh.groups[f.selection], mesh)
    axis = int(np.argmax(np.abs(normals.mean(axis=0))))
    if not np.allclose(np.abs(normals[:, axis]), 1.0, atol=1e-6):
        raise InputError(
            f"Elmer takes an elastic support on a face along an axis; '{f.selection}' isn't",
            code="unsupported",
        )
    return [f"  Spring {d + 1} = {kn if d == axis else kt:.10g}" for d in range(3)]


def _matc_cross(keyword: str, base, axis, origin) -> list[str]:
    """`keyword i` = base + axis x (x - origin), component by component, as MATC in the
    coordinates (a traction, or a rigid displacement)."""
    a, b, c = (np.asarray(v, float) for v in (base, axis, origin))
    d = [f"(tx({j})-({c[j]:.10g}))" for j in range(3)]
    exprs = [
        f"{a[0]:.10g}+({b[1]:.10g})*{d[2]}-({b[2]:.10g})*{d[1]}",
        f"{a[1]:.10g}+({b[2]:.10g})*{d[0]}-({b[0]:.10g})*{d[2]}",
        f"{a[2]:.10g}+({b[0]:.10g})*{d[1]}-({b[1]:.10g})*{d[0]}",
    ]
    out = []
    for i, e in enumerate(exprs, start=1):
        out += [
            f"  {keyword} {i} = Variable Coordinate 1, Coordinate 2, Coordinate 3",
            f'    Real MATC "{e}"',
        ]
    return out


def _in_time(keyword: str, value: float, points) -> list[str]:
    """A constant, or (ADR 0037) a table in time: the value times the amplitude, linear
    between its points; Elmer holds the end values beyond them, as Puzzle does."""
    if points is None:
        return [f"  {keyword} = {value:.10g}"]
    # Far points at both ends hold the end values (Elmer extrapolates otherwise).
    span = 1e6 * max(1.0, points[-1][0] - points[0][0])
    held = [(points[0][0] - span, points[0][1]), *points, (points[-1][0] + span, points[-1][1])]
    rows = [f"    {t:.10g} {value * factor:.10g}" for t, factor in held]
    return [f"  {keyword} = Variable Time", "    Real", *rows, "    End"]


def _write_restart(path: Path, field, emesh: ElmerMesh) -> None:
    """An ASCII Elmer result holding one variable, Temperature, in Elmer node order."""
    values = field.at(emesh.node_ids)[:, 0]
    n = len(values)
    lines = [
        " ASCII 3",
        "!File started by Puzzle",
        " Degrees of freedom: ",
        f"temperature              :  {n}  {n}   1 : puzzle transfer",
        " Total DOFs:            1",
        f" Number Of Nodes:  {n}",
        "Time:       1      1  1.00000000E+000",
        "temperature",
        f"Perm:  {n}  {n}",
        *(f"{i} {i}" for i in range(1, n + 1)),
        *(f"{v:.17g}" for v in values),
    ]
    path.write_text("\n".join(lines) + "\n")


def _reference(comp) -> float:
    from puzzle.adapters.calculix.inp import reference_temperature

    return reference_temperature(comp)


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
