"""Mesh helpers shared by solver adapters."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import CellBlock, Group, Mesh

# Volume element faces as corner positions (Gmsh corner order), numbered the way
# CalculiX numbers them (S1, S2, ...). Other solvers only use the parent element.
FACES = {
    "tet4": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "tet10": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "hex8": [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
}

BODY_LOADS = {"gravity", "acceleration", "rotation"}  # ADR 0037: on bodies, per volume
REMOTE = {"remote_force", "remote_force_rigid", "remote_displacement"}  # ADR 0037: on faces
PRETENSION = {"bolt_pretension", "bolt_adjustment"}  # ADR 0037: on a section face
STRUCTURAL = (
    {"fixed", "displacement", "force", "pressure", "elastic_support", "compression_only_support"}
    | BODY_LOADS
    | REMOTE
    | PRETENSION
)
HOLDING = ("fixed", "displacement", "remote_displacement")  # kept as constraints in modal
THERMAL = {"temperature", "heat_flux", "convection"}
FLUID = {"velocity_inlet", "mass_flow_inlet", "pressure_outlet", "wall", "symmetry"}
ELECTROSTATIC = {"electric_potential", "ground", "surface_charge"}
MAGNETIC = {"coil_current", "coil_start", "coil_end", "magnetic_insulation"}


ROOM_K = 293.15


def initial_temperature(physics) -> float:
    """The `initial_temperature` setting of a heat-transfer physics, in K (default 20 °C)."""
    value = physics.settings.get("initial_temperature") if physics is not None else None
    if value is None:
        return ROOM_K
    if isinstance(value, dict):  # a quantity as stored in settings: {"value", "unit"}
        from puzzle.model.schema import Quantity

        value = Quantity(**value)
    return value.to_si() if hasattr(value, "to_si") else float(value)


THERMAL_TABLES = ("thermal_conductivity", "specific_heat", "density")


def thermal_tables(material, component) -> dict[str, list[tuple[float, float]]]:
    """Temperature tables to write for a heat-transfer solve: property -> [(T in K, SI value)].

    A material's tables are used when the component solves heat transfer, unless its
    physics sets `temperature_dependent_materials = false`; properties without a table
    keep their single value. Tables are held constant beyond their ends.
    """
    heat = next((ph for ph in component.physics if ph.type == "heat_transfer"), None)
    if heat is None or not heat.settings.get("temperature_dependent_materials", True):
        return {}
    from puzzle.model.schema import Quantity

    out = {}
    for prop in THERMAL_TABLES:
        table = material.tables.get(prop)
        if table is None or len(table.curve()) < 2:
            continue
        scale = Quantity(value=1.0, unit=table.unit).to_si()  # table values are in its unit
        out[prop] = [(t, v * scale) for t, v in table.curve()]
    return out


def time_settings(step) -> tuple[float, float, int]:
    """(time_step, end_time, number of steps) of a transient solve step, in s."""
    try:
        dt, end = (step.settings[k] for k in ("time_step", "end_time"))
    except KeyError as missing:
        raise InputError(f"a transient step needs a {missing} setting") from None
    dt = dt.to_si() if hasattr(dt, "to_si") else float(dt)
    end = end.to_si() if hasattr(end, "to_si") else float(end)
    if dt <= 0 or end <= 0:
        raise InputError("time_step and end_time must be positive")
    return dt, end, max(1, round(end / dt))


def check_contacts(component, implemented: set[str], solver: str) -> None:
    """Refuse contacts this adapter doesn't write yet.

    Conformal bonded contacts need nothing from a solver (the parts share nodes), so
    they are always accepted. Anything else unwritten would leave the parts unconnected.
    """
    for c in component.contacts:
        if c.capability != "bonded_conformal" and c.capability not in implemented:
            raise InputError(
                f"the {solver} adapter does not write {c.capability} contact ('{c.name}') yet",
                code="unsupported",
                path=c.name,
            )


def refuse_line_bodies(mesh, solver: str) -> None:
    """Refuse beams and shells (line and sheet bodies, ADR 0027) where the adapter
    writes solids only: their elements would otherwise be dropped from the model."""
    what = [w for w, blocks in (("beams (line bodies)", mesh.beam_blocks()),
            ("shells (sheet bodies)", mesh.shell_blocks())) if blocks]  # fmt: skip
    if what:
        raise InputError(
            f"the {solver} adapter does not write {' or '.join(what)} yet; "
            "CalculiX and Code_Aster do",
            code="unsupported",
        )


def refuse_large_deflection(steps, solver: str) -> None:
    """Refuse geometric_nonlinearity where the adapter only writes small-deflection statics."""
    if any(s.settings.get("geometric_nonlinearity") for s in steps):
        raise InputError(
            f"the {solver} adapter does not run geometrically nonlinear steps yet; "
            "CalculiX and Code_Aster do",
            code="unsupported",
        )


TIME_SCHEMES = ("backward_euler", "crank_nicolson", "bdf2", "theta")


def time_scheme(step) -> tuple[str, float | None]:
    """A transient step's time scheme and, for theta schemes, its theta.

    The default is backward Euler on every solver, so shoot-outs compare like with
    like (theta = 1; crank_nicolson is theta = 0.5; "theta" takes a `theta` setting).
    """
    name = str(step.settings.get("time_scheme", "backward_euler"))
    if name not in TIME_SCHEMES:
        raise InputError(
            f"unknown time_scheme '{name}'",
            code="invalid",
            details={"time_schemes": list(TIME_SCHEMES)},
        )
    if name == "theta":
        theta = step.settings.get("theta")
        if theta is None:
            raise InputError("time_scheme 'theta' needs a theta setting (0.5 to 1)")
        theta = float(theta)
        if not 0.5 <= theta <= 1.0:
            raise InputError("theta must be between 0.5 and 1 (unconditionally stable)")
        return name, theta
    return name, {"backward_euler": 1.0, "crank_nicolson": 0.5}.get(name)


class InputError(PuzzleError, ValueError):
    """The study can't be written for this solver; the message says why."""

    code = "invalid_input"


