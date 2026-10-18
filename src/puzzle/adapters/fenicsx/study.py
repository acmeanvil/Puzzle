"""Puzzle's FEniCSx solver script. Runs inside the FEniCSx environment, not in Puzzle.

    python study.py            (reads case.json and mesh.msh; writes results.npz)

case.json says what to solve:
    {"physics": "solid_mechanics" | "heat_transfer" | "electrostatics" | "magnetic_fields",
     "step": {"type": "stationary" | "modal" | "transient", "modes": 10,
              "time_step": 10.0, "steps": 60},          (transient heat only)
     "initial_temperature": 293.15,
     "materials": [{"group": "Bar/Body", "properties": {"youngs_modulus": ..., ...},
                    "tables": {"thermal_conductivity": [[T, k], ...]}}],   (heat only)
     "features": [{"type": "fixed", "group": "Bar/Root", "values": {...}, "area": ...}]}
Values are SI. Group names are Gmsh physical group names in mesh.msh.

results.npz holds `coords` (one row per output node) and, as computed, `displacement`,
`stress` (xx, yy, zz, xy, yz, zx), `temperature`, `frequencies` (Hz) and `modes`
(mode x node x 3), `times` and `temperature_series` (step x node) for transients,
`potential`, `electric_field` and `electric_energy` (J); `magnetic_flux_density`,
`current_density` and `magnetic_energy` (J); plus `backend` (which linear algebra
solved it).

Linear algebra: PETSc (MUMPS, SLEPc) where dolfinx was built with it; otherwise SciPy
(SuperLU, ARPACK), e.g. conda-forge's Windows dolfinx, which has no PETSc.
PUZZLE_FENICSX_BACKEND=scipy forces the SciPy path.
"""

from __future__ import annotations

import json
import os

import numpy as np
import ufl
from dolfinx import fem, la
from dolfinx.io.gmsh import read_from_msh
from mpi4py import MPI

try:
    if os.environ.get("PUZZLE_FENICSX_BACKEND") == "scipy":
        raise ImportError("SciPy backend requested")
    from dolfinx.fem.petsc import LinearProblem
    from dolfinx.fem.petsc import assemble_matrix as petsc_matrix
    from petsc4py import PETSc

    BACKEND = "petsc"
    # MUMPS when PETSc has it (not every platform's build does), else PETSc's own LU.
    LU_PACKAGE = "mumps" if PETSc.Sys.hasExternalPackage("mumps") else "petsc"
except ImportError:
    BACKEND = "scipy"
    LU_PACKAGE = "superlu"


def main() -> None:
    with open("case.json") as f:
        case = json.load(f)
    data = read_from_msh("mesh.msh", MPI.COMM_WORLD, 0, gdim=3)
    msh, cells, facets, groups = data.mesh, data.cell_tags, data.facet_tags, data.physical_groups
    degree = (msh.geometry.cmaps[0] if hasattr(msh.geometry, "cmaps") else msh.geometry.cmap).degree
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

    def of_temperature(prop: str, T):
        """A property as a UFL expression of temperature T: each material's table
        interpolated linearly (constant past its ends), or its single value."""
        if not any(prop in m.get("tables", {}) for m in case["materials"]):
            return piecewise(prop)
        expr = 0
        for m in case["materials"]:
            indicator = fem.Function(fem.functionspace(msh, ("DG", 0)))
            indicator.x.array[cells.find(tag(m["group"]))] = 1.0
            points = m.get("tables", {}).get(prop)
            if points is None:
                if prop not in m["properties"]:
                    raise SystemExit(f"material for '{m['group']}' is missing {prop}")
                expr += indicator * m["properties"][prop]
                continue
            # v0 + sum of slope_i * (clamp(T, t_i, t_i+1) - t_i): piecewise linear in T.
            value = points[0][1]
            for (t0, v0), (t1, v1) in zip(points[:-1], points[1:], strict=True):
                clamped = ufl.min_value(ufl.max_value(T, t0), t1)
                value += (v1 - v0) / (t1 - t0) * (clamped - t0)
            expr += indicator * value
        return expr

    out: dict[str, np.ndarray] = {"backend": np.array(BACKEND)}
    solve = {
        "solid_mechanics": solve_solid,
        "heat_transfer": solve_heat,
        "electrostatics": solve_electrostatics,
        "magnetic_fields": solve_magnetostatics,
    }[case["physics"]]
    if case["physics"] == "heat_transfer":
        solve_heat(case, msh, degree, dx, ds, facets, tag, of_temperature, out)
    else:
        solve(case, msh, degree, dx, ds, facets, tag, piecewise, out)
    np.savez("results.npz", **out)


