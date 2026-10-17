"""Write an Elmer solver input file (.sif) and its mesh from a study.

Feature values follow the same names as the CalculiX adapter: displacement x/y/z,
force x/y/z (total, applied as a uniform traction), pressure p (positive pushes into
the surface), temperature T, heat_flux q (positive into the body), convection h and
T_inf. Everything is SI.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.common import STRUCTURAL, THERMAL, InputError, surface_area
from puzzle.adapters.elmer.meshfiles import ElmerMesh, write_mesh
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Feature, Material, Project, Study

MESH_DIR = "mesh"
OUTPUT = "case"
ROOM_K = 293.15
DIRECT = ["  Linear System Solver = Direct", "  Linear System Direct Method = Umfpack"]


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path) -> tuple[Path, ElmerMesh]:
    comp = project.get(study.component, Component)
    steps = study.solve_steps()
    if len(steps) != 1:
        raise InputError("the Elmer adapter runs studies with exactly one solve step for now")
    step = steps[0]
    solved = set(step.physics) or {p.type for p in comp.physics}
    if solved not in ({"solid_mechanics"}, {"heat_transfer"}):
        raise InputError(
            "the Elmer adapter solves solid_mechanics or heat_transfer alone for now, "
            f"not {sorted(solved)}"
        )
    if step.type not in ("stationary", "modal") or (
        step.type == "modal" and solved != {"solid_mechanics"}
    ):
        raise InputError(f"the Elmer adapter does not run {step.type} {sorted(solved)} steps yet")

    wanted = STRUCTURAL if "solid_mechanics" in solved else THERMAL
    features = [
        f for ph in comp.physics if ph.type in solved for f in ph.features if f.type in wanted
    ]
    if step.type == "modal":
        features = [f for f in features if f.type in ("fixed", "displacement")]
    boundary_names = list(dict.fromkeys(f.selection for f in features))
    body_names = [a.selection for a in comp.materials]
    if not body_names:
        raise InputError(f"component '{comp.name}' has no material assignments")
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
        "  Simulation Type = Steady state",
        "  Steady State Max Iterations = 1",
        "  Output Intervals(1) = 1",
        "End",
        "",
        "Constants",
        "  Stefan Boltzmann = 5.670374419e-08",
        "End",
        "",
    ]
    for i, assignment in enumerate(comp.materials, start=1):
        out += [f"Body {i}", f"  Target Bodies(1) = {i}", "  Equation = 1", f"  Material = {i}"]
        if "heat_transfer" in solved:
            out.append("  Initial Condition = 1")
        out += ["End", ""]
        out += _material(i, project.get(assignment.material, Material), solved)
    if "heat_transfer" in solved:
        out += ["Initial Condition 1", f"  Temperature = {ROOM_K}", "End", ""]

    out += ["Equation 1", "  Active Solvers(1) = 1", "End", ""]
    out += _solver(step, solved)
    out += [
        "Solver 2",
        "  Exec Solver = After Simulation",
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

    sif = workdir / f"{OUTPUT}.sif"
    sif.write_text("\n".join(out) + "\n")
    return sif, emesh


def _material(index: int, m: Material, solved: set[str]) -> list[str]:
    p = {k: q.to_si() for k, q in m.properties.items()}

    def need(*keys: str) -> list[float]:
        missing = [k for k in keys if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        return [p[k] for k in keys]

    out = [f"Material {index}", f'  Name = "{m.name}"']
    if "density" in p:
        out.append(f"  Density = {p['density']:.10g}")
    if "solid_mechanics" in solved:
        e, nu = need("youngs_modulus", "poissons_ratio")
        out += [f"  Youngs Modulus = {e:.10g}", f"  Poisson Ratio = {nu:.10g}"]
    if "heat_transfer" in solved:
        (k,) = need("thermal_conductivity")
        out.append(f"  Heat Conductivity = {k:.10g}")
        if "specific_heat" in p:
            out.append(f"  Heat Capacity = {p['specific_heat']:.10g}")
    return out + ["End", ""]


def _solver(step, solved: set[str]) -> list[str]:
    if solved == {"solid_mechanics"}:
        out = [
            "Solver 1",
            '  Equation = "Linear elasticity"',
            '  Procedure = "StressSolve" "StressSolver"',
            "  Variable = -dofs 3 Displacement",
            *DIRECT,
        ]
        if step.type == "modal":
            out += [
                "  Eigen Analysis = True",
                f"  Eigen System Values = {int(step.settings.get('modes', 10))}",
            ]
        else:
            out.append("  Calculate Stresses = True")
    else:
        out = [
            "Solver 1",
            '  Equation = "Heat Equation"',
            '  Procedure = "HeatSolve" "HeatSolver"',
            "  Variable = Temperature",
            *DIRECT,
        ]
    return out + ["End", ""]


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
    return out + ["End", ""]