@dataclass
class FaceLookup:
    """Which volume element face each surface element sits on."""

    lookup: dict[frozenset[int], tuple[int, int]]

    @classmethod
    def build(cls, blocks: Iterable[CellBlock]) -> FaceLookup:
        lookup = {}
        for b in blocks:
            if b.element not in FACES:
                raise InputError(f"{b.element} elements are not supported yet")
            for face_no, corners in enumerate(FACES[b.element], start=1):
                keys = b.connectivity[:, corners]
                for eid, key in zip(b.ids, keys, strict=True):
                    lookup[frozenset(key.tolist())] = (int(eid), face_no)
        return cls(lookup)

    def of(self, surface: CellBlock) -> list[tuple[int, int]]:
        """(parent element id, face number) for each element of a surface block."""
        corners = 3 if surface.element.startswith("tri") else 4
        out = []
        for row in surface.connectivity[:, :corners]:
            hit = self.lookup.get(frozenset(row.tolist()))
            if hit is None:
                raise InputError("a surface element does not lie on any volume element face")
            out.append(hit)
        return out


def surface_blocks(group: Group, mesh: Mesh) -> list[CellBlock]:
    blocks = [b for b in group.blocks(mesh) if b.dim == 2]
    if not blocks:
        raise InputError(f"'{group.name}' must be a face selection for surface loads")
    return blocks


def surface_area(group: Group, mesh: Mesh) -> float:
    """Total area of a face selection, from its (flat-sided) triangles."""
    area = 0.0
    for b in surface_blocks(group, mesh):
        if b.element.startswith("quad"):
            # Two triangles per (planar) quad: corners 0-1-2 and 0-2-3.
            q = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3)
            for tri in (q[:, [0, 1, 2]], q[:, [0, 2, 3]]):
                area += float(
                    0.5
                    * np.linalg.norm(
                        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1
                    ).sum()
                )
            continue
        if not b.element.startswith("tri"):
            raise InputError(f"area of {b.element} faces is not supported yet")
        xyz = mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        area += float(
            0.5
            * np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1).sum()
        )
    return area


def match_points(
    points: np.ndarray, reference: np.ndarray, ids: np.ndarray, rel_tol: float = 1e-5
) -> np.ndarray:
    """For each point, the id of the reference point at the same place.

    Solvers write coordinates with less precision than Puzzle keeps (EnSight uses
    float32), so points match within `rel_tol` of the model size. A grid hash checks
    the neighbouring cells too, so values near a cell edge still match.
    """
    size = float(np.linalg.norm(reference.max(axis=0) - reference.min(axis=0))) or 1.0
    tol = rel_tol * size
    grid: dict[tuple[int, int, int], list[int]] = {}
    keys = np.floor(reference / tol).astype(np.int64)
    for i, k in enumerate(map(tuple, keys)):
        grid.setdefault(k, []).append(i)
    out = np.empty(len(points), dtype=np.int64)
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for j, p in enumerate(points):
        k = np.floor(p / tol).astype(np.int64)
        best, best_d = -1, tol
        for o in offsets:
            for i in grid.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ()):
                d = float(np.linalg.norm(reference[i] - p))
                if d <= best_d:
                    best, best_d = i, d
        if best < 0:
            raise InputError(f"result point {p} matches no mesh entity")
        out[j] = ids[best]
    return out