# --- linear algebra, PETSc or SciPy ----------------------------------------------


def solve_linear(a, L, bcs, V, name: str, *, mass_matrix: bool = False) -> fem.Function:
    """Solve a(u, v) = L(v) with Dirichlet conditions `bcs` for a Function on V."""
    if BACKEND == "petsc":
        options = (
            {"ksp_type": "cg", "pc_type": "jacobi", "ksp_rtol": 1e-12}
            if mass_matrix
            else {"ksp_type": "preonly", "pc_type": "lu", "pc_factor_mat_solver_type": LU_PACKAGE}
        )
        problem = LinearProblem(
            a, L, bcs=bcs, petsc_options_prefix=f"puzzle_{name}_", petsc_options=options
        )
        return problem.solve()

    from scipy.sparse.linalg import spsolve

    a_form, L_form = fem.form(a), fem.form(L)
    A = fem.assemble_matrix(a_form, bcs=bcs)
    A.scatter_reverse()
    b = fem.assemble_vector(L_form)
    fem.apply_lifting(b.array, [a_form], bcs=[bcs])
    b.scatter_reverse(la.InsertMode.add)
    for bc in bcs:
        bc.set(b.array)
    uh = fem.Function(V)
    matrix = A.to_scipy()
    n = matrix.shape[0]
    if mass_matrix:
        # Well conditioned: conjugate gradients with a diagonal preconditioner beats LU.
        from scipy.sparse import diags
        from scipy.sparse.linalg import cg

        matrix = matrix.tocsr()
        solution, info = cg(matrix, b.array[:n], rtol=1e-12, M=diags(1.0 / matrix.diagonal()))
        if info:
            raise SystemExit(f"projection did not converge (cg info {info})")
        uh.x.array[:n] = solution
    else:
        uh.x.array[:n] = spsolve(matrix.tocsc(), b.array[:n])
    uh.x.scatter_forward()
    return uh


def eigenpairs(a, m, bcs, count: int) -> tuple[list[float], list[np.ndarray]]:
    """The `count` smallest eigenvalues (omega^2) and vectors of K x = lambda M x."""
    if BACKEND == "scipy":
        from scipy.sparse.linalg import eigsh

        K = fem.assemble_matrix(fem.form(a), bcs=bcs, diag=1.0)
        K.scatter_reverse()
        M = fem.assemble_matrix(fem.form(m), bcs=bcs, diag=0.0)
        M.scatter_reverse()
        values, vectors = eigsh(
            K.to_scipy().tocsc(), k=count, M=M.to_scipy().tocsc(), sigma=0.0, which="LM"
        )
        return list(values), [vectors[:, i] for i in range(vectors.shape[1])]

    from slepc4py import SLEPc

    K = petsc_matrix(fem.form(a), bcs=bcs, diag=1.0)
    K.assemble()
    M = petsc_matrix(fem.form(m), bcs=bcs, diag=0.0)
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
    values, vectors = [], []
    for i in range(min(eps.getConverged(), count)):
        values.append(eps.getEigenpair(i, vec).real)
        vectors.append(vec.array.copy())
    return values, vectors


def dirichlet(value, dofs, V) -> fem.DirichletBC:
    return fem.dirichletbc(np.asarray(value, dtype=np.float64), dofs, V)


def project(expr, msh, degree, dx, name: str, size: int = 3, region=None) -> np.ndarray:
    """L2 projection of a vector expression onto nodal Lagrange values (node x size).

    `region` (a subdomain measure) limits where the expression is nonzero.
    """
    W = fem.functionspace(msh, ("Lagrange", degree, (size,)))
    p, q = ufl.TrialFunction(W), ufl.TestFunction(W)
    mass, rhs = ufl.inner(p, q) * dx, ufl.inner(expr, q) * (region or dx)
    return solve_linear(mass, rhs, [], W, name, mass_matrix=True).x.array.reshape(-1, size)


def integral(expr) -> float:
    return MPI.COMM_WORLD.allreduce(fem.assemble_scalar(fem.form(expr)), op=MPI.SUM)


