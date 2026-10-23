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

STRUCTURAL = {"fixed", "displacement", "force", "pressure"}
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