def hydraulic_diameter(group, mesh: Mesh) -> float:
    """4 A / P of a face selection: its area over its outline (edges used once)."""
    from collections import Counter

    edges: Counter = Counter()
    for b in surface_blocks(group, mesh):
        corners = b.connectivity[:, : (3 if b.element.startswith("tri") else 4)]
        for row in corners:
            for a, c in zip(row, np.roll(row, -1), strict=True):
                edges[(min(a, c), max(a, c))] += 1
    outline = [e for e, n in edges.items() if n == 1]
    ends = mesh.node_coords(np.array(outline).ravel()).reshape(-1, 2, 3)
    perimeter = float(np.linalg.norm(ends[:, 1] - ends[:, 0], axis=1).sum())
    return 4 * surface_area(group, mesh) / perimeter


def plastic_strain_tensor(strain, stress, elastic: list[tuple[float, float, list[int]]]):
    """The plastic strain tensor from the total strain and the stress, for solvers that
    write only the equivalent plastic strain (CalculiX): ep = e - [(1 + nu) s - nu tr(s) I]/E,
    exact for small strains. `elastic`: (E, nu, node ids) per material; a node shared by
    two materials takes the last. Tensor components xx, yy, zz, xy, yz, zx (tensor shear)."""
    import numpy as np

    from puzzle.results.model import Field

    ids = strain.ids
    s = stress.at(ids)
    e = strain.values
    young = np.full(len(ids), np.nan)
    poisson = np.full(len(ids), np.nan)
    where = {int(n): i for i, n in enumerate(ids)}
    for E, nu, nodes in elastic:
        index = [where[n] for n in nodes if n in where]
        young[index], poisson[index] = E, nu
    trace = s[:, 0] + s[:, 1] + s[:, 2]
    elastic_strain = (1 + poisson)[:, None] * s / young[:, None]
    elastic_strain[:, :3] -= (poisson * trace / young)[:, None]
    return Field(
        name="plastic_strain",
        location=strain.location,
        components=list(strain.components),
        ids=ids,
        values=e - elastic_strain,
    )


@dataclass(frozen=True)
class BodyLoad:
    """A body load (ADR 0037) as a force per volume rho * b: uniform (gravity, or the
    inertia −a of a linear acceleration) or centrifugal about an axis."""

    uniform: tuple[float, float, float] | None  # b, m/s^2 (force density = rho * b)
    omega: float = 0.0  # rad/s
    centre: tuple[float, float, float] = (0.0, 0.0, 0.0)  # a point on the axis, m
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)  # unit vector

    def at(self, xyz: np.ndarray) -> np.ndarray:
        """b at points (n, 3): the uniform field, or omega^2 times the distance from the
        axis, outward."""
        if self.uniform is not None:
            return np.tile(np.asarray(self.uniform), (len(xyz), 1))
        d = np.asarray(xyz) - np.asarray(self.centre)
        n = np.asarray(self.axis)
        return self.omega**2 * (d - np.outer(d @ n, n))


def body_load(f) -> BodyLoad:
    """The feature's body load, in SI."""
    v = {k: q.to_si() for k, q in f.values.items()}
    if f.type == "gravity":
        return BodyLoad(tuple(v.get(f"g{a}", 0.0) for a in "xyz"))
    if f.type == "acceleration":  # the body's inertia resists it: b = −a
        return BodyLoad(tuple(0.0 - v.get(f"a{a}", 0.0) for a in "xyz"))  # no −0
    if f.type == "rotation":
        given = any(f"n{a}" in v for a in "xyz")  # no axis given: about z, as the catalog
        axis = np.array([v.get(f"n{a}", 0.0) for a in "xyz"]) if given else np.array([0, 0, 1.0])
        if not np.linalg.norm(axis):
            raise InputError(f"rotation on '{f.selection}': its axis has no direction")
        return BodyLoad(
            None,
            v.get("omega", 0.0),
            tuple(v.get(f"{a}0", 0.0) for a in "xyz"),
            tuple(axis / np.linalg.norm(axis)),
        )
    raise InputError(f"'{f.type}' is not a body load")


def body_load_on_material(comp, f, solver: str) -> None:
    """Solvers that assign loads per material body (Elmer, FEniCSx) need a body load on
    a selection a material is assigned to."""
    if f.selection not in {a.selection for a in comp.materials}:
        raise InputError(
            f"{solver} applies a {f.type} load to a body with a material: '{f.selection}' "
            "has none (use the material's body selection)",
            code="unsupported",
        )