# --- physics -----------------------------------------------------------------------


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
    modal = case["step"]["type"] == "modal"
    bcs, L = [], ufl.inner(fem.Constant(msh, np.zeros(3)), v) * dx
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "fixed":
            bcs.append(dirichlet(np.zeros(3), fem.locate_dofs_topological(V, 2, where), V))
        elif f["type"] == "displacement":
            for i, axis in enumerate("xyz"):
                if axis in vals:
                    dofs = fem.locate_dofs_topological(V.sub(i), 2, where)
                    bcs.append(dirichlet(vals[axis], dofs, V.sub(i)))
        elif f["type"] == "force" and not modal:
            traction = np.array([vals.get(c, 0.0) for c in "xyz"]) / f["area"]
            L += ufl.inner(fem.Constant(msh, traction), v) * ds(tag(f["group"]))
        elif f["type"] == "pressure" and not modal:
            L += -vals["p"] * ufl.inner(n, v) * ds(tag(f["group"]))

    out["coords"] = V.tabulate_dof_coordinates()
    if modal:
        rho = piecewise("density")
        count = int(case["step"].get("modes", 10))
        values, vectors = eigenpairs(a, ufl.inner(rho * u, v) * dx, bcs, count)
        hz = np.sqrt(np.maximum(values, 0.0)) / (2 * np.pi)
        order = np.argsort(hz)
        out["frequencies"] = hz[order]
        out["modes"] = np.asarray([vectors[i].reshape(-1, 3) for i in order])
        return

    uh = solve_linear(a, L, bcs, V, "u")
    out["displacement"] = uh.x.array.reshape(-1, 3)

    # Nodal stress: L2 projection of the stress components onto the displacement's nodes.
    S = fem.functionspace(msh, ("Lagrange", degree, (6,)))
    s = sigma(uh)
    voigt = ufl.as_vector([s[0, 0], s[1, 1], s[2, 2], s[0, 1], s[1, 2], s[2, 0]])
    p, q = ufl.TrialFunction(S), ufl.TestFunction(S)
    mass, rhs = ufl.inner(p, q) * dx, ufl.inner(voigt, q) * dx
    out["stress"] = solve_linear(mass, rhs, [], S, "s", mass_matrix=True).x.array.reshape(-1, 6)


def solve_heat(case, msh, degree, dx, ds, facets, tag, of_temperature, out) -> None:
    """Heat conduction; temperature tables make it nonlinear, solved by Picard iteration
    (properties at the previous iterate), which needs only linear solves on any backend."""
    V = fem.functionspace(msh, ("Lagrange", degree))
    T, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    previous = fem.Function(V)  # the last iterate
    previous.x.array[:] = case["initial_temperature"]
    nonlinear = any(m.get("tables") for m in case["materials"])
    k = of_temperature("thermal_conductivity", previous)
    a = k * ufl.inner(ufl.grad(T), ufl.grad(v)) * dx
    L = fem.Constant(msh, 0.0) * v * dx
    bcs = []
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "temperature":
            bcs.append(dirichlet(vals["T"], fem.locate_dofs_topological(V, 2, where), V))
        elif f["type"] == "heat_flux":
            L += vals["q"] * v * ds(tag(f["group"]))
        elif f["type"] == "convection":
            a += vals["h"] * T * v * ds(tag(f["group"]))
            L += vals["h"] * vals["T_inf"] * v * ds(tag(f["group"]))
    out["coords"] = V.tabulate_dof_coordinates()

    def picard(a_form, L_form, iterate: fem.Function) -> fem.Function:
        if not nonlinear:
            return solve_linear(a_form, L_form, bcs, V, "t")
        for _ in range(100):
            current = solve_linear(a_form, L_form, bcs, V, "t")
            change = np.abs(current.x.array - iterate.x.array).max()
            scale = max(np.abs(current.x.array).max(), 1.0)
            iterate.x.array[:] = current.x.array
            if change <= 1e-10 * scale:
                break
        else:
            raise SystemExit("temperature iteration did not converge in 100 steps")
        return current

    if case["step"]["type"] != "transient":
        out["temperature"] = picard(a, L, previous).x.array.reshape(-1, 1)
        return

    # Implicit (backward) Euler: rho cp (T - T_old) / dt = div(k grad T).
    rho, cp = of_temperature("density", previous), of_temperature("specific_heat", previous)
    dt, steps = case["step"]["time_step"], case["step"]["steps"]
    old = fem.Function(V)
    old.x.array[:] = case["initial_temperature"]
    a_t = rho * cp / dt * T * v * dx + a
    L_t = rho * cp / dt * old * v * dx + L
    series = []
    for _ in range(steps):
        current = picard(a_t, L_t, previous)
        old.x.array[:] = current.x.array
        series.append(current.x.array.copy())
    out["times"] = dt * np.arange(1, steps + 1)
    out["temperature_series"] = np.asarray(series)


EPSILON_0 = 8.8541878128e-12  # F/m, as the Elmer adapter
MU_0 = 1.25663706212e-6  # H/m


