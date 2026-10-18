"""Puzzle's FEniCSx solver script. Runs inside the FEniCSx environment, not in Puzzle.

    python study.py            (reads case.json and mesh.msh; writes results.npz)

case.json says what to solve:
    {"physics": "solid_mechanics" | "heat_transfer",
     "step": {"type": "stationary" | "modal", "modes": 10},
     "materials": [{"group": "Bar/Body", "properties": {"youngs_modulus": ..., ...}}],
     "features": [{"type": "fixed", "group": "Bar/Root", "values": {...}, "area": ...}]}
Values are SI. Group names are Gmsh physical group names in mesh.msh.

results.npz holds `coords` (one row per output node) and, as computed, `displacement`,
`stress` (xx, yy, zz, xy, yz, zx), `temperature`, `frequencies` (Hz) and `modes`
(mode x node x 3).
"""

from __future__ import annotations

import json

import numpy as np
import ufl
from dolfinx import fem
from dolfinx.fem.petsc import LinearProblem, assemble_matrix
from dolfinx.io.gmsh import read_from_msh
from mpi4py import MPI
from petsc4py import PETSc

# MUMPS when PETSc has it (not every platform's build does), else PETSc's own LU.
LU_PACKAGE = "mumps" if PETSc.Sys.hasExternalPackage("mumps") else "petsc"
DIRECT = {"ksp_type": "preonly", "pc_type": "lu", "pc_factor_mat_solver_type": LU_PACKAGE}


def main() -> None:
    with open("case.json") as f:
        case = json.load(f)
    data = read_from_msh("mesh.msh", MPI.COMM_WORLD, 0, gdim=3)
    msh, cells, facets, groups = data.mesh, data.cell_tags, data.facet_tags, data.physical_groups
    degree = msh.geometry.cmap.degree
    dx = ufl.Measure("dx", domain=msh, subdomain_data=cells)
    ds = ufl.Measure("ds", domain=msh, subdomain_data=facets)

    def tag(name: str) -> int:
        if name not in groups:
            raise SystemExit(f"no group '{name}' in mesh.msh")
        return groups[name][1]

    def piecewise(prop: str) -> fem.Function:
        """A material property as a cellwise constant, from the material assignments."""
        space = fem.functionspace(msh, ("DG", 0))
        f = fem.Function(space)
        for m in case["materials"]:
            if prop not in m["properties"]:
                raise SystemExit(f"material for '{m['group']}' is missing {prop}")
            f.x.array[cells.find(tag(m["group"]))] = m["properties"][prop]
        return f

    out: dict[str, np.ndarray] = {}
    if case["physics"] == "solid_mechanics":
        solve_solid(case, msh, degree, dx, ds, facets, tag, piecewise, out)
    else:
        solve_heat(case, msh, degree, dx, ds, facets, tag, piecewise, out)
    np.savez("results.npz", **out)