# --- remote loads (ADR 0037) -------------------------------------------------------------

# A 6-point rule on the reference triangle, exact to degree 4: (xi, eta, weight).
_TRI_RULE = [
    (0.445948490915965, 0.445948490915965, 0.223381589678011),
    (0.108103018168070, 0.445948490915965, 0.223381589678011),
    (0.445948490915965, 0.108103018168070, 0.223381589678011),
    (0.091576213509771, 0.091576213509771, 0.109951743655322),
    (0.816847572980459, 0.091576213509771, 0.109951743655322),
    (0.091576213509771, 0.816847572980459, 0.109951743655322),
]


def _shape(element: str, xi: float, eta: float):
    """Gmsh-ordered shape functions of a triangle face and their derivatives in xi and
    eta: (N, dN/dxi, dN/deta), tri3 or tri6 (corners, then mid-edges)."""
    z = 1.0 - xi - eta
    if element == "tri3":
        return np.array([z, xi, eta]), np.array([-1.0, 1, 0]), np.array([-1.0, 0, 1])
    n = np.array(
        [z * (2 * z - 1), xi * (2 * xi - 1), eta * (2 * eta - 1), 4 * z * xi, 4 * xi * eta,
         4 * eta * z]
    )  # fmt: skip
    dxi = np.array([1 - 4 * z, 4 * xi - 1, 0.0, 4 * (z - xi), 4 * eta, -4 * eta])
    deta = np.array([1 - 4 * z, 0.0, 4 * eta - 1, -4 * xi, 4 * xi, 4 * (z - eta)])
    return n, dxi, deta


def _quadrature(group, mesh: Mesh):
    """Quadrature over the selection's faces on their own (curved) geometry: for each
    block and point, (connectivity, N, positions (ne, 3), areas dA (ne,))."""
    blocks = surface_blocks(group, mesh)
    if not blocks:
        raise InputError("a remote load needs a face selection")
    for b in blocks:
        if b.element not in ("tri3", "tri6"):
            raise InputError(f"a remote load on {b.element} faces is not supported yet")
        nodes = mesh.node_coords(b.connectivity.ravel()).reshape(len(b.connectivity), -1, 3)
        for xi, eta, w in _TRI_RULE:
            n, dxi, deta = _shape(b.element, xi, eta)
            x = np.einsum("k,eki->ei", n, nodes)
            jac = np.cross(np.einsum("k,eki->ei", dxi, nodes), np.einsum("k,eki->ei", deta, nodes))
            yield b.connectivity, n, x, 0.5 * w * np.linalg.norm(jac, axis=1)


def _face_properties(group, mesh: Mesh):
    """Area, centroid and the second-moment tensor ∫ r rᵀ dA about the centroid."""
    pts = list(_quadrature(group, mesh))
    area = sum(float(da.sum()) for *_, da in pts)
    centroid = sum((x * da[:, None]).sum(axis=0) for _, _, x, da in pts) / area
    rr = sum(np.einsum("ei,ej,e->ij", x - centroid, x - centroid, da) for _, _, x, da in pts)
    return area, centroid, rr


@dataclass(frozen=True)
class RemoteLoad:
    """A force and moment at a point, spread over a face as a distributing coupling
    (RBE3, area-weighted) spreads them: the linear traction
        t(x) = F/A + (K M_c) x (x - c),
    with c the face's centroid, A its area, M_c the moment about c (M + (p - c) x F) and
    K the inverse of the face's polar second-moment tensor  ∫ (|r|² I - r rᵀ) dA, all
    on the faces' own curved geometry. Its resultant is F and its moment about c is M_c,
    exactly."""

    centroid: np.ndarray
    area: float
    uniform: np.ndarray  # F / A
    twist: np.ndarray  # K M_c

    def at(self, xyz) -> np.ndarray:
        r = np.asarray(xyz, float) - self.centroid
        return self.uniform + np.cross(self.twist, r)


def remote_load(group, mesh: Mesh, force, moment, point=None) -> RemoteLoad:
    """The traction that carries `force` and `moment` (at `point`; None: the centroid)."""
    area, centroid, rr = _face_properties(group, mesh)
    polar = np.trace(rr) * np.eye(3) - rr
    force = np.asarray(force, float)
    m_c = np.asarray(moment, float)
    if point is not None:
        m_c = m_c + np.cross(np.asarray(point, float) - centroid, force)
    twist = np.linalg.pinv(polar) @ m_c  # pinv: a straight edge has no twist about itself
    return RemoteLoad(centroid, area, force / area, twist)


