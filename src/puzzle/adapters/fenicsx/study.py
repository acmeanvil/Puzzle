"""Puzzle's FEniCSx solver script. Runs inside the FEniCSx environment, not in Puzzle.

    python study.py            (reads case.json and mesh.msh; writes results.npz)

case.json says what to solve:
    {"physics": "solid_mechanics" | "heat_transfer" | "electrostatics" | "magnetic_fields",
     "step": {"type": "stationary" | "modal" | "transient", "modes": 10,
              "time_step": 10.0, "steps": 60,           (transient heat only)
              "scheme": "backward_euler" | "crank_nicolson" | "bdf2" | "theta", "theta": 1.0},
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
    }.get(case["physics"])
    if case["physics"] == "custom_pde":
        solve_custom(case, msh, degree, cells, facets, tag, piecewise, out)
    elif case["physics"] == "heat_transfer":
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


def interpolate_nodal(points: np.ndarray, values: np.ndarray, at: np.ndarray) -> np.ndarray:
    """Nodal values given at `points`, at coordinates `at`: exact at mesh nodes, linear
    (inverse-distance over the 4 nearest) between them, for higher-order dofs."""
    from scipy.spatial import cKDTree

    distance, index = cKDTree(points).query(at, k=min(4, len(points)))
    distance, index = np.atleast_2d(distance), np.atleast_2d(index)
    exact = distance[:, 0] < 1e-12 * max(np.ptp(points), 1.0)
    weights = 1.0 / np.maximum(distance, 1e-300)
    out = (weights * values[index]).sum(axis=1) / weights.sum(axis=1)
    out[exact] = values[index[exact, 0]]
    return out


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
    # A transferred temperature field: thermal strain alpha (T - T_ref), isotropic.
    thermal = None
    if "temperature_load" in case and case["step"]["type"] != "modal":
        load = case["temperature_load"]
        data = np.load(load["file"])
        S = fem.functionspace(msh, ("Lagrange", degree))
        T = fem.Function(S)
        T.x.array[:] = interpolate_nodal(
            data["coords"], data["values"], S.tabulate_dof_coordinates()
        )
        alpha = piecewise("thermal_expansion")
        thermal = alpha * (T - load["reference"]) * ufl.Identity(3)
    n = ufl.FacetNormal(msh)
    modal = case["step"]["type"] == "modal"
    bcs, L = [], ufl.inner(fem.Constant(msh, np.zeros(3)), v) * dx
    if thermal is not None:
        L += ufl.inner(lmbda * ufl.tr(thermal) * ufl.Identity(3) + 2 * mu * thermal, eps(v)) * dx
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
        elif f["type"] == "remote_force" and not modal:  # RBE3-equivalent traction
            t0, twist, centre = (ufl.as_vector(w) for w in f["traction"])
            t = t0 + ufl.cross(twist, ufl.SpatialCoordinate(msh) - centre)
            L += ufl.inner(t, v) * ds(tag(f["group"]))
        elif f["type"] == "remote_displacement":  # the face moves rigidly
            u0, theta, point = (np.asarray(w, dtype=float) for w in f["motion"])
            motion = fem.Function(V)
            motion.interpolate(
                lambda X, u0=u0, theta=theta, point=point: (
                    u0[:, None] + np.cross(theta, (X - point[:, None]).T).T
                )
            )
            bcs.append(fem.dirichletbc(motion, fem.locate_dofs_topological(V, 2, where)))
        elif f["type"] in ("gravity", "acceleration", "rotation") and not modal:
            # A body load (ADR 0037): rho * b per volume over the body's cells.
            load, rho = f["load"], piecewise("density")
            if load["uniform"] is not None:
                b = fem.Constant(msh, np.asarray(load["uniform"], dtype=float))
            else:
                x = ufl.SpatialCoordinate(msh)
                c = ufl.as_vector(load["centre"])
                axis = ufl.as_vector(load["axis"])
                d = x - c
                b = load["omega"] ** 2 * (d - ufl.dot(d, axis) * axis)
            L += rho * ufl.inner(b, v) * dx(tag(f["group"]))

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
    if thermal is not None:  # stress is C : (strain - thermal strain)
        s = s - (lmbda * ufl.tr(thermal) * ufl.Identity(3) + 2 * mu * thermal)
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
    bcs, films, amplitudes = [], [], []
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "temperature":
            bcs.append(dirichlet(vals["T"], fem.locate_dofs_topological(V, 2, where), V))
        elif f["type"] == "heat_flux":
            factor = 1.0
            if "amplitude" in f:  # ADR 0037: q times the amplitude at each step's time
                factor = fem.Constant(msh, 0.0)
                amplitudes.append((factor, np.asarray(f["amplitude"], dtype=float)))
            L += vals["q"] * factor * v * ds(tag(f["group"]))
        elif f["type"] == "convection":
            films.append((vals["h"], tag(f["group"])))
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

    # rho cp dT/dt + A(T) = F, with A the conduction (+ film) operator and F the loads.
    # theta method: rho cp (T - T_old)/dt + theta A(T) + (1 - theta) A(T_old) = F;
    # bdf2: rho cp (3T - 4T_old + T_older)/(2 dt) + A(T) = F, its first step by Euler.
    rho, cp = of_temperature("density", previous), of_temperature("specific_heat", previous)
    dt, steps = case["step"]["time_step"], case["step"]["steps"]
    scheme = case["step"].get("scheme", "backward_euler")
    old, older = fem.Function(V), fem.Function(V)
    old.x.array[:] = older.x.array[:] = case["initial_temperature"]
    capacity = rho * cp / dt

    def operator_on(u):
        k_u = of_temperature("thermal_conductivity", u)
        form = k_u * ufl.inner(ufl.grad(u), ufl.grad(v)) * dx
        for h, where in films:
            form += h * u * v * ds(where)
        return form

    euler = (capacity * T * v * dx + a, capacity * old * v * dx + L)
    if scheme == "bdf2":
        later = (1.5 * capacity * T * v * dx + a, capacity * (2.0 * old - 0.5 * older) * v * dx + L)
    else:
        theta = case["step"].get("theta") or 1.0
        later = (
            capacity * T * v * dx + theta * a,
            capacity * old * v * dx + L - (1.0 - theta) * operator_on(old),
        )
        if theta == 1.0:
            later = euler
        euler = later  # theta schemes need no starting step
    series = []
    for n in range(steps):
        for factor, table in amplitudes:  # the loads' factors at this step's end time
            factor.value = np.interp((n + 1) * dt, table[:, 0], table[:, 1])
        a_t, L_t = euler if n == 0 else later
        current = picard(a_t, L_t, previous)
        older.x.array[:] = old.x.array
        old.x.array[:] = current.x.array
        series.append(current.x.array.copy())
    out["times"] = dt * np.arange(1, steps + 1)
    out["temperature_series"] = np.asarray(series)


# --- custom PDEs ---------------------------------------------------------------------


class _Named(ufl.Measure):
    """A measure that also takes a selection name: dx("Bar/Body"), ds("Bar/Tip")."""

    tag = None  # set per run: selection name -> physical tag

    def __call__(self, subdomain_id=None, *args, **kwargs):
        if isinstance(subdomain_id, str) and subdomain_id not in ("everywhere", "otherwise"):
            subdomain_id = _Named.tag(subdomain_id)
        return super().__call__(subdomain_id, *args, **kwargs)


# What a weak form may use besides its unknown, test function and coefficients.
UFL_NAMES = {
    name: getattr(ufl, name)
    for name in (
        "grad",
        "div",
        "curl",
        "inner",
        "dot",
        "outer",
        "nabla_grad",
        "nabla_div",
        "sym",
        "tr",
        "det",
        "inv",
        "transpose",
        "Identity",
        "as_vector",
        "as_matrix",
        "sqrt",
        "exp",
        "ln",
        "sin",
        "cos",
        "tan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "erf",
        "conditional",
        "lt",
        "gt",
        "le",
        "ge",
        "eq",
        "ne",
        "max_value",
        "min_value",
        "sign",
        "pi",
        "e",
    )
    if hasattr(ufl, name)
}


def solve_custom(case, msh, degree, cells, facets, tag, piecewise, out) -> None:
    """F(u; v) == 0 from the user's UFL residual, by Newton with the exact Jacobian.

    The weak form is evaluated with no builtins, only UFL operators and these names:
    the unknown `<u>`, its test function `v_<u>`, its previous value `<u>_n` and `dt`
    (transient), the coefficients, `x` (coordinates), `dx` and `ds` (with optional
    selection names) and `prop("<material property>")` (cellwise). Each Newton step is
    a linear solve, so any backend works.
    """
    eq = case["equation"]
    (unknown,) = eq["unknowns"]
    name = unknown["name"]
    shape = (3,) if unknown["shape"] == "vector" else ()
    V = fem.functionspace(msh, ("Lagrange", unknown["degree"], shape))
    u, v, du = fem.Function(V), ufl.TestFunction(V), ufl.TrialFunction(V)
    u_n = fem.Function(V)
    u.x.array[:] = u_n.x.array[:] = eq["initial_value"]
    _Named.tag = staticmethod(tag)
    dx = _Named("dx", domain=msh, subdomain_data=cells)
    ds = _Named("ds", domain=msh, subdomain_data=facets)
    transient = case["step"]["type"] == "transient"
    dt = fem.Constant(msh, float(case["step"].get("time_step", 1.0)))
    names = dict(UFL_NAMES)
    names.update(
        {
            name: u,
            f"v_{name}": v,
            f"{name}_n": u_n,
            "dt": dt,
            "x": ufl.SpatialCoordinate(msh),
            "dx": dx,
            "ds": ds,
            "prop": piecewise,
            **{k: fem.Constant(msh, float(c)) for k, c in eq["coefficients"].items()},
        }
    )
    try:
        F = eval(eq["weak_form"], {"__builtins__": {}}, names)  # noqa: S307 - the user's PDE
    except Exception as exc:  # noqa: BLE001 - report any mistake in the expression
        raise SystemExit(f"weak form: {type(exc).__name__}: {exc}") from None
    bcs, held = [], []  # held: (dofs, space) of each condition, for zero corrections
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "dirichlet":
            if shape:
                for i, axis in enumerate("xyz"):
                    if axis in vals:
                        dofs = fem.locate_dofs_topological(V.sub(i), 2, where)
                        bcs.append(dirichlet(vals[axis], dofs, V.sub(i)))
                        held.append((dofs, V.sub(i)))
            else:
                value = vals.get(name, vals.get("value"))
                if value is None:
                    raise SystemExit(f"dirichlet on {f['group']} needs a '{name}' value")
                dofs = fem.locate_dofs_topological(V, 2, where)
                bcs.append(dirichlet(value, dofs, V))
                held.append((dofs, V))
        elif f["type"] == "neumann":  # a flux g into the body: -g v ds
            g = vals.get(name, vals.get("value", 0.0))
            F = F - g * v * ds(tag(f["group"]))
    J = ufl.derivative(F, u, du)

    def newton() -> None:
        for bc in bcs:
            bc.set(u.x.array)  # start from the boundary values; corrections keep them
        zero = [dirichlet(0.0, dofs, space) for dofs, space in held]
        for _ in range(50):
            delta = solve_linear(J, -F, zero, V, "newton")
            u.x.array[:] += delta.x.array
            if np.abs(delta.x.array).max() <= 1e-10 * max(np.abs(u.x.array).max(), 1.0):
                return
        raise SystemExit("custom PDE: Newton did not converge in 50 iterations")

    out_space = fem.functionspace(msh, ("Lagrange", degree, shape))
    out["coords"] = out_space.tabulate_dof_coordinates()
    out["custom_fields"] = np.array([name])

    def nodal() -> np.ndarray:
        shown = fem.Function(out_space)
        shown.interpolate(u)
        return shown.x.array.reshape(len(out["coords"]), -1).copy()

    if not transient:
        newton()
        out[f"field_{name}"] = nodal()
        return
    steps = case["step"]["steps"]
    series = []
    for _ in range(steps):
        newton()
        u_n.x.array[:] = u.x.array
        series.append(nodal())
    out["times"] = float(dt.value) * np.arange(1, steps + 1)
    out[f"series_{name}"] = np.asarray(series)


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
