"""Puzzle's FEniCSx solver script. Runs inside the FEniCSx environment, not in Puzzle.

    python study.py            (reads case.json and mesh.msh; writes results.npz)

case.json says what to solve:
    {"physics": "solid_mechanics" | "heat_transfer" | "electrostatics" | "magnetic_fields",
     "step": {"type": "stationary" | "modal" | "transient" | "frequency_domain",
              "modes": 10, "time_step": 10.0, "steps": 60,             (transients)
              "hht_alpha": 0.0, "rayleigh": [alpha_M, beta_K],      (structural dynamics)
              "frequencies": [100.0, ...],                           (harmonic, Hz)
              "scheme": "backward_euler" | "crank_nicolson" | "bdf2" | "theta", "theta": 1.0},
     "initial_temperature": 293.15,
     "materials": [{"group": "Bar/Body", "properties": {"youngs_modulus": ..., ...},
                    "tables": {"thermal_conductivity": [[T, k], ...]}}],   (heat only)
     "features": [{"type": "fixed", "group": "Bar/Root", "values": {...}, "area": ...,
                   "fields": {"p": "field-0-p.npz"}}]}       (values from imported fields)
Values are SI. Group names are Gmsh physical group names in mesh.msh.

results.npz holds `coords` (one row per output node) and, as computed, `displacement`,
`stress` (xx, yy, zz, xy, yz, zx), `temperature`, `frequencies` (Hz) and `modes`
(mode x node x 3), `times` and `temperature_series` (step x node) for transients,
`dynamic_times`, `displacement_series` and `stress_series` for structural transients,
`harmonic_frequencies`, `displacement_real` and `displacement_imag` for harmonic steps,
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
    SETTINGS.update(case.get("settings", {}))
    planar = case.get("dimension", "3d") != "3d"  # ADR 0048: a 2D model in the XY plane
    data = read_from_msh("mesh.msh", MPI.COMM_WORLD, 0, gdim=2 if planar else 3)
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

    for f in case["features"]:  # ADR 0044: values from imported fields, as functions
        for name, file in f.get("fields", {}).items():
            f["values"][name] = face_field(file, msh, degree)

    load_trackers()
    step = case["step"]
    transient = step["type"] == "transient"
    emit(
        "step",
        type=step["type"],
        end_time=float(step.get("time_step", 1.0)) * int(step.get("steps", 1))
        if transient
        else None,
    )
    out: dict[str, np.ndarray] = {"backend": np.array(BACKEND)}
    solve = {
        "solid_mechanics": solve_solid,
        "heat_transfer": solve_heat,
        "electrostatics": solve_electrostatics,
        "magnetic_fields": solve_magnetostatics,
    }.get(case["physics"])
    if case["physics"] == "custom_pde":
        solve_custom(case, msh, degree, cells, facets, tag, piecewise, out)
    elif case["physics"] == "solid_mechanics" and planar:
        solve_solid_2d(case, msh, degree, dx, ds, facets, tag, piecewise, out)
    elif case["physics"] == "heat_transfer":
        solve_heat(case, msh, degree, dx, ds, facets, tag, of_temperature, out)
    else:
        solve(case, msh, degree, dx, ds, facets, tag, piecewise, out)
    if not transient and step["type"] != "frequency_domain":  # one solve: one increment
        if "displacement" in out:
            track("displacement", out["coords"], out["displacement"], 1)
        if "temperature" in out:
            track("temperature", out["coords"], out["temperature"], 1)
        emit("converged", increment=1)
    np.savez("results.npz", **out)


# --- linear algebra, PETSc or SciPy ----------------------------------------------

# ADR 0023: case.json's "settings" (only what the study set): the linear method and
# PETSc options of each field's own solve, the nonlinear tolerance and iteration
# limit, and output every n-th time step.
METHODS = {
    "lu": None,  # the default: LU (MUMPS where PETSc has it)
    "cg_gamg": {"ksp_type": "cg", "pc_type": "gamg", "ksp_rtol": 1e-10},
    "cg_jacobi": {"ksp_type": "cg", "pc_type": "jacobi", "ksp_rtol": 1e-10},
    "gmres_gamg": {"ksp_type": "gmres", "pc_type": "gamg", "ksp_rtol": 1e-10},
}


SETTINGS: dict = {}  # case.json's "settings", filled by main()


def setting(key: str, default):
    return SETTINGS.get(key, default)


# --- progress (ADR 0054) ----------------------------------------------------------------
# Events go to the log as tagged JSON lines, which Puzzle's FEniCSx parser reads; the
# kinds and fields are puzzle.progress's. Tracked probes come from trackers.json, which
# Puzzle writes beside this script when a probe is followed while solving.

TAG = "PUZZLE-EVENT "
TRACKERS: list[dict] = []  # [{"name", "field", "what", "reduce", "unit", "points"}]


def emit(kind: str, **data) -> None:
    print(TAG + json.dumps({"kind": kind, **data}), flush=True)


def load_trackers() -> None:
    if os.path.exists("trackers.json"):
        with open("trackers.json") as f:
            TRACKERS[:] = json.load(f)


def track(field: str, coords: np.ndarray, values: np.ndarray, increment: int, time=None) -> None:
    """Each tracker of `field` from nodal `values` (one row per row of `coords`): the
    nearest node to each of its points, reduced as Puzzle's Tracker.value does."""
    values = np.asarray(values, dtype=float)
    values = values[:, None] if values.ndim == 1 else values
    for t in TRACKERS:
        if t["field"] != field:
            continue
        if t.get("_n") != len(coords):  # nearest rows, found once per output space
            from scipy.spatial import cKDTree

            points = np.asarray(t["points"], dtype=float).reshape(-1, 3)
            t["_idx"] = cKDTree(np.asarray(coords)[:, :3]).query(points)[1]
            t["_n"] = len(coords)
        rows = values[t["_idx"]]
        if t["what"] == "total":
            per = np.linalg.norm(rows, axis=1)
        else:
            per = rows[:, {"x": 0, "y": 1, "z": 2}.get(t["what"], 0)]
        reduce = {"min": np.min, "mean": np.mean}.get(t["reduce"], np.max)
        emit("tracked", name=t["name"], increment=increment, time=time,
             value=float(reduce(per)), unit=t.get("unit", ""))  # fmt: skip