def nodal_traction(group, mesh: Mesh, traction) -> dict[int, np.ndarray]:
    """Consistent nodal forces ∫ N_k t dA of a traction field `traction(xyz) -> (n, 3)`
    over the selection's faces (tri3, tri6), node id -> force."""
    out: dict[int, np.ndarray] = {}
    for conn, n, x, da in _quadrature(group, mesh):
        t = traction(x) * da[:, None]
        for k, column in enumerate(conn.T):
            for nid, f in zip(column, n[k] * t, strict=True):
                out[int(nid)] = out.get(int(nid), 0.0) + f
    return out


def remote_values(f) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """A remote force feature's force, moment and point (None: the face centroid)."""
    v = {k: q.to_si() for k, q in f.values.items()}
    force = np.array([v.get(f"F{a}", 0.0) for a in "xyz"])
    moment = np.array([v.get(f"M{a}", 0.0) for a in "xyz"])
    point = np.array([v[f"p{a}"] for a in "xyz"]) if all(f"p{a}" in v for a in "xyz") else None
    return force, moment, point


@dataclass(frozen=True)
class RigidMotion:
    """A prescribed rigid motion of a face: u(x) = u0 + theta x (x - p), small rotations."""

    u0: np.ndarray
    theta: np.ndarray
    point: np.ndarray

    def at(self, xyz) -> np.ndarray:
        return self.u0 + np.cross(self.theta, np.asarray(xyz, float) - self.point)


def rigid_motion(f, group, mesh: Mesh) -> RigidMotion:
    """A remote displacement: every component prescribed (missing ones are zero), about
    its point (default: the face's centroid)."""
    v = {k: q.to_si() for k, q in f.values.items()}
    if all(f"p{a}" in v for a in "xyz"):
        point = np.array([v[f"p{a}"] for a in "xyz"])
    else:
        point = _face_properties(group, mesh)[1]
    return RigidMotion(
        np.array([v.get(f"u{a}", 0.0) for a in "xyz"]),
        np.array([v.get(f"r{a}", 0.0) for a in "xyz"]),
        point,
    )


# --- elastic supports (ADR 0037) -----------------------------------------------------------


def support_stiffness(f) -> tuple[float, float]:
    """An elastic support's normal and tangential stiffness per area (N/m³)."""
    v = {k: q.to_si() for k, q in f.values.items()}
    return v.get("kn", 0.0), v.get("kt", 0.0)


def support_nodes(group, mesh: Mesh):
    """Each face node's share of the area and its normal, for springs to ground. Shares
    are ∫N dA, as consistent loads are, so a uniform state is exact (on tri6 faces that
    puts them on the mid-edge nodes; corners have none). Normals are area-weighted and
    point away from the model's centre. -> (node ids, areas, unit normals (n, 3))"""
    centre = mesh.coords.mean(axis=0)
    share: dict[int, float] = {}
    normal: dict[int, np.ndarray] = {}
    for conn, n, _x, da in _quadrature(group, mesh):
        # Normal from the corner triangle (the same for every quadrature point).
        corners = mesh.node_coords(conn[:, :3].ravel()).reshape(-1, 3, 3)
        nrm = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-300)
        flip = np.einsum("ij,ij->i", nrm, corners.mean(axis=1) - centre) < 0
        nrm[flip] *= -1
        weights = n
        for k, column in enumerate(conn.T):
            for nid, w, a, d in zip(column, np.full(len(da), weights[k]), da, nrm, strict=True):
                share[int(nid)] = share.get(int(nid), 0.0) + w * a
                normal[int(nid)] = normal.get(int(nid), 0.0) + w * a * d
    ids = np.array([i for i in sorted(share) if share[i] > 1e-12 * max(share.values())])
    areas = np.array([share[i] for i in ids])
    normals = np.array([normal[i] for i in ids])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-300)
    return ids, areas, normals


# --- local axes (ADR 0037) -----------------------------------------------------------------


def local_axes(project, f):
    """The feature's CoordinateSystem, or None for global axes."""
    if f.axes is None:
        return None
    from puzzle.model.schema import CoordinateSystem

    return project.get(f.axes, CoordinateSystem)


def global_force(project, f) -> list[float]:
    """A force feature's total in global components (its axes must be Cartesian)."""
    v = [f.values[a].to_si() if a in f.values else 0.0 for a in "xyz"]
    axes = local_axes(project, f)
    if axes is None:
        return v
    if axes.type != "cartesian":
        raise InputError(f"a force in cylindrical axes isn't defined (on {f.selection})")
    return (axes.rotation() @ np.asarray(v)).tolist()