def solve_electrostatics(case, msh, degree, dx, ds, facets, tag, piecewise, out) -> None:
    V = fem.functionspace(msh, ("Lagrange", degree))
    eps = EPSILON_0 * piecewise("relative_permittivity")
    phi, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    a = eps * ufl.inner(ufl.grad(phi), ufl.grad(v)) * dx
    L = fem.Constant(msh, 0.0) * v * dx
    bcs = []
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        dofs = fem.locate_dofs_topological(V, 2, where)
        if f["type"] == "electric_potential":
            bcs.append(dirichlet(vals["V"], dofs, V))
        elif f["type"] == "ground":
            bcs.append(dirichlet(0.0, dofs, V))
        elif f["type"] == "surface_charge":
            L += vals["rho_s"] * v * ds(tag(f["group"]))
    uh = solve_linear(a, L, bcs, V, "phi")
    out["coords"] = V.tabulate_dof_coordinates()
    out["potential"] = uh.x.array.reshape(-1, 1)
    out["electric_field"] = project(-ufl.grad(uh), msh, degree, dx, "e")
    out["electric_energy"] = np.array(
        integral(0.5 * eps * ufl.inner(ufl.grad(uh), ufl.grad(uh)) * dx)
    )


def solve_magnetostatics(case, msh, degree, dx, ds, facets, tag, piecewise, out) -> None:
    """Steady magnetic field of one coil, in the vector potential A (Nedelec edges).

    As Elmer's CoilSolver: a conduction solve in the coil body between its terminals
    gives the current's path; it is scaled to the requested total current. Then
    curl(1/mu curl A) = J with n x A = 0 on the terminals and insulated faces.
    """
    coil = next(f for f in case["features"] if f["type"] == "coil_current")
    current = coil["values"]["I"]
    sigma, mu = piecewise("electrical_conductivity"), MU_0 * piecewise("relative_permeability")

    dc = dx(tag(coil["group"]))  # the coil body
    S = fem.functionspace(msh, ("Lagrange", 1))
    w, z = ufl.TrialFunction(S), ufl.TestFunction(S)
    reference = float(np.max(sigma.x.array)) or 1.0
    # A tiny conductance outside the coil keeps the system regular (~1e-12 of the current).
    a_v = sigma * ufl.inner(ufl.grad(w), ufl.grad(z)) * dc
    a_v += 1e-12 * reference * ufl.inner(ufl.grad(w), ufl.grad(z)) * dx
    terminals = {"coil_start": 1.0, "coil_end": 0.0}
    bcs_v = [
        dirichlet(
            terminals[f["type"]], fem.locate_dofs_topological(S, 2, facets.find(tag(f["group"]))), S
        )
        for f in case["features"]
        if f["type"] in terminals
    ]
    if len(bcs_v) != 2:
        raise SystemExit("the coil needs one coil_start and one coil_end face group")
    potential = solve_linear(a_v, fem.Constant(msh, 0.0) * z * dx, bcs_v, S, "coil")
    # With a unit potential drop, the current equals the power drawn.
    unit_current = integral(sigma * ufl.inner(ufl.grad(potential), ufl.grad(potential)) * dc)
    J = -(current / unit_current) * sigma * ufl.grad(potential)  # in the coil; zero elsewhere

    N = fem.functionspace(msh, ("N1curl", degree))
    A, q = ufl.TrialFunction(N), ufl.TestFunction(N)
    # The curl-curl operator is singular on gradients; a small mass term picks one gauge.
    size = np.ptp(msh.geometry.x, axis=0).max()
    gauge = 1e-6 / (MU_0 * size**2)
    a = (1 / mu) * ufl.inner(ufl.curl(A), ufl.curl(q)) * dx + gauge * ufl.inner(A, q) * dx
    L = ufl.inner(J, q) * dc
    zero = fem.Function(N)
    bcs = [
        fem.dirichletbc(zero, fem.locate_dofs_topological(N, 2, facets.find(tag(f["group"]))))
        for f in case["features"]
        if f["type"] in ("coil_start", "coil_end", "magnetic_insulation")
    ]
    Ah = solve_linear(a, L, bcs, N, "a")
    B = ufl.curl(Ah)
    out["coords"] = fem.functionspace(msh, ("Lagrange", degree)).tabulate_dof_coordinates()
    out["magnetic_flux_density"] = project(B, msh, degree, dx, "b")
    out["current_density"] = project(J, msh, degree, dx, "j", region=dc)
    out["magnetic_energy"] = np.array(integral(0.5 / mu * ufl.inner(B, B) * dx))


if __name__ == "__main__":
    main()