_EQUATIONS: set = set()


def equations(V, name: str) -> None:
    """The size of each field's system, once per field (a Resource event)."""
    if name in _EQUATIONS:
        return
    _EQUATIONS.add(name)
    index = V.dofmap.index_map
    emit("resource", equations=int(index.size_global * V.dofmap.index_map_bs))


def solve_linear(a, L, bcs, V, name: str, *, mass_matrix: bool = False) -> fem.Function:
    """Solve a(u, v) = L(v) with Dirichlet conditions `bcs` for a Function on V."""
    method, extra = setting("linear_method", "lu"), setting("petsc_options", {})
    if not mass_matrix:
        equations(V, name)
    if BACKEND != "petsc" and not mass_matrix and (method != "lu" or extra):
        raise SystemExit(
            "this dolfinx has no PETSc (SciPy backend): the linear method and PETSc options "
            "can't be applied; leave them at their defaults"
        )
    if BACKEND == "petsc":
        options = (
            {"ksp_type": "cg", "pc_type": "jacobi", "ksp_rtol": 1e-12}
            if mass_matrix
            else METHODS[method]
            or {"ksp_type": "preonly", "pc_type": "lu", "pc_factor_mat_solver_type": LU_PACKAGE}
        )
        if not mass_matrix:  # the user's options over everything (the pass-through)
            options = {**options, **extra}
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