def global_displacement(project, f, xyz) -> np.ndarray:
    """A fully prescribed displacement in local axes, in global components at points."""
    axes = local_axes(project, f)
    local = [f.values[a].to_si() for a in "xyz"]
    return axes.to_global(local, xyz)


def globalize(project, features):
    """Features with Cartesian local axes in global components where that is a constant
    vector: forces, and displacements prescribing x, y and z. The rest keep their axes
    (cylindrical, or some components only) for the adapter to write."""
    from puzzle.model.schema import Quantity

    out = []
    for f in features:
        axes = local_axes(project, f)
        full = all(a in f.values for a in "xyz")
        if (
            axes is not None
            and axes.type == "cartesian"
            and (f.type == "force" or (f.type == "displacement" and full))
        ):
            local = [f.values[a].to_si() if a in f.values else 0.0 for a in "xyz"]
            unit = "N" if f.type == "force" else "m"
            g = axes.rotation() @ np.asarray(local)
            values = {a: Quantity(value=float(v), unit=unit) for a, v in zip("xyz", g, strict=True)}
            f = f.model_copy(update={"values": values, "axes": None})
        out.append(f)
    return out


def cylindrical_expressions(axes, local, coords: tuple[str, str, str]) -> list[str]:
    """The global components of a vector given radially, tangentially and axially in
    cylindrical axes, as expressions in the coordinate names `coords` (for solvers that
    take formulas: Code_Aster's X, Y, Z; Elmer's tx(0), tx(1), tx(2))."""
    a = np.asarray(axes.axis, float) / np.linalg.norm(axes.axis)
    o = np.asarray(axes.origin, float)
    ur, ut, uz = (float(v) for v in local)
    d = [f"({c}-({o[i]:.12g}))" for i, c in enumerate(coords)]
    dot = "+".join(f"{d[i]}*({a[i]:.12g})" for i in range(3))
    p = [f"({d[i]}-({dot})*({a[i]:.12g}))" for i in range(3)]
    norm = "sqrt(" + "+".join(f"{q}*{q}" for q in p) + ")"
    er = [f"({q}/{norm})" for q in p]
    et = [
        f"(({a[1]:.12g})*{er[2]}-({a[2]:.12g})*{er[1]})",
        f"(({a[2]:.12g})*{er[0]}-({a[0]:.12g})*{er[2]})",
        f"(({a[0]:.12g})*{er[1]}-({a[1]:.12g})*{er[0]})",
    ]
    return [f"{ur:.12g}*{er[i]}+{ut:.12g}*{et[i]}+{uz:.12g}*({a[i]:.12g})" for i in range(3)]


@dataclass
class SectionCut:
    """A bolt's pretension section cut through the mesh (ADR 0037): `doubles` maps each
    section node to its twin, which the elements on the normal's side (`moved`) use
    instead; `ref` is the reference node whose first dof is the section's opening."""

    ref: int
    normal: np.ndarray
    centre: np.ndarray
    doubles: dict[int, int]
    moved: set[int]


def section_cuts(comp, mesh: Mesh, refs: dict) -> dict:
    """id(feature) -> SectionCut for each pretension feature; `refs` gives each its
    reference node id, and twins are numbered after the largest of them."""
    next_id = max(refs.values(), default=0) + 1
    cuts = {}
    for f in [f for ph in comp.physics for f in ph.features if f.type in PRETENSION]:
        group = mesh.groups.get(f.selection)
        if group is None:
            raise InputError(f"bolt pretension on '{f.selection}': no such selection")
        if group.dim != 2:
            raise InputError(f"bolt pretension on '{f.selection}': pick the section face")
        section = {int(n) for n in group.node_ids}
        normal = section_normal(group, mesh)
        centre = mesh.node_coords(group.node_ids).mean(axis=0)
        moved = set()
        for b in mesh.blocks:
            if b.dim != 3:
                continue
            touching = np.isin(b.connectivity, list(section)).any(axis=1)
            for eid, row in zip(b.ids[touching], b.connectivity[touching], strict=True):
                if (mesh.node_coords(row).mean(axis=0) - centre) @ normal > 0:
                    moved.add(int(eid))
        if not moved:
            raise InputError(f"bolt pretension on '{f.selection}': no elements beside it")
        doubles = {n: next_id + i for i, n in enumerate(sorted(section))}
        next_id += len(doubles)
        cuts[id(f)] = SectionCut(refs[id(f)], normal, centre, doubles, moved)
    return cuts