def solve_solid(case, msh, degree, dx, ds, facets, tag, piecewise, out) -> None:
    V = fem.functionspace(msh, ("Lagrange", degree, (3,)))
    E, nu = piecewise("youngs_modulus"), piecewise("poissons_ratio")
    mu = E / (2 * (1 + nu))
    lmbda = E * nu / ((1 + nu) * (1 - 2 * nu))

    def eps(w):
        return ufl.sym(ufl.grad(w))

    def sigma(w):
        return lmbda * ufl.tr(eps(w)) * ufl.Identity(3) + 2 * mu * eps(w)

    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    a = ufl.inner(sigma(u), eps(v)) * dx
    n = ufl.FacetNormal(msh)
    bcs, L = [], ufl.inner(fem.Constant(msh, np.zeros(3)), v) * dx
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "fixed":
            bcs.append(fem.dirichletbc(np.zeros(3), fem.locate_dofs_topological(V, 2, where), V))
        elif f["type"] == "displacement":
            for i, axis in enumerate("xyz"):
                if axis in vals:
                    dofs = fem.locate_dofs_topological(V.sub(i), 2, where)
                    bcs.append(fem.dirichletbc(PETSc.ScalarType(vals[axis]), dofs, V.sub(i)))
        elif f["type"] == "force" and case["step"]["type"] != "modal":
            traction = np.array([vals.get(a_, 0.0) for a_ in "xyz"]) / f["area"]
            L += ufl.inner(fem.Constant(msh, traction), v) * ds(tag(f["group"]))
        elif f["type"] == "pressure" and case["step"]["type"] != "modal":
            L += -vals["p"] * ufl.inner(n, v) * ds(tag(f["group"]))

    if case["step"]["type"] == "modal":
        rho = piecewise("density")
        modal(V, a, ufl.inner(rho * u, v) * dx, bcs, int(case["step"].get("modes", 10)), out)
        return

    uh = LinearProblem(
        a, L, bcs=bcs, petsc_options_prefix="puzzle_u_", petsc_options=DIRECT
    ).solve()
    out["coords"] = V.tabulate_dof_coordinates()
    out["displacement"] = uh.x.array.reshape(-1, 3)

    # Nodal stress: L2 projection of the stress components onto the displacement's nodes.
    S = fem.functionspace(msh, ("Lagrange", degree, (6,)))
    s = sigma(uh)
    voigt = ufl.as_vector([s[0, 0], s[1, 1], s[2, 2], s[0, 1], s[1, 2], s[2, 0]])
    p, q = ufl.TrialFunction(S), ufl.TestFunction(S)
    projected = LinearProblem(
        ufl.inner(p, q) * dx,
        ufl.inner(voigt, q) * dx,
        petsc_options_prefix="puzzle_s_",
        petsc_options={"ksp_type": "cg", "pc_type": "jacobi", "ksp_rtol": 1e-12},
    ).solve()
    out["stress"] = projected.x.array.reshape(-1, 6)


def modal(V, a, m, bcs, count: int, out) -> None:
    from slepc4py import SLEPc

    K = assemble_matrix(fem.form(a), bcs=bcs, diag=1.0)
    K.assemble()
    M = assemble_matrix(fem.form(m), bcs=bcs, diag=0.0)
    M.assemble()
    eps = SLEPc.EPS().create(MPI.COMM_WORLD)
    eps.setOperators(K, M)
    eps.setProblemType(SLEPc.EPS.ProblemType.GHEP)
    eps.setDimensions(count)
    eps.setWhichEigenpairs(SLEPc.EPS.Which.TARGET_MAGNITUDE)
    eps.setTarget(0.0)
    st = eps.getST()
    st.setType(SLEPc.ST.Type.SINVERT)
    ksp = st.getKSP()
    ksp.setType("preonly")
    ksp.getPC().setType("lu")
    ksp.getPC().setFactorSolverType(LU_PACKAGE)
    eps.solve()
    vec = K.createVecRight()
    frequencies, shapes = [], []
    for i in range(min(eps.getConverged(), count)):
        lam = eps.getEigenpair(i, vec).real
        frequencies.append(np.sqrt(max(lam, 0.0)) / (2 * np.pi))
        shapes.append(vec.array.copy().reshape(-1, 3))
    order = np.argsort(frequencies)
    out["coords"] = V.tabulate_dof_coordinates()
    out["frequencies"] = np.asarray(frequencies)[order]
    out["modes"] = np.asarray(shapes)[order]


def solve_heat(case, msh, degree, dx, ds, facets, tag, piecewise, out) -> None:
    V = fem.functionspace(msh, ("Lagrange", degree))
    k = piecewise("thermal_conductivity")
    T, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    a = k * ufl.inner(ufl.grad(T), ufl.grad(v)) * dx
    L = fem.Constant(msh, 0.0) * v * dx
    bcs = []
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "temperature":
            bcs.append(
                fem.dirichletbc(
                    PETSc.ScalarType(vals["T"]), fem.locate_dofs_topological(V, 2, where), V
                )
            )
        elif f["type"] == "heat_flux":
            L += vals["q"] * v * ds(tag(f["group"]))
        elif f["type"] == "convection":
            a += vals["h"] * T * v * ds(tag(f["group"]))
            L += vals["h"] * vals["T_inf"] * v * ds(tag(f["group"]))
    Th = LinearProblem(
        a, L, bcs=bcs, petsc_options_prefix="puzzle_t_", petsc_options=DIRECT
    ).solve()
    out["coords"] = V.tabulate_dof_coordinates()
    out["temperature"] = Th.x.array.reshape(-1, 1)


if __name__ == "__main__":
    main()