def face_field(file: str, msh, degree: int) -> fem.Function:
    """An imported field's nodal values on a selection (ADR 0044) as a Lagrange function
    of the mesh's degree: exact at the selection's nodes, so on its faces it is the
    field's finite-element interpolant (elsewhere it is never integrated)."""
    data = np.load(file)
    S = fem.functionspace(msh, ("Lagrange", degree))
    u = fem.Function(S)
    u.x.array[:] = interpolate_nodal(data["coords"], data["values"], S.tabulate_dof_coordinates())
    return u


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
    loads = []  # (load form, its amplitude table or None): dynamics scale each in time

    def add_load(term, f):
        nonlocal L
        L += term
        loads.append((term, np.asarray(f["amplitude"], dtype=float) if "amplitude" in f else None))

    if thermal is not None:
        L += ufl.inner(lmbda * ufl.tr(thermal) * ufl.Identity(3) + 2 * mu * thermal, eps(v)) * dx
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "fixed":
            bcs.append(dirichlet(np.zeros(3), fem.locate_dofs_topological(V, 2, where), V))
        elif f["type"] == "displacement" and "cylindrical" in f:  # (r, θ, z) at each point
            cyl = f["cylindrical"]
            o, ax, (ur, ut, uz) = (np.asarray(w, dtype=float) for w in
                                   (cyl["origin"], cyl["axis"], cyl["local"]))  # fmt: skip

            def field(X, o=o, ax=ax, ur=ur, ut=ut, uz=uz):
                d = X.T - o
                p = d - np.outer(d @ ax, ax)
                er = p / np.maximum(np.linalg.norm(p, axis=1, keepdims=True), 1e-300)
                et = np.cross(ax, er)
                return (ur * er + ut * et + uz * ax).T

            target = fem.Function(V)
            target.interpolate(field)
            bcs.append(fem.dirichletbc(target, fem.locate_dofs_topological(V, 2, where)))
        elif f["type"] == "displacement":
            for i, axis in enumerate("xyz"):
                if axis in vals:
                    dofs = fem.locate_dofs_topological(V.sub(i), 2, where)
                    bcs.append(dirichlet(vals[axis], dofs, V.sub(i)))
        elif f["type"] == "force" and not modal:
            traction = np.array([vals.get(c, 0.0) for c in "xyz"]) / f["area"]
            add_load(ufl.inner(fem.Constant(msh, traction), v) * ds(tag(f["group"])), f)
        elif f["type"] == "pressure" and not modal:
            add_load(-vals["p"] * ufl.inner(n, v) * ds(tag(f["group"])), f)
        elif f["type"] == "traction" and not modal:  # force per area (ADR 0044)
            t = np.array([vals.get(c, 0.0) for c in ("tx", "ty", "tz")])
            add_load(ufl.inner(fem.Constant(msh, t), v) * ds(tag(f["group"])), f)
        elif f["type"] == "body_force" and not modal:  # force per volume, no density
            b = np.array([vals.get(c, 0.0) for c in ("fx", "fy", "fz")])
            add_load(ufl.inner(fem.Constant(msh, b), v) * dx(tag(f["group"])), f)
        elif f["type"] == "elastic_support":  # a foundation: kn normal, kt tangential
            kn, kt = vals.get("kn", 0.0), vals.get("kt", 0.0)
            un, vn = ufl.dot(u, n), ufl.dot(v, n)
            a += kn * un * vn * ds(tag(f["group"]))
            if kt:
                a += kt * ufl.inner(u - un * n, v - vn * n) * ds(tag(f["group"]))
        elif f["type"] == "remote_force" and not modal:  # RBE3-equivalent traction
            t0, twist, centre = (ufl.as_vector(w) for w in f["traction"])
            t = t0 + ufl.cross(twist, ufl.SpatialCoordinate(msh) - centre)
            add_load(ufl.inner(t, v) * ds(tag(f["group"])), f)
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
            add_load(rho * ufl.inner(b, v) * dx(tag(f["group"])), f)

    out["coords"] = V.tabulate_dof_coordinates()
    if case["step"]["type"] in ("transient", "frequency_domain"):
        rho = piecewise("density")
        stress = _stress_projector(msh, degree, dx, sigma, V)
        dynamics = solve_transient if case["step"]["type"] == "transient" else solve_harmonic
        dynamics(case["step"], a, ufl.inner(rho * u, v) * dx, loads, bcs, V, stress, out)
        return
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