def section_normal(group, mesh: Mesh) -> np.ndarray:
    """A flat section's unit normal (either way round)."""
    tri = [
        mesh.node_coords(b.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        for b in surface_blocks(group, mesh)
    ]
    xyz = np.concatenate(tri)
    each = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    each /= np.linalg.norm(each, axis=1, keepdims=True)
    each[each @ each[0] < 0] *= -1  # one orientation
    n = each.mean(axis=0)
    n /= np.linalg.norm(n)
    if (each @ n).min() < 1 - 1e-8:
        raise InputError("a pretension section must be flat (a plane cut through the bolt)")
    return n


def mark_mesh_nodes(input_file, mesh: Mesh) -> None:
    """Leave the mesh's largest node id beside the input, so results at helper nodes
    added past it (rigid bodies, springs' grounds, pretension twins) can be dropped."""
    input_file.with_suffix(".meshnodes").write_text(str(int(mesh.node_ids.max())))


def drop_helper_nodes(results, input_file):
    """Results without the helper nodes `mark_mesh_nodes` recorded (ADR 0037)."""
    last = input_file.with_suffix(".meshnodes")
    if not last.exists():
        return results
    top = int(last.read_text())
    for step in results.steps:
        for name, f in list(step.fields.items()):
            if f.location == "node" and f.ids.size and f.ids.max() > top:
                keep = f.ids <= top
                step.fields[name] = type(f)(
                    f.name, f.location, f.components, f.ids[keep], f.values[keep]
                )
    return results


# --- beam fasteners (ADR 0027) -------------------------------------------------------------


@dataclass
class FastenerEnd:
    """Where a fastener meets a selection: its centre (a hole face's is on its axis), and
    the selection's nodes with their weights (∫N dA over the faces, summing to 1)."""

    centre: np.ndarray
    nodes: np.ndarray
    weights: np.ndarray


def fastener_end(selection: str, mesh: Mesh, what: str) -> FastenerEnd:
    group = mesh.groups.get(selection)
    if group is None:
        raise InputError(f"{what}: no selection '{selection}' in the mesh")
    if group.dim != 2:
        raise InputError(f"{what}: '{selection}' must be a face selection (a hole's face)")
    ids, areas, _normals = support_nodes(group, mesh)
    weights = areas / areas.sum()
    centre = weights @ mesh.node_coords(ids)
    return FastenerEnd(centre, ids, weights)


def rbe3_rows(end: FastenerEnd, mesh: Mesh) -> list[list[tuple[int, int, float]]]:
    """A distributing (RBE3) end's relations, one row per dof of the reference point:
    its translation is the weighted mean of the nodes' (dofs 1-3), its rotation the
    least-squares rotation of the nodes about the centre (dofs 4-6), so rigid motions
    are exact and it adds no stiffness. Row k: [(node, dof, coefficient)] with
    reference dof k = Σ coefficient × u(node, dof)."""
    r = mesh.node_coords(end.nodes) - end.centre
    w = end.weights
    inertia = (w * (r * r).sum(axis=1)).sum() * np.eye(3) - (w[:, None] * r).T @ r
    if np.linalg.cond(inertia) > 1e12:
        raise InputError("a distributing end needs nodes spread over a face, not on a line")
    inv = np.linalg.inv(inertia)
    rows: list[list[tuple[int, int, float]]] = []
    for k in range(3):
        rows.append([(int(n), k + 1, float(wi)) for n, wi in zip(end.nodes, w, strict=True)])
    for k in range(3):
        row = []
        for n, wi, ri in zip(end.nodes, w, r, strict=True):
            # θ = I⁻¹ Σ w r × u; (r × u)_m = Σ_j skew(r)_mj u_j
            skew = np.array([[0, -ri[2], ri[1]], [ri[2], 0, -ri[0]], [-ri[1], ri[0], 0]])
            coef = wi * (inv[k] @ skew)
            row += [(int(n), j + 1, float(c)) for j, c in enumerate(coef) if abs(c) > 1e-14]
        rows.append(row)
    return rows


def fastener_frame(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The unit axis from a to b, and a unit direction square to it (the section's 1-axis)."""
    axis = b - a
    length = np.linalg.norm(axis)
    if length < 1e-12:
        raise InputError("a fastener's two ends are at the same point")
    axis /= length
    side = np.cross(axis, [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0])
    return axis, side / np.linalg.norm(side)


def bolt_mesh(a: np.ndarray, b: np.ndarray, diameter: float, layers: int = 1, split=False):
    """A fastener as a small solid (ADR 0027): a round bar of quadratic tets from a to b,
    in two halves sharing a mid-plane when `split` (a pretension section). -> (coordinates
    (n, 3), tet10 connectivity in Gmsh order (0-based), the indices of the nodes on the
    face at a, at b, and on the mid-plane (empty unless split))."""
    import gmsh

    from puzzle.geometry.session import gmsh_session

    length = float(np.linalg.norm(b - a))
    with gmsh_session("fastener"):
        if split:
            mid = (a + b) / 2
            one = gmsh.model.occ.addCylinder(*a, *(mid - a), diameter / 2)
            two = gmsh.model.occ.addCylinder(*mid, *(b - mid), diameter / 2)
            gmsh.model.occ.fragment([(3, one)], [(3, two)])
        else:
            gmsh.model.occ.addCylinder(*a, *(b - a), diameter / 2)
        gmsh.model.occ.synchronize()
        size = min(diameter / 3, length / max(layers, 1) / (4 if split else 2))
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)
        ids, xyz, _ = gmsh.model.mesh.getNodes()
        types, _tags, nodes = gmsh.model.mesh.getElements(3)
    ids = np.asarray(ids, dtype=np.int64)
    xyz = np.asarray(xyz).reshape(-1, 3)
    index = {int(n): i for i, n in enumerate(ids)}
    conn = np.concatenate([np.asarray(n) for t, n in zip(types, nodes, strict=True) if t == 11])
    tets = np.array([index[int(n)] for n in conn], dtype=np.int64).reshape(-1, 10)
    axis = (b - a) / length
    along = (xyz - a) @ axis
    tol = 1e-9 * max(length, diameter)
    middle = np.flatnonzero(np.abs(along - length / 2) < tol) if split else np.array([], int)
    return xyz, tets, np.flatnonzero(along < tol), np.flatnonzero(along > length - tol), middle


def split_bolt(xyz: np.ndarray, tets: np.ndarray, middle: np.ndarray, a, b, first_id: int):
    """Cut a split bolt at its mid-plane for a pretension: the mid-plane nodes get twins
    (ids from `first_id`), which the tets of the half towards b use. -> (tets with twins
    as indices past the bolt's own nodes, {node index: twin index}, twin coordinates)."""
    axis = (b - a) / np.linalg.norm(b - a)
    centre = (a + b) / 2
    twin = {int(m): len(xyz) + k for k, m in enumerate(middle)}
    out = tets.copy()
    beyond = (xyz[tets[:, :4]].mean(axis=1) - centre) @ axis > 0
    for row in np.flatnonzero(beyond):
        out[row] = [twin.get(int(n), n) for n in tets[row]]
    return out, twin, xyz[middle]


FASTENER_COMPONENTS = [
    "axial", "shear", "torsion", "bending", "Fx", "Fy", "Fz", "Mx", "My", "Mz",
]  # fmt: skip


def fastener_values(reactions: dict, ends: list[dict]) -> np.ndarray:
    """Each fastener's forces at its first end (n, 10; FASTENER_COMPONENTS) from the
    reactions on its bolt's end-face nodes (node -> vector): a reaction is the bolt
    elements' resisting force, so the bolt pulls on the part with the opposite. Summed
    about the end's centre: exact, by equilibrium. Axial is positive in tension."""
    values = []
    for e in ends:
        axis, end = np.asarray(e["axis"]), e["a"]
        arm = np.asarray(end["xyz"]) - np.asarray(end["centre"])
        forces = -np.array([reactions.get(n, np.zeros(3)) for n in end["nodes"]])
        force, moment = forces.sum(axis=0), np.cross(arm, forces).sum(axis=0)
        along, twist = float(force @ axis), float(moment @ axis)
        values.append(
            [
                along,
                float(np.linalg.norm(force - along * axis)),
                twist,
                float(np.linalg.norm(moment - twist * axis)),
                *force,
                *moment,
            ]
        )
    return np.array(values)


def check_rigid_ends(fasteners, mesh: Mesh, held: set[int] | None = None) -> None:
    """A rigid end can't take nodes a fixed or displaced selection holds: the solver
    would see one dof held twice. Refused with the fix."""
    if held is None:
        return
    for f in fasteners:
        if f.ends != "rigid":
            continue
        for sel in (f.source, f.target):
            group = mesh.groups.get(sel)
            if group is not None and held & {int(n) for n in group.node_ids}:
                raise InputError(
                    f"fastener '{f.name}': '{sel}' shares nodes with a fixed or displaced "
                    "selection; a rigid end can't also be held there (use distributing "
                    "ends, or hold another face)",
                    code="unsupported",
                )