# --- structural dynamics (ADR 0041) -----------------------------------------------------
# Matrices are assembled once, without boundary conditions, and the held dofs eliminated:
# SciPy factors the effective matrix once, on either backend (PETSc or not).


def _scipy(form):
    m = fem.assemble_matrix(fem.form(form))
    m.scatter_reverse()
    return m.to_scipy().tocsr()


def _vector(form, size: int) -> np.ndarray:
    b = fem.assemble_vector(fem.form(form))
    b.scatter_reverse(la.InsertMode.add)
    return b.array[:size].copy()


def _held(bcs, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The dofs the boundary conditions hold, and their values."""
    values = np.zeros(size)
    dofs = []
    for bc in bcs:
        dofs.append(bc.dof_indices()[0])
        bc.set(values)
    held = np.unique(np.concatenate(dofs)) if dofs else np.zeros(0, dtype=np.int32)
    held = held[held < size]
    return held, values[held]


def _stress_projector(msh, degree, dx, sigma, V):
    """A function from a displacement array to nodal stress (xx, yy, zz, xy, yz, zx):
    the L2 projection, its mass matrix factored once."""
    from scipy.sparse.linalg import splu

    S = fem.functionspace(msh, ("Lagrange", degree, (6,)))
    p, q = ufl.TrialFunction(S), ufl.TestFunction(S)
    mass = splu(_scipy(ufl.inner(p, q) * dx).tocsc())
    uh = fem.Function(V)
    s = sigma(uh)
    voigt = ufl.as_vector([s[0, 0], s[1, 1], s[2, 2], s[0, 1], s[1, 2], s[2, 0]])
    rhs = fem.form(ufl.inner(voigt, q) * dx)
    size = S.dofmap.index_map.size_local * 6

    def project(values: np.ndarray) -> np.ndarray:
        uh.x.array[: len(values)] = values
        uh.x.scatter_forward()
        b = fem.assemble_vector(rhs)
        b.scatter_reverse(la.InsertMode.add)
        return mass.solve(b.array[:size]).reshape(-1, 6)

    return project


def _load_vectors(loads, size: int):
    """Each load's vector and amplitude table; the constant ones summed into one."""
    steady = np.zeros(size)
    varying = []
    for term, table in loads:
        vector = _vector(term, size)
        if table is None:
            steady += vector
        else:
            varying.append((vector, table))
    return lambda t: (
        steady
        + sum(
            (np.interp(t, table[:, 0], table[:, 1]) * vec for vec, table in varying), np.zeros(size)
        )
    )


def solve_transient(step, a, m, loads, bcs, V, stress, out) -> None:
    """Newmark's average acceleration (beta 1/4, gamma 1/2) or HHT-alpha (alpha in
    [-1/3, 0], beta = (1 - alpha)^2 / 4, gamma = 1/2 - alpha), with Rayleigh damping
    C = alpha_M M + beta_K K; from rest, the loads at their t = 0 values (a sudden load)."""
    from scipy.sparse.linalg import splu

    size = V.dofmap.index_map.size_local * 3
    K, M = _scipy(a)[:size, :size], _scipy(m)[:size, :size]
    am, bk = step.get("rayleigh", [0.0, 0.0])
    C = am * M + bk * K
    force = _load_vectors(loads, size)
    held, prescribed = _held(bcs, size)
    free = np.setdiff1d(np.arange(size), held)
    alpha = float(step.get("hht_alpha", 0.0))
    beta, gamma = (1 - alpha) ** 2 / 4, 0.5 - alpha
    dt, steps = float(step["time_step"]), int(step["steps"])
    u, vel = np.zeros(size), np.zeros(size)
    u[held] = prescribed
    acc = np.zeros(size)
    Mff = M[free][:, free]
    acc[free] = splu(Mff.tocsc()).solve((force(0.0) - K @ u - C @ vel)[free])
    A = (M + (1 + alpha) * (gamma * dt * C + beta * dt * dt * K))[free][:, free]
    factor = splu(A.tocsc())
    every = int(setting("output_every", 1))
    coords = V.tabulate_dof_coordinates()
    times, series, stresses = [], [], []
    for k in range(1, steps + 1):
        t = k * dt
        t_alpha = t + alpha * dt  # HHT's load time (alpha <= 0)
        u_pred = u + dt * vel + dt * dt * (0.5 - beta) * acc
        v_pred = vel + dt * (1 - gamma) * acc
        u_pred[held], v_pred[held] = prescribed, 0.0
        rhs = force(t_alpha) - (1 + alpha) * (C @ v_pred + K @ u_pred) + alpha * (C @ vel + K @ u)
        new_acc = np.zeros(size)
        new_acc[free] = factor.solve(rhs[free])
        u = u_pred + beta * dt * dt * new_acc
        vel = v_pred + gamma * dt * new_acc
        acc = new_acc
        emit("increment", number=k, size=dt, time=t, end_time=dt * steps)
        if TRACKERS:
            track("displacement", coords, u.reshape(-1, 3), k, t)
        emit("converged", increment=k, iterations=1, time=t)
        if k % every == 0:
            times.append(t)
            series.append(u.reshape(-1, 3).copy())
            stresses.append(stress(u))
    out["dynamic_times"] = np.asarray(times)
    out["displacement_series"] = np.asarray(series)
    out["stress_series"] = np.asarray(stresses)


def solve_harmonic(step, a, m, loads, bcs, V, stress, out) -> None:
    """(K + i w C - w^2 M) U = F at each frequency, F the loads at their full values;
    held dofs prescribed (in phase). Displacement as real and imaginary parts."""
    from scipy.sparse.linalg import splu

    size = V.dofmap.index_map.size_local * 3
    K, M = _scipy(a)[:size, :size], _scipy(m)[:size, :size]
    am, bk = step.get("rayleigh", [0.0, 0.0])
    C = am * M + bk * K
    F = _load_vectors([(term, None) for term, _ in loads], size)(0.0)
    held, prescribed = _held(bcs, size)
    free = np.setdiff1d(np.arange(size), held)
    real, imag = [], []
    for hz in step["frequencies"]:
        w = 2 * np.pi * hz
        Z = (K + 1j * w * C - w * w * M).tocsr()
        U = np.zeros(size, dtype=complex)
        U[held] = prescribed
        rhs = F - Z[:, held] @ U[held]
        number = len(real) + 1
        emit("increment", number=number, time=number, end_time=len(step["frequencies"]))
        U[free] = splu(Z[free][:, free].tocsc()).solve(rhs[free].astype(complex))
        emit("converged", increment=number, iterations=1, time=number)
        real.append(U.real.reshape(-1, 3))
        imag.append(U.imag.reshape(-1, 3))
    out["harmonic_frequencies"] = np.asarray(step["frequencies"], dtype=float)
    out["displacement_real"] = np.asarray(real)
    out["displacement_imag"] = np.asarray(imag)


def solve_solid_2d(case, msh, degree, dx, ds, facets, tag, piecewise, out) -> None:
    """Plane stress, plane strain or axisymmetric elasticity (ADR 0048), Y the axis and
    X the radius. Every integral carries the part's weight: its thickness (plane stress;
    1 m of depth in plane strain) or 2 pi r, so loads given as totals are consistent."""
    V = fem.functionspace(msh, ("Lagrange", degree, (2,)))
    fdim = msh.topology.dim - 1
    E, nu = piecewise("youngs_modulus"), piecewise("poissons_ratio")
    mu = E / (2 * (1 + nu))
    lmbda = E * nu / ((1 + nu) * (1 - 2 * nu))
    axi = case["dimension"] == "2d_axisymmetric"
    if not axi and case.get("planar_model") == "plane_stress":
        lmbda = 2 * lmbda * mu / (lmbda + 2 * mu)
    x = ufl.SpatialCoordinate(msh)
    weight = 2 * np.pi * x[0] if axi else piecewise("thickness")
    I2 = ufl.Identity(2)

    def parts(w):
        """In-plane strain and stress, and the out-of-plane (hoop) strain and stress."""
        e = ufl.sym(ufl.grad(w))
        hoop = w[0] / x[0] if axi else 0.0
        tr = ufl.tr(e) + hoop
        return e, hoop, lmbda * tr * I2 + 2 * mu * e, lmbda * tr + 2 * mu * hoop

    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    _eu, _hu, su, shu = parts(u)
    ev, hv, _, _ = parts(v)
    a = (ufl.inner(su, ev) + (shu * hv if axi else 0)) * weight * dx
    n = ufl.FacetNormal(msh)
    bcs, L = [], ufl.inner(fem.Constant(msh, np.zeros(2)), v) * dx
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "fixed":
            bcs.append(dirichlet(np.zeros(2), fem.locate_dofs_topological(V, fdim, where), V))
        elif f["type"] == "displacement":
            for i, axis in enumerate("xy"):
                if axis in vals:
                    dofs = fem.locate_dofs_topological(V.sub(i), fdim, where)
                    bcs.append(dirichlet(vals[axis], dofs, V.sub(i)))
        elif f["type"] == "force":  # the total over the edge, spread over its weight
            traction = np.array([vals.get(c, 0.0) for c in "xy"]) / f["area"]
            L += ufl.inner(fem.Constant(msh, traction), v) * weight * ds(tag(f["group"]))
        elif f["type"] == "pressure":
            L += -vals["p"] * ufl.inner(n, v) * weight * ds(tag(f["group"]))
        elif f["type"] == "elastic_support":
            kn, kt = vals.get("kn", 0.0), vals.get("kt", 0.0)
            un, vn = ufl.dot(u, n), ufl.dot(v, n)
            a += kn * un * vn * weight * ds(tag(f["group"]))
            if kt:
                a += kt * ufl.inner(u - un * n, v - vn * n) * weight * ds(tag(f["group"]))
        else:
            raise SystemExit(f"{f['type']} is not written for 2D models")
    uh = solve_linear(a, L, bcs, V, "u")
    out["coords"] = V.tabulate_dof_coordinates()
    disp = uh.x.array.reshape(-1, 2)
    out["displacement"] = np.column_stack([disp, np.zeros(len(disp))])
    e, _hoop, s, s_hoop = parts(uh)
    zero = 0.0 * s[0, 0]
    if axi:
        zz = s_hoop
    elif case.get("planar_model") == "plane_strain":
        zz = lmbda * ufl.tr(e)
    else:
        zz = zero
    voigt = ufl.as_vector([s[0, 0], s[1, 1], zz, s[0, 1], zero, zero])
    S = fem.functionspace(msh, ("Lagrange", degree, (6,)))
    p, q = ufl.TrialFunction(S), ufl.TestFunction(S)
    mass, rhs = ufl.inner(p, q) * dx, ufl.inner(voigt, q) * dx
    out["stress"] = solve_linear(mass, rhs, [], S, "s", mass_matrix=True).x.array.reshape(-1, 6)


def solve_heat(case, msh, degree, dx, ds, facets, tag, of_temperature, out) -> None:
    """Heat conduction; temperature tables make it nonlinear, solved by Picard iteration
    (properties at the previous iterate), which needs only linear solves on any backend."""
    V = fem.functionspace(msh, ("Lagrange", degree))
    T, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    fdim = msh.topology.dim - 1
    # Axisymmetric (ADR 0048): every integral weighted by the radius x (2 pi cancels);
    # in plane, the thickness cancels as every load here is per area.
    r = ufl.SpatialCoordinate(msh)[0] if case.get("dimension") == "2d_axisymmetric" else 1.0
    previous = fem.Function(V)  # the last iterate
    previous.x.array[:] = case["initial_temperature"]
    nonlinear = any(m.get("tables") for m in case["materials"])
    k = r * of_temperature("thermal_conductivity", previous)
    a = k * ufl.inner(ufl.grad(T), ufl.grad(v)) * dx
    L = fem.Constant(msh, 0.0) * v * dx
    bcs, films, amplitudes = [], [], []
    for f in case["features"]:
        where, vals = facets.find(tag(f["group"])), f.get("values", {})
        if f["type"] == "temperature":
            bcs.append(dirichlet(vals["T"], fem.locate_dofs_topological(V, fdim, where), V))
        elif f["type"] == "heat_flux":
            factor = 1.0
            if "amplitude" in f:  # ADR 0037: q times the amplitude at each step's time
                factor = fem.Constant(msh, 0.0)
                amplitudes.append((factor, np.asarray(f["amplitude"], dtype=float)))
            L += r * vals["q"] * factor * v * ds(tag(f["group"]))
        elif f["type"] == "convection":
            films.append((r * vals["h"], tag(f["group"])))
            a += r * vals["h"] * T * v * ds(tag(f["group"]))
            L += r * vals["h"] * vals["T_inf"] * v * ds(tag(f["group"]))
    out["coords"] = V.tabulate_dof_coordinates()

    increment = [1]  # the time step Picard's iterations belong to

    def picard(a_form, L_form, iterate: fem.Function) -> fem.Function:
        if not nonlinear:
            return solve_linear(a_form, L_form, bcs, V, "t")
        limit, tolerance = setting("max_iterations", 100), setting("tolerance", 1e-10)
        for i in range(1, limit + 1):
            current = solve_linear(a_form, L_form, bcs, V, "t")
            change = np.abs(current.x.array - iterate.x.array).max()
            scale = max(np.abs(current.x.array).max(), 1.0)
            iterate.x.array[:] = current.x.array
            emit("iteration", increment=increment[0], number=i,
                 residuals={"temperature change": change / scale},
                 criteria={"temperature change": tolerance})  # fmt: skip
            if change <= tolerance * scale:
                break
        else:
            raise SystemExit(f"temperature iteration did not converge in {limit} steps")
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
    capacity = r * rho * cp / dt

    def operator_on(u):
        k_u = r * of_temperature("thermal_conductivity", u)
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
        increment[0], t = n + 1, (n + 1) * dt
        emit("increment", number=n + 1, size=dt, time=t, end_time=dt * steps)
        current = picard(a_t, L_t, previous)
        older.x.array[:] = old.x.array
        old.x.array[:] = current.x.array
        track("temperature", out["coords"], current.x.array, n + 1, t)
        emit("converged", increment=n + 1, time=t)
        series.append(current.x.array.copy())
    every = setting("output_every", 1)
    out["times"] = (dt * np.arange(1, steps + 1))[every - 1 :: every]
    out["temperature_series"] = np.asarray(series)[every - 1 :: every]


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

    increment = [1]

    def newton() -> None:
        for bc in bcs:
            bc.set(u.x.array)  # start from the boundary values; corrections keep them
        zero = [dirichlet(0.0, dofs, space) for dofs, space in held]
        limit, tolerance = setting("max_iterations", 50), setting("tolerance", 1e-10)
        for i in range(1, limit + 1):
            delta = solve_linear(J, -F, zero, V, "newton")
            u.x.array[:] += delta.x.array
            change = np.abs(delta.x.array).max() / max(np.abs(u.x.array).max(), 1.0)
            emit("iteration", increment=increment[0], number=i,
                 residuals={"correction": change}, criteria={"correction": tolerance})  # fmt: skip
            if change <= tolerance:
                return
        raise SystemExit(f"custom PDE: Newton did not converge in {limit} iterations")

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
    for n in range(1, steps + 1):
        increment[0], t = n, n * float(dt.value)
        emit("increment", number=n, size=float(dt.value), time=t, end_time=steps * float(dt.value))
        newton()
        u_n.x.array[:] = u.x.array
        series.append(nodal())
        emit("converged", increment=n, time=t)
    every = setting("output_every", 1)
    out["times"] = (float(dt.value) * np.arange(1, steps + 1))[every - 1 :: every]
    out[f"series_{name}"] = np.asarray(series)[every - 1 :: every]


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
